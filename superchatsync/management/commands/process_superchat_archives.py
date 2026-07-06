import hashlib
import json
import os
import shutil
import zipfile

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from superchatsync.models import SuperchatSyncRun, SuperchatSyncCandidate


BASE_DIR = "/opt/superchat-ai-agent"
DATA_DIR = os.path.join(BASE_DIR, "data", "superchat_exports")
EXTRACTED_DIR = os.path.join(DATA_DIR, "extracted")
PDFS_DIR = os.path.join(DATA_DIR, "conversation_pdfs")


def ensure_dirs():
    os.makedirs(EXTRACTED_DIR, exist_ok=True)
    os.makedirs(PDFS_DIR, exist_ok=True)


def safe_name(value):
    return str(value or "").replace("/", "_").replace("\\", "_").replace(" ", "_")


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def find_conversation_pdf(zip_names):
    pdfs = [name for name in zip_names if name.lower().endswith(".pdf")]

    if not pdfs:
        return None

    if len(pdfs) == 1:
        return pdfs[0]

    preferred = []
    for name in pdfs:
        lower = name.lower()
        score = 0
        if "conversation" in lower:
            score += 100
        if "cv_" in lower:
            score += 50
        if "cex_" in lower:
            score += 50
        preferred.append((score, name))

    preferred.sort(reverse=True)
    return preferred[0][1]


def count_attachments(zip_names):
    count = 0
    for name in zip_names:
        lower = name.lower()
        if lower.startswith("attachments/") and not lower.endswith("/"):
            count += 1
    return count


def upsert_conversation_export(candidate):
    metadata = {
        "candidate_id": str(candidate.candidate_id),
        "run_id": str(candidate.run_id),
        "archive_status": candidate.archive_status,
        "extracted_dir": candidate.extracted_dir,
        "attachments_found": candidate.attachments_found,
    }

    if not candidate.export_id:
        return

    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO conversation_exports (
                export_id,
                conversation_id,
                source,
                export_status,
                downloaded_at,
                raw_pdf_path,
                raw_zip_path,
                raw_file_hash,
                error,
                metadata,
                created_at,
                updated_at
            )
            VALUES (
                %s, %s, 'superchat', %s, NOW(),
                %s, %s, %s, %s, %s::jsonb,
                NOW(), NOW()
            )
            ON CONFLICT (export_id) DO UPDATE SET
                conversation_id = EXCLUDED.conversation_id,
                export_status = EXCLUDED.export_status,
                downloaded_at = EXCLUDED.downloaded_at,
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
                candidate.archive_status,
                candidate.pdf_path,
                candidate.zip_path,
                candidate.raw_zip_hash,
                candidate.archive_error,
                json.dumps(metadata),
            ],
        )


class Command(BaseCommand):
    help = "Process downloaded Superchat ZIP archives: verify ZIP, extract, find conversation PDF, copy PDF to central folder."

    def add_arguments(self, parser):
        parser.add_argument("--run-id", required=False)
        parser.add_argument("--force", action="store_true")

    def handle(self, *args, **options):
        ensure_dirs()

        run_id = options.get("run_id")
        force = options.get("force")

        candidates = SuperchatSyncCandidate.objects.exclude(zip_path__isnull=True).exclude(zip_path="")

        if run_id:
            try:
                run = SuperchatSyncRun.objects.get(run_id=run_id)
            except SuperchatSyncRun.DoesNotExist:
                raise CommandError(f"Run ID not found: {run_id}")

            candidates = candidates.filter(run=run)

        if not force:
            candidates = candidates.exclude(archive_status="pdf_extracted")

        total = candidates.count()
        self.stdout.write(f"Archives to process: {total}")

        processed = 0
        errors = 0

        for candidate in candidates.order_by("created_at"):
            zip_path = candidate.zip_path
            conversation_id = candidate.conversation_id
            export_id = candidate.export_id or "no_export_id"

            self.stdout.write(f"Processing archive: {conversation_id} | {zip_path}")

            try:
                if not os.path.exists(zip_path):
                    raise RuntimeError(f"ZIP not found: {zip_path}")

                zip_size = os.path.getsize(zip_path)

                if zip_size <= 0:
                    raise RuntimeError(f"ZIP is empty: {zip_path}")

                zip_hash = file_sha256(zip_path)

                candidate.raw_zip_size = zip_size
                candidate.raw_zip_hash = zip_hash
                candidate.archive_status = "archive_verifying"
                candidate.archive_error = None
                candidate.save(update_fields=[
                    "raw_zip_size",
                    "raw_zip_hash",
                    "archive_status",
                    "archive_error",
                    "updated_at",
                ])

                with zipfile.ZipFile(zip_path, "r") as z:
                    bad_file = z.testzip()
                    if bad_file:
                        raise RuntimeError(f"Corrupted file inside ZIP: {bad_file}")

                    names = z.namelist()

                    pdf_inside_zip = find_conversation_pdf(names)
                    if not pdf_inside_zip:
                        raise RuntimeError("No conversation PDF found inside ZIP.")

                    extract_folder_name = f"{safe_name(conversation_id)}_{safe_name(export_id)}"
                    extract_dir = os.path.join(EXTRACTED_DIR, extract_folder_name)

                    if force and os.path.exists(extract_dir):
                        shutil.rmtree(extract_dir)

                    os.makedirs(extract_dir, exist_ok=True)
                    z.extractall(extract_dir)

                extracted_pdf_path = os.path.join(extract_dir, pdf_inside_zip)

                if not os.path.exists(extracted_pdf_path):
                    raise RuntimeError(f"Extracted PDF not found: {extracted_pdf_path}")

                final_pdf_name = f"{safe_name(conversation_id)}_{safe_name(export_id)}.pdf"
                final_pdf_path = os.path.join(PDFS_DIR, final_pdf_name)

                shutil.copy2(extracted_pdf_path, final_pdf_path)

                attachments_found = count_attachments(names)

                candidate.extracted_dir = extract_dir
                candidate.pdf_path = final_pdf_path
                candidate.attachments_found = attachments_found
                candidate.archive_status = "pdf_extracted"
                candidate.archive_error = None
                candidate.archive_processed_at = timezone.now()
                candidate.save(update_fields=[
                    "extracted_dir",
                    "pdf_path",
                    "attachments_found",
                    "archive_status",
                    "archive_error",
                    "archive_processed_at",
                    "updated_at",
                ])

                upsert_conversation_export(candidate)

                processed += 1
                self.stdout.write(self.style.SUCCESS(
                    f"OK {conversation_id}: PDF={final_pdf_path}, attachments={attachments_found}"
                ))

            except Exception as e:
                candidate.archive_status = "archive_error"
                candidate.archive_error = str(e)
                candidate.archive_processed_at = timezone.now()
                candidate.save(update_fields=[
                    "archive_status",
                    "archive_error",
                    "archive_processed_at",
                    "updated_at",
                ])

                errors += 1
                self.stdout.write(self.style.ERROR(f"ERROR {conversation_id}: {e}"))

        self.stdout.write(self.style.SUCCESS(
            f"Archive processing completed. processed={processed}, errors={errors}"
        ))
