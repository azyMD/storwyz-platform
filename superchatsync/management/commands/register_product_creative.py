import json
import mimetypes
import shutil
import uuid
from pathlib import Path
from urllib.parse import quote

from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.db import connection


class Command(BaseCommand):
    help = "Register an image/video/document creative asset for a product."

    def add_arguments(self, parser):
        parser.add_argument("--product-id", required=True)
        parser.add_argument("--file", required=True)
        parser.add_argument("--title", required=True)
        parser.add_argument("--description", required=True)

        parser.add_argument("--usage-context", default="")
        parser.add_argument("--sales-stage", default="")
        parser.add_argument("--intent", default="")
        parser.add_argument("--next-best-action", default="")
        parser.add_argument("--tags", default="")
        parser.add_argument("--priority", type=int, default=100)
        parser.add_argument("--public-url", default="")
        parser.add_argument("--asset-type", default="")

    def handle(self, *args, **options):
        product_id = str(options["product_id"]).strip()
        source_file = Path(options["file"]).expanduser().resolve()

        title = str(options["title"]).strip()
        description = str(options["description"]).strip()

        if not source_file.exists() or not source_file.is_file():
            raise CommandError(f"File not found: {source_file}")

        if not description:
            raise CommandError("Description is required. Creative assets must explain when/how to use them.")

        mime_type, _ = mimetypes.guess_type(str(source_file))
        mime_type = mime_type or "application/octet-stream"

        asset_type = str(options["asset_type"] or "").strip().lower()

        if not asset_type:
            if mime_type.startswith("image/"):
                asset_type = "image"
            elif mime_type.startswith("video/"):
                asset_type = "video"
            else:
                asset_type = "document"

        if asset_type not in ["image", "video", "document"]:
            raise CommandError("--asset-type must be image, video, or document")

        media_root = Path(getattr(settings, "MEDIA_ROOT", "") or "/opt/superchat-ai-agent/web/media")
        dest_dir = media_root / "product_creatives" / product_id
        dest_dir.mkdir(parents=True, exist_ok=True)

        ext = source_file.suffix.lower()
        asset_uuid = str(uuid.uuid4())
        dest_name = f"{asset_uuid}{ext}"
        dest_path = dest_dir / dest_name

        shutil.copy2(source_file, dest_path)

        public_url = str(options["public_url"] or "").strip()

        if not public_url:
            base_url = (
                getattr(settings, "PUBLIC_BASE_URL", None)
                or getattr(settings, "SITE_URL", None)
                or "https://storwyz.com"
            ).rstrip("/")

            public_url = (
                base_url
                + "/media/product_creatives/"
                + quote(product_id)
                + "/"
                + quote(dest_name)
            )

        tags = [
            tag.strip()
            for tag in str(options["tags"] or "").split(",")
            if tag.strip()
        ]

        metadata = {
            "registered_by": "register_product_creative",
            "source_file": str(source_file),
        }

        with connection.cursor() as cur:
            cur.execute("""
                INSERT INTO product_creative_assets (
                    product_id,
                    asset_type,
                    title,
                    description,
                    usage_context,
                    sales_stage,
                    intent,
                    next_best_action,
                    tags,
                    priority,
                    public_url,
                    storage_path,
                    original_filename,
                    mime_type,
                    file_size_bytes,
                    is_active,
                    metadata,
                    created_at,
                    updated_at
                )
                VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    %s::jsonb,
                    %s,
                    %s, %s, %s, %s, %s,
                    TRUE,
                    %s::jsonb,
                    NOW(),
                    NOW()
                )
                RETURNING asset_id
            """, [
                product_id,
                asset_type,
                title,
                description,
                options["usage_context"],
                options["sales_stage"],
                options["intent"],
                options["next_best_action"],
                json.dumps(tags, ensure_ascii=False),
                options["priority"],
                public_url,
                str(dest_path),
                source_file.name,
                mime_type,
                source_file.stat().st_size,
                json.dumps(metadata, ensure_ascii=False),
            ])

            asset_id = cur.fetchone()[0]

        self.stdout.write(self.style.SUCCESS("Creative registered successfully."))
        self.stdout.write(f"asset_id: {asset_id}")
        self.stdout.write(f"product_id: {product_id}")
        self.stdout.write(f"asset_type: {asset_type}")
        self.stdout.write(f"public_url: {public_url}")
        self.stdout.write(f"storage_path: {dest_path}")
