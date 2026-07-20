from django.contrib import admin
from django.contrib.admin.views.decorators import staff_member_required
from django.db import transaction
from django.http import HttpResponse, HttpResponseBadRequest
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, redirect
from django.urls import path
from django.conf import settings
from django.conf.urls.static import static
from django.utils import timezone
from django.utils.html import escape
import uuid

from superchatsync.peeko_admin import peeko_admin_site
from superchatsync.landing_leads import (
    landing_lead_api,
    landing_lead_detail,
    landing_leads_dashboard,
    landing_leads_login,
    landing_leads_logout,
    landing_product_mapping_send_pending,
    landing_product_mappings,
)
from superchatsync.views_health import healthz, readyz
from superchatsync.views_shortlinks import shortlink_redirect, shortlinks_dashboard



urlpatterns = [
    path("healthz/", healthz, name="healthz"),
    path("readyz/", readyz, name="readyz"),
    path("r/<slug:code>/", shortlink_redirect, name="shortlink_redirect"),
    path("shortlinks/", shortlinks_dashboard, name="shortlinks_dashboard"),
    path("api/landing-leads/", landing_lead_api, name="landing_lead_api"),
    path("landing-leads/", landing_leads_dashboard, name="landing_leads_dashboard"),
    path("landing-leads/login/", landing_leads_login, name="landing_leads_login"),
    path("landing-leads/logout/", landing_leads_logout, name="landing_leads_logout"),
    path("landing-leads/products/", landing_product_mappings, name="landing_product_mappings"),
    path(
        "landing-leads/products/<uuid:mapping_id>/send-pending/",
        landing_product_mapping_send_pending,
        name="landing_product_mapping_send_pending",
    ),
    path("landing-leads/<uuid:lead_id>/", landing_lead_detail, name="landing_lead_detail"),
    path("peeko-admin/", peeko_admin_site.urls),
    path("admin/", admin.site.urls),
]


