import os
import re
import uuid
from pathlib import Path

import requests
from django.db import transaction
from django.utils import timezone

from superchatsync.conversation_context import record_sent_response
from superchatsync.models import (
    AiResponseProcessRun,
    AiResponseProcessStep,
    ProductCreativeAsset,
)


def load_env():
    for env_path in (
        Path("/opt/superchat-ai-agent/.env"),
        Path("/opt/superchat-ai-agent/web/.env"),
    ):
        if not env_path.exists():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _is_true(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _peeko_test_keep_handle_status():
    return _is_true(os.environ.get("PEEKO_TEST_KEEP_HANDLE_STATUS", "0"))


def _digits(value):
    return re.sub(r"\D", "", str(value or ""))


def _scalar_values(value):
    if isinstance(value, dict):
        for nested in value.values():
            yield from _scalar_values(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _scalar_values(nested)
    elif value is not None:
        yield str(value)


def _normalize_handle_status(value):
    return str(value or "").strip().casefold()


def _contact_custom_attribute(contact, name):
    target = str(name or "").strip().casefold()
    for attribute in contact.get("custom_attributes") or []:
        if str(attribute.get("name") or "").strip().casefold() == target:
            return attribute
    return None


def _custom_attribute_definition(config, name, resource="contact"):
    target = str(name or "").strip().casefold()
    target_resource = str(resource or "").strip().casefold()
    data = _get_json(config, "/v1.0/custom-attributes")
    for attribute in data.get("results") or []:
        if str(attribute.get("name") or "").strip().casefold() != target:
            continue
        if str(attribute.get("resource") or "").strip().casefold() != target_resource:
            continue
        return attribute
    return None


def get_config():
    load_env()
    config = {
        "api_key": os.environ.get("SUPERCHAT_API_KEY"),
        "base_url": (
            os.environ.get("SUPERCHAT_API_BASE")
            or os.environ.get("SUPERCHAT_BASE_URL")
            or "https://api.superchat.com"
        ).rstrip("/"),
        "allowed_phone": os.environ.get("AI_TEST_ALLOWED_PHONE"),
        "test_conversation_id": os.environ.get("AI_TEST_CONVERSATION_ID"),
        "send_enabled": (
            _is_true(os.environ.get("AI_TEST_SEND_ENABLED"))
            or _is_true(os.environ.get("AI_AUTOREPLY_SEND"))
        ),
    }
    missing = [key for key in ("api_key",) if not config[key]]
    if missing:
        raise RuntimeError(f"Lipsesc setări obligatorii: {', '.join(missing)}")
    if config["allowed_phone"] and not _digits(config["allowed_phone"]):
        raise RuntimeError("AI_TEST_ALLOWED_PHONE nu este valid.")
    return config


def _get_json(config, path):
    response = requests.get(
        config["base_url"] + path,
        headers={"X-API-KEY": config["api_key"], "Accept": "application/json"},
        timeout=30,
    )
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"Superchat GET a eșuat pentru {path}: HTTP {response.status_code}")
    return response.json()


def _patch_json(config, path, payload):
    response = requests.patch(
        config["base_url"] + path,
        headers={
            "X-API-KEY": config["api_key"],
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json=payload,
        timeout=30,
    )
    try:
        response_json = response.json()
    except ValueError:
        response_json = {}
    return response, response_json


def resolve_verified_conversation_target(config, conversation_id, expected_phone=None):
    conversation = _get_json(config, f"/v1.0/conversations/{conversation_id}")
    contacts = conversation.get("contacts") or []
    channel = conversation.get("channel") or {}
    if not contacts or not contacts[0].get("id"):
        raise RuntimeError("Conversația de test nu are contact_id.")
    if not channel.get("id") or channel.get("type") != "whats_app":
        raise RuntimeError("Conversația de test nu folosește un canal WhatsApp valid.")

    contact_id = contacts[0]["id"]
    contact = _get_json(config, f"/v1.0/contacts/{contact_id}")
    expected_digits = _digits(expected_phone)
    phone_verified = True
    if expected_digits:
        phone_verified = any(_digits(value) == expected_digits for value in _scalar_values(contact))
    if expected_digits and not phone_verified:
        raise RuntimeError("Contactul conversației nu corespunde numărului din allowlist.")

    time_window = conversation.get("time_window") or {}
    handle_status_attribute = _contact_custom_attribute(contact, "handle_status")
    inbox = channel.get("inbox") if isinstance(channel.get("inbox"), dict) else {}
    return {
        "conversation_id": conversation_id,
        "contact_id": contact_id,
        "channel_id": channel["id"],
        "channel_type": channel.get("type"),
        "channel_name": channel.get("name"),
        "channel_phone_number": channel.get("phone_number"),
        "inbox_id": inbox.get("id"),
        "inbox_name": inbox.get("name"),
        "time_window_state": time_window.get("state"),
        "time_window_open_until": time_window.get("open_until"),
        "phone_verified": phone_verified,
        "handle_status": (handle_status_attribute or {}).get("value"),
        "handle_status_attribute_id": (handle_status_attribute or {}).get("id"),
    }


def get_conversation_handle_status(config, conversation_id):
    target = resolve_verified_conversation_target(config, conversation_id)
    value = target.get("handle_status")
    normalized = _normalize_handle_status(value)
    return {
        "ok": normalized == "agent",
        "value": value,
        "normalized": normalized,
        "contact_id": target.get("contact_id"),
        "attribute_id": target.get("handle_status_attribute_id"),
        "channel_id": target.get("channel_id"),
        "channel_type": target.get("channel_type"),
        "channel_name": target.get("channel_name"),
        "channel_phone_number": target.get("channel_phone_number"),
        "inbox_id": target.get("inbox_id"),
        "inbox_name": target.get("inbox_name"),
        "reason": "handle_status_agent" if normalized == "agent" else "handle_status_not_agent",
    }


def set_conversation_handle_status(config, conversation_id, value):
    target = resolve_verified_conversation_target(config, conversation_id)
    contact_id = target.get("contact_id")
    attribute_id = target.get("handle_status_attribute_id")
    if not attribute_id:
        return {
            "ok": False,
            "reason": "handle_status_attribute_missing",
            "contact_id": contact_id,
        }

    requested_values = []
    raw_value = str(value or "").strip()
    if raw_value:
        requested_values.append(raw_value)
        capitalized = raw_value[:1].upper() + raw_value[1:]
        if capitalized not in requested_values:
            requested_values.append(capitalized)

    attempts = []
    for candidate_value in requested_values:
        patch_attempts = [
            (
                f"/v1.0/contacts/{contact_id}/custom-attributes/{attribute_id}",
                {"value": candidate_value},
            ),
            (
                f"/v1.0/contacts/{contact_id}/custom-attributes/{attribute_id}",
                {"custom_attribute": {"value": candidate_value}},
            ),
            (
                f"/v1.0/contacts/{contact_id}",
                {"custom_attributes": [{"id": attribute_id, "value": candidate_value}]},
            ),
        ]
        for path, payload in patch_attempts:
            response, response_json = _patch_json(config, path, payload)
            attempts.append(
                {
                    "path": path,
                    "payload_shape": list(payload.keys()),
                    "value": candidate_value,
                    "http_status": response.status_code,
                    "response": response_json or response.text[:300],
                }
            )
            if 200 <= response.status_code < 300:
                return {
                    "ok": True,
                    "value": candidate_value,
                    "contact_id": contact_id,
                    "attribute_id": attribute_id,
                    "http_status": response.status_code,
                    "path": path,
                    "attempts": attempts,
                }

    return {
        "ok": False,
        "reason": "superchat_attribute_update_failed",
        "contact_id": contact_id,
        "attribute_id": attribute_id,
        "attempts": attempts,
    }


def set_conversation_custom_attribute(
    config,
    conversation_id,
    attribute_name,
    value,
    expected_phone=None,
):
    target = resolve_verified_conversation_target(
        config,
        conversation_id,
        expected_phone=expected_phone,
    )
    contact_id = target.get("contact_id")
    contact = _get_json(config, f"/v1.0/contacts/{contact_id}")
    attribute = _contact_custom_attribute(contact, attribute_name)
    if not attribute:
        attribute = _custom_attribute_definition(config, attribute_name, resource="contact")
    attribute_id = (attribute or {}).get("id")
    if not attribute_id:
        return {
            "ok": False,
            "reason": "custom_attribute_missing",
            "attribute_name": attribute_name,
            "contact_id": contact_id,
        }

    raw_value = str(value or "").strip()
    if not raw_value:
        return {
            "ok": False,
            "reason": "custom_attribute_empty_value",
            "attribute_name": attribute_name,
            "contact_id": contact_id,
            "attribute_id": attribute_id,
        }

    attempts = []
    patch_attempts = [
        (
            f"/v1.0/contacts/{contact_id}/custom-attributes/{attribute_id}",
            {"value": raw_value},
        ),
        (
            f"/v1.0/contacts/{contact_id}/custom-attributes/{attribute_id}",
            {"custom_attribute": {"value": raw_value}},
        ),
        (
            f"/v1.0/contacts/{contact_id}",
            {"custom_attributes": [{"id": attribute_id, "value": raw_value}]},
        ),
    ]
    for path, payload in patch_attempts:
        response, response_json = _patch_json(config, path, payload)
        attempts.append(
            {
                "path": path,
                "payload_shape": list(payload.keys()),
                "value": raw_value,
                "http_status": response.status_code,
                "response": response_json or response.text[:300],
            }
        )
        if 200 <= response.status_code < 300:
            return {
                "ok": True,
                "value": raw_value,
                "attribute_name": attribute_name,
                "contact_id": contact_id,
                "attribute_id": attribute_id,
                "http_status": response.status_code,
                "path": path,
                "attempts": attempts,
            }

    return {
        "ok": False,
        "reason": "superchat_attribute_update_failed",
        "attribute_name": attribute_name,
        "contact_id": contact_id,
        "attribute_id": attribute_id,
        "attempts": attempts,
    }


def resolve_verified_test_target(config):
    if not config.get("test_conversation_id") or not config.get("allowed_phone"):
        raise RuntimeError("AI_TEST_CONVERSATION_ID și AI_TEST_ALLOWED_PHONE sunt obligatorii pentru test sender.")
    return resolve_verified_conversation_target(
        config,
        config["test_conversation_id"],
        expected_phone=config["allowed_phone"],
    )


def _selected_creative(run):
    step = (
        run.steps.filter(step_name="response_enrichment")
        .order_by("-created_at")
        .first()
    )
    output = step.output_json or {} if step else {}
    candidate = output.get("creative") or {}
    asset_id = candidate.get("asset_id")
    if not asset_id:
        return None
    return (
        ProductCreativeAsset.objects.filter(
            asset_id=asset_id,
            is_active=True,
            use_superchat_file=True,
        )
        .exclude(superchat_file_id__isnull=True)
        .exclude(superchat_file_id="")
        .first()
    )


def build_quick_reply_payload(target, run, creative=None):
    buttons = []
    for button in (run.final_buttons or [])[:3]:
        value = str(button or "").strip()[:40]
        if value:
            buttons.append({"value": value})
    if not buttons:
        raise RuntimeError("Run-ul nu are CTA quick replies.")

    content = {
        "type": "whats_app_quick_reply",
        "body": str(run.final_body or "").strip(),
        "replies": buttons,
    }
    if creative and creative.asset_type in {"image", "video", "document"}:
        content["header"] = {
            "type": creative.asset_type,
            "value": creative.superchat_file_id,
        }

    return {
        "to": [{"identifier": target["contact_id"]}],
        "from": {"channel_id": target["channel_id"]},
        "content": content,
    }


def build_quick_reply_payload_from_parts(target, body, buttons):
    replies = []
    for button in (buttons or [])[:3]:
        value = str(button or "").strip()[:40]
        if value:
            replies.append({"value": value})
    if not replies:
        raise RuntimeError("Recovery flow nu are CTA quick replies.")
    return {
        "to": [{"identifier": target["contact_id"]}],
        "from": {"channel_id": target["channel_id"]},
        "content": {
            "type": "whats_app_quick_reply",
            "body": str(body or "").strip(),
            "replies": replies,
        },
    }


def build_link_buttons_payload_from_parts(target, body, buttons):
    link_buttons = []
    for button in (buttons or [])[:3]:
        if not isinstance(button, dict):
            continue
        title = str(button.get("title") or button.get("value") or "").strip()[:20]
        target_url = str(button.get("target") or button.get("url") or "").strip()
        if title and target_url:
            link_buttons.append(
                {
                    "type": "url",
                    "title": title,
                    "target": target_url,
                }
            )
    if not link_buttons:
        raise RuntimeError("Nu există URL buttons valide pentru Superchat.")
    return {
        "to": [{"identifier": target["contact_id"]}],
        "from": {"channel_id": target["channel_id"]},
        "content": {
            "type": "live_chat_buttons",
            "body": str(body or "").strip(),
            "buttons": link_buttons,
        },
    }


def build_text_payload(target, body):
    return {
        "to": [{"identifier": target["contact_id"]}],
        "from": {"channel_id": target["channel_id"]},
        "content": {
            "type": "text",
            "body": str(body or "").strip(),
        },
    }


def build_whats_app_template_payload(target, template_id, variables=None):
    content = {
        "type": "whats_app_template",
        "template_id": str(template_id or "").strip(),
    }
    if not content["template_id"]:
        raise RuntimeError("WhatsApp template_id lipsește.")
    if variables:
        content["variables"] = variables
    return {
        "to": [{"identifier": target["contact_id"]}],
        "from": {"channel_id": target["channel_id"]},
        "content": content,
    }


def _send_payload(config, payload, failure_label):
    response = requests.post(
        f"{config['base_url']}/v1.0/messages",
        headers={
            "X-API-KEY": config["api_key"],
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json=payload,
        timeout=30,
    )
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"{failure_label} a eșuat: HTTP {response.status_code}: {response.text[:500]}")
    try:
        response_json = response.json()
    except ValueError:
        response_json = {}
    return response, response_json


def _schedule_peeko_url_handoff(
    target,
    sent_at,
    template_name="",
    template_message_id="",
    product_id="business:peeko",
    expected_phone=None,
    delay_seconds=None,
    aux_cta_labels=None,
):
    delay = int(delay_seconds or os.environ.get("PEEKO_URL_HANDOFF_DELAY_SECONDS") or 300)
    if delay <= 0:
        delay = 300
    from superchatsync.tasks import peeko_url_handoff_if_no_aux_cta

    task = peeko_url_handoff_if_no_aux_cta.apply_async(
        kwargs={
            "conversation_id": target["conversation_id"],
            "sent_at_iso": sent_at.isoformat(),
            "template_name": template_name,
            "template_message_id": template_message_id,
            "product_id": product_id or "business:peeko",
            "expected_phone": expected_phone,
            "aux_cta_labels": aux_cta_labels or [],
        },
        countdown=delay,
    )
    return {"task_id": task.id, "delay_seconds": delay}


def send_test_text_message(body, product_id=None, intent="order_confirmation"):
    config = get_config()
    if not config["send_enabled"]:
        raise RuntimeError("AI_TEST_SEND_ENABLED este false; trimiterea reală este blocată.")
    target = resolve_verified_test_target(config)
    return send_text_message_to_target(
        config,
        target,
        body,
        product_id=product_id,
        intent=intent,
    )


def send_text_message_to_conversation(conversation_id, body, product_id=None, intent="order_confirmation", expected_phone=None):
    config = get_config()
    if not config["send_enabled"]:
        raise RuntimeError("AI_TEST_SEND_ENABLED este false; trimiterea reală este blocată.")
    target = resolve_verified_conversation_target(config, conversation_id, expected_phone=expected_phone)
    return send_text_message_to_target(
        config,
        target,
        body,
        product_id=product_id,
        intent=intent,
    )


def send_text_message_to_target(config, target, body, product_id=None, intent="order_confirmation"):
    if target["time_window_state"] != "open":
        raise RuntimeError("Fereastra WhatsApp nu este deschisă pentru conversația de test.")

    payload = build_text_payload(target, body)
    response = requests.post(
        f"{config['base_url']}/v1.0/messages",
        headers={
            "X-API-KEY": config["api_key"],
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json=payload,
        timeout=30,
    )
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"Superchat send text a eșuat: HTTP {response.status_code}")
    try:
        response_json = response.json()
    except ValueError:
        response_json = {}
    message_id = response_json.get("id") or response_json.get("message_id")

    context_recorded = True
    try:
        record_sent_response(
            target["conversation_id"],
            product_id or os.environ.get("AI_DEFAULT_PRODUCT_ID") or "",
            str(body or "").strip(),
            [],
            creative_asset_id=None,
            intent=intent,
        )
    except Exception:
        context_recorded = False

    return {
        "http_status": response.status_code,
        "message_id": message_id,
        "context_recorded": context_recorded,
    }


def send_whats_app_template_to_conversation(
    conversation_id,
    template_id,
    variables=None,
    product_id=None,
    intent="whats_app_template",
    expected_phone=None,
    template_name="",
    schedule_operator_handoff=False,
    handoff_delay_seconds=None,
    aux_cta_labels=None,
):
    config = get_config()
    if not config["send_enabled"]:
        raise RuntimeError("AI_TEST_SEND_ENABLED este false; trimiterea reală este blocată.")
    target = resolve_verified_conversation_target(config, conversation_id, expected_phone=expected_phone)
    return send_whats_app_template_to_target(
        config,
        target,
        template_id,
        variables=variables,
        product_id=product_id,
        intent=intent,
        expected_phone=expected_phone,
        template_name=template_name,
        schedule_operator_handoff=schedule_operator_handoff,
        handoff_delay_seconds=handoff_delay_seconds,
        aux_cta_labels=aux_cta_labels,
    )


def send_whats_app_template_to_target(
    config,
    target,
    template_id,
    variables=None,
    product_id=None,
    intent="whats_app_template",
    expected_phone=None,
    template_name="",
    schedule_operator_handoff=False,
    handoff_delay_seconds=None,
    aux_cta_labels=None,
):
    payload = build_whats_app_template_payload(target, template_id, variables=variables)
    sent_at = timezone.now()
    response, response_json = _send_payload(config, payload, "Superchat send WhatsApp template")
    message_id = response_json.get("id") or response_json.get("message_id")

    context_recorded = True
    try:
        variable_labels = []
        if isinstance(variables, list):
            variable_labels = [
                str(item.get("value") or "")[:40]
                for item in variables
                if isinstance(item, dict) and item.get("value")
            ]
        record_sent_response(
            target["conversation_id"],
            product_id or os.environ.get("AI_DEFAULT_PRODUCT_ID") or "",
            f"template:{template_name or template_id}",
            variable_labels,
            creative_asset_id=None,
            intent=intent,
        )
    except Exception:
        context_recorded = False

    scheduled_handoff = None
    if schedule_operator_handoff:
        if str(product_id or "").startswith("business:peeko") and _peeko_test_keep_handle_status():
            scheduled_handoff = {
                "disabled": True,
                "reason": "peeko_test_keep_handle_status",
            }
        else:
            scheduled_handoff = _schedule_peeko_url_handoff(
                target,
                sent_at,
                template_name=template_name or str(template_id),
                template_message_id=message_id or "",
                product_id=product_id or "business:peeko",
                expected_phone=expected_phone,
                delay_seconds=handoff_delay_seconds,
                aux_cta_labels=aux_cta_labels,
            )

    return {
        "http_status": response.status_code,
        "message_id": message_id,
        "context_recorded": context_recorded,
        "scheduled_handoff": scheduled_handoff,
    }


def send_quick_reply_to_conversation(conversation_id, body, buttons, product_id=None, intent="recovery_flow", expected_phone=None):
    config = get_config()
    if not config["send_enabled"]:
        raise RuntimeError("AI_TEST_SEND_ENABLED este false; trimiterea reală este blocată.")
    target = resolve_verified_conversation_target(config, conversation_id, expected_phone=expected_phone)
    return send_quick_reply_to_target(
        config,
        target,
        body,
        buttons,
        product_id=product_id,
        intent=intent,
    )


def send_quick_reply_to_target(config, target, body, buttons, product_id=None, intent="recovery_flow"):
    if target["time_window_state"] != "open":
        raise RuntimeError("Fereastra WhatsApp nu este deschisă pentru conversația de test.")

    payload = build_quick_reply_payload_from_parts(target, body, buttons)
    response = requests.post(
        f"{config['base_url']}/v1.0/messages",
        headers={
            "X-API-KEY": config["api_key"],
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json=payload,
        timeout=30,
    )
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"Superchat send quick reply a eșuat: HTTP {response.status_code}")
    try:
        response_json = response.json()
    except ValueError:
        response_json = {}
    message_id = response_json.get("id") or response_json.get("message_id")

    context_recorded = True
    try:
        record_sent_response(
            target["conversation_id"],
            product_id or os.environ.get("AI_DEFAULT_PRODUCT_ID") or "",
            str(body or "").strip(),
            buttons,
            creative_asset_id=None,
            intent=intent,
        )
    except Exception:
        context_recorded = False

    return {
        "http_status": response.status_code,
        "message_id": message_id,
        "buttons": list(buttons or []),
        "context_recorded": context_recorded,
    }


def send_link_buttons_to_conversation(conversation_id, body, buttons, product_id=None, intent="link_buttons", expected_phone=None):
    config = get_config()
    if not config["send_enabled"]:
        raise RuntimeError("AI_TEST_SEND_ENABLED este false; trimiterea reală este blocată.")
    target = resolve_verified_conversation_target(config, conversation_id, expected_phone=expected_phone)
    return send_link_buttons_to_target(
        config,
        target,
        body,
        buttons,
        product_id=product_id,
        intent=intent,
    )


def send_link_buttons_to_target(config, target, body, buttons, product_id=None, intent="link_buttons"):
    if target["time_window_state"] != "open":
        raise RuntimeError("Fereastra WhatsApp nu este deschisă pentru conversația de test.")

    payload = build_link_buttons_payload_from_parts(target, body, buttons)
    response = requests.post(
        f"{config['base_url']}/v1.0/messages",
        headers={
            "X-API-KEY": config["api_key"],
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        json=payload,
        timeout=30,
    )
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"Superchat send link buttons a eșuat: HTTP {response.status_code}: {response.text[:300]}")
    try:
        response_json = response.json()
    except ValueError:
        response_json = {}
    message_id = response_json.get("id") or response_json.get("message_id")

    titles = [str(button.get("title") or "").strip()[:20] for button in (buttons or []) if isinstance(button, dict)]
    context_recorded = True
    try:
        record_sent_response(
            target["conversation_id"],
            product_id or os.environ.get("AI_DEFAULT_PRODUCT_ID") or "",
            str(body or "").strip(),
            titles,
            creative_asset_id=None,
            intent=intent,
        )
    except Exception:
        context_recorded = False

    return {
        "http_status": response.status_code,
        "message_id": message_id,
        "buttons": buttons,
        "context_recorded": context_recorded,
    }


def prepare_reviewed_conversation_send(run_id, conversation_id, expected_phone=None):
    config = get_config()
    run = AiResponseProcessRun.objects.get(run_id=run_id)
    if run.status != "human_approved_review_only":
        raise RuntimeError("Run-ul trebuie aprobat manual înainte de test.")
    if str(run.conversation_id) != str(conversation_id):
        raise RuntimeError("Run-ul nu aparține conversației țintă.")
    if not (run.final_body or "").strip():
        raise RuntimeError("Run-ul nu are text final.")

    target = resolve_verified_conversation_target(config, conversation_id, expected_phone=expected_phone)
    creative = _selected_creative(run)
    payload = build_quick_reply_payload(target, run, creative)
    return {
        "config": config,
        "run": run,
        "target": target,
        "creative": creative,
        "payload": payload,
    }


def prepare_reviewed_test_send(run_id):
    config = get_config()
    if not config.get("test_conversation_id"):
        raise RuntimeError("AI_TEST_CONVERSATION_ID este obligatoriu pentru test sender.")
    return prepare_reviewed_conversation_send(
        run_id,
        config["test_conversation_id"],
        expected_phone=config.get("allowed_phone"),
    )


def send_reviewed_to_conversation(run_id, conversation_id, expected_phone=None):
    prepared = prepare_reviewed_conversation_send(run_id, conversation_id, expected_phone=expected_phone)
    config = prepared["config"]
    target = prepared["target"]
    run = prepared["run"]
    creative = prepared["creative"]

    if not config["send_enabled"]:
        raise RuntimeError("AI_TEST_SEND_ENABLED este false; trimiterea reală este blocată.")
    if target["time_window_state"] != "open":
        raise RuntimeError("Fereastra WhatsApp nu este deschisă pentru conversația de test.")

    with transaction.atomic():
        locked_run = AiResponseProcessRun.objects.select_for_update().get(run_id=run.run_id)
        if locked_run.status != "human_approved_review_only":
            raise RuntimeError("Run-ul nu mai este disponibil pentru trimitere.")
        locked_run.status = "test_sending"
        locked_run.final_action = "test_sending"
        locked_run.save(update_fields=["status", "final_action"])

    try:
        response = requests.post(
            f"{config['base_url']}/v1.0/messages",
            headers={
                "X-API-KEY": config["api_key"],
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=prepared["payload"],
            timeout=30,
        )
        if not 200 <= response.status_code < 300:
            raise RuntimeError(f"Superchat send a eșuat: HTTP {response.status_code}")
    except Exception:
        AiResponseProcessRun.objects.filter(
            run_id=run.run_id,
            status="test_sending",
        ).update(
            status="human_approved_review_only",
            final_action="review_approved",
        )
        raise

    try:
        response_json = response.json()
    except ValueError:
        response_json = {}
    message_id = response_json.get("id") or response_json.get("message_id")
    now = timezone.now()

    with transaction.atomic():
        run = AiResponseProcessRun.objects.select_for_update().get(run_id=run.run_id)
        if run.status != "test_sending":
            raise RuntimeError("Starea run-ului s-a schimbat în timpul trimiterii.")
        AiResponseProcessStep.objects.create(
            step_id=uuid.uuid4(),
            run=run,
            conversation_id=run.conversation_id,
            product_id=run.product_id,
            step_name="test_send",
            attempt=1,
            input_json={"target": "configured_test_allowlist"},
            output_json={
                "http_status": response.status_code,
                "message_id": message_id,
                "creative_asset_id": str(creative.asset_id) if creative else None,
                "phone_verified": True,
            },
            approved=True,
            score=100,
            severity="info",
            action="test_sent",
            fail_reasons=[],
            blocking_issues=[],
            feedback_for_repair="",
            created_at=now,
        )
        run.status = "test_sent"
        run.final_action = "test_sent"
        run.save(update_fields=["status", "final_action"])

        if creative:
            with transaction.get_connection().cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO product_creative_usage_history (
                        conversation_id, product_id, asset_id, sales_stage, intent,
                        next_best_action, reason, sent, source, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE, %s, %s)
                    """,
                    [
                        target["conversation_id"],
                        run.product_id,
                        creative.asset_id,
                        "test",
                        "reviewed_test",
                        "quick_reply",
                        "approved manual test send",
                        "safe_test_sender",
                        now,
                    ],
                )

    signal_step = run.steps.filter(step_name="signal").order_by("-created_at").first()
    signal_output = signal_step.output_json or {} if signal_step else {}
    context_recorded = True
    try:
        record_sent_response(
            target["conversation_id"],
            run.product_id,
            run.final_body,
            run.final_buttons,
            creative_asset_id=creative.asset_id if creative else None,
            intent=signal_output.get("intent"),
        )
    except Exception:
        context_recorded = False

    return {
        "http_status": response.status_code,
        "message_id": message_id,
        "creative_asset_id": str(creative.asset_id) if creative else None,
        "buttons": list(run.final_buttons or []),
        "context_recorded": context_recorded,
    }


def send_reviewed_test(run_id):
    config = get_config()
    if not config.get("test_conversation_id"):
        raise RuntimeError("AI_TEST_CONVERSATION_ID este obligatoriu pentru test sender.")
    return send_reviewed_to_conversation(
        run_id,
        config["test_conversation_id"],
        expected_phone=config.get("allowed_phone"),
    )
