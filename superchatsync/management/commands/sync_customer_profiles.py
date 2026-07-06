import uuid
from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from superchatsync.models import Conversation, CustomerProfile


def clean_value(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_phone(value):
    text = clean_value(value)
    if not text:
        return None
    digits = "".join(ch for ch in text if ch.isdigit())
    return f"+{digits}" if digits else None


def profile_key_for(conversation):
    phone = normalize_phone(getattr(conversation, "client_phone", None))
    if not phone:
        return None
    return f"phone:{phone}"


class Command(BaseCommand):
    help = "Build customer_profiles from imported Superchat conversations."

    def handle(self, *args, **options):
        grouped = defaultdict(list)

        conversations = (
            Conversation.objects
            .prefetch_related("messages")
            .order_by("last_message_at", "first_message_at")
        )

        for conversation in conversations.iterator(chunk_size=200):
            grouped[profile_key_for(conversation)].append(conversation)

        now = timezone.now()
        created = 0
        updated = 0
        skipped_without_phone = 0

        with transaction.atomic():
            for profile_key, items in grouped.items():
                if not profile_key:
                    skipped_without_phone += len(items)
                    continue
                items = sorted(
                    items,
                    key=lambda item: item.last_message_at or item.first_message_at or now,
                )
                first = items[0]
                last = items[-1]

                first_seen = min(
                    [
                        item.first_message_at
                        for item in items
                        if item.first_message_at is not None
                    ],
                    default=None,
                )
                last_seen = max(
                    [
                        item.last_message_at or item.first_message_at
                        for item in items
                        if item.last_message_at is not None or item.first_message_at is not None
                    ],
                    default=None,
                )
                total_messages = sum(item.messages.count() for item in items)

                defaults = {
                    "display_name": clean_value(last.client_name) or clean_value(first.client_name),
                    "phone": normalize_phone(getattr(last, "client_phone", None)) or normalize_phone(getattr(first, "client_phone", None)),
                    "email": clean_value(getattr(last, "client_email", None)) or clean_value(getattr(first, "client_email", None)),
                    "first_seen_at": first_seen,
                    "last_seen_at": last_seen,
                    "total_conversations": len(items),
                    "total_messages": total_messages,
                    "last_product_detected": clean_value(last.product_detected),
                    "last_conversation_id": last.conversation_id,
                    "status": "active",
                    "metadata": {
                        "source": "sync_customer_profiles",
                        "identity": "phone",
                        "phone_digits": "".join(ch for ch in profile_key if ch.isdigit()),
                        "phone_normalized": profile_key.replace("phone:", "", 1),
                        "conversation_ids": [item.conversation_id for item in items],
                    },
                    "updated_at": now,
                }

                obj = CustomerProfile.objects.filter(profile_key=profile_key).first()

                if obj is None:
                    CustomerProfile.objects.create(
                        customer_id=uuid.uuid4(),
                        profile_key=profile_key,
                        created_at=now,
                        **defaults,
                    )
                    created += 1
                else:
                    for field, value in defaults.items():
                        setattr(obj, field, value)
                    obj.save(update_fields=[*defaults.keys()])
                    updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                "Customer profiles synced. "
                f"Created={created}, updated={updated}, "
                f"skipped_without_phone={skipped_without_phone}, total_keys={len(grouped)}"
            )
        )
