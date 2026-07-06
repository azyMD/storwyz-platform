import json
import logging
import os
from pathlib import Path

from django.http import JsonResponse, HttpResponseNotAllowed, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt

from .ai_webhook_runtime import process_webhook_payload
from .tasks import process_superchat_webhook_payload


logger = logging.getLogger(__name__)


def _is_true(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def load_env():
    for env_path in [
        Path("/opt/superchat-ai-agent/.env"),
        Path("/opt/superchat-ai-agent/web/.env"),
    ]:
        if not env_path.exists():
            continue

        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()

            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _payload_identifier(payload):
    if not isinstance(payload, dict):
        return None
    message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    conversation = payload.get("conversation") if isinstance(payload.get("conversation"), dict) else {}
    return (
        message.get("id")
        or payload.get("message_id")
        or payload.get("id")
        or payload.get("conversation_id")
        or conversation.get("id")
    )


@csrf_exempt
def superchat_webhook(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])

    load_env()

    secret = os.environ.get("SUPERCHAT_WEBHOOK_SECRET")

    if secret:
        supplied = (
            request.headers.get("X-Superchat-Webhook-Secret")
            or request.headers.get("X-Webhook-Secret")
            or request.GET.get("token")
        )

        if supplied != secret:
            return HttpResponseForbidden("Invalid webhook secret")

    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except Exception as e:
        return JsonResponse({"ok": False, "error": f"Invalid JSON: {e}"}, status=400)

    async_enabled = _is_true(os.environ.get("SUPERCHAT_WEBHOOK_ASYNC_ENABLED", "true"))
    if async_enabled:
        try:
            task = process_superchat_webhook_payload.apply_async(
                args=[payload],
                queue="whatsapp_ai",
            )
            logger.warning(
                "superchat_webhook queued task=%s event=%s payload_id=%s",
                task.id,
                payload.get("event") if isinstance(payload, dict) else None,
                _payload_identifier(payload),
            )
            return JsonResponse(
                {
                    "ok": True,
                    "decision": "queued",
                    "task_id": task.id,
                    "payload_id": _payload_identifier(payload),
                },
                status=202,
                json_dumps_params={"ensure_ascii": False},
            )
        except Exception as e:
            logger.exception("superchat_webhook queue failed event=%s", payload.get("event"))
            if not _is_true(os.environ.get("SUPERCHAT_WEBHOOK_SYNC_FALLBACK", "false")):
                return JsonResponse({"ok": False, "decision": "queue_failed", "error": str(e)}, status=503)

    try:
        result = process_webhook_payload(payload)
        logger.warning(
            "superchat_webhook decision=%s reason=%s event=%s",
            result.get("decision"),
            result.get("reason"),
            payload.get("event") if isinstance(payload, dict) else None,
        )
        return JsonResponse(result, json_dumps_params={"ensure_ascii": False})
    except Exception as e:
        logger.exception("superchat_webhook failed event=%s", payload.get("event"))
        return JsonResponse({"ok": False, "error": str(e)}, status=500)