@staff_member_required
def legacy_ai_roadmap_page(request):
    from superchatsync.models import AiResponseProcessRun

    mode = request.GET.get("mode", "safe")
    queryset = AiResponseProcessRun.objects.order_by("-created_at")
    legacy_statuses = ("bricks_approved", "bricks_best_effort", "failopen_sent")

    reviewed_statuses = ("human_approved_review_only", "human_rejected", "test_sending", "test_sent")

    if mode == "all":
        description = "Include și rânduri legacy. WhatsApp send este în continuare dezactivat pentru safe review."
    elif mode == "reviewed":
        queryset = queryset.filter(status__in=reviewed_statuses)
        description = "Drafturi aprobate sau respinse manual. Aprobarea nu trimite mesajul pe WhatsApp."
    elif mode == "legacy":
        queryset = queryset.filter(status__in=legacy_statuses)
        description = "Rânduri istorice din pipeline-ul vechi, utile doar pentru audit."
    else:
        mode = "safe"
        queryset = queryset.filter(status__in=("approved_review_only", "needs_review"))
        description = "Doar run-urile noi review-only. WhatsApp send este în continuare dezactivat."

    safe_active = "active" if mode == "safe" else ""
    reviewed_active = "active" if mode == "reviewed" else ""
    all_active = "active" if mode == "all" else ""
    legacy_active = "active" if mode == "legacy" else ""
    runs = queryset[:50]
    rows = []

    for run in runs:
        body = (run.final_body or "").strip()
        if len(body) > 260:
            body = body[:260] + "..."

        rows.append(
            f"""
            <tr>
                <td><code>{escape(str(run.run_id)[:8])}</code></td>
                <td><span class="status status-{escape((run.status or 'unknown').replace('_', '-'))}">{escape(run.status or "-")}</span></td>
                <td>{escape(str(run.final_score or "-"))}</td>
                <td>{escape(run.final_action or "-")}</td>
                <td>{escape(run.product_id or "-")}</td>
                <td>{escape(run.created_at.strftime("%Y-%m-%d %H:%M") if run.created_at else "-")}</td>
                <td>{escape(body or "-")}</td>
                <td><a class="review-link" href="/ai-debug/review/{escape(str(run.run_id))}/">Review</a></td>
            </tr>
            """
        )

    table_rows = "\n".join(rows) or '<tr><td colspan="8">Nu există încă AI decisions.</td></tr>'

    return HttpResponse(
        f"""
        <!doctype html>
        <html lang="ro">
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>AI Roadmap</title>
            <style>
                body {{ font-family: system-ui, -apple-system, sans-serif; margin: 24px; line-height: 1.45; color: #111; }}
                a {{ color: #0b57d0; }}
                .top {{ display: flex; align-items: baseline; justify-content: space-between; gap: 16px; flex-wrap: wrap; }}
                table {{ width: 100%; border-collapse: collapse; margin-top: 18px; font-size: 14px; }}
                th, td {{ border-bottom: 1px solid #ddd; padding: 10px 8px; text-align: left; vertical-align: top; }}
                th {{ background: #f6f6f6; position: sticky; top: 0; }}
                code {{ font-size: 13px; }}
                .muted {{ color: #666; }}
                .tabs {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 12px; }}
                .tabs a {{ border: 1px solid #ccc; border-radius: 6px; color: #111; padding: 7px 10px; text-decoration: none; }}
                .tabs a.active {{ background: #111; border-color: #111; color: white; }}
                .status {{ border-radius: 999px; display: inline-block; font-size: 12px; padding: 3px 8px; white-space: nowrap; }}
                .status-approved-review-only {{ background: #e8f5e9; color: #1b5e20; }}
                .status-needs-review {{ background: #fff3e0; color: #8a4b00; }}
                .status-human-approved-review-only {{ background: #dff5e5; color: #145c2e; }}
                .status-human-rejected {{ background: #ffe5e5; color: #8b1a1a; }}
                .status-failopen-sent {{ background: #ffebee; color: #8b0000; }}
                .status-bricks-approved, .status-bricks-best-effort {{ background: #eef2ff; color: #25307a; }}
                .review-link {{ font-weight: 650; }}
                @media (max-width: 760px) {{
                    body {{ margin: 16px; }}
                    table {{ display: block; overflow-x: auto; white-space: nowrap; }}
                }}
            </style>
        </head>
        <body>
            <div class="top">
                <div>
                    <h1>AI Roadmap</h1>
                    <p class="muted">{escape(description)}</p>
                    <div class="tabs">
                        <a class="{safe_active}" href="/ai-debug/roadmap/?mode=safe">Safe review</a>
                        <a class="{reviewed_active}" href="/ai-debug/roadmap/?mode=reviewed">Revizuite</a>
                        <a class="{all_active}" href="/ai-debug/roadmap/?mode=all">Toate</a>
                        <a class="{legacy_active}" href="/ai-debug/roadmap/?mode=legacy">Legacy</a>
                    </div>
                </div>
                <p><a href="/admin/superchatsync/aidecisionroadmap/">Django Admin view</a></p>
            </div>

            <table>
                <thead>
                    <tr>
                        <th>Run</th>
                        <th>Status</th>
                        <th>Score</th>
                        <th>Action</th>
                        <th>Product</th>
                        <th>Created</th>
                        <th>Draft preview</th>
                        <th>Details</th>
                    </tr>
                </thead>
                <tbody>
                    {table_rows}
                </tbody>
            </table>
        </body>
        </html>
        """,
        content_type="text/html; charset=utf-8",
    )


