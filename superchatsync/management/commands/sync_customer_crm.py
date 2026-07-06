import re

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from superchatsync.management.commands.sync_customer_profiles import clean_value, profile_key_for
from superchatsync.models import (
    Conversation,
    CustomerChannelIdentity,
    CustomerCommunicationEvent,
    CustomerConversionEvent,
    CustomerProfile,
)


def normalize_channel(value):
    text = str(value or "").strip().lower()
    if text in {"whats_app", "whatsapp", "wa"}:
        return "whatsapp"
    if text in {"sms", "phone", "email", "push", "web"}:
        return text
    return "other"


def normalize_identifier(channel, value):
    text = clean_value(value) or ""
    if channel in {"whatsapp", "sms", "phone"}:
        digits = re.sub(r"\D", "", text)
        return f"+{digits}" if digits else text.lower()
    if channel == "email":
        return text.lower()
    return text.lower()


def direction_for(message):
    if message.is_client_reply:
        return "inbound"
    sender = str(message.sender_type or "").lower()
    if sender in {"operator", "ai", "assistant", "system"}:
        return "outbound" if sender != "system" else "system"
    return "unknown"


def conversion_type_for(direction):
    if direction == "inbound":
        return "replied"
    if direction == "outbound":
        return "sent"
    return None


class Command(BaseCommand):
    help = "Populate CRM channel identities, communication history, and basic conversion events from existing conversations/messages."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=None)

    def handle(self, *args, **options):
        conversations = Conversation.objects.prefetch_related("messages").order_by("last_message_at", "first_message_at")
        if options.get("limit"):
            conversations = conversations[: options["limit"]]

        now = timezone.now()
        identities_created = 0
        identities_updated = 0
        events_created = 0
        events_updated = 0
        conversions_created = 0
        skipped_without_profile = 0

        with transaction.atomic():
            for conversation in conversations:
                profile_key = profile_key_for(conversation)
                if not profile_key:
                    skipped_without_profile += 1
                    continue
                profile = CustomerProfile.objects.filter(profile_key=profile_key).first()
                if not profile:
                    skipped_without_profile += 1
                    continue

                channel = normalize_channel(conversation.channel)
                identifiers = []
                if conversation.client_phone:
                    identifiers.append((channel if channel in {"whatsapp", "sms", "phone"} else "phone", conversation.client_phone))
                if conversation.client_email:
                    identifiers.append(("email", conversation.client_email))

                primary_identity = None
                for identity_channel, identifier in identifiers:
                    normalized = normalize_identifier(identity_channel, identifier)
                    identity, created = CustomerChannelIdentity.objects.update_or_create(
                        channel=identity_channel,
                        normalized_identifier=normalized,
                        defaults={
                            "customer_id": profile.customer_id,
                            "identifier": identifier,
                            "provider": "superchat" if identity_channel == "whatsapp" else None,
                            "is_primary": primary_identity is None,
                            "status": "active",
                            "first_seen_at": conversation.first_message_at,
                            "last_seen_at": conversation.last_message_at or conversation.first_message_at,
                            "metadata": {
                                "source": "sync_customer_crm",
                                "last_conversation_id": conversation.conversation_id,
                            },
                        },
                    )
                    if created:
                        identities_created += 1
                    else:
                        identities_updated += 1
                    if primary_identity is None:
                        primary_identity = identity

                for message in conversation.messages.all():
                    provider_message_id = message.message_id or str(message.message_pk)
                    direction = direction_for(message)
                    event, created = CustomerCommunicationEvent.objects.update_or_create(
                        provider="superchat",
                        provider_message_id=provider_message_id,
                        defaults={
                            "customer_id": profile.customer_id,
                            "channel_identity_id": primary_identity.identity_id if primary_identity else None,
                            "channel": channel,
                            "direction": direction,
                            "event_type": "message",
                            "status": "received" if direction == "inbound" else "sent",
                            "conversation_id": conversation.conversation_id,
                            "message_id": message.message_id,
                            "campaign_id": conversation.campaign_id,
                            "workflow_id": conversation.workflow_id,
                            "body_preview": (message.message_text or "")[:500],
                            "occurred_at": message.sent_at or message.created_at or now,
                            "metadata": {
                                "source": "sync_customer_crm",
                                "message_pk": str(message.message_pk),
                                "message_type": message.message_type,
                                "button_clicked": message.button_clicked,
                                "sender_type": message.sender_type,
                            },
                        },
                    )
                    if created:
                        events_created += 1
                    else:
                        events_updated += 1

                    conversion_type = conversion_type_for(direction)
                    if conversion_type and not CustomerConversionEvent.objects.filter(
                        communication_event_id=event.event_id,
                        event_type=conversion_type,
                    ).exists():
                        CustomerConversionEvent.objects.create(
                            customer_id=profile.customer_id,
                            communication_event_id=event.event_id,
                            channel=channel,
                            event_type=conversion_type,
                            product_id=conversation.product_detected,
                            campaign_id=conversation.campaign_id,
                            conversation_id=conversation.conversation_id,
                            occurred_at=event.occurred_at,
                            metadata={"source": "sync_customer_crm"},
                        )
                        conversions_created += 1

        self.stdout.write(
            self.style.SUCCESS(
                "CRM sync complete. "
                f"identities created={identities_created}, updated={identities_updated}; "
                f"events created={events_created}, updated={events_updated}; "
                f"conversions created={conversions_created}; "
                f"skipped_without_profile={skipped_without_profile}"
            )
        )
