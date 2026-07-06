import hashlib
import json
import os
import shutil
import time
import zipfile
from datetime import datetime, timezone as dt_timezone

import requests
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from dotenv import load_dotenv

from superchatsync.models import SuperchatSyncRun, SuperchatSyncCandidate


BASE_DIR = "/opt/superchat-ai-agent"
DATA_DIR = os.path.join(BASE_DIR, "data", "superchat_exports")
ZIP_DIR = os.path.join(DATA_DIR, "zips")
EXTRACTED_DIR = os.path.join(DATA_DIR, "extracted")
PDF_DIR = os.path.join(DATA_DIR, "pdfs")

load_dotenv(os.path.join(BASE_DIR, ".env"))


def safe_name(value):
    return str(value).replace("/", "_").replace("\\", "_").replace(" ", "_")


def ensure_dirs():
    os.makedirs(ZIP_DIR, exist_ok=True)
    os.makedirs(EXTRACTED_DIR, exist_ok=True)
    os.makedirs(PDF_DIR, exist_ok=True)


def parse_dt(value):
    if not value:
        return None

    text = str(value)

    # Superchat poate trimite nanoseconds; Django parsează sigur microseconds.
    if "." in text and text.endswith("Z"):
        head, rest = text.split(".", 1)
        fraction = rest[:-1]
        fraction = fraction[:6].ljust(6, "0")
        text = f"{head}.{fraction}Z"

    dt = parse_datetime(text)
    if dt and timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone=dt_timezone.utc)
    return dt


def to_superchat_iso(dt):
    if not dt:
        dt = timezone.now()
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone=dt_timezone.utc)
    dt = dt.astimezone(dt_timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def download_file(url, path):
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)


