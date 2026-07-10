import difflib
import json
import re
import shutil
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from productfeed.models import Product
from superchatsync.models import FitexpressProductMapping, ProductKnowledgeItem


COUNTRY_SUFFIX_PATTERN = re.compile(
    r"\s+(?:RO|MD|ES|IT|FR|DE|UK|US|PT|PL|BG|HU|GR|CZ|SK|HR|TR)$",
    flags=re.IGNORECASE,
)


def normalize_product_label(value):
    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_value = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", ascii_value.lower())


def catalog_product_label(value):
    return COUNTRY_SUFFIX_PATTERN.sub("", (value or "").strip()).strip()


class ProductSkuResolver:
    def __init__(self, products, mappings, knowledge_product_ids):
        self.all_labels = defaultdict(set)
        self.knowledge_labels = defaultdict(set)
        self.label_text = {}
        knowledge_product_ids = {str(value) for value in knowledge_product_ids if value}

        for product_id, product_name in products:
            self._add_label(product_id, product_name, product_id in knowledge_product_ids)

        for product_id, product_name, aliases in mappings:
            is_knowledge_product = product_id in knowledge_product_ids
            self._add_label(product_id, product_name, is_knowledge_product)
            for alias in aliases if isinstance(aliases, list) else []:
                self._add_label(product_id, alias, is_knowledge_product)

    @classmethod
    def from_database(cls):
        products = list(Product.objects.values_list("product_id", "product_name"))
        mappings = list(
            FitexpressProductMapping.objects.filter(active=True).values_list(
                "product_id",
                "product_name",
                "aliases",
            )
        )
        knowledge_product_ids = ProductKnowledgeItem.objects.exclude(product_id=None).values_list(
            "product_id",
            flat=True,
        )
        return cls(products, mappings, knowledge_product_ids)

    def _add_label(self, product_id, label, is_knowledge_product):
        product_id = str(product_id or "").strip()
        key = normalize_product_label(label)
        if not product_id or not key:
            return
        self.all_labels[key].add(product_id)
        if is_knowledge_product:
            self.knowledge_labels[key].add(product_id)
        self.label_text.setdefault(key, str(label).strip())

    @staticmethod
    def _single_match(index, key):
        matches = index.get(key, set())
        if len(matches) == 1:
            return next(iter(matches))
        return None

    def resolve(self, product_name):
        key = normalize_product_label(catalog_product_label(product_name))
        if not key:
            return None, "empty"

        product_id = self._single_match(self.knowledge_labels, key)
        if product_id:
            return product_id, "knowledge_exact"

        product_id = self._single_match(self.all_labels, key)
        if product_id:
            return product_id, "catalog_exact"

        if len(key) >= 5:
            prefix_ids = {
                product_id
                for label, product_ids in self.knowledge_labels.items()
                if label.startswith(key) or key.startswith(label)
                for product_id in product_ids
            }
            if len(prefix_ids) == 1:
                return next(iter(prefix_ids)), "knowledge_prefix"

        fuzzy_matches = []
        for label, product_ids in self.knowledge_labels.items():
            score = difflib.SequenceMatcher(None, key, label).ratio()
            for product_id in product_ids:
                fuzzy_matches.append((score, product_id, label))
        fuzzy_matches.sort(reverse=True)
        if fuzzy_matches and fuzzy_matches[0][0] >= 0.86:
            best_score, best_product_id, _ = fuzzy_matches[0]
            competing_scores = [
                score for score, product_id, _ in fuzzy_matches if product_id != best_product_id
            ]
            next_score = max(competing_scores, default=0)
            if best_score - next_score >= 0.08:
                return best_product_id, "knowledge_fuzzy"

        return None, "unmatched"


class Command(BaseCommand):
    help = "Backfill product_sku in catalog brochure manifests from the product knowledge catalog."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Write matched SKUs. Without this flag the command only prints a dry run.",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        catalog_dir = Path(settings.MEDIA_ROOT) / "catalog_brochures"
        manifest_paths = sorted(catalog_dir.glob("*/*/manifest.json"))
        resolver = ProductSkuResolver.from_database()
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        stats = {"matched": 0, "updated": 0, "existing": 0, "unmatched": 0, "invalid": 0}

        for manifest_path in manifest_paths:
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                stats["invalid"] += 1
                self.stderr.write(f"INVALID {manifest_path}: {exc}")
                continue

            product_name = str(manifest.get("product_name") or "").strip()
            existing_sku = str(manifest.get("product_sku") or "").strip()
            if existing_sku:
                stats["existing"] += 1
                self.stdout.write(f"EXISTS  {product_name}: {existing_sku}")
                continue

            product_sku, match_method = resolver.resolve(product_name)
            if not product_sku:
                stats["unmatched"] += 1
                self.stdout.write(f"SKIP    {product_name}: no unambiguous product match")
                continue

            stats["matched"] += 1
            self.stdout.write(f"MATCH   {product_name}: {product_sku} ({match_method})")
            if not apply_changes:
                continue

            backup_path = manifest_path.with_name(f"manifest.json.bak_sku_{timestamp}")
            shutil.copy2(manifest_path, backup_path)
            manifest["product_sku"] = product_sku
            manifest["product_sku_source"] = "product_knowledge_backfill"
            temp_path = manifest_path.with_name(".manifest.json.sku-backfill.tmp")
            temp_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temp_path.replace(manifest_path)
            stats["updated"] += 1

        mode = "APPLY" if apply_changes else "DRY RUN"
        self.stdout.write(
            self.style.SUCCESS(
                f"{mode}: {len(manifest_paths)} manifests, {stats['matched']} matched, "
                f"{stats['updated']} updated, {stats['existing']} already set, "
                f"{stats['unmatched']} unmatched, {stats['invalid']} invalid."
            )
        )
