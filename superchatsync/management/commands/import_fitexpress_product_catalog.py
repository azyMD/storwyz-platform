import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from productfeed.models import Product
from superchatsync.models import FitexpressProductMapping, ProductKnowledgeItem


class Command(BaseCommand):
    help = "Import the full Fitexpress product catalog as ID/name mapping data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fixture",
            default="superchatsync/data/fitexpress_full_product_catalog.json",
            help="Path to JSON fixture with products: [{product_id, product_name}].",
        )

    def handle(self, *args, **options):
        fixture_path = Path(options["fixture"])
        if not fixture_path.is_absolute():
            fixture_path = Path.cwd() / fixture_path
        if not fixture_path.exists():
            raise CommandError(f"Fixture not found: {fixture_path}")

        data = json.loads(fixture_path.read_text(encoding="utf-8"))
        rows = data.get("products") or []
        if not rows:
            raise CommandError("Fixture has no products.")

        stats = {
            "rows": 0,
            "products_created": 0,
            "products_updated": 0,
            "mappings_created": 0,
            "mappings_updated": 0,
            "knowledge_products": 0,
        }
        now = timezone.now()

        with transaction.atomic():
            for row in rows:
                product_id = str(row.get("product_id") or "").strip()
                product_name = str(row.get("product_name") or "").strip()
                if not product_id or not product_name:
                    continue
                stats["rows"] += 1

                has_knowledge = ProductKnowledgeItem.objects.filter(product_id=product_id).exists()
                if has_knowledge:
                    stats["knowledge_products"] += 1

                product, created = Product.objects.get_or_create(
                    product_id=product_id,
                    defaults={
                        "product_name": product_name,
                        "short_description": product_name,
                        "active": True,
                    },
                )
                if created:
                    stats["products_created"] += 1
                else:
                    changed_fields = []
                    if product.product_name != product_name and not has_knowledge:
                        product.product_name = product_name
                        changed_fields.append("product_name")
                    if not product.short_description:
                        product.short_description = product_name
                        changed_fields.append("short_description")
                    if not product.active:
                        product.active = True
                        changed_fields.append("active")
                    if changed_fields:
                        product.save(update_fields=changed_fields)
                        stats["products_updated"] += 1

                mapping, mapping_created = FitexpressProductMapping.objects.get_or_create(
                    product_id=product_id,
                    defaults={
                        "product_name": product_name,
                        "fitexpress_product_id": product_id,
                        "aliases": [],
                        "match_status": "catalog_only" if not has_knowledge else "exact",
                        "active": True,
                        "metadata": {
                            "catalog_only": not has_knowledge,
                            "has_product_knowledge": has_knowledge,
                            "catalog_source": data.get("source") or "fitexpress_catalog",
                            "catalog_imported_at": now.isoformat(),
                        },
                    },
                )
                if mapping_created:
                    stats["mappings_created"] += 1
                else:
                    metadata = mapping.metadata if isinstance(mapping.metadata, dict) else {}
                    metadata.update(
                        {
                            "catalog_only": not has_knowledge,
                            "has_product_knowledge": has_knowledge,
                            "catalog_source": data.get("source") or "fitexpress_catalog",
                            "catalog_imported_at": now.isoformat(),
                        }
                    )
                    update_fields = []
                    if mapping.product_name != product_name:
                        mapping.product_name = product_name
                        update_fields.append("product_name")
                    if mapping.fitexpress_product_id != product_id:
                        mapping.fitexpress_product_id = product_id
                        update_fields.append("fitexpress_product_id")
                    if not mapping.active:
                        mapping.active = True
                        update_fields.append("active")
                    if not mapping.match_status or mapping.match_status == "catalog_only":
                        mapping.match_status = "exact" if has_knowledge else "catalog_only"
                        update_fields.append("match_status")
                    mapping.metadata = metadata
                    update_fields.extend(["metadata", "updated_at"])
                    mapping.save(update_fields=sorted(set(update_fields)))
                    stats["mappings_updated"] += 1

        self.stdout.write(
            self.style.SUCCESS(
                "Catalog import complete: "
                f"{stats['rows']} rows, "
                f"{stats['products_created']} products created, "
                f"{stats['products_updated']} products updated, "
                f"{stats['mappings_created']} mappings created, "
                f"{stats['mappings_updated']} mappings updated, "
                f"{stats['knowledge_products']} products already have knowledge."
            )
        )