def extract_zip(zip_path, extract_dir):
    os.makedirs(extract_dir, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(extract_dir)
        names = z.namelist()

    pdf_path = None
    attachments_count = 0

    for name in names:
        lower = name.lower()
        full_path = os.path.join(extract_dir, name)

        if lower.endswith(".pdf") and pdf_path is None:
            pdf_path = full_path

        if lower.startswith("attachments/") and not name.endswith("/"):
            attachments_count += 1

    return pdf_path, attachments_count, names


def upsert_conversation_export(candidate, start_iso, end_iso, zip_hash):
    metadata = {
        "candidate_id": str(candidate.candidate_id),
        "run_id": str(candidate.run_id),
        "export_link_valid_until": (
            candidate.export_link_valid_until.isoformat()
            if candidate.export_link_valid_until else None
        ),
    }

    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO conversation_exports (
                export_id,
                conversation_id,
                source,
                export_status,
                requested_at,
                downloaded_at,
                export_from,
                export_to,
                raw_pdf_path,
                raw_zip_path,
                raw_file_hash,
                error,
                metadata,
                created_at,
                updated_at
            )
            VALUES (
                %s, %s, 'superchat', %s, NOW(), NOW(), %s, %s,
                %s, %s, %s, %s, %s::jsonb, NOW(), NOW()
            )
            ON CONFLICT (export_id) DO UPDATE SET
                conversation_id = EXCLUDED.conversation_id,
                export_status = EXCLUDED.export_status,
                downloaded_at = EXCLUDED.downloaded_at,
                export_from = EXCLUDED.export_from,
                export_to = EXCLUDED.export_to,
                raw_pdf_path = EXCLUDED.raw_pdf_path,
                raw_zip_path = EXCLUDED.raw_zip_path,
                raw_file_hash = EXCLUDED.raw_file_hash,
                error = EXCLUDED.error,
                metadata = EXCLUDED.metadata,
                updated_at = NOW();
            """,
            [
                candidate.export_id,
                candidate.conversation_id,
                candidate.export_status or candidate.extract_status,
                start_iso,
                end_iso,
                candidate.pdf_path,
                candidate.zip_path,
                zip_hash,
                candidate.error,
                json.dumps(metadata),
            ],
        )


class Command(BaseCommand):
    help = "Extract approved Superchat conversation exports: create export, poll, download ZIP, extract PDF."

    def add_arguments(self, parser):
        parser.add_argument("--run-id", required=True)
        parser.add_argument("--poll-sleep", type=float, default=5.0)
        parser.add_argument("--max-polls", type=int, default=120)

    def handle(self, *args, **options):
        ensure_dirs()

        api_key = os.getenv("SUPERCHAT_API_KEY")
        base_url = os.getenv("SUPERCHAT_API_BASE_URL", "https://api.superchat.com/v1.0").rstrip("/")

        if not api_key:
            raise CommandError("SUPERCHAT_API_KEY lipsește din /opt/superchat-ai-agent/.env")

        run_id = options["run_id"]
        poll_sleep = options["poll_sleep"]
        max_polls = options["max_polls"]

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

        candidates = (
            SuperchatSyncCandidate.objects
            .filter(run=run, decision="approved")
            .exclude(extract_status__in=["downloaded", "parsed", "skipped"])
            .order_by("created_at")
        )

        total = candidates.count()

        if total == 0:
            run.status = "waiting_approval"
            run.notes = (run.notes or "") + "\nNo approved candidates to extract."
            run.save(update_fields=["status", "notes", "updated_at"])
            self.stdout.write("No approved candidates to extract.")
            return

        run.status = "extracting"
        run.stop_requested = False
        run.total_to_extract = total
        run.processed_count = 0
        run.downloaded_count = 0
        run.error_count = 0
        run.current_conversation_id = None
        run.started_at = timezone.now()
        run.finished_at = None
        run.error = None
        run.save()

        headers = {
            "X-API-Key": api_key,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

        start_iso = to_superchat_iso(run.start_date or datetime(2020, 1, 1, tzinfo=dt_timezone.utc))
        end_iso = to_superchat_iso(run.end_date or timezone.now())

        self.stdout.write(f"Starting extraction run: {run.run_id}")
        self.stdout.write(f"Candidates: {total}")
        self.stdout.write(f"Range: {start_iso} -> {end_iso}")

        try:
            for candidate in candidates:
                run.refresh_from_db()

                if run.stop_requested or run.status == "stopping":
                    run.status = "stopped"
                    run.finished_at = timezone.now()
                    run.current_conversation_id = None
                    run.notes = (run.notes or "") + "\nStopped by user during extraction."
                    run.save(update_fields=["status", "finished_at", "current_conversation_id", "notes", "updated_at"])
                    self.stdout.write("Stopped by user.")
                    return

                run.current_conversation_id = candidate.conversation_id
                run.save(update_fields=["current_conversation_id", "updated_at"])

                self.stdout.write(f"Extracting {candidate.conversation_id}")

                try:
                    candidate.extract_status = "exporting"
                    candidate.error = None
                    candidate.save(update_fields=["extract_status", "error", "updated_at"])

                    # 1. Create export job
                    create_resp = requests.post(
                        f"{base_url}/conversations/{candidate.conversation_id}/export",
                        headers=headers,
                        json={"start": start_iso, "end": end_iso},
                        timeout=60,
                    )

                    if create_resp.status_code >= 400:
                        raise RuntimeError(
                            f"Create export failed {create_resp.status_code}: {create_resp.text[:1000]}"
                        )

                    create_data = create_resp.json()

                    export_id = create_data.get("id")
                    if not export_id:
                        raise RuntimeError(f"Export ID missing in response: {create_data}")

                    candidate.export_id = export_id
                    candidate.export_status = create_data.get("status")
                    candidate.extract_status = "export_pending"
                    candidate.raw_payload = {
                        "create_export_response": create_data,
                    }
                    candidate.save(update_fields=[
                        "export_id",
                        "export_status",
                        "extract_status",
                        "raw_payload",
                        "updated_at",
                    ])

                    # 2. Poll export job
                    link_url = None
                    link_valid_until = None
                    status = None
                    poll_data = None

                    for poll_index in range(max_polls):
                        run.refresh_from_db()

                        if run.stop_requested or run.status == "stopping":
                            run.status = "stopped"
                            run.finished_at = timezone.now()
                            run.current_conversation_id = None
                            run.notes = (run.notes or "") + "\nStopped by user during export polling."
                            run.save(update_fields=["status", "finished_at", "current_conversation_id", "notes", "updated_at"])
                            self.stdout.write("Stopped by user.")
                            return

                        poll_resp = requests.get(
                            f"{base_url}/conversations/{candidate.conversation_id}/export/{export_id}",
                            headers=headers,
                            timeout=60,
                        )

                        if poll_resp.status_code >= 400:
                            raise RuntimeError(
                                f"Poll export failed {poll_resp.status_code}: {poll_resp.text[:1000]}"
                            )

                        poll_data = poll_resp.json()
                        status = poll_data.get("status")
                        link = poll_data.get("link") or {}

                        if status == "done" and link.get("url"):
                            link_url = link.get("url")
                            link_valid_until = parse_dt(link.get("valid_until"))
                            break

                        candidate.export_status = status
                        candidate.save(update_fields=["export_status", "updated_at"])

                        time.sleep(poll_sleep)

                    if not link_url:
                        raise RuntimeError(f"Export not ready after polling. Last status={status}, data={poll_data}")

                    candidate.export_status = status
                    candidate.export_link = link_url
                    candidate.export_link_valid_until = link_valid_until
                    candidate.extract_status = "downloading"
                    candidate.save(update_fields=[
                        "export_status",
                        "export_link",
                        "export_link_valid_until",
                        "extract_status",
                        "updated_at",
                    ])

                    # 3. Download ZIP
                    zip_name = f"{safe_name(candidate.conversation_id)}_{safe_name(export_id)}.zip"
                    zip_path = os.path.join(ZIP_DIR, zip_name)
                    download_file(link_url, zip_path)
                    zip_hash = sha256_file(zip_path)

                    # 4. Extract ZIP
                    extract_dir = os.path.join(EXTRACTED_DIR, f"{safe_name(candidate.conversation_id)}_{safe_name(export_id)}")
                    pdf_path, attachments_count, names = extract_zip(zip_path, extract_dir)

                    if not pdf_path:
                        raise RuntimeError(f"No PDF found inside ZIP: {zip_path}")

                    # 5. Copy PDF to central PDF folder
                    final_pdf_name = os.path.basename(pdf_path)
                    final_pdf_path = os.path.join(PDF_DIR, final_pdf_name)
                    shutil.copy2(pdf_path, final_pdf_path)

                    candidate.zip_path = zip_path
                    candidate.pdf_path = final_pdf_path
                    candidate.attachments_found = attachments_count
                    candidate.extract_status = "downloaded"
                    candidate.raw_payload = {
                        "create_export_response": create_data,
                        "last_poll_response": poll_data,
                        "zip_files": names,
                    }
                    candidate.save(update_fields=[
                        "zip_path",
                        "pdf_path",
                        "attachments_found",
                        "extract_status",
                        "raw_payload",
                        "updated_at",
                    ])

                    upsert_conversation_export(candidate, start_iso, end_iso, zip_hash)

                    run.downloaded_count += 1
                    self.stdout.write(self.style.SUCCESS(f"Downloaded: {zip_path}"))

                except Exception as candidate_error:
                    candidate.extract_status = "error"
                    candidate.error = str(candidate_error)
                    candidate.save(update_fields=["extract_status", "error", "updated_at"])

                    run.error_count += 1
                    self.stdout.write(self.style.ERROR(f"ERROR {candidate.conversation_id}: {candidate_error}"))

                finally:
                    run.processed_count += 1
                    run.save(update_fields=[
                        "processed_count",
                        "downloaded_count",
                        "error_count",
                        "updated_at",
                    ])

            run.status = "completed"
            run.finished_at = timezone.now()
            run.current_conversation_id = None
            run.save(update_fields=["status", "finished_at", "current_conversation_id", "updated_at"])

            self.stdout.write(
                self.style.SUCCESS(
                    f"Extraction completed. Processed={run.processed_count}, downloaded={run.downloaded_count}, errors={run.error_count}"
                )
            )

        except Exception as e:
            run.status = "failed"
            run.error = str(e)
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "error", "finished_at", "updated_at"])
            raise
