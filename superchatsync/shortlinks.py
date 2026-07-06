import os
import re
import secrets
from urllib.parse import urlsplit

from django.db import IntegrityError

from .models import ShortLink


DEFAULT_THANK_YOU_BODY = "Thanks for checking it. If you need help choosing, just reply here."


def _is_true(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def public_base_url():
    return (
        os.environ.get("SHORTLINK_PUBLIC_BASE_URL")
        or os.environ.get("SHORTLINK_BASE_URL")
        or "https://storwyz.com"
    ).rstrip("/")


def short_url_for_code(code):
    return f"{public_base_url()}/r/{code}/"


def _valid_target_url(url):
    parsed = urlsplit(str(url or "").strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def generate_code(length=8):
    raw = secrets.token_urlsafe(max(6, length))[:length]
    return re.sub(r"[^A-Za-z0-9_-]", "", raw) or secrets.token_hex(4)


def create_short_link(
    target_url,
    *,
    title="",
    business_slug="",
    conversation_id="",
    contact_id="",
    channel_id="",
    customer_id=None,
    phone="",
    product_id="",
    product_name="",
    campaign_id="",
    source_channel="whatsapp",
    source_template="",
    source_message_id="",
    intent="",
    thank_you_enabled=True,
    thank_you_body="",
    metadata=None,
    expires_at=None,
    created_by="system",
):
    if not _valid_target_url(target_url):
        raise ValueError("target_url must be an absolute http/https URL.")

    for _ in range(12):
        code = generate_code()
        try:
            return ShortLink.objects.create(
                code=code,
                target_url=str(target_url).strip(),
                title=title or "",
                business_slug=business_slug or "",
                source_channel=source_channel or "whatsapp",
                source_template=source_template or "",
                source_message_id=source_message_id or "",
                intent=intent or "",
                conversation_id=conversation_id or "",
                contact_id=contact_id or "",
                channel_id=channel_id or "",
                customer_id=customer_id,
                phone=phone or "",
                product_id=product_id or "",
                product_name=product_name or "",
                campaign_id=campaign_id or "",
                thank_you_enabled=bool(thank_you_enabled),
                thank_you_body=thank_you_body or os.environ.get("SHORTLINK_THANK_YOU_BODY") or DEFAULT_THANK_YOU_BODY,
                metadata=metadata or {},
                expires_at=expires_at,
                created_by=created_by or "system",
            )
        except IntegrityError:
            continue
    raise RuntimeError("Could not allocate a unique shortlink code.")


def create_short_url(*args, **kwargs):
    link = create_short_link(*args, **kwargs)
    return link, short_url_for_code(link.code)


def shortlinks_enabled():
    return _is_true(os.environ.get("PEEKO_PRODUCT_TEMPLATE_USES_STORWYZ_SHORTLINK"))
