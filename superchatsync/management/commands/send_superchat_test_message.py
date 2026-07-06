import json
import os
from pathlib import Path

import requests
from django.core.management.base import BaseCommand, CommandError


def load_env():
    for env_path in [
        Path("/opt/superchat-ai-agent/.env"),
        Path("/opt/superchat-ai-agent/web/.env"),
    ]:
        if not env_path.exists():
            continue

        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()

            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def get_config():
    api_key = (
        os.environ.get("SUPERCHAT_API_KEY")
        or os.environ.get("SUPERCHAT_TOKEN")
        or os.environ.get("SUPERCHAT_ACCESS_TOKEN")
    )

    base_url = (
        os.environ.get("SUPERCHAT_API_BASE")
        or os.environ.get("SUPERCHAT_BASE_URL")
        or "https://api.superchat.com"
    ).rstrip("/")

    if not api_key:
        raise CommandError("SUPERCHAT_API_KEY lipsește în .env")

    return api_key, base_url


def get_json(base_url, api_key, path):
    response = requests.get(
        base_url + path,
        headers={
            "X-API-KEY": api_key,
            "Accept": "application/json",
        },
        timeout=30,
    )

    if response.status_code < 200 or response.status_code >= 300:
        raise CommandError(
            f"GET failed {path}. Status={response.status_code}, body={response.text[:2000]}"
        )

    return response.json()


def extract_ids(base_url, api_key, conversation_id):
    conversation = get_json(base_url, api_key, f"/v1.0/conversations/{conversation_id}")

    contacts = conversation.get("contacts") or []
    channel = conversation.get("channel") or {}

    if not contacts:
        raise CommandError("Conversația nu are contacts[].")

    contact_id = contacts[0].get("id")
    channel_id = channel.get("id")

    if not contact_id:
        raise CommandError("Nu am găsit contact_id.")

    if not channel_id:
        raise CommandError("Nu am găsit channel_id.")

    return {
        "conversation_id": conversation_id,
        "contact_id": contact_id,
        "channel_id": channel_id,
        "channel_type": channel.get("type"),
        "time_window": conversation.get("time_window"),
    }


def build_payload(contact_id, channel_id, message):
    return {
        "to": [
            {
                "identifier": contact_id
            }
        ],
        "from": {
            "channel_id": channel_id
        },
        "content": {
            "type": "text",
            "body": message
        }
    }


class Command(BaseCommand):
    help = "Send a manual test message via Superchat /v1.0/messages."

    def add_arguments(self, parser):
        parser.add_argument("--conversation-id", required=True)
        parser.add_argument("--message", required=True)
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        load_env()

        api_key, base_url = get_config()

        conversation_id = options["conversation_id"]
        message = options["message"]
        dry_run = options["dry_run"]

        ids = extract_ids(base_url, api_key, conversation_id)

        url = f"{base_url}/v1.0/messages"

        payload = build_payload(
            contact_id=ids["contact_id"],
            channel_id=ids["channel_id"],
            message=message,
        )

        self.stdout.write("CONVERSATION:")
        self.stdout.write(json.dumps(ids, ensure_ascii=False, indent=2))

        self.stdout.write("\nURL:")
        self.stdout.write(url)

        self.stdout.write("\nPAYLOAD:")
        self.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2))

        if dry_run:
            self.stdout.write(self.style.WARNING("\nDry-run only. Message was NOT sent."))
            return

        response = requests.post(
            url,
            headers={
                "X-API-KEY": api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=payload,
            timeout=30,
        )

        self.stdout.write(f"\nSTATUS: {response.status_code}")

        try:
            self.stdout.write(json.dumps(response.json(), ensure_ascii=False, indent=2))
        except Exception:
            self.stdout.write(response.text[:5000])

        if response.status_code < 200 or response.status_code >= 300:
            raise CommandError("Superchat send failed.")

        self.stdout.write(self.style.SUCCESS("Message sent to Superchat."))