@staff_member_required
def ai_review_page(request, run_id):
    from superchatsync.models import AiResponseProcessRun, AiResponseProcessStep, Message

    run = get_object_or_404(AiResponseProcessRun, run_id=run_id)
    enrichment_step = (
        run.steps.filter(step_name="response_enrichment")
        .order_by("-created_at")
        .first()
    )
    enrichment = enrichment_step.output_json or {} if enrichment_step else {}
    creative = enrichment.get("creative") or {}
    context_step = (
        run.steps.filter(step_name="conversation_context")
        .order_by("-created_at")
        .first()
    )
    context_snapshot = context_step.output_json or {} if context_step else {}
    judge_step = run.steps.filter(step_name="judge").order_by("-created_at").first()
    judge_output = judge_step.output_json or {} if judge_step else {}
    repetition = judge_output.get("repetition") or {}

    if request.method == "POST":
        decision = request.POST.get("decision", "")

        if decision == "send_test":
            from superchatsync.superchat_safe_send import send_reviewed_test

            try:
                send_reviewed_test(run.run_id)
            except Exception:
                return redirect(f"/ai-debug/review/{run.run_id}/?saved=send_error")
            return redirect(f"/ai-debug/review/{run.run_id}/?saved=test_sent")

        draft = request.POST.get("draft", "").strip()
        note = request.POST.get("note", "").strip()[:1000]

        if decision not in {"approve", "reject"}:
            return HttpResponseBadRequest("Decizie invalidă.")
        if decision == "approve" and not draft:
            return HttpResponseBadRequest("Draftul nu poate fi gol la aprobare.")
        if len(draft) > 5000:
            return HttpResponseBadRequest("Draftul este prea lung.")

        original_body = (run.final_body or "").strip()
        approved = decision == "approve"
        now = timezone.now()

        with transaction.atomic():
            if approved:
                run.final_body = draft
                run.status = "human_approved_review_only"
                run.final_action = "review_approved"
            else:
                run.status = "human_rejected"
                run.final_action = "no_send"
            run.save(update_fields=["final_body", "status", "final_action"])

            AiResponseProcessStep.objects.create(
                step_id=uuid.uuid4(),
                run=run,
                conversation_id=run.conversation_id,
                product_id=run.product_id,
                step_name="human_review",
                attempt=1,
                input_json={"previous_status": request.POST.get("previous_status", "")},
                output_json={
                    "decision": decision,
                    "reviewer": request.user.get_username(),
                    "edited": approved and draft != original_body,
                    "note": note,
                    "buttons": list(run.final_buttons or []),
                    "creative_asset_id": creative.get("asset_id"),
                    "send_enabled": False,
                },
                approved=approved,
                score=run.final_score,
                severity="info" if approved else "warning",
                action="review_approved" if approved else "no_send",
                fail_reasons=[],
                blocking_issues=[],
                feedback_for_repair=note,
                created_at=now,
            )

        return redirect(f"/ai-debug/review/{run.run_id}/?saved={decision}")

    recent_messages = list(
        Message.objects.filter(conversation_id=run.conversation_id)
        .order_by("-sent_at")[:12]
    )
    recent_messages.reverse()

    message_rows = []
    for message in recent_messages:
        sender = message.sender_name or message.sender_type or "Necunoscut"
        css_class = "client" if message.is_client_reply else "operator"
        sent_at = message.sent_at.strftime("%Y-%m-%d %H:%M") if message.sent_at else ""
        text = message.message_text or message.button_clicked or f"[{message.message_type or 'mesaj'}]"
        message_rows.append(
            f'<div class="message {css_class}"><div class="message-meta">'
            f'{escape(sender)} · {escape(sent_at)}</div><div>{escape(text)}</div></div>'
        )

    messages_html = "".join(message_rows) or '<p class="muted">Nu există mesaje sincronizate.</p>'
    buttons_html = "".join(
        f'<span class="quick-reply">{escape(str(button))}</span>'
        for button in (run.final_buttons or [])
    ) or '<span class="muted">Fără CTA configurat.</span>'
    creative_html = '<p class="muted">Niciun creative selectat pentru acest context.</p>'
    if creative:
        media = ""
        public_url = creative.get("public_url")
        if not public_url and creative.get("asset_id"):
            public_url = f'/ai-debug/creative/{escape(str(creative.get("asset_id")))}/preview/'
        asset_type = creative.get("asset_type")
        if public_url and asset_type == "image":
            media = f'<img class="creative-media" src="{escape(public_url)}" alt="{escape(creative.get("title") or "Creative produs")}">'
        elif public_url and asset_type == "video":
            media = f'<video class="creative-media" src="{escape(public_url)}" controls preload="metadata"></video>'
        creative_html = (
            f'{media}<strong>{escape(creative.get("title") or "Creative produs")}</strong>'
            f'<div class="muted">{escape(creative.get("asset_type") or "-")} · {escape(creative.get("selection_reason") or "selectat contextual")}</div>'
        )
    topics_html = "".join(
        f'<span class="context-chip">{escape(str(topic))}</span>'
        for topic in (context_snapshot.get("answered_topics") or [])
    ) or '<span class="muted">Niciun subiect anterior detectat.</span>'
    context_html = (
        f'<p>{escape(context_snapshot.get("summary") or "Contextul va fi disponibil la următoarea generare.")}</p>'
        f'<div class="context-chips">{topics_html}</div>'
        f'<p class="muted">Mesaje recente: {escape(str(context_snapshot.get("recent_message_count", 0)))} · '
        f'Răspunsuri anterioare comparate: {escape(str(context_snapshot.get("previous_reply_count", 0)))} · '
        f'Scor repetiție: {escape(str(repetition.get("score", 0)))}</p>'
    )
    audit_steps = run.steps.filter(step_name="human_review").order_by("-created_at")[:10]
    audit_rows = []
    for step in audit_steps:
        output = step.output_json or {}
        audit_rows.append(
            "<tr>"
            f"<td>{escape(step.created_at.strftime('%Y-%m-%d %H:%M') if step.created_at else '-')}</td>"
            f"<td>{escape(output.get('reviewer') or '-')}</td>"
            f"<td>{escape(output.get('decision') or '-')}</td>"
            f"<td>{'Da' if output.get('edited') else 'Nu'}</td>"
            f"<td>{escape(output.get('note') or '-')}</td>"
            "</tr>"
        )
    audit_html = "".join(audit_rows) or '<tr><td colspan="5">Nicio decizie manuală încă.</td></tr>'
    saved = request.GET.get("saved")
    notice = ""
    if saved == "approve":
        notice = '<div class="notice success">Draft aprobat pentru review. Nu a fost trimis pe WhatsApp.</div>'
    elif saved == "reject":
        notice = '<div class="notice rejected">Draft respins. Nu a fost trimis pe WhatsApp.</div>'
    elif saved == "test_sent":
        notice = '<div class="notice success">Mesajul de test a fost trimis exclusiv numărului din allowlist.</div>'
    elif saved == "send_error":
        notice = '<div class="notice rejected">Trimiterea testului a fost blocată sau a eșuat. Run-ul nu a fost marcat ca trimis.</div>'

    csrf_token = get_token(request)
    test_send_html = ""
    if run.status == "human_approved_review_only":
        test_send_html = f"""
            <form method="post" class="test-send-form">
                <input type="hidden" name="csrfmiddlewaretoken" value="{escape(csrf_token)}">
                <button class="send-test" type="submit" name="decision" value="send_test" onclick="return confirm('Trimiți acest mesaj către numărul unic din allowlist?')">Trimite test</button>
                <span>Exclusiv către numărul verificat din allowlist.</span>
            </form>
        """
    elif run.status == "test_sending":
        test_send_html = '<div class="notice">Trimiterea testului este în curs.</div>'
    elif run.status == "test_sent":
        test_send_html = '<div class="notice success">Acest run a fost deja trimis în test.</div>'
    created_at = run.created_at.strftime("%Y-%m-%d %H:%M") if run.created_at else "-"

    return HttpResponse(
        f"""
        <!doctype html>
        <html lang="ro">
        <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>Review AI draft</title>
            <style>
                * {{ box-sizing: border-box; }}
                body {{ font-family: system-ui, -apple-system, sans-serif; margin: 0; color: #151515; background: #f6f7f8; }}
                a {{ color: #075ab8; }}
                header {{ background: #fff; border-bottom: 1px solid #dfe2e5; padding: 18px max(20px, calc((100vw - 1180px) / 2)); }}
                header h1 {{ font-size: 24px; margin: 8px 0 4px; }}
                main {{ max-width: 1180px; margin: 0 auto; padding: 22px 20px 48px; }}
                .meta {{ color: #5c6268; font-size: 14px; }}
                .layout {{ display: grid; grid-template-columns: minmax(0, 1fr) minmax(340px, .75fr); gap: 20px; }}
                section {{ background: #fff; border: 1px solid #dfe2e5; border-radius: 7px; padding: 18px; margin-bottom: 20px; }}
                h2 {{ font-size: 17px; margin: 0 0 14px; }}
                label {{ display: block; font-weight: 650; margin: 0 0 7px; }}
                textarea {{ width: 100%; min-height: 210px; resize: vertical; border: 1px solid #aeb4ba; border-radius: 6px; padding: 12px; font: inherit; line-height: 1.5; }}
                textarea.note {{ min-height: 76px; margin-bottom: 14px; }}
                .actions {{ display: flex; gap: 10px; flex-wrap: wrap; }}
                button {{ border: 0; border-radius: 6px; padding: 10px 15px; font: inherit; font-weight: 700; cursor: pointer; }}
                button.approve {{ background: #176b39; color: #fff; }}
                button.reject {{ background: #a32929; color: #fff; }}
                button.send-test {{ background: #075ab8; color: #fff; }}
                .test-send-form {{ align-items: center; background: #eaf3ff; border: 1px solid #9cc2ed; border-radius: 6px; display: flex; gap: 12px; margin-bottom: 18px; padding: 12px; }}
                .test-send-form span {{ color: #31506f; font-size: 14px; }}
                .message {{ max-width: 88%; border-radius: 7px; padding: 10px 12px; margin: 8px 0; white-space: pre-wrap; }}
                .message.client {{ background: #eef3f7; margin-right: auto; }}
                .message.operator {{ background: #e8f5eb; margin-left: auto; }}
                .message-meta {{ color: #606870; font-size: 12px; margin-bottom: 4px; }}
                .client-request {{ white-space: pre-wrap; background: #f7f7f7; border-left: 3px solid #8b949e; padding: 12px; }}
                .quick-replies {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }}
                .quick-reply {{ background: #eef5ff; border: 1px solid #8db5ea; border-radius: 6px; color: #174d87; padding: 8px 11px; font-size: 14px; font-weight: 650; }}
                .creative-media {{ display: block; width: 100%; max-height: 360px; object-fit: contain; background: #111; border-radius: 6px; margin-bottom: 12px; }}
                .context-chips {{ display: flex; gap: 6px; flex-wrap: wrap; margin: 10px 0; }}
                .context-chip {{ background: #f0f1f2; border: 1px solid #d0d4d7; border-radius: 999px; padding: 4px 8px; font-size: 12px; }}
                .notice {{ border-radius: 6px; padding: 11px 13px; margin-bottom: 18px; font-weight: 650; }}
                .notice.success {{ background: #e4f4e8; color: #145c2e; }}
                .notice.rejected {{ background: #fde7e7; color: #842222; }}
                .send-warning {{ background: #fff6d9; border: 1px solid #ead386; border-radius: 6px; padding: 11px 13px; margin-bottom: 16px; }}
                .muted {{ color: #666; }}
                table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
                th, td {{ border-bottom: 1px solid #e4e4e4; padding: 8px; text-align: left; vertical-align: top; }}
                @media (max-width: 820px) {{
                    .layout {{ grid-template-columns: 1fr; }}
                    main {{ padding: 16px 12px 36px; }}
                    section {{ padding: 14px; }}
                }}
            </style>
        </head>
        <body>
            <header>
                <a href="/ai-debug/roadmap/?mode=safe">← Înapoi la Safe review</a>
                <h1>Review AI draft</h1>
                <div class="meta">Run {escape(str(run.run_id)[:8])} · produs {escape(run.product_id or '-')} · scor {escape(str(run.final_score or '-'))} · {escape(created_at)}</div>
            </header>
            <main>
                {notice}
                <div class="send-warning"><strong>Trimiterea automată este oprită.</strong> După aprobare, testarea este permisă doar către numărul din allowlist.</div>
                {test_send_html}
                <div class="layout">
                    <div>
                        <section>
                            <h2>Draft propus</h2>
                            <form method="post">
                                <input type="hidden" name="csrfmiddlewaretoken" value="{escape(csrf_token)}">
                                <input type="hidden" name="previous_status" value="{escape(run.status or '')}">
                                <label for="draft">Mesaj către client</label>
                                <textarea id="draft" name="draft" maxlength="5000">{escape(run.final_body or '')}</textarea>
                                <label for="note" style="margin-top:14px">Notă internă</label>
                                <textarea class="note" id="note" name="note" maxlength="1000" placeholder="Opțional: motivul editării sau respingerii"></textarea>
                                <div class="actions">
                                    <button class="approve" type="submit" name="decision" value="approve">Aprobă draftul</button>
                                    <button class="reject" type="submit" name="decision" value="reject">Respinge</button>
                                </div>
                            </form>
                        </section>
                        <section>
                            <h2>CTA quick replies</h2>
                            <div class="quick-replies">{buttons_html}</div>
                        </section>
                        <section>
                            <h2>Creative selectat</h2>
                            {creative_html}
                        </section>
                        <section>
                            <h2>Conversation Context</h2>
                            {context_html}
                        </section>
                        <section>
                            <h2>Cererea analizată</h2>
                            <div class="client-request">{escape(run.client_message or '-')}</div>
                        </section>
                    </div>
                    <div>
                        <section>
                            <h2>Context conversație</h2>
                            {messages_html}
                        </section>
                    </div>
                </div>
                <section>
                    <h2>Audit review</h2>
                    <div style="overflow-x:auto">
                        <table><thead><tr><th>Moment</th><th>Reviewer</th><th>Decizie</th><th>Editat</th><th>Notă</th></tr></thead><tbody>{audit_html}</tbody></table>
                    </div>
                </section>
            </main>
        </body>
        </html>
        """,
        content_type="text/html; charset=utf-8",
    )


