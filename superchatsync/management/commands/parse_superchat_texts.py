import hashlib
import json
import re
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from superchatsync.models import SuperchatSyncRun, SuperchatSyncCandidate


EXTRACTION_VERSION = "pdf_text_v1"

HEADER_RE = re.compile(r"^(Sent|Received)\s+(\d{2}\.\d{2}\.\d{4})\s+(\d{2}:\d{2})$")
MESSAGE_ID_RE = re.compile(r"ID:\s+(ms_[A-Za-z0-9]+)")
CONVERSATION_ID_RE = re.compile(r"ID:\s+(cv_[A-Za-z0-9]+)")
WEB_LINK_RE = re.compile(r"https://app\.superchat\.de/inbox/[^\s]+")
PAGE_MARKER_RE = re.compile(r"^=+\s*PAGE\s+\d+\s*=+$")


def sha256_text(value):
    return hashlib.sha256(str(value or "").encode("utf-8", errors="ignore")).hexdigest()


def parse_dt(date_str, time_str):
    dt = datetime.strptime(f"{date_str} {time_str}", "%d.%m.%Y %H:%M")
    return timezone.make_aware(dt, timezone.get_current_timezone())


def clean_line(line):
    return str(line or "").strip()


def extract_conversation_id(text, fallback=None):
    match = CONVERSATION_ID_RE.search(text or "")
    if match:
        return match.group(1)
    return fallback


def extract_web_link(text):
    match = WEB_LINK_RE.search(text or "")
    if match:
        return match.group(0)
    return None


def extract_channel(text):
    lines = [clean_line(x) for x in (text or "").splitlines()]
    for i, line in enumerate(lines):
        if line == "Channel" and i + 1 < len(lines):
            value = clean_line(lines[i + 1])
            if value and value not in ("Web-Link", "Conversation"):
                return value
    return None


def extract_client_name(text):
    lines = [clean_line(x) for x in (text or "").splitlines() if clean_line(x)]

    for i, line in enumerate(lines):
        if line == "Contact" and i + 1 < len(lines):
            nxt = lines[i + 1]
            if nxt not in ("Channel", "Conversation", "Web-Link"):
                return nxt

    return None


def remove_noise_lines(lines):
    cleaned = []

    skip_prefixes = (
        "Export ID:",
        "Sent via Campaign",
        "Sent via Workflow",
        "Reply to:",
        "Attachment:",
        "Attachments:",
        "ID: ms_",
        "ID: fi_",
    )

    exact_noise = {
        "",
        "Conversation Export",
        "Export Information",
        "Participants",
        "Conversation",
        "BODY",
        "BUTTONS",
        "QUICK REPLY BUTTONS",
        "Expres Colet SRL",
        "Contact",
        "Channel",
        "Web-Link",
        "Time span of the conversation",
    }

    for raw in lines:
        line = clean_line(raw)

        if not line:
            continue

        if PAGE_MARKER_RE.match(line):
            continue

        if line in exact_noise:
            continue

        if any(line.startswith(prefix) for prefix in skip_prefixes):
            continue

        if re.match(r"^Page\s+\d+\s+of\s+\d+$", line):
            continue

        if WEB_LINK_RE.match(line):
            continue

        cleaned.append(line)

    return cleaned


