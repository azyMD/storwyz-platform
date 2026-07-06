import os
import logging
import uuid
from datetime import timezone as datetime_timezone

from celery import shared_task
from django.db import close_old_connections
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .ai_webhook_runtime import process_webhook_payload
from .models import AiResponseProcessStep, Message


logger = logging.getLogger(__name__)


def _is_true(value):
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


@shared_task(
    bind=True,
    name="superchatsync.process_superchat_webhook",
    queue="whatsapp_ai",
    autoretry_for=(Exception,),
    retry_backoff=10,
    retry_jitter=True,
    retry_kwargs={"max_retries": 2},
)
def process_superchat_webhook_payload(self, payload):
    close_old_connections()
    try:
        result = process_webhook_payload(payload if isinstance(payload, dict) else {})
        logger.warning(
            "superchat_webhook_task id=%s decision=%s reason=%s",
            self.request.id,
            result.get("decision") if isinstance(result, dict) else None,
            result.get("reason") if isinstance(result, dict) else None,
        )
        return result
    finally:
        close_old_connections()


@shared_task(
    bind=True,
    name="superchatsync.peeko_url_handoff_if_no_aux_cta",
    queue="whatsapp_ai",
    autoretry_for=(Exception,),
    retry_backoff=10,
    retry_jitter=True,
    retry_kwargs={"max_retries": 2},
)
def peeko_url_handoff_if_no_aux_cta(
    self,
    conversation_id,
    sent_at_iso,
    template_name="",
    template_message_id="",
    product_id="business:peeko",
    expected_phone=None,
    aux_cta_labels=None,
):
    close_old_connections()
    try:
        sent_at = parse_datetime(str(sent_at_iso or ""))
        if sent_at is None:
            sent_at = timezone.now()
        if timezone.is_naive(sent_at):
            sent_at = timezone.make_aware(sent_at, timezone=datetime_timezone.utc)

        normalized_aux_labels = {
            str(label or "").strip().casefold()
            for label in (aux_cta_labels or ["Help me choose", "More options", "Browse deals"])
            if str(label or "").strip()
        }
        candidates = list(
            Message.objects.filter(
                conversation_id=conversation_id,
                sender_type="client",
                sent_at__gte=sent_at,
            )
            .order_by("sent_at")
        )
        aux_reply = None
        for candidate in candidates:
            clicked = str(candidate.button_clicked or "").strip().casefold()
            text = str(candidate.message_text or "").strip().casefold()
            if clicked in normalized_aux_labels or text in normalized_aux_labels:
                aux_reply = candidate
                break
        if aux_reply:
            result = {
                "ok": True,
                "decision": "handoff_skipped_aux_cta_clicked",
                "conversation_id": conversation_id,
                "button_clicked": aux_reply.button_clicked,
                "message_id": aux_reply.message_id,
            }
            AiResponseProcessStep.objects.create(
                step_id=uuid.uuid4(),
                run=None,
                conversation_id=conversation_id,
                product_id=product_id,
                step_name="peeko_url_handoff_timeout",
                attempt=1,
                input_json={
                    "template_name": template_name,
                    "template_message_id": template_message_id,
                    "sent_at": sent_at.isoformat(),
                    "expected_phone": expected_phone,
                    "aux_cta_labels": sorted(normalized_aux_labels),
                },
                output_json=result,
                approved=True,
                score=100,
                severity="info",
                action="handoff_skipped_aux_cta_clicked",
                fail_reasons=[],
                blocking_issues=[],
                feedback_for_repair="",
                created_at=timezone.now(),
            )
            return result

        if str(product_id or "").startswith("business:peeko") and _is_true(
            os.environ.get("PEEKO_TEST_KEEP_HANDLE_STATUS", "0")
        ):
            result = {
                "ok": True,
                "decision": "operator_handoff_suppressed_test_keep_handle_status",
                "conversation_id": conversation_id,
                "template_name": template_name,
                "template_message_id": template_message_id,
            }
            AiResponseProcessStep.objects.create(
                step_id=uuid.uuid4(),
                run=None,
                conversation_id=conversation_id,
                product_id=product_id,
                step_name="peeko_url_handoff_timeout",
                attempt=1,
                input_json={
                    "template_name": template_name,
                    "template_message_id": template_message_id,
                    "sent_at": sent_at.isoformat(),
                    "expected_phone": expected_phone,
                    "aux_cta_labels": sorted(normalized_aux_labels),
                    "timeout_seconds": 300,
                    "reason": "no_auxiliary_quick_reply_after_url_template",
                },
                output_json=result,
                approved=True,
                score=100,
                severity="info",
                action="operator_handoff_suppressed_test_keep_handle_status",
                fail_reasons=[],
                blocking_issues=[],
                feedback_for_repair="",
                created_at=timezone.now(),
            )
            logger.warning(
                "peeko_url_handoff_timeout_task id=%s conversation_id=%s action=%s",
                self.request.id,
                conversation_id,
                result["decision"],
            )
            return result

        from .superchat_safe_send import get_config, set_conversation_handle_status

        config = get_config()
        result = set_conversation_handle_status(config, conversation_id, "operator")
        action = "operator_handoff_after_url_timeout" if result.get("ok") else "operator_handoff_after_url_timeout_failed"
        AiResponseProcessStep.objects.create(
            step_id=uuid.uuid4(),
            run=None,
            conversation_id=conversation_id,
            product_id=product_id,
            step_name="peeko_url_handoff_timeout",
            attempt=1,
            input_json={
                "template_name": template_name,
                "template_message_id": template_message_id,
                "sent_at": sent_at.isoformat(),
                "expected_phone": expected_phone,
                "aux_cta_labels": sorted(normalized_aux_labels),
                "timeout_seconds": 300,
                "reason": "no_auxiliary_quick_reply_after_url_template",
            },
            output_json=result,
            approved=bool(result.get("ok")),
            score=100 if result.get("ok") else 0,
            severity="info" if result.get("ok") else "warning",
            action=action,
            fail_reasons=[] if result.get("ok") else [result.get("reason") or "handle_status_update_failed"],
            blocking_issues=[],
            feedback_for_repair="" if result.get("ok") else str(result.get("reason") or ""),
            created_at=timezone.now(),
        )
        logger.warning(
            "peeko_url_handoff_timeout_task id=%s conversation_id=%s action=%s ok=%s",
            self.request.id,
            conversation_id,
            action,
            result.get("ok"),
        )
        return {
            "ok": bool(result.get("ok")),
            "decision": action,
            "conversation_id": conversation_id,
            "result": result,
        }
    finally:
        close_old_connections()
