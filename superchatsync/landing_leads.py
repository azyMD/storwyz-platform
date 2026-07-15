import hashlib
import ipaddress
import json
import os
import secrets
from decimal import Decimal, InvalidOperation
from functools import wraps

import requests
from django.contrib.auth.hashers import check_password
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponse, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, redirect
from django.utils import timezone
from django.utils.html import escape
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST

from superchatsync.landing_product_mapping import normalize_product_name, resolve_landing_product
from superchatsync.models import LandingLeadSubmission, LandingProductMapping


LANDING_LEADS_SESSION_KEY = "landing_leads_dashboard_authenticated"
FITSPACE_DEFAULT_URL = "https://fitexpress.space/api/neworder"
MAX_REQUEST_BYTES = 128 * 1024
MAX_UPSTREAM_RESPONSE_CHARS = 8000


def _text(value, max_length=2000):
    return str(value or "").strip()[:max_length]


def _json_safe_payload(value):
    if isinstance(value, dict):
        return {str(key)[:200]: _json_safe_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe_payload(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _request_payload(request):
    content_type = (request.content_type or "").lower()
    if "application/json" in content_type:
        try:
            decoded = json.loads(request.body.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return {}, {"body": f"Invalid JSON: {exc}"}
        if not isinstance(decoded, dict):
            return {}, {"body": "The JSON body must be an object."}
        return _json_safe_payload(decoded), {}
    return _json_safe_payload(request.POST.dict()), {}


def _positive_integer(value, field, errors):
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        errors[field] = "Must be a positive integer."
        return None
    if parsed <= 0:
        errors[field] = "Must be a positive integer."
        return None
    return parsed


def validate_landing_lead(payload):
    errors = {}
    customer_name = _text(payload.get("customer_name"), 300)
    customer_phone = _text(payload.get("customer_phone"), 80)
    customer_address = _text(payload.get("customer_address"), 2000)
    product = _text(payload.get("product"), 120)

    if len(customer_name) < 2:
        errors["customer_name"] = "Customer name is required."
    phone_digits = "".join(character for character in customer_phone if character.isdigit())
    if len(phone_digits) < 7:
        errors["customer_phone"] = "A valid customer phone is required."
    if not customer_address:
        errors["customer_address"] = "Customer address is required."
    if not product:
        errors["product"] = "Product name is required."

    customer_region = _positive_integer(payload.get("customer_region"), "customer_region", errors)
    quantity = _positive_integer(payload.get("quantity"), "quantity", errors)

    try:
        cost = Decimal(str(payload.get("cost", "")).strip())
        if not cost.is_finite() or cost < 0:
            raise InvalidOperation
        cost = cost.quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        errors["cost"] = "Cost must be zero or a positive number."
        cost = None

    if cost is not None:
        json_cost = int(cost) if cost == cost.to_integral() else float(cost)
    else:
        json_cost = None

    cleaned = {
        "customer_name": customer_name,
        "customer_phone": customer_phone,
        "customer_region": customer_region,
        "customer_address": customer_address,
        "quantity": quantity,
        "cost": json_cost,
        "product": product,
        "referral": _text(payload.get("referral") or "Landing", 300),
        "customer_comment": _text(payload.get("customer_comment"), 4000),
    }
    return cleaned, errors


def _source_ip(request):
    candidate = (
        request.META.get("HTTP_CF_CONNECTING_IP")
        or (request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip()
        or request.META.get("REMOTE_ADDR")
        or ""
    )
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def _external_order_id(response_text):
    try:
        payload = json.loads(response_text or "{}")
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, dict):
        return ""
    for key in ("id", "order_id", "orderId", "external_order_id"):
        if payload.get(key):
            return str(payload[key])[:300]
    return ""


def _forward_timeout():
    try:
        return min(max(int(os.getenv("LANDING_LEAD_FORWARD_TIMEOUT", "20")), 1), 60)
    except ValueError:
        return 20


def _json_cost(value):
    if value is None:
        return None
    decimal_value = Decimal(str(value))
    return int(decimal_value) if decimal_value == decimal_value.to_integral() else float(decimal_value)


def _fitspace_payload_for_lead(lead, sku):
    return {
        "customer_name": lead.customer_name or "",
        "customer_phone": lead.customer_phone or "",
        "customer_region": lead.customer_region,
        "customer_address": lead.customer_address or "",
        "quantity": lead.quantity,
        "cost": _json_cost(lead.cost),
        "product": str(sku),
        "referral": lead.referral or "Landing",
        "customer_comment": lead.customer_comment or "",
    }


def deliver_landing_lead(lead, mapping):
    forwarded_payload = _fitspace_payload_for_lead(lead, mapping.sku)
    forward_url = os.getenv("LANDING_LEAD_FORWARD_URL") or os.getenv("ORDER_WEBHOOK_URL") or FITSPACE_DEFAULT_URL
    lead.product_mapping = mapping
    lead.product_sku = mapping.sku
    lead.product_normalized = mapping.normalized_name
    lead.forwarded_payload = forwarded_payload
    lead.forward_url = forward_url
    lead.status = LandingLeadSubmission.STATUS_RECEIVED
    lead.upstream_http_status = None
    lead.upstream_response = None
    lead.external_order_id = None
    lead.error = None
    lead.sent_at = None
    try:
        response = requests.post(
            forward_url,
            json=forwarded_payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=_forward_timeout(),
        )
        lead.upstream_http_status = response.status_code
        lead.upstream_response = (response.text or "")[:MAX_UPSTREAM_RESPONSE_CHARS]
        lead.external_order_id = _external_order_id(response.text) or None
        if 200 <= response.status_code < 300:
            lead.status = LandingLeadSubmission.STATUS_SENT
            lead.sent_at = timezone.now()
        else:
            lead.status = LandingLeadSubmission.STATUS_FAILED
            lead.error = f"Fitspace returned HTTP {response.status_code}."
    except requests.RequestException as exc:
        lead.status = LandingLeadSubmission.STATUS_FAILED
        lead.error = _text(exc, 4000)
    lead.save(
        update_fields=[
            "product_mapping",
            "product_sku",
            "product_normalized",
            "forwarded_payload",
            "forward_url",
            "upstream_http_status",
            "upstream_response",
            "external_order_id",
            "status",
            "sent_at",
            "error",
            "updated_at",
        ]
    )
    return lead


def _cors(response):
    response["Access-Control-Allow-Origin"] = "*"
    response["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    response["Access-Control-Allow-Headers"] = "Content-Type, Accept"
    response["Cache-Control"] = "no-store"
    return response


@csrf_exempt
@require_http_methods(["POST", "OPTIONS"])
def landing_lead_api(request):
    if request.method == "OPTIONS":
        return _cors(HttpResponse(status=204))

    try:
        content_length = int(request.META.get("CONTENT_LENGTH") or 0)
    except ValueError:
        content_length = 0
    if content_length > MAX_REQUEST_BYTES:
        return _cors(JsonResponse({"ok": False, "error": "Request body is too large."}, status=413))

    payload, parse_errors = _request_payload(request)
    cleaned_payload, validation_errors = validate_landing_lead(payload)
    validation_errors.update(parse_errors)
    product_name = cleaned_payload.get("product") or ""
    if validation_errors:
        mapping = None
        normalized_product = normalize_product_name(product_name)
    else:
        mapping, normalized_product = resolve_landing_product(product_name)
    initial_status = LandingLeadSubmission.STATUS_RECEIVED
    if validation_errors:
        initial_status = LandingLeadSubmission.STATUS_VALIDATION_FAILED
    elif not mapping:
        initial_status = LandingLeadSubmission.STATUS_MAPPING_REQUIRED
    lead = LandingLeadSubmission.objects.create(
        status=initial_status,
        customer_name=cleaned_payload.get("customer_name") or None,
        customer_phone=cleaned_payload.get("customer_phone") or None,
        customer_region=cleaned_payload.get("customer_region"),
        customer_address=cleaned_payload.get("customer_address") or None,
        quantity=cleaned_payload.get("quantity"),
        cost=cleaned_payload.get("cost"),
        product=product_name or None,
        product_normalized=normalized_product or None,
        product_sku=mapping.sku if mapping else None,
        product_mapping=mapping,
        referral=cleaned_payload.get("referral") or None,
        customer_comment=cleaned_payload.get("customer_comment") or None,
        request_payload=payload,
        forwarded_payload={},
        validation_errors=validation_errors,
        source_ip=_source_ip(request),
        user_agent=_text(request.META.get("HTTP_USER_AGENT"), 2000) or None,
        origin=_text(request.META.get("HTTP_ORIGIN") or request.META.get("HTTP_REFERER"), 2000) or None,
    )

    if validation_errors:
        return _cors(
            JsonResponse(
                {
                    "ok": False,
                    "lead_id": str(lead.lead_id),
                    "status": lead.status,
                    "errors": validation_errors,
                },
                status=400,
            )
        )

    if not mapping:
        return _cors(
            JsonResponse(
                {
                    "ok": True,
                    "accepted": True,
                    "forwarded": False,
                    "lead_id": str(lead.lead_id),
                    "status": lead.status,
                    "product_received": lead.product,
                    "message": "Lead stored and waiting for a Product / SKU mapping.",
                },
                status=202,
            )
        )

    deliver_landing_lead(lead, mapping)

    response_status = 201 if lead.status == LandingLeadSubmission.STATUS_SENT else 502
    return _cors(
        JsonResponse(
            {
                "ok": lead.status == LandingLeadSubmission.STATUS_SENT,
                "lead_id": str(lead.lead_id),
                "status": lead.status,
                "product_received": lead.product,
                "product_sku": lead.product_sku,
                "upstream_http_status": lead.upstream_http_status,
                "external_order_id": lead.external_order_id,
            },
            status=response_status,
        )
    )


def _dashboard_authenticated(request):
    return request.session.get(LANDING_LEADS_SESSION_KEY) is True


def _dashboard_required(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not _dashboard_authenticated(request):
            return redirect("landing_leads_login")
        return view(request, *args, **kwargs)

    return wrapped


def _dashboard_response(body, status=200):
    response = HttpResponse(body, status=status, content_type="text/html; charset=utf-8")
    response["Cache-Control"] = "no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


def _base_html(title, content, actions=""):
    return f"""
    <!doctype html>
    <html lang="en">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>{escape(title)} · Storwyz</title>
      <style>
        :root {{ color-scheme:light; --bg:#f4f6f5; --panel:#fff; --ink:#17201d; --muted:#69746f; --line:#dce3df; --accent:#176b52; --danger:#a92f36; font-family:Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif; }}
        * {{ box-sizing:border-box; }} body {{ margin:0; color:var(--ink); background:var(--bg); }}
        header {{ min-height:68px; padding:14px clamp(16px,4vw,44px); display:flex; align-items:center; justify-content:space-between; gap:20px; color:#fff; background:#173f34; }}
        header strong {{ font-size:20px; }} header span {{ color:#cce0d9; font-size:13px; }} header a {{ color:#fff; }} .nav {{ display:flex; align-items:center; gap:14px; flex-wrap:wrap; }} .nav a {{ text-decoration:none; }} .nav a.active {{ border-bottom:2px solid #fff; }}
        main {{ width:min(1440px,96vw); margin:22px auto 40px; }} h1 {{ margin:0; font-size:28px; letter-spacing:0; }} h2 {{ margin:0 0 14px; font-size:18px; }} p {{ color:var(--muted); }}
        .titlebar {{ display:flex; align-items:end; justify-content:space-between; gap:16px; margin-bottom:18px; flex-wrap:wrap; }}
        .panel {{ border:1px solid var(--line); border-radius:8px; background:var(--panel); box-shadow:0 8px 24px rgba(25,48,40,.05); }}
        .stats {{ display:grid; grid-template-columns:repeat(5,minmax(140px,1fr)); gap:12px; margin-bottom:16px; }}
        .stat {{ padding:16px; }} .stat span {{ display:block; color:var(--muted); font-size:12px; text-transform:uppercase; }} .stat strong {{ display:block; margin-top:5px; font-size:26px; }}
        form.filters {{ padding:14px; display:flex; gap:10px; align-items:end; flex-wrap:wrap; margin-bottom:16px; }}
        label {{ display:block; color:var(--muted); font-size:12px; font-weight:700; }} input,select {{ width:100%; height:40px; margin-top:5px; padding:0 11px; border:1px solid var(--line); border-radius:7px; background:#fff; }}
        .grow {{ flex:1 1 320px; }} .field {{ min-width:190px; }} button,.button {{ min-height:40px; padding:0 14px; border:0; border-radius:7px; display:inline-flex; align-items:center; justify-content:center; color:#fff; background:var(--accent); font-weight:750; text-decoration:none; cursor:pointer; }}
        .button.secondary {{ color:var(--ink); background:#e8eeeb; }} .table-wrap {{ overflow:auto; }} table {{ width:100%; border-collapse:collapse; font-size:13px; }} th,td {{ padding:11px 12px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }} th {{ color:var(--muted); background:#f8faf9; font-size:11px; text-transform:uppercase; white-space:nowrap; }} td {{ white-space:nowrap; }} td.wrap {{ min-width:220px; white-space:normal; }}
        .status {{ padding:4px 8px; border-radius:999px; display:inline-block; font-size:11px; font-weight:800; }} .sent {{ color:#17603f; background:#dff4e9; }} .failed,.validation_failed {{ color:#8b252d; background:#fde7e8; }} .received {{ color:#72500a; background:#fff0c7; }} .mapping_required {{ color:#70460a; background:#ffedbd; }}
        .pagination {{ padding:14px; display:flex; align-items:center; justify-content:space-between; gap:12px; }} code,pre {{ font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }} pre {{ margin:0; padding:14px; overflow:auto; border:1px solid var(--line); border-radius:7px; background:#f7f9f8; white-space:pre-wrap; overflow-wrap:anywhere; }}
        .details {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:1px; overflow:hidden; }} .detail {{ min-height:76px; padding:14px; background:#fff; border-bottom:1px solid var(--line); }} .detail span {{ display:block; color:var(--muted); font-size:11px; text-transform:uppercase; }} .detail strong {{ display:block; margin-top:5px; overflow-wrap:anywhere; }}
        .stack {{ display:grid; gap:16px; }} .login {{ width:min(420px,92vw); margin:10vh auto; padding:24px; }} .error {{ color:var(--danger); }} .notice {{ padding:12px 14px; margin-bottom:16px; color:#17603f; background:#e2f4ea; border:1px solid #bddfca; border-radius:7px; }} .inline-form {{ display:inline; }} .inline-form button {{ min-height:32px; padding:0 10px; font-size:12px; }}
        @media(max-width:1000px) {{ .stats{{grid-template-columns:repeat(2,1fr)}} .details{{grid-template-columns:1fr}} header{{align-items:flex-start;flex-direction:column}} }}
      </style>
    </head>
    <body>
      <header><div><strong>Storwyz</strong><br><span>Landing lead delivery</span></div><div>{actions}</div></header>
      {content}
    </body>
    </html>
    """


def _dashboard_actions(active):
    leads_class = "active" if active == "leads" else ""
    products_class = "active" if active == "products" else ""
    return f"""
      <nav class="nav">
        <a class="{leads_class}" href="/landing-leads/">Leads</a>
        <a class="{products_class}" href="/landing-leads/products/">Product / SKU</a>
        <a href="/landing-leads/logout/">Sign out</a>
      </nav>
    """


@require_http_methods(["GET", "POST"])
def landing_leads_login(request):
    if _dashboard_authenticated(request):
        return redirect("landing_leads_dashboard")
    error = ""
    if request.method == "POST":
        login_key = "landing-leads-login:" + hashlib.sha256(
            (_source_ip(request) or "unknown").encode("utf-8")
        ).hexdigest()
        failed_attempts = int(cache.get(login_key, 0) or 0)
        if failed_attempts >= 10:
            content = "<main><section class=\"panel login\"><h1>Try again later</h1><p>Too many failed login attempts.</p></section></main>"
            return _dashboard_response(_base_html("Login temporarily blocked", content), status=429)
        username = _text(request.POST.get("username"), 300)
        password = str(request.POST.get("password") or "")
        expected_username = os.getenv("LANDING_LEADS_DASHBOARD_USER", "")
        expected_password_hash = os.getenv("LANDING_LEADS_DASHBOARD_PASSWORD_HASH", "")
        credentials_valid = bool(expected_username and expected_password_hash) and secrets.compare_digest(
            username,
            expected_username,
        ) and check_password(password, expected_password_hash)
        if credentials_valid:
            cache.delete(login_key)
            request.session[LANDING_LEADS_SESSION_KEY] = True
            request.session.set_expiry(12 * 60 * 60)
            return redirect("landing_leads_dashboard")
        cache.set(login_key, failed_attempts + 1, timeout=15 * 60)
        error = "Invalid username or password."

    token = get_token(request)
    content = f"""
      <main><section class="panel login">
        <h1>Lead dashboard</h1><p>Sign in to review landing submissions and Fitspace delivery status.</p>
        {'<p class="error">' + escape(error) + '</p>' if error else ''}
        <form method="post" action="/landing-leads/login/">
          <input type="hidden" name="csrfmiddlewaretoken" value="{escape(token)}">
          <label>Email<input type="email" name="username" autocomplete="username" required autofocus></label><br>
          <label>Password<input type="password" name="password" autocomplete="current-password" required></label><br>
          <button type="submit">Sign in</button>
        </form>
      </section></main>
    """
    return _dashboard_response(_base_html("Lead dashboard login", content))


def landing_leads_logout(request):
    request.session.pop(LANDING_LEADS_SESSION_KEY, None)
    return redirect("landing_leads_login")


@_dashboard_required
def landing_leads_dashboard(request):
    status_filter = _text(request.GET.get("status"), 32)
    query = _text(request.GET.get("q"), 300)
    queryset = LandingLeadSubmission.objects.all()
    allowed_statuses = {value for value, _ in LandingLeadSubmission.STATUS_CHOICES}
    if status_filter in allowed_statuses:
        queryset = queryset.filter(status=status_filter)
    if query:
        queryset = queryset.filter(
            Q(customer_name__icontains=query)
            | Q(customer_phone__icontains=query)
            | Q(product__icontains=query)
            | Q(product_sku__icontains=query)
            | Q(external_order_id__icontains=query)
        )

    page = Paginator(queryset, 50).get_page(request.GET.get("page"))
    stats = LandingLeadSubmission.objects.aggregate(
        total=Count("lead_id"),
        mapping=Count("lead_id", filter=Q(status=LandingLeadSubmission.STATUS_MAPPING_REQUIRED)),
        sent=Count("lead_id", filter=Q(status=LandingLeadSubmission.STATUS_SENT)),
        failed=Count("lead_id", filter=Q(status=LandingLeadSubmission.STATUS_FAILED)),
        invalid=Count("lead_id", filter=Q(status=LandingLeadSubmission.STATUS_VALIDATION_FAILED)),
    )
    rows = []
    for lead in page.object_list:
        received = timezone.localtime(lead.received_at).strftime("%Y-%m-%d %H:%M:%S")
        upstream = str(lead.upstream_http_status or "-")
        rows.append(
            f"""
            <tr>
              <td><code>{escape(str(lead.lead_id)[:8])}</code></td>
              <td>{escape(received)}</td>
              <td class="wrap"><strong>{escape(lead.customer_name or '-')}</strong><br>{escape(lead.customer_phone or '-')}</td>
              <td class="wrap"><strong>{escape(lead.product or '-')}</strong><br><span>SKU: {escape(lead.product_sku or '-')}</span></td>
              <td>{escape(str(lead.quantity or '-'))}</td>
              <td>{escape(str(lead.cost if lead.cost is not None else '-'))}</td>
              <td><span class="status {escape(lead.status)}">{escape(lead.get_status_display())}</span></td>
              <td>{escape(upstream)}</td>
              <td>{escape(lead.external_order_id or '-')}</td>
              <td><a href="/landing-leads/{escape(str(lead.lead_id))}/">Details</a></td>
            </tr>
            """
        )
    table_rows = "".join(rows) or '<tr><td colspan="10">No leads found.</td></tr>'
    query_suffix = f"&status={escape(status_filter)}&q={escape(query)}"
    previous_link = (
        f'<a href="?page={page.previous_page_number()}{query_suffix}">Previous</a>'
        if page.has_previous()
        else ""
    )
    next_link = (
        f'<a href="?page={page.next_page_number()}{query_suffix}">Next</a>' if page.has_next() else ""
    )
    status_options = ['<option value="">All statuses</option>']
    for value, label in LandingLeadSubmission.STATUS_CHOICES:
        selected = " selected" if status_filter == value else ""
        status_options.append(
            f'<option value="{escape(value)}"{selected}>{escape(label)}</option>'
        )
    status_options = "".join(status_options)
    content = f"""
      <main>
        <div class="titlebar"><div><h1>Landing leads</h1><p>Fitspace delivery status for leads received from landing pages.</p></div><code>POST /api/landing-leads/</code></div>
        <section class="stats">
          <div class="panel stat"><span>Total</span><strong>{stats['total']}</strong></div>
          <div class="panel stat"><span>Needs mapping</span><strong>{stats['mapping']}</strong></div>
          <div class="panel stat"><span>Sent</span><strong>{stats['sent']}</strong></div>
          <div class="panel stat"><span>Failed</span><strong>{stats['failed']}</strong></div>
          <div class="panel stat"><span>Invalid</span><strong>{stats['invalid']}</strong></div>
        </section>
        <form class="panel filters" method="get">
          <label class="grow">Search<input name="q" value="{escape(query)}" placeholder="Name, phone, product or order ID"></label>
          <label class="field">Status<select name="status">{status_options}</select></label>
          <button type="submit">Apply filters</button><a class="button secondary" href="/landing-leads/">Reset</a>
        </form>
        <section class="panel table-wrap"><table><thead><tr><th>Lead</th><th>Received</th><th>Customer</th><th>Product</th><th>Qty</th><th>Cost</th><th>Status</th><th>HTTP</th><th>Order ID</th><th></th></tr></thead><tbody>{table_rows}</tbody></table>
          <div class="pagination"><span>{previous_link}</span><span>Page {page.number} of {page.paginator.num_pages}</span><span>{next_link}</span></div>
        </section>
      </main>
    """
    return _dashboard_response(
        _base_html("Landing leads", content, _dashboard_actions("leads"))
    )


@_dashboard_required
@require_http_methods(["GET", "POST"])
def landing_product_mappings(request):
    error = ""
    posted_product_name = ""
    posted_sku = ""
    posted_mapping_id = ""
    posted_active = True
    if request.method == "POST":
        mapping_id = _text(request.POST.get("mapping_id"), 80)
        product_name = _text(request.POST.get("product_name"), 300)
        sku = _text(request.POST.get("sku"), 120)
        active = request.POST.get("active") == "on"
        posted_product_name = product_name
        posted_sku = sku
        posted_mapping_id = mapping_id
        posted_active = active
        normalized_name = normalize_product_name(product_name)
        if not normalized_name:
            error = "Enter a product name."
        elif not sku:
            error = "Enter the Fitspace SKU."
        else:
            duplicate = LandingProductMapping.objects.filter(normalized_name=normalized_name)
            if mapping_id:
                duplicate = duplicate.exclude(mapping_id=mapping_id)
            if duplicate.exists():
                error = "A mapping already exists for this normalized product name."
            else:
                if mapping_id:
                    mapping = get_object_or_404(LandingProductMapping, mapping_id=mapping_id)
                    mapping.product_name = product_name
                    mapping.normalized_name = normalized_name
                    mapping.sku = sku
                    mapping.active = active
                    mapping.source = LandingProductMapping.SOURCE_MANUAL
                    mapping.save()
                else:
                    LandingProductMapping.objects.create(
                        product_name=product_name,
                        normalized_name=normalized_name,
                        sku=sku,
                        active=active,
                        source=LandingProductMapping.SOURCE_MANUAL,
                    )
                return redirect("/landing-leads/products/?saved=1")

    query = _text(request.GET.get("q"), 300)
    mappings = LandingProductMapping.objects.all()
    if query:
        mappings = mappings.filter(
            Q(product_name__icontains=query)
            | Q(sku__icontains=query)
            | Q(normalized_name__icontains=normalize_product_name(query))
        )
    page = Paginator(mappings, 100).get_page(request.GET.get("page"))
    pending_counts = {
        row["product_normalized"]: row["count"]
        for row in LandingLeadSubmission.objects.filter(
            status=LandingLeadSubmission.STATUS_MAPPING_REQUIRED
        )
        .values("product_normalized")
        .annotate(count=Count("lead_id"))
    }
    edit_mapping = None
    edit_id = _text(request.GET.get("edit"), 80)
    if edit_id:
        edit_mapping = LandingProductMapping.objects.filter(mapping_id=edit_id).first()

    token = get_token(request)
    rows = []
    for mapping in page.object_list:
        pending = pending_counts.get(mapping.normalized_name, 0)
        send_action = "-"
        if pending and mapping.active:
            send_action = f"""
              <form class="inline-form" method="post" action="/landing-leads/products/{escape(str(mapping.mapping_id))}/send-pending/">
                <input type="hidden" name="csrfmiddlewaretoken" value="{escape(token)}">
                <button type="submit">Send {pending} pending</button>
              </form>
            """
        rows.append(
            f"""
            <tr>
              <td class="wrap"><strong>{escape(mapping.product_name)}</strong><br><span>{escape(mapping.normalized_name)}</span></td>
              <td><code>{escape(mapping.sku)}</code></td>
              <td>{escape(mapping.get_source_display())}</td>
              <td>{'Yes' if mapping.active else 'No'}</td>
              <td>{pending}</td>
              <td>{send_action}</td>
              <td><a href="?edit={escape(str(mapping.mapping_id))}&q={escape(query)}">Edit</a></td>
            </tr>
            """
        )
    table_rows = "".join(rows) or '<tr><td colspan="7">No product mappings found.</td></tr>'
    selected = edit_mapping
    form_title = "Edit mapping" if selected else "Add mapping"
    if request.method == "POST" and error:
        form_product = posted_product_name
        form_sku = posted_sku
        form_id = posted_mapping_id
        form_checked = " checked" if posted_active else ""
        form_title = "Edit mapping" if posted_mapping_id else "Add mapping"
    else:
        form_product = selected.product_name if selected else ""
        form_sku = selected.sku if selected else ""
        form_id = str(selected.mapping_id) if selected else ""
        form_checked = " checked" if selected is None or selected.active else ""
    notice = ""
    if request.GET.get("saved") == "1":
        notice = '<div class="notice">Product mapping saved.</div>'
    elif request.GET.get("sent"):
        notice = f'<div class="notice">Processed {escape(request.GET.get("sent"))} pending leads.</div>'
    previous_link = (
        f'<a href="?page={page.previous_page_number()}&q={escape(query)}">Previous</a>'
        if page.has_previous()
        else ""
    )
    next_link = (
        f'<a href="?page={page.next_page_number()}&q={escape(query)}">Next</a>'
        if page.has_next()
        else ""
    )
    content = f"""
      <main>
        <div class="titlebar"><div><h1>Product / SKU</h1><p>Exact product-name mapping used before Fitspace delivery.</p></div><strong>{page.paginator.count} mappings</strong></div>
        {notice}
        {'<div class="notice error">' + escape(error) + '</div>' if error else ''}
        <section class="panel" style="padding:16px;margin-bottom:16px">
          <h2>{form_title}</h2>
          <form method="post" action="/landing-leads/products/" class="filters" style="padding:0;margin:0">
            <input type="hidden" name="csrfmiddlewaretoken" value="{escape(token)}">
            <input type="hidden" name="mapping_id" value="{escape(form_id)}">
            <label class="grow">Product name<input name="product_name" value="{escape(form_product)}" required></label>
            <label class="field">Fitspace SKU<input name="sku" value="{escape(form_sku)}" required></label>
            <label class="field">Active<input type="checkbox" name="active"{form_checked} style="width:auto"></label>
            <button type="submit">Save mapping</button>
            {'<a class="button secondary" href="/landing-leads/products/">Cancel</a>' if selected else ''}
          </form>
        </section>
        <form class="panel filters" method="get">
          <label class="grow">Search<input name="q" value="{escape(query)}" placeholder="Product name or SKU"></label>
          <button type="submit">Search</button><a class="button secondary" href="/landing-leads/products/">Reset</a>
        </form>
        <section class="panel table-wrap"><table><thead><tr><th>Product text</th><th>SKU</th><th>Source</th><th>Active</th><th>Pending</th><th></th><th></th></tr></thead><tbody>{table_rows}</tbody></table>
          <div class="pagination"><span>{previous_link}</span><span>Page {page.number} of {page.paginator.num_pages}</span><span>{next_link}</span></div>
        </section>
      </main>
    """
    return _dashboard_response(
        _base_html("Product / SKU", content, _dashboard_actions("products"))
    )


@require_POST
@_dashboard_required
def landing_product_mapping_send_pending(request, mapping_id):
    mapping = get_object_or_404(LandingProductMapping, mapping_id=mapping_id, active=True)
    pending_leads = list(
        LandingLeadSubmission.objects.filter(
            status=LandingLeadSubmission.STATUS_MAPPING_REQUIRED,
            product_normalized=mapping.normalized_name,
        ).order_by("received_at")[:100]
    )
    for lead in pending_leads:
        deliver_landing_lead(lead, mapping)
    return redirect(f"/landing-leads/products/?sent={len(pending_leads)}")


@_dashboard_required
def landing_lead_detail(request, lead_id):
    lead = get_object_or_404(LandingLeadSubmission, lead_id=lead_id)
    received = timezone.localtime(lead.received_at).strftime("%Y-%m-%d %H:%M:%S %Z")
    sent = timezone.localtime(lead.sent_at).strftime("%Y-%m-%d %H:%M:%S %Z") if lead.sent_at else "-"
    details = [
        ("Status", lead.get_status_display()),
        ("Received", received),
        ("Sent", sent),
        ("Customer", lead.customer_name or "-"),
        ("Phone", lead.customer_phone or "-"),
        ("Region", lead.customer_region or "-"),
        ("Address", lead.customer_address or "-"),
        ("Product received", lead.product or "-"),
        ("Mapped SKU", lead.product_sku or "-"),
        ("Quantity", lead.quantity or "-"),
        ("Cost", lead.cost if lead.cost is not None else "-"),
        ("Referral", lead.referral or "-"),
        ("Fitspace HTTP", lead.upstream_http_status or "-"),
        ("External order ID", lead.external_order_id or "-"),
        ("Source IP", lead.source_ip or "-"),
        ("Origin", lead.origin or "-"),
    ]
    detail_cells = "".join(
        f'<div class="detail"><span>{escape(str(label))}</span><strong>{escape(str(value))}</strong></div>'
        for label, value in details
    )
    payload_json = escape(json.dumps(lead.request_payload or {}, ensure_ascii=False, indent=2))
    forwarded_json = escape(json.dumps(lead.forwarded_payload or {}, ensure_ascii=False, indent=2))
    validation_json = escape(json.dumps(lead.validation_errors or {}, ensure_ascii=False, indent=2))
    content = f"""
      <main class="stack">
        <div class="titlebar"><div><h1>Lead {escape(str(lead.lead_id)[:8])}</h1><p>Full request and Fitspace delivery audit.</p></div><a class="button secondary" href="/landing-leads/">Back to leads</a></div>
        <section class="panel details">{detail_cells}</section>
        <section class="panel" style="padding:16px"><h2>Customer comment</h2><pre>{escape(lead.customer_comment or '-')}</pre></section>
        <section class="panel" style="padding:16px"><h2>Received payload</h2><pre>{payload_json}</pre></section>
        <section class="panel" style="padding:16px"><h2>Forwarded payload</h2><pre>{forwarded_json}</pre></section>
        <section class="panel" style="padding:16px"><h2>Validation</h2><pre>{validation_json}</pre></section>
        <section class="panel" style="padding:16px"><h2>Fitspace response</h2><pre>{escape(lead.upstream_response or '-')}</pre></section>
        <section class="panel" style="padding:16px"><h2>Error</h2><pre>{escape(lead.error or '-')}</pre></section>
      </main>
    """
    return _dashboard_response(
        _base_html("Lead details", content, _dashboard_actions("leads"))
    )