@staff_member_required
def creative_preview(request, asset_id):
    from superchatsync.models import ProductCreativeAsset
    from superchatsync.superchat_safe_send import _get_json, get_config

    asset = get_object_or_404(ProductCreativeAsset, asset_id=asset_id, is_active=True)
    if asset.public_url:
        return redirect(asset.public_url)
    if not asset.superchat_file_id:
        return HttpResponseBadRequest("Creative-ul nu are fișier Superchat.")

    config = get_config()
    data = _get_json(config, f"/v1.0/files/{asset.superchat_file_id}")
    link = data.get("link") or {}
    link_url = link.get("url") if isinstance(link, dict) else link
    if not link_url:
        return HttpResponseBadRequest("Superchat nu a returnat un link de preview.")
    return redirect(link_url)


urlpatterns += [
    path("ai-debug/roadmap/", legacy_ai_roadmap_page, name="legacy_ai_roadmap_page"),
    path("ai-debug/review/<uuid:run_id>/", ai_review_page, name="ai_review_page"),
    path("ai-debug/creative/<uuid:asset_id>/preview/", creative_preview, name="creative_preview"),
]


if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)


# Superchat AI webhook
from django.urls import path as _superchat_path
from superchatsync.views_superchat_webhook import superchat_webhook as _superchat_webhook

urlpatterns += [
    _superchat_path("superchat/webhook/", _superchat_webhook, name="superchat_ai_webhook"),
]


# Catalog brochure builder
from superchatsync.catalog_builder import (
    catalog_admin,
    catalog_create,
    catalog_delete,
    catalog_login,
    catalog_logout,
    catalog_public,
    catalog_public_root,
)

urlpatterns += [
    path("catalog-admin/", catalog_admin, name="catalog_admin"),
    path("catalog-admin/login/", catalog_login, name="catalog_login"),
    path("catalog-admin/logout/", catalog_logout, name="catalog_logout"),
    path("catalog-admin/create/", catalog_create, name="catalog_create"),
    path(
        "catalog-admin/delete/<slug:product_slug>/<slug:country_code>/",
        catalog_delete,
        name="catalog_delete",
    ),
    path("catalog/<slug:product_slug>/<slug:country_code>/", catalog_public, name="catalog_public"),
    path("<slug:product_slug>/<slug:country_code>/", catalog_public_root, name="catalog_public_root"),
]
