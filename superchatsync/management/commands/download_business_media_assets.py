import hashlib
import mimetypes
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

import requests
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.db.models import Q
from django.utils import timezone

from superchatsync.models import BusinessMediaAsset


USER_AGENT = "StorwyzKnowledgeBot/1.0 (+https://storwyz.com)"


def hash_value(value):
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def download_one(asset_id):
    close_old_connections()
    try:
        asset = BusinessMediaAsset.objects.select_related("business", "product").get(asset_id=asset_id)
        product_slug = asset.product.slug if asset.product else "general"
        image_dir = Path(settings.MEDIA_ROOT) / "business_clients" / asset.business.slug / "product_images" / product_slug
        image_dir.mkdir(parents=True, exist_ok=True)
        response = requests.get(asset.source_url, headers={"User-Agent": USER_AGENT}, timeout=45)
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
        ext = mimetypes.guess_extension(content_type) or Path(urlparse(asset.source_url).path).suffix or ".img"
        path = image_dir / f"{hash_value(asset.source_url)[:20]}{ext}"
        if not path.exists():
            path.write_bytes(response.content)
        width = height = None
        try:
            from PIL import Image

            with Image.open(path) as image:
                width, height = image.size
        except Exception:
            pass
        BusinessMediaAsset.objects.filter(asset_id=asset_id).update(
            local_path=str(path),
            mime_type=content_type,
            file_size_bytes=len(response.content),
            width=width,
            height=height,
            last_seen_at=timezone.now(),
            updated_at=timezone.now(),
        )
        return True, str(asset_id), ""
    except Exception as exc:
        return False, str(asset_id), str(exc)
    finally:
        close_old_connections()


class Command(BaseCommand):
    help = "Download missing local files for business media assets concurrently."

    def add_arguments(self, parser):
        parser.add_argument("--business-slug", required=True)
        parser.add_argument("--concurrency", type=int, default=20)
        parser.add_argument("--limit", type=int, default=0)

    def handle(self, *args, **options):
        queryset = (
            BusinessMediaAsset.objects.filter(business__slug=options["business_slug"])
            .filter(Q(local_path__isnull=True) | Q(local_path=""))
            .order_by("created_at")
            .values_list("asset_id", flat=True)
        )
        if options["limit"] and options["limit"] > 0:
            queryset = queryset[: options["limit"]]
        asset_ids = list(queryset)
        total = len(asset_ids)
        if not total:
            self.stdout.write(self.style.SUCCESS("No media assets need download."))
            return
        ok = 0
        failed = 0
        with ThreadPoolExecutor(max_workers=max(1, options["concurrency"])) as executor:
            futures = [executor.submit(download_one, asset_id) for asset_id in asset_ids]
            for index, future in enumerate(as_completed(futures), start=1):
                success, asset_id, error = future.result()
                if success:
                    ok += 1
                else:
                    failed += 1
                    self.stderr.write(f"failed asset={asset_id} error={error[:300]}")
                if index % 100 == 0 or index == total:
                    self.stdout.write(f"downloaded={ok} failed={failed} total={total}")
        self.stdout.write(self.style.SUCCESS(f"done downloaded={ok} failed={failed} total={total}"))
