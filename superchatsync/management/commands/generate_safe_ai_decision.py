from django.core.management.base import BaseCommand, CommandError

from superchatsync.ai_safe_agent import generate_safe_ai_decision
from superchatsync.models import Conversation


class Command(BaseCommand):
    help = "Generate safe review-only AI decisions for selected conversations. Does not send WhatsApp messages."

    def add_arguments(self, parser):
        parser.add_argument(
            "--conversation-id",
            action="append",
            dest="conversation_ids",
            help="Conversation ID to process. Can be passed multiple times.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Process the latest N conversations when no conversation IDs are provided.",
        )
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="Suppress per-conversation output.",
        )

    def handle(self, *args, **options):
        conversation_ids = options.get("conversation_ids") or []
        limit = options.get("limit") or 0
        quiet = options.get("quiet")

        if not conversation_ids:
            if limit <= 0:
                raise CommandError("Pass --conversation-id or --limit.")
            conversation_ids = list(
                Conversation.objects
                .order_by("-last_message_at")
                .values_list("conversation_id", flat=True)[:limit]
            )

        created = 0
        failed = 0

        for conversation_id in conversation_ids:
            try:
                run = generate_safe_ai_decision(conversation_id)
                created += 1
                if not quiet:
                    self.stdout.write(f"created run={run.run_id} conversation={conversation_id} status={run.status}")
            except Exception as exc:
                failed += 1
                self.stderr.write(f"failed conversation={conversation_id}: {exc}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Safe AI decisions complete. Created={created}, failed={failed}"
            )
        )
