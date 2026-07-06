import json
import os
import re
import uuid
from decimal import Decimal, InvalidOperation
from datetime import timedelta

import requests
from django.db import connection, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from productfeed.models import Offer
from superchatsync.fitexpress_reference import (
    resolve_fitexpress_country_id,
    resolve_fitexpress_product_id,
)
from superchatsync.models import (
    CustomerChannelIdentity,
    CustomerConversionEvent,
    CustomerOrder,
    CustomerOrderPhoneLink,
    CustomerProfile,
)
from superchatsync.superchat_safe_send import _get_json, get_config, load_env


ORDER_WORDS = (
    "comanda",
    "comandă",
    "comand",
    "vreau comanda",
    "vreau comandă",
    "confirm comanda",
    "confirm comanda",
    "cumpar",
    "cumpăr",
    "vreau premium",
    "vreau 1",
    "vreau o bucata",
    "vreau o bucată",
    "vreau 2",
    "vreau doua",
    "vreau două",
    "order now",
    "confirm order",
    "i want to order",
    "i want 1",
    "i want 2",
    "quiero pedir",
    "quiero ordenar",
    "confirmar pedido",
    "pedir ahora",
    "quiero 1",
    "quiero una",
    "quiero 2",
    "voglio ordinare",
    "ordina ora",
    "je veux commander",
    "commander",
    "ich will bestellen",
    "ich möchte bestellen",
    "jetzt bestellen",
    "заказать",
    "хочу заказать",
    "подтвердить заказ",
    "замовити",
    "хочу замовити",
    "підтвердити замовлення",
)

ADDRESS_KEYWORDS = (
    "adresa",
    "adresă",
    "strada",
    "str.",
    "nr",
    "bloc",
    "scara",
    "apartament",
    "ap.",
    "oras",
    "oraș",
    "judet",
    "județ",
    "sat",
    "comuna",
    "sector",
    "address",
    "street",
    "city",
    "region",
    "адрес",
    "улица",
    "город",
    "місто",
    "адреса",
)
ADDRESS_FIELD_NAMES = ADDRESS_KEYWORDS + (
    "customer_address",
    "delivery_address",
    "shipping_address",
    "billing_address",
    "full_address",
    "last_address",
    "last_delivery_address",
    "last_shipping_address",
    "verified_address",
    "logistics_address",
    "wyzbox_address",
    "wyzbox_delivery_address",
    "adresa_livrare",
    "adresa livrare",
    "ultima_adresa",
    "adresa_client",
    "adresa_clientului",
    "livrare",
    "delivery",
    "shipping",
)
PHONE_FIELD_NAMES = (
    "phone",
    "telefon",
    "telephone",
    "mobile",
    "whatsapp",
    "number",
    "numar",
    "număr",
)

NAME_PLACEHOLDERS = {
    "",
    "client",
    "whatsapp client",
    "whatsapp",
    "unknown",
    "necunoscut",
    "vezi oferta",
    "detalii produs",
    "detalii livrare",
    "mai multe detalii",
    "cum se foloseste",
    "cum se folosește",
    "pentru gratar",
    "pentru grătar",
    "pentru bucatarie",
    "pentru bucătărie",
    "pentru cadou",
    "mai am o intrebare",
    "mai am o întrebare",
    "vreau comanda",
    "vreau comandă",
    "nu acum",
}
ADDRESS_PLACEHOLDERS = {
    "",
    "no address",
    "none",
    "n/a",
    "-",
    "unknown",
    "necunoscut",
}
NON_ADDRESS_PREFIXES = (
    "http://",
    "https://",
    "api.",
)