def split_messages(text):
    lines = (text or "").splitlines()
    timestamp_positions = []

    for idx, raw_line in enumerate(lines):
        line = clean_line(raw_line)
        match = HEADER_RE.match(line)
        if match:
            timestamp_positions.append({
                "idx": idx,
                "direction": match.group(1),
                "date": match.group(2),
                "time": match.group(3),
            })

    messages = []

    for pos_index, item in enumerate(timestamp_positions):
        current_idx = item["idx"]

        if pos_index == 0:
            block_start = 0
        else:
            block_start = timestamp_positions[pos_index - 1]["idx"] + 1

        block_lines = lines[block_start:current_idx]
        raw_block = "\n".join(block_lines).strip()

        id_match = MESSAGE_ID_RE.search(raw_block)
        message_id = id_match.group(1) if id_match else None

        content_lines = remove_noise_lines(block_lines)
        message_text = "\n".join(content_lines).strip()

        if not message_text:
            continue

        direction = item["direction"]
        sent_at = parse_dt(item["date"], item["time"])

        if direction == "Received":
            sender_type = "client"
            is_client_reply = True
        else:
            is_client_reply = False
            if "Sent via Workflow" in raw_block:
                sender_type = "workflow"
            elif "Sent via Campaign" in raw_block:
                sender_type = "campaign"
            else:
                sender_type = "operator"

        messages.append({
            "message_id": message_id,
            "sent_at": sent_at,
            "sender_type": sender_type,
            "sender_name": None,
            "message_text": message_text,
            "message_type": "text",
            "button_clicked": message_text if is_client_reply and len(message_text) <= 120 else None,
            "is_client_reply": is_client_reply,
            "raw_block": raw_block,
        })

    return messages


def get_text_for_candidate(candidate):
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT text_content, text_path
            FROM conversation_texts
            WHERE conversation_id = %s
              AND export_id = %s
              AND extraction_version = %s
            ORDER BY extracted_at DESC
            LIMIT 1;
            """,
            [
                candidate.conversation_id,
                candidate.export_id,
                EXTRACTION_VERSION,
            ],
        )
        row = cur.fetchone()

    if not row:
        return None, None

    return row[0], row[1]


def upsert_conversation(candidate, text, messages):
    conversation_id = extract_conversation_id(text, fallback=candidate.conversation_id)
    client_name = extract_client_name(text)
    channel = extract_channel(text)
    web_link = extract_web_link(text)

    first_message_at = None
    first_client_reply_at = None
    last_message_at = None

    if messages:
        ordered = sorted(messages, key=lambda x: x["sent_at"])
        first_message_at = ordered[0]["sent_at"]
        last_message_at = ordered[-1]["sent_at"]

        client_messages = [m for m in ordered if m["is_client_reply"]]
        if client_messages:
            first_client_reply_at = client_messages[0]["sent_at"]

    metadata = {
        "web_link": web_link,
        "candidate_id": str(candidate.candidate_id),
        "run_id": str(candidate.run_id),
        "parser_version": "text_parser_v1",
        "source_text_path": candidate.text_path,
    }

    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO conversations (
                conversation_id,
                channel,
                client_name,
                product_detected,
                first_message_at,
                first_client_reply_at,
                last_message_at,
                has_client_reply,
                operator_names,
                raw_pdf_path,
                raw_zip_path,
                source,
                status,
                metadata,
                created_at,
                updated_at,
                last_imported_at
            )
            VALUES (
                %s, %s, %s, NULL,
                %s, %s, %s, %s,
                %s,
                %s, %s,
                'superchat',
                %s,
                %s::jsonb,
                NOW(), NOW(), NOW()
            )
            ON CONFLICT (conversation_id) DO UPDATE SET
                channel = EXCLUDED.channel,
                client_name = EXCLUDED.client_name,
                first_message_at = EXCLUDED.first_message_at,
                first_client_reply_at = EXCLUDED.first_client_reply_at,
                last_message_at = EXCLUDED.last_message_at,
                has_client_reply = EXCLUDED.has_client_reply,
                raw_pdf_path = EXCLUDED.raw_pdf_path,
                raw_zip_path = EXCLUDED.raw_zip_path,
                status = EXCLUDED.status,
                metadata = EXCLUDED.metadata,
                updated_at = NOW(),
                last_imported_at = NOW();
            """,
            [
                conversation_id,
                channel,
                client_name,
                first_message_at,
                first_client_reply_at,
                last_message_at,
                bool(first_client_reply_at),
                [],
                candidate.pdf_path,
                candidate.zip_path,
                candidate.superchat_status,
                json.dumps(metadata),
            ],
        )

    return conversation_id


