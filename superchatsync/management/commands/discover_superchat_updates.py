import os
import time
import requests
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from dotenv import load_dotenv

from superchatsync.models import SuperchatSyncRun, SuperchatSyncCandidate


BASE_DIR = "/opt/superchat-ai-agent"
load_dotenv(os.path.join(BASE_DIR, ".env"))


def parse_dt(value):
    if not value:
        return None
    dt = parse_datetime(value)
    if dt and timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone=timezone.utc)
    return dt


def get_local_conversation(conversation_id):
    """
    Returnează local_last_imported_at dacă conversația există în tabela conversations.
    Nu folosim model Django aici pentru că tabela conversations a fost creată separat.
    """
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT conversation_id, last_imported_at
            FROM conversations
            WHERE conversation_id = %s
            LIMIT 1
            """,
            [conversation_id],
        )
        row = cur.fetchone()

    if not row:
        return False, None

    return True, row[1]


class Command(BaseCommand):
    help = "Discover Superchat conversations that are new or possibly updated."

    def add_arguments(self, parser):
        parser.add_argument("--run-id", required=True)
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--max-pages", type=int, default=10000)
        parser.add_argument("--sleep", type=float, default=0.2)

    def handle(self, *args, **options):
        api_key = os.getenv("SUPERCHAT_API_KEY")
        base_url = os.getenv("SUPERCHAT_API_BASE_URL", "https://api.superchat.com/v1.0").rstrip("/")

        if not api_key:
            raise CommandError("SUPERCHAT_API_KEY lipsește din /opt/superchat-ai-agent/.env")

        run_id = options["run_id"]
        limit = options["limit"]
        max_pages = options["max_pages"]
        sleep_seconds = options["sleep"]

        try:
            run = SuperchatSyncRun.objects.get(run_id=run_id)
        except SuperchatSyncRun.DoesNotExist:
            raise CommandError(f"Run ID not found: {run_id}")

        active_exists = (
            SuperchatSyncRun.objects
            .filter(status__in=["discovering", "extracting", "stopping"])
            .exclude(run_id=run.run_id)
            .exists()
        )

        if active_exists:
            run.status = "failed"
            run.error = "Another Superchat sync is already running."
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "error", "finished_at", "updated_at"])
            raise CommandError("Another Superchat sync is already running.")

        run.status = "discovering"
        run.error = None
        run.stop_requested = False
        run.total_checked = 0
        run.candidates_found = 0
        run.total_to_extract = 0
        run.processed_count = 0
        run.downloaded_count = 0
        run.parsed_count = 0
        run.error_count = 0
        run.current_conversation_id = None
        run.started_at = timezone.now()
        run.finished_at = None
        run.save()

        headers = {
            "X-API-Key": api_key,
            "Accept": "application/json",
        }

        after = None
        page = 0
        candidates_found = 0

        self.stdout.write(f"Starting discover run: {run.run_id}")

        try:
            while True:
                run.refresh_from_db()

                if run.stop_requested or run.status == "stopping":
                    run.status = "stopped"
                    run.finished_at = timezone.now()
                    run.notes = (run.notes or "") + "\nStopped by user during discover."
                    run.save(update_fields=["status", "finished_at", "notes", "updated_at"])
                    self.stdout.write("Stopped by user.")
                    return

                page += 1

                if page > max_pages:
                    break

                params = {"limit": limit}
                if after:
                    params["after"] = after

                response = requests.get(
                    f"{base_url}/conversations",
                    headers=headers,
                    params=params,
                    timeout=60,
                )

                if response.status_code >= 400:
                    raise RuntimeError(
                        f"Superchat API error {response.status_code}: {response.text[:500]}"
                    )

                data = response.json()
                results = data.get("results", [])

                if not results:
                    break

                for item in results:
                    conversation_id = item.get("id")
                    if not conversation_id:
                        continue

                    run.refresh_from_db()

                    if run.stop_requested or run.status == "stopping":
                        run.status = "stopped"
                        run.finished_at = timezone.now()
                        run.notes = (run.notes or "") + "\nStopped by user during discover."
                        run.save(update_fields=["status", "finished_at", "notes", "updated_at"])
                        self.stdout.write("Stopped by user.")
                        return

                    run.current_conversation_id = conversation_id
                    run.total_checked += 1
                    run.save(update_fields=["current_conversation_id", "total_checked", "updated_at"])

                    local_exists, local_last_imported_at = get_local_conversation(conversation_id)

                    time_window = item.get("time_window") or {}
                    open_until = parse_dt(time_window.get("open_until"))

                    # Prima versiune:
                    # - dacă nu există local => candidat nou
                    # - dacă există local, dar time_window.open_until e mai nou decât last_imported_at => posibil update
                    # Dacă Superchat oferă ulterior updated_at clar, îl folosim pe acela.
                    is_candidate = False
                    change_reason = None

                    if not local_exists:
                        is_candidate = True
                        change_reason = "new_conversation"
                    elif open_until and local_last_imported_at and open_until > local_last_imported_at:
                        is_candidate = True
                        change_reason = "possibly_updated_time_window"
                    elif local_exists and not local_last_imported_at:
                        is_candidate = True
                        change_reason = "local_exists_without_import_timestamp"

                    if is_candidate:
                        channel = item.get("channel") or {}
                        inbox = item.get("inbox") or {}

                        candidate, created = SuperchatSyncCandidate.objects.update_or_create(
                            run=run,
                            conversation_id=conversation_id,
                            defaults={
                                "superchat_status": item.get("status"),
                                "channel_id": channel.get("id"),
                                "channel_type": channel.get("type"),
                                "inbox_id": inbox.get("id"),
                                "inbox_name": inbox.get("name"),
                                "superchat_url": item.get("url"),
                                "local_exists": local_exists,
                                "local_last_imported_at": local_last_imported_at,
                                "superchat_open_until": open_until,
                                "change_reason": change_reason,
                                "decision": "pending",
                                "extract_status": "pending",
                                "raw_payload": item,
                            },
                        )

                        if created:
                            candidates_found += 1
                            run.candidates_found = candidates_found
                            run.total_to_extract = candidates_found
                            run.save(update_fields=["candidates_found", "total_to_extract", "updated_at"])

                pagination = data.get("pagination") or {}
                after = pagination.get("next_cursor")

                self.stdout.write(
                    f"Page {page}: checked={run.total_checked}, candidates={run.candidates_found}"
                )

                if not after:
                    break

                time.sleep(sleep_seconds)

            run.status = "waiting_approval"
            run.finished_at = timezone.now()
            run.current_conversation_id = None
            run.save(update_fields=["status", "finished_at", "current_conversation_id", "updated_at"])

            self.stdout.write(
                self.style.SUCCESS(
                    f"Discover completed. Checked={run.total_checked}, candidates={run.candidates_found}"
                )
            )

        except Exception as e:
            run.status = "failed"
            run.error = str(e)
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "error", "finished_at", "updated_at"])
            raise