def _is_true(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _digits(value):
    return re.sub(r"\D", "", str(value or ""))


def _normalized_phone(value):
    digits = _digits(value)
    if not digits:
        return ""
    return "+" + digits


def _phone_lookup_values(value):
    digits = _digits(value)
    values = []
    if digits:
        values.extend([_normalized_phone(digits), digits])
    result = []
    seen = set()
    for item in values:
        key = str(item or "").strip()
        if key and key not in seen:
            seen.add(key)
            result.append(key)
    return result


def _alpha_words(value):
    return re.findall(r"[A-Za-zÀ-žА-Яа-яЁёІіЇїЄєҐґ'-]{2,}", str(value or ""))


def _strip_order_intent_words(text):
    value = str(text or "")
    for word in sorted(ORDER_WORDS, key=len, reverse=True):
        value = re.sub(re.escape(word), " ", value, flags=re.IGNORECASE)
    return _clean(value)


def _iter_named_scalars(value, path=""):
    if isinstance(value, dict):
        label = _clean(
            value.get("name")
            or value.get("label")
            or value.get("key")
            or value.get("field")
            or value.get("title")
        )
        for value_key in ("value", "text", "body"):
            scalar = value.get(value_key)
            if scalar is not None and not isinstance(scalar, (dict, list)):
                yield label or path or value_key, scalar
        for key, nested in value.items():
            next_path = f"{path}.{key}" if path else str(key)
            yield from _iter_named_scalars(nested, next_path)
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            next_path = f"{path}.{index}" if path else str(index)
            yield from _iter_named_scalars(nested, next_path)
    elif value is not None:
        yield path, value


def is_order_request(text):
    value = _clean(text).lower()
    if not value:
        return False
    return any(word in value for word in ORDER_WORDS)


def _recent_client_texts(conversation, current_text):
    texts = [_clean(current_text).lower()]
    recent = (
        conversation.messages.filter(is_client_reply=True)
        .exclude(message_text__isnull=True)
        .exclude(message_text="")
        .order_by("-sent_at")[:6]
    )
    texts.extend(_clean(message.message_text).lower() for message in recent)
    return texts


def _recent_client_message_texts(conversation, limit=20):
    return [
        str(text or "")
        for text in (
            conversation.messages.filter(is_client_reply=True)
            .exclude(message_text__isnull=True)
            .exclude(message_text="")
            .order_by("-sent_at")
            .values_list("message_text", flat=True)[:limit]
        )
    ]


def infer_quantity(conversation, current_text):
    joined = " ".join(_recent_client_texts(conversation, current_text))
    if re.search(r"\b(?:2|doua|două)\b", joined) or "2 buc" in joined:
        return 2
    return 1


def _offer_quantity(offer):
    if getattr(offer, "quantity", None):
        try:
            return int(offer.quantity)
        except (TypeError, ValueError):
            pass
    text = f"{offer.offer_name or ''} {offer.variant or ''}".lower()
    match = re.search(r"\b([2-9])\s*(?:x|buc|bucati|bucăți)\b", text)
    if match:
        return int(match.group(1))
    if re.search(r"\b(?:2|doua|două)\b", text) or "2 buc" in text:
        return 2
    return 1


def _decimal_or_none(value):
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def resolve_cost(product_id, quantity):
    candidates = []
    for offer in Offer.objects.filter(product_id=str(product_id), active=True):
        label = f"{offer.offer_name or ''} {offer.variant or ''}".lower()
        is_landing_offer = (
            "_landing_offer_" in str(offer.offer_id or "")
            or str(offer.variant or "").startswith("landing_")
        )
        if ("reducere" in label or "discount" in label) and not is_landing_offer:
            continue
        price = _decimal_or_none(offer.price)
        if price is None:
            continue
        candidates.append((_offer_quantity(offer), price))

    for offer_quantity, price in candidates:
        if offer_quantity == quantity:
            return int(price) if price == price.to_integral_value() else float(price)
    for offer_quantity, price in candidates:
        if offer_quantity == 1:
            total = price * Decimal(quantity)
            return int(total) if total == total.to_integral_value() else float(total)
    return 0


def _contact_name(contact):
    for key in ("name", "full_name", "display_name"):
        value = _clean(contact.get(key))
        if value:
            return value
    first_name = _clean(contact.get("first_name"))
    last_name = _clean(contact.get("last_name"))
    full_name = _clean(f"{first_name} {last_name}")
    return full_name or "WhatsApp Client"


def _contact_phone(contact):
    for label, value in _iter_named_scalars(contact):
        label_text = str(label or "").casefold()
        digits = _digits(value)
        if len(digits) < 8:
            continue
        if any(marker in label_text for marker in PHONE_FIELD_NAMES):
            return _clean(value)
    handles = contact.get("handles") if isinstance(contact, dict) else []
    if isinstance(handles, list):
        for handle in handles:
            if not isinstance(handle, dict):
                continue
            digits = _digits(handle.get("value"))
            if len(digits) >= 8:
                return _clean(handle.get("value"))
    return ""


def _superchat_conversation_url(conversation_id):
    return f"https://app.superchat.de/inbox/{conversation_id}?conversationId={conversation_id}"


def _load_order_state(conversation_id):
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT raw_state
            FROM ai_sales_conversation_state
            WHERE conversation_id = %s
            """,
            [conversation_id],
        )
        row = cursor.fetchone()
    if not row or row[0] is None:
        return {}
    if isinstance(row[0], dict):
        return row[0]
    try:
        return json.loads(row[0])
    except (TypeError, json.JSONDecodeError):
        return {}


def _pending_window_hours():
    try:
        return int(os.environ.get("ORDER_PENDING_WINDOW_HOURS", "48"))
    except ValueError:
        return 48


def _pending_order_state(conversation_id, product_id=None):
    raw_state = _load_order_state(conversation_id)
    pending = raw_state.get("pending_order_request")
    if not isinstance(pending, dict):
        return None
    if product_id and str(pending.get("product_id") or "") not in {"", str(product_id)}:
        return None
    requested_at = parse_datetime(str(pending.get("requested_at") or ""))
    if requested_at and requested_at < timezone.now() - timedelta(hours=_pending_window_hours()):
        return None
    return pending


def has_pending_order_request(conversation, product_id=None):
    return bool(_pending_order_state(conversation.conversation_id, product_id))


def _record_pending_order_state(conversation_id, product_id, details, missing, current_text):
    now = timezone.now()
    raw_state = _load_order_state(conversation_id)
    raw_state["pending_order_request"] = {
        "requested_at": now.isoformat(),
        "product_id": str(product_id),
        "known_details": details,
        "missing": missing,
        "last_client_text": _clean(current_text)[:500],
    }
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO ai_sales_conversation_state (
                    conversation_id, sales_stage, intent, decision_readiness,
                    friction_points, information_gap, known_customer_data,
                    missing_order_data, product_focus, cta_history,
                    next_best_action, raw_state, created_at, updated_at
                ) VALUES (
                    %s, 'order_pending', 'wants_to_order', NULL,
                    '[]'::jsonb, '[]'::jsonb, %s::jsonb,
                    %s::jsonb, %s, '[]'::jsonb,
                    'collect_order_data', %s::jsonb, %s, %s
                )
                ON CONFLICT (conversation_id) DO UPDATE SET
                    sales_stage = EXCLUDED.sales_stage,
                    intent = EXCLUDED.intent,
                    known_customer_data = EXCLUDED.known_customer_data,
                    missing_order_data = EXCLUDED.missing_order_data,
                    product_focus = EXCLUDED.product_focus,
                    next_best_action = EXCLUDED.next_best_action,
                    raw_state = EXCLUDED.raw_state,
                    updated_at = EXCLUDED.updated_at
                """,
                [
                    conversation_id,
                    json.dumps(details, ensure_ascii=False),
                    json.dumps(missing, ensure_ascii=False),
                    str(product_id),
                    json.dumps(raw_state, ensure_ascii=False),
                    now,
                    now,
                ],
            )