def replace_messages(conversation_id, messages):
    with connection.cursor() as cur:
        cur.execute("DELETE FROM messages WHERE conversation_id = %s", [conversation_id])

        for msg in messages:
            raw_line_hash = sha256_text(
                f"{conversation_id}|{msg.get('message_id')}|{msg['sent_at']}|{msg['sender_type']}|{msg['message_text']}"
            )

            raw_payload = {
                "parser_version": "text_parser_v1",
                "raw_block": msg.get("raw_block"),
            }

            cur.execute(
                """
                INSERT INTO messages (
                    message_id,
                    conversation_id,
                    sent_at,
                    sender_type,
                    sender_name,
                    message_text,
                    message_type,
                    button_clicked,
                    is_client_reply,
                    raw_line_hash,
                    raw_payload,
                    created_at
                )
                VALUES (
                    %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s::jsonb,
                    NOW()
                )
                ON CONFLICT (conversation_id, raw_line_hash) DO NOTHING;
                """,
                [
                    msg.get("message_id"),
                    conversation_id,
                    msg["sent_at"],
                    msg["sender_type"],
                    msg.get("sender_name"),
                    msg["message_text"],
                    msg.get("message_type"),
                    msg.get("button_clicked"),
                    msg.get("is_client_reply"),
                    raw_line_hash,
                    json.dumps(raw_payload),
                ],
            )


class Command(BaseCommand):
    help = "Parse extracted Superchat text into conversations and messages."

    def add_arguments(self, parser):
        parser.add_argument("--run-id", required=False)
        parser.add_argument("--force", action="store_true")

    def handle(self, *args, **options):
        run_id = options.get("run_id")
        force = options.get("force")

        candidates = (
            SuperchatSyncCandidate.objects
            .filter(text_status="text_extracted")
            .exclude(text_path__isnull=True)
            .exclude(text_path="")
        )

        if run_id:
            try:
                run = SuperchatSyncRun.objects.get(run_id=run_id)
            except SuperchatSyncRun.DoesNotExist:
                raise CommandError(f"Run ID not found: {run_id}")

            candidates = candidates.filter(run=run)

        if not force:
            candidates = candidates.exclude(parse_status="parsed")

        total = candidates.count()
        self.stdout.write(f"Texts to parse: {total}")

        processed = 0
        errors = 0

        for candidate in candidates.order_by("created_at"):
            self.stdout.write(f"Parsing text: {candidate.conversation_id}")

            try:
                text_content, text_path = get_text_for_candidate(candidate)

                if not text_content:
                    raise RuntimeError("No extracted text found in conversation_texts.")

                candidate.text_path = candidate.text_path or text_path
                messages = split_messages(text_content)

                if not messages:
                    raise RuntimeError("No messages detected in extracted text.")

                conversation_id = upsert_conversation(candidate, text_content, messages)
                replace_messages(conversation_id, messages)

                candidate.messages_found = len(messages)
                candidate.parse_status = "parsed"
                candidate.parse_error = None
                candidate.parsed_at = timezone.now()
                candidate.save(update_fields=[
                    "messages_found",
                    "parse_status",
                    "parse_error",
                    "parsed_at",
                    "text_path",
                    "updated_at",
                ])

                processed += 1
                self.stdout.write(self.style.SUCCESS(
                    f"OK {conversation_id}: messages={len(messages)}"
                ))

            except Exception as e:
                candidate.parse_status = "parse_error"
                candidate.parse_error = str(e)
                candidate.parsed_at = timezone.now()
                candidate.save(update_fields=[
                    "parse_status",
                    "parse_error",
                    "parsed_at",
                    "updated_at",
                ])

                errors += 1
                self.stdout.write(self.style.ERROR(
                    f"ERROR {candidate.conversation_id}: {e}"
                ))

        self.stdout.write(self.style.SUCCESS(
            f"Parsing completed. processed={processed}, errors={errors}"
        ))
