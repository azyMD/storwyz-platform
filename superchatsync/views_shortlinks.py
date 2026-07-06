from django.contrib.admin.views.decorators import staff_member_required
from django.db import transaction
from django.http import Http404, HttpResponse, HttpResponseRedirect
from django.utils import timezone
from django.utils.html import escape

from .models import ShortLink, ShortLinkClick
from .shortlinks import short_url_for_code


PREVIEW_USER_AGENT_PARTS = (
    "facebookexternalhit",
    "facebot",
    "twitterbot",
    "slackbot",
    "telegrambot",
    "linkedinbot",
    "discordbot",
    "googlebot",
    "bingbot",
    "crawler",
    "spider",
    "preview",
)


def _client_ip(request):
    cf_ip = request.META.get("HTTP_CF_CONNECTING_IP")
    if cf_ip:
        return cf_ip.strip()
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.META.get("REMOTE_ADDR")


def _is_preview_request(request):
    user_agent = str(request.META.get("HTTP_USER_AGENT") or "").casefold()
    if request.method == "HEAD":
        return True
    return any(part in user_agent for part in PREVIEW_USER_AGENT_PARTS)


def _send_click_thank_you(link, click):
    from .superchat_safe_send import send_text_message_to_conversation

    product_focus = link.product_id or (f"business:{link.business_slug}" if link.business_slug else "")
    try:
        result = send_text_message_to_conversation(
            link.conversation_id,
            link.thank_you_body,
            product_id=product_focus,
            intent="shortlink_click_thank_you",
            expected_phone=link.phone or None,
        )
    except Exception as exc:
        error = str(exc)[:2000]
        ShortLink.objects.filter(link_id=link.link_id).update(last_thank_you_error=error)
        ShortLinkClick.objects.filter(click_id=click.click_id).update(
            thank_you_result={"ok": False, "error": error}
        )
        return

    now = timezone.now()
    ShortLink.objects.filter(link_id=link.link_id).update(
        thank_you_sent_at=now,
        thank_you_message_id=result.get("message_id") or "",
        last_thank_you_error="",
    )
    ShortLinkClick.objects.filter(click_id=click.click_id).update(
        thank_you_result={"ok": True, "result": result}
    )


def shortlink_redirect(request, code):
    now = timezone.now()
    should_send_thank_you = False

    with transaction.atomic():
        try:
            link = ShortLink.objects.select_for_update().get(code=code)
        except ShortLink.DoesNotExist as exc:
            raise Http404("Short link not found.") from exc

        if not link.active or link.is_expired:
            raise Http404("Short link is inactive.")

        is_preview = _is_preview_request(request)
        click = ShortLinkClick.objects.create(
            link=link,
            clicked_at=now,
            ip_address=_client_ip(request),
            user_agent=(request.META.get("HTTP_USER_AGENT") or "")[:4000],
            referer=(request.META.get("HTTP_REFERER") or "")[:4000],
            request_method=request.method,
            query_params={key: request.GET.getlist(key) for key in request.GET.keys()},
            is_preview=is_preview,
            metadata={
                "path": request.path,
                "host": request.get_host(),
            },
        )

        link.click_count = (link.click_count or 0) + 1
        link.first_clicked_at = link.first_clicked_at or now
        link.last_clicked_at = now

        if (
            link.thank_you_enabled
            and not is_preview
            and request.method == "GET"
            and link.conversation_id
            and not link.thank_you_attempted_at
            and not link.thank_you_sent_at
        ):
            link.thank_you_attempted_at = now
            click.thank_you_queued = True
            should_send_thank_you = True

        link.save(
            update_fields=[
                "click_count",
                "first_clicked_at",
                "last_clicked_at",
                "thank_you_attempted_at",
                "updated_at",
            ]
        )
        if click.thank_you_queued:
            click.save(update_fields=["thank_you_queued"])

    if should_send_thank_you:
        _send_click_thank_you(link, click)

    return HttpResponseRedirect(link.target_url)


@staff_member_required
def shortlinks_dashboard(request):
    links = ShortLink.objects.order_by("-created_at")[:100]
    rows = []
    for link in links:
        rows.append(
            f"""
            <tr>
                <td><code>{escape(link.code)}</code></td>
                <td><a href="{escape(short_url_for_code(link.code))}" target="_blank">{escape(short_url_for_code(link.code))}</a></td>
                <td>{escape(link.business_slug or "-")}</td>
                <td>{escape(link.conversation_id or "-")}</td>
                <td>{escape(link.product_name or link.product_id or "-")}</td>
                <td>{escape(str(link.click_count or 0))}</td>
                <td>{escape(link.first_clicked_at.strftime("%Y-%m-%d %H:%M") if link.first_clicked_at else "-")}</td>
                <td>{escape("sent" if link.thank_you_sent_at else ("attempted" if link.thank_you_attempted_at else "-"))}</td>
                <td><a href="/admin/superchatsync/shortlink/{escape(str(link.link_id))}/change/">Admin</a></td>
            </tr>
            """
        )

    return HttpResponse(
        f"""
        <!doctype html>
        <html lang="en">
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>Shortlinks</title>
            <style>
                body {{ font-family: system-ui, -apple-system, sans-serif; margin: 24px; color: #111; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 18px; font-size: 14px; }}
                th, td {{ border-bottom: 1px solid #ddd; padding: 9px 8px; text-align: left; vertical-align: top; }}
                th {{ background: #f6f6f6; }}
                code {{ font-size: 13px; }}
                .top {{ display: flex; justify-content: space-between; align-items: baseline; gap: 16px; flex-wrap: wrap; }}
                .muted {{ color: #666; }}
            </style>
        </head>
        <body>
            <div class="top">
                <div>
                    <h1>Shortlinks</h1>
                    <p class="muted">Recent tracked redirects and WhatsApp click thank-you status.</p>
                </div>
                <p><a href="/admin/superchatsync/shortlink/">Django Admin</a></p>
            </div>
            <table>
                <thead>
                    <tr>
                        <th>Code</th>
                        <th>Short URL</th>
                        <th>Business</th>
                        <th>Conversation</th>
                        <th>Product</th>
                        <th>Clicks</th>
                        <th>First click</th>
                        <th>Thanks</th>
                        <th></th>
                    </tr>
                </thead>
                <tbody>{''.join(rows) or '<tr><td colspan="9">No shortlinks yet.</td></tr>'}</tbody>
            </table>
        </body>
        </html>
        """,
        content_type="text/html; charset=utf-8",
    )
