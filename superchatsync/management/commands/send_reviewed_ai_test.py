import json

from django.core.management.base import BaseCommand, CommandError

from superchatsync.superchat_safe_send import (
    prepare_reviewed_test_send,
    send_reviewed_test,
)


class Command(BaseCommand):
    help = "Dry-run or send one manually approved AI response to the configured test allowlist."

    def add_arguments(self, parser):
        parser.add_argument("--run-id", required=True)
        parser.add_argument("--send", action="store_true")

    def handle(self, *args, **options):
        run_id = options["run_id"]
        try:
            if options["send"]:
                result = send_reviewed_test(run_id)
                self.stdout.write(json.dumps(result, ensure_ascii=False, indent=2))
                self.stdout.write(self.style.SUCCESS("Test message sent to the verified allowlist."))
                return

            prepared = prepare_reviewed_test_send(run_id)
        except Exception as exc:
            raise CommandError(str(exc)) from exc

        creative = prepared["creative"]
        summary = {
            "dry_run": True,
            "run_id": str(prepared["run"].run_id),
            "target": {
                "conversation_id": prepared["target"]["conversation_id"],
                "channel_type": prepared["target"]["channel_type"],
                "time_window_state": prepared["target"]["time_window_state"],
                "time_window_open_until": prepared["target"]["time_window_open_until"],
                "phone_verified": prepared["target"]["phone_verified"],
            },
            "content": prepared["payload"]["content"],
            "creative": {
                "asset_id": str(creative.asset_id),
                "title": creative.title,
                "asset_type": creative.asset_type,
            } if creative else None,
            "send_enabled": prepared["config"]["send_enabled"],
        }
        self.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2))
        self.stdout.write(self.style.WARNING("Dry-run only. No message was sent."))