def _record_order_state(conversation_id, product_id, payload, response_status, response_body, dry_run):
    now = timezone.now()
    raw_state = _load_order_state(conversation_id)
    raw_state.pop("pending_order_request", None)
    raw_state["last_order_submission"] = {
        "submitted_at": now.isoformat(),
        "product_id": str(product_id),
        "quantity": payload.get("quantity"),
        "cost": payload.get("cost"),
        "customer_phone": payload.get("customer_phone"),
        "http_status": response_status,
        "dry_run": dry_run,
        "response_preview": _clean(response_body)[:500],
    }
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO ai_sales_conversation_state (
                    conversation_id, sales_stage, intent, decision_readiness,
                    friction_points, information_gap, known_customer_data,
                    missing_order_data, product_focus, cta_history,
                    next_best_action, raw_state, created_at, updated_at
                ) VALUES (
                    %s, 'order_submitted', 'wants_to_order', NULL,
                    '[]'::jsonb, '[]'::jsonb, '[]'::jsonb,
                    '[]'::jsonb, %s, '[]'::jsonb,
                    'thank_customer', %s::jsonb, %s, %s
                )
                ON CONFLICT (conversation_id) DO UPDATE SET
                    sales_stage = EXCLUDED.sales_stage,
                    intent = EXCLUDED.intent,
                    product_focus = EXCLUDED.product_focus,
                    next_best_action = EXCLUDED.next_best_action,
                    raw_state = EXCLUDED.raw_state,
                    updated_at = EXCLUDED.updated_at
                """,
                [
                    conversation_id,
                    str(product_id),
                    json.dumps(raw_state, ensure_ascii=False),
                    now,
                    now,
                ],
            )


def _duplicate_window_hours():
    try:
        return int(os.environ.get("ORDER_DUPLICATE_WINDOW_HOURS", "24"))
    except ValueError:
        return 24


def _already_submitted(product_id, phone, include_dry_run=False):
    phone_digits = _digits(phone)
    if not phone_digits:
        return False
    cutoff = timezone.now() - timedelta(hours=_duplicate_window_hours())
    with connection.cursor() as cursor:
        dry_run_filter = ""
        if not include_dry_run:
            dry_run_filter = """
              AND coalesce((raw_state->'last_order_submission'->>'dry_run')::boolean, FALSE) = FALSE
            """
        cursor.execute(
            """
            SELECT conversation_id
            FROM ai_sales_conversation_state
            WHERE raw_state ? 'last_order_submission'
              AND raw_state->'last_order_submission'->>'product_id' = %s
              AND regexp_replace(
                    coalesce(raw_state->'last_order_submission'->>'customer_phone', ''),
                    '\\D', '', 'g'
                  ) = %s
              AND (raw_state->'last_order_submission'->>'submitted_at')::timestamptz >= %s
            """ + dry_run_filter + """
            LIMIT 1
            """,
            [str(product_id), phone_digits, cutoff],
        )
        return cursor.fetchone() is not None


def _fetch_superchat_contact(conversation):
    config = get_config()
    contact_id = None
    contact = {}
    try:
        target = _get_json(config, f"/v1.0/conversations/{conversation.conversation_id}")
        contacts = target.get("contacts") or []
        contact_id = contacts[0].get("id") if contacts else None
    except Exception:
        contact_id = None
    if contact_id:
        try:
            contact = _get_json(config, f"/v1.0/contacts/{contact_id}")
        except Exception:
            contact = {}
    return contact


def _extract_labeled_value(text, labels):
    labels_pattern = "|".join(re.escape(label) for label in labels)
    pattern = rf"(?im)\b(?:{labels_pattern})\b\s*(?:[:=-]\s*|\s+)(.+)$"
    match = re.search(pattern, str(text or ""))
    if match:
        return _clean(match.group(1))
    return ""


def _looks_like_address(value):
    text = _clean(value).lower()
    if len(text) < 10:
        return False
    if text.startswith(NON_ADDRESS_PREFIXES):
        return False
    if re.fullmatch(r"[a-z0-9_:/.-]{10,}", text) and not re.search(r"\s|,", text):
        return False
    if any(keyword in text for keyword in ADDRESS_KEYWORDS):
        return True
    if not re.search(r"\d", text):
        return False
    if "," in text or ";" in text:
        return True
    words = _alpha_words(text)
    order_words = {"vreau", "comanda", "comandă", "premium", "bucati", "bucăți", "order"}
    if any(word in text for word in order_words):
        return False
    return len(words) >= 3 and len(text) >= 16


def _valid_customer_name(value):
    text = _clean(value).lower()
    if text in NAME_PLACEHOLDERS:
        return False
    words = _alpha_words(value)
    return len(" ".join(words)) >= 3


def _valid_customer_address(value, from_named_field=False):
    text = _clean(value)
    if text.lower() in ADDRESS_PLACEHOLDERS:
        return False
    if from_named_field and len(text) >= 6:
        return True
    return _looks_like_address(text)


def _extract_address_from_contact(contact):
    if not isinstance(contact, dict):
        return ""

    candidates = []
    for label, value in _iter_named_scalars(contact):
        text = _clean(value)
        if not text:
            continue
        label_text = str(label or "").replace("_", " ").replace(".", " ").casefold()
        is_address_field = any(marker in label_text for marker in ADDRESS_FIELD_NAMES)
        if is_address_field and _valid_customer_address(text, from_named_field=True):
            return text
        if _valid_customer_address(text):
            candidates.append(text)
    return candidates[0] if candidates else ""


def _extract_name_from_text(text):
    labeled = _extract_labeled_value(
        text,
        ("nume", "numele", "name", "full name", "имя", "ім'я"),
    )
    if labeled:
        labeled = re.split(
            r"\b(?:adresa|adresă|address|адрес|адреса)\b",
            labeled,
            flags=re.IGNORECASE,
        )[0]
        return re.split(r"[\n,;.]", labeled, 1)[0].strip()

    candidate_text = _strip_order_intent_words(text)
    parts = [part.strip() for part in re.split(r"[\n;,]", candidate_text) if part.strip()]
    for part in parts[:2]:
        if is_order_request(part) or _looks_like_address(part) or re.search(r"\d", part):
            continue
        words = _alpha_words(part)
        if len(words) >= 2 and len(part) <= 80:
            return _clean(" ".join(words[:4]))
    return ""


def _extract_address_from_text(text):
    labeled = _extract_labeled_value(
        text,
        ("adresa", "adresă", "address", "адрес", "адреса"),
    )
    if labeled:
        labeled = re.split(
            r"\b(?:nume|numele|name|full name|имя|ім'я)\b",
            labeled,
            flags=re.IGNORECASE,
        )[0]
        return _clean(labeled)

    raw = str(text or "")
    parts = [part.strip() for part in re.split(r"[\n;]", raw) if part.strip()]
    for part in parts:
        if _looks_like_address(part):
            return _clean(part)

    comma_parts = [part.strip() for part in raw.split(",") if part.strip()]
    if len(comma_parts) >= 2:
        candidate = _clean(", ".join(comma_parts[1:]))
        if _looks_like_address(candidate):
            return candidate
    return ""


def _extract_name_from_history(conversation):
    for text in _recent_client_message_texts(conversation):
        name = _extract_name_from_text(text)
        if _valid_customer_name(name):
            return name
    return ""


def _extract_address_from_history(conversation):
    for text in _recent_client_message_texts(conversation):
        address = _extract_address_from_text(text)
        if _valid_customer_address(address):
            return address
    return ""


def _add_unique_profile(profiles, seen, profile):
    if profile and profile.customer_id not in seen:
        profiles.append(profile)
        seen.add(profile.customer_id)


def _customer_profile_candidates(conversation, phone=None):
    profiles = []
    seen = set()
    phone_digits = _digits(phone or getattr(conversation, "client_phone", ""))
    if phone_digits:
        canonical_phone = _normalized_phone(phone_digits)
        phone_values = _phone_lookup_values(phone_digits)
        _add_unique_profile(
            profiles,
            seen,
            CustomerProfile.objects.filter(profile_key=f"phone:{canonical_phone}").first(),
        )
        _add_unique_profile(
            profiles,
            seen,
            CustomerProfile.objects.filter(profile_key=f"phone:{phone_digits}").first(),
        )
        for profile in CustomerProfile.objects.filter(phone__in=phone_values).only(
            "customer_id",
            "profile_key",
            "display_name",
            "phone",
            "metadata",
        )[:10]:
            _add_unique_profile(profiles, seen, profile)

        identity_customer_ids = []
        identity_customer_ids.extend(
            CustomerChannelIdentity.objects.filter(
                channel__in=["whatsapp", "phone", "sms"],
                normalized_identifier__in=phone_values,
            )
            .values_list("customer_id", flat=True)
            .distinct()[:20]
        )
        identity_customer_ids.extend(
            CustomerOrderPhoneLink.objects.filter(normalized_phone__in=phone_values)
            .exclude(customer_id__isnull=True)
            .values_list("customer_id", flat=True)
            .distinct()[:20]
        )

        if identity_customer_ids:
            for profile in CustomerProfile.objects.filter(customer_id__in=identity_customer_ids):
                _add_unique_profile(profiles, seen, profile)

    return profiles


def _extract_address_from_metadata(metadata):
    if not isinstance(metadata, dict):
        return ""
    return _extract_address_from_contact(metadata)


def _extract_name_from_metadata(metadata):
    if not isinstance(metadata, dict):
        return ""
    for label, value in _iter_named_scalars(metadata):
        label_text = str(label or "").replace("_", " ").replace(".", " ").casefold()
        if any(marker in label_text for marker in ("customer name", "full name", "nume", "name")):
            candidate = _clean(value)
            if _valid_customer_name(candidate):
                return candidate
    return ""


def _profile_details(profile):
    if not profile:
        return {}
    metadata = profile.metadata if isinstance(profile.metadata, dict) else {}
    name = _clean(profile.display_name) if _valid_customer_name(profile.display_name) else ""
    if not name:
        name = _extract_name_from_metadata(metadata)
    address = _extract_address_from_metadata(metadata)
    return {
        "customer_name": name,
        "customer_address": address,
        "source": "customer_profile_metadata" if address else "customer_profile",
    }


def _details_from_order_payload(payload):
    if not isinstance(payload, dict):
        return {}
    name = _clean(payload.get("customer_name"))
    if not _valid_customer_name(name):
        name = _extract_name_from_metadata(payload)
    address = _clean(payload.get("customer_address"))
    if not _valid_customer_address(address):
        address = _extract_address_from_metadata(payload)
    return {
        "customer_name": name if _valid_customer_name(name) else "",
        "customer_address": address if _valid_customer_address(address) else "",
    }


def _previous_order_details(profile=None, phone=None):
    phone_digits = _digits(phone)
    queryset = CustomerOrder.objects.exclude(order_payload__isnull=True).order_by("-submitted_at")
    if profile:
        queryset = queryset.filter(customer_id=profile.customer_id)
    elif not phone_digits:
        return {}
    else:
        queryset = queryset[:500]

    found = {
        "customer_name": "",
        "customer_address": "",
    }
    for order in queryset[:500]:
        payload = order.order_payload if isinstance(order.order_payload, dict) else {}
        if phone_digits and not profile and _digits(payload.get("customer_phone")) != phone_digits:
            continue
        details = _details_from_order_payload(payload)
        if not found["customer_name"] and details.get("customer_name"):
            found["customer_name"] = details["customer_name"]
        if not found["customer_address"] and details.get("customer_address"):
            found["customer_address"] = details["customer_address"]
        if found["customer_name"] and found["customer_address"]:
            break
    if found["customer_name"] or found["customer_address"]:
        found["source"] = "previous_customer_order"
        return found
    return {}


def _crm_order_details(conversation, phone):
    for profile in _customer_profile_candidates(conversation, phone):
        profile_details = _profile_details(profile)
        order_details = _previous_order_details(profile=profile, phone=phone)
        details = {
            "customer_name": (
                profile_details.get("customer_name")
                or order_details.get("customer_name")
                or ""
            ),
            "customer_address": (
                profile_details.get("customer_address")
                or order_details.get("customer_address")
                or ""
            ),
            "source": (
                profile_details.get("source")
                if profile_details.get("customer_address")
                else order_details.get("source")
            ),
        }
        if details["customer_name"] or details["customer_address"]:
            return details

    return _previous_order_details(profile=None, phone=phone)


def build_order_details(conversation, product_id, current_text):
    contact = _fetch_superchat_contact(conversation)
    pending = _pending_order_state(conversation.conversation_id, product_id) or {}
    pending_details = pending.get("known_details") if isinstance(pending.get("known_details"), dict) else {}

    quantity = infer_quantity(conversation, current_text)
    contact_phone = _contact_phone(contact)
    phone = (
        getattr(conversation, "client_phone", "")
        or contact_phone
        or os.environ.get("AI_TEST_ALLOWED_PHONE")
        or ""
    )
    text_name = _extract_name_from_text(current_text)
    text_address = _extract_address_from_text(current_text)
    history_name = _extract_name_from_history(conversation)
    history_address = _extract_address_from_history(conversation)
    pending_name = pending_details.get("customer_name")
    if not _valid_customer_name(pending_name):
        pending_name = ""
    pending_address = pending_details.get("customer_address")
    if not _valid_customer_address(pending_address):
        pending_address = ""
    crm_details = _crm_order_details(conversation, phone)
    crm_name = crm_details.get("customer_name")
    if not _valid_customer_name(crm_name):
        crm_name = ""
    crm_address = crm_details.get("customer_address")
    if not _valid_customer_address(crm_address):
        crm_address = ""
    contact_name = _contact_name(contact)
    contact_address = _extract_address_from_contact(contact)
    default_address = os.environ.get("ORDER_DEFAULT_ADDRESS", "")
    default_region = os.environ.get("ORDER_DEFAULT_REGION", "1001")
    fitexpress_product_id = resolve_fitexpress_product_id(product_id=product_id)

    return {
        "customer_name": (
            text_name
            or pending_name
            or crm_name
            or history_name
            or contact_name
            or "WhatsApp Client"
        ),
        "customer_phone": phone,
        "customer_region": resolve_fitexpress_country_id(phone, default_region),
        "customer_address": (
            text_address
            or pending_address
            or crm_address
            or history_address
            or contact_address
            or default_address
            or "No address"
        ),
        "quantity": quantity,
        "cost": resolve_cost(product_id, quantity),
        "product": fitexpress_product_id,
        "referral": os.environ.get("ORDER_REFERRAL", "Whatsapp"),
        "customer_comment": _superchat_conversation_url(conversation.conversation_id),
    }


def _missing_order_fields(details):
    missing = []
    if not _digits(details.get("customer_phone")):
        missing.append("customer_phone")
    if not _is_true(os.environ.get("ORDER_REQUIRE_CUSTOMER_DETAILS", "true")):
        return missing
    if not _valid_customer_name(details.get("customer_name")):
        missing.append("customer_name")
    if _is_true(os.environ.get("ORDER_REQUIRE_CUSTOMER_ADDRESS", "false")) and not _valid_customer_address(details.get("customer_address")):
        missing.append("customer_address")
    return missing


def prepare_order_request(conversation, product_id, current_text):
    details = build_order_details(conversation, product_id, current_text)
    missing = _missing_order_fields(details)
    if missing:
        _record_pending_order_state(
            conversation.conversation_id,
            product_id,
            details,
            missing,
            current_text,
        )
    return {
        "ready": not missing,
        "missing": missing,
        "details": details,
    }


def build_order_payload(conversation, product_id, current_text, order_details=None):
    details = order_details or build_order_details(conversation, product_id, current_text)
    return dict(details)


def _customer_for_conversation(conversation):
    profiles = _customer_profile_candidates(
        conversation,
        getattr(conversation, "client_phone", ""),
    )
    return profiles[0] if profiles else None


def ensure_phone_customer_profile(phone, display_name="", metadata=None):
    phone_digits = _digits(phone)
    if not phone_digits:
        return None, False
    canonical_phone = _normalized_phone(phone_digits)
    metadata = metadata if isinstance(metadata, dict) else {}
    defaults = {
        "customer_id": uuid.uuid4(),
        "display_name": _clean(display_name) or None,
        "phone": canonical_phone,
        "status": "active",
        "metadata": {
            **metadata,
            "phone_digits": phone_digits,
            "phone_normalized": canonical_phone,
            "identity_source": metadata.get("identity_source") or "phone",
        },
        "created_at": timezone.now(),
        "updated_at": timezone.now(),
    }
    profile, created = CustomerProfile.objects.get_or_create(
        profile_key=f"phone:{canonical_phone}",
        defaults=defaults,
    )
    changed = False
    if not profile.phone:
        profile.phone = canonical_phone
        changed = True
    if display_name and not profile.display_name:
        profile.display_name = _clean(display_name)
        changed = True
    profile_metadata = profile.metadata if isinstance(profile.metadata, dict) else {}
    for key, value in defaults["metadata"].items():
        if value and not profile_metadata.get(key):
            profile_metadata[key] = value
            changed = True
    if changed:
        profile.metadata = profile_metadata
        profile.updated_at = timezone.now()
        profile.save(update_fields=["phone", "display_name", "metadata", "updated_at"])
    return profile, created


def _extract_external_order_id(response_text):
    try:
        data = json.loads(response_text or "{}")
    except json.JSONDecodeError:
        return None
    for key in ("id", "order_id", "orderId", "external_order_id"):
        value = data.get(key)
        if value:
            return str(value)
    return None


def _record_crm_order(conversation, product_id, payload, webhook_url, response_status, response_text):
    now = timezone.now()
    customer = _customer_for_conversation(conversation)
    idempotency_key = f"{conversation.conversation_id}:{product_id}:{now.isoformat()}"
    order = CustomerOrder.objects.create(
        customer_id=customer.customer_id if customer else None,
        product_id=str(product_id),
        sku=str(payload.get("product") or product_id),
        quantity=int(payload.get("quantity") or 1),
        cost=Decimal(str(payload.get("cost") or "0")),
        currency="RON",
        status="submitted",
        source_channel="whatsapp",
        source_conversation_id=conversation.conversation_id,
        external_order_id=_extract_external_order_id(response_text),
        idempotency_key=idempotency_key,
        webhook_url=webhook_url,
        webhook_http_status=response_status,
        customer_comment=payload.get("customer_comment"),
        order_payload=payload,
        raw_response=(response_text or "")[:2000],
        submitted_at=now,
    )
    if customer:
        CustomerConversionEvent.objects.create(
            customer_id=customer.customer_id,
            order_id=order.order_id,
            channel="whatsapp",
            event_type="order_submitted",
            product_id=str(product_id),
            conversation_id=conversation.conversation_id,
            value=order.cost,
            currency=order.currency,
            occurred_at=now,
            metadata={"source": "order_webhook", "webhook_http_status": response_status},
        )
        CustomerConversionEvent.objects.create(
            customer_id=customer.customer_id,
            order_id=order.order_id,
            channel="whatsapp",
            event_type="buy",
            product_id=str(product_id),
            conversation_id=conversation.conversation_id,
            value=order.cost,
            currency=order.currency,
            occurred_at=now,
            metadata={"source": "order_webhook"},
        )
    return order


def submit_order(conversation, product_id, current_text, order_details=None):
    load_env()
    webhook_url = os.environ.get("ORDER_WEBHOOK_URL")
    dry_run = _is_true(os.environ.get("ORDER_WEBHOOK_DRY_RUN"))
    payload = build_order_payload(
        conversation,
        product_id,
        current_text,
        order_details=order_details,
    )
    webhook_product_id = str(payload.get("product") or product_id)
    missing = _missing_order_fields(payload)
    if missing:
        _record_pending_order_state(
            conversation.conversation_id,
            product_id,
            payload,
            missing,
            current_text,
        )
        return {
            "ok": False,
            "decision": "order_missing_customer_data",
            "payload": payload,
            "missing": missing,
            "http_status": None,
            "dry_run": dry_run,
        }

    if _already_submitted(webhook_product_id, payload.get("customer_phone"), include_dry_run=dry_run):
        return {
            "ok": True,
            "decision": "order_already_submitted",
            "payload": payload,
            "http_status": None,
            "dry_run": dry_run,
        }

    if dry_run:
        _record_order_state(conversation.conversation_id, webhook_product_id, payload, 200, "dry_run", True)
        return {
            "ok": True,
            "decision": "order_registered_dry_run",
            "payload": payload,
            "http_status": 200,
            "dry_run": True,
        }

    if not webhook_url:
        return {
            "ok": False,
            "decision": "order_webhook_not_configured",
            "payload": payload,
            "http_status": None,
            "dry_run": False,
        }

    response = requests.post(
        webhook_url,
        json=payload,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        timeout=int(os.environ.get("ORDER_WEBHOOK_TIMEOUT", "20")),
    )
    if not 200 <= response.status_code < 300:
        return {
            "ok": False,
            "decision": "order_webhook_failed",
            "payload": payload,
            "http_status": response.status_code,
            "dry_run": False,
        }

    _record_order_state(
        conversation.conversation_id,
        webhook_product_id,
        payload,
        response.status_code,
        response.text,
        False,
    )
    crm_order = _record_crm_order(
        conversation,
        product_id,
        payload,
        webhook_url,
        response.status_code,
        response.text,
    )
    return {
        "ok": True,
        "decision": "order_registered",
        "payload": payload,
        "http_status": response.status_code,
        "crm_order_id": str(crm_order.order_id),
        "dry_run": False,
    }
