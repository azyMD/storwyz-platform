import hashlib
import os

import fitz
from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from superchatsync.models import SuperchatSyncRun, SuperchatSyncCandidate


BASE_DIR = "/opt/superchat-ai-agent"
DATA_DIR = os.path.join(BASE_DIR, "data", "superchat_exports")
TEXT_DIR = os.path.join(DATA_DIR, "extracted_text")
EXTRACTION_VERSION = "pdf_text_v1"


def ensure_dirs():
    os.makedirs(TEXT_DIR, exist_ok=True)


def safe_name(value):
    return str(value or "").replace("/", "_").replace("\\", "_").replace(" ", "_")


def text_sha256(text):
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def extract_pdf_text(pdf_path):
    doc = fitz.open(pdf_path)
    pages = []

    for page_index, page in enumerate(doc, start=1):
        page_text = page.get_text("text")
        pages.append(f"\n\n===== PAGE {page_index} =====\n\n{page_text}")

    doc.close()
    return "\n".join(pages).strip()


def upsert_conversation_text(candidate, text_content, text_hash):
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO conversation_texts (
                candidate_id,
                conversation_id,
                export_id,
                pdf_path,
                text_path,
                text_hash,
                extraction_version,
                text_content,
                extracted_at,
                created_at,
                updated_at
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s,
                NOW(), NOW(), NOW()
            )
            ON CONFLICT (conversation_id, export_id, extraction_version)
            DO UPDATE SET
                candidate_id = EXCLUDED.candidate_id,
                pdf_path = EXCLUDED.pdf_path,
                text_path = EXCLUDED.text_path,
                text_hash = EXCLUDED.text_hash,
                text_content = EXCLUDED.text_content,
                extracted_at = NOW(),
                updated_at = NOW();
            """,
            [
                str(candidate.candidate_id),
                candidate.conversation_id,
                candidate.export_id,
                candidate.pdf_path,
                candidate.text_path,
                text_hash,
                EXTRACTION_VERSION,
                text_content,
            ],
        )


class Command(BaseCommand):
    help = "Extract raw text from conversation PDFs and save .txt + DB record."

    def add_arguments(self, parser):
        parser.add_argument("--run-id", required=False)
        parser.add_argument("--force", action="store_true")

    def handle(self, *args, **options):
        ensure_dirs()

        run_id = options.get("run_id")
        force = options.get("force")

        candidates = (
            SuperchatSyncCandidate.objects
            .filter(archive_status="pdf_extracted")
            .exclude(pdf_path__isnull=True)
            .exclude(pdf_path="")
        )

        if run_id:
            try:
                run = SuperchatSyncRun.objects.get(run_id=run_id)
            except SuperchatSyncRun.DoesNotExist:
                raise CommandError(f"Run ID not found: {run_id}")

            candidates = candidates.filter(run=run)

        if not force:
            candidates = candidates.exclude(text_status="text_extracted")

        total = candidates.count()
        self.stdout.write(f"PDFs to extract text from: {total}")

        processed = 0
        errors = 0

        for candidate in candidates.order_by("created_at"):
            self.stdout.write(f"Extracting text: {candidate.conversation_id} | {candidate.pdf_path}")

            try:
                if not os.path.exists(candidate.pdf_path):
                    raise RuntimeError(f"PDF not found: {candidate.pdf_path}")

                text_content = extract_pdf_text(candidate.pdf_path)
                text_hash = text_sha256(text_content)

                if not text_content:
                    raise RuntimeError("Extracted text is empty.")

                export_id = candidate.export_id or "no_export_id"
                text_name = f"{safe_name(candidate.conversation_id)}_{safe_name(export_id)}.txt"
                text_path = os.path.join(TEXT_DIR, text_name)

                with open(text_path, "w", encoding="utf-8") as f:
                    f.write(text_content)

                candidate.text_path = text_path
                candidate.text_hash = text_hash
                candidate.text_status = "text_extracted"
                candidate.text_extraction_version = EXTRACTION_VERSION
                candidate.text_extracted_at = timezone.now()
                candidate.save(update_fields=[
                    "text_path",
                    "text_hash",
                    "text_status",
                    "text_extraction_version",
                    "text_extracted_at",
                    "updated_at",
                ])

                upsert_conversation_text(candidate, text_content, text_hash)

                processed += 1
                self.stdout.write(self.style.SUCCESS(
                    f"OK {candidate.conversation_id}: text={text_path}"
                ))

            except Exception as e:
                candidate.text_status = "text_error"
                candidate.archive_error = str(e)
                candidate.save(update_fields=[
                    "text_status",
                    "archive_error",
                    "updated_at",
                ])

                errors += 1
                self.stdout.write(self.style.ERROR(
                    f"ERROR {candidate.conversation_id}: {e}"
                ))

        self.stdout.write(self.style.SUCCESS(
            f"Text extraction completed. processed={processed}, errors={errors}"
        ))
