from django.core.management.base import BaseCommand
from django.db import transaction

from productfeed.models import Product
from superchatsync.landing_product_mapping import normalize_product_name
from superchatsync.models import FitexpressProductMapping, LandingProductMapping


class Command(BaseCommand):
    help = "Seed landing Product / SKU mappings from product knowledge names and explicit aliases."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Create missing mappings. Without this flag the command is a dry run.",
        )

    def handle(self, *args, **options):
        entries = {}
        source_conflicts = set()

        def add_entry(product_name, sku, source):
            product_name = str(product_name or "").strip()
            sku = str(sku or "").strip()
            normalized_name = normalize_product_name(product_name)
            if not product_name or not sku or not normalized_name:
                return
            existing = entries.get(normalized_name)
            if existing and existing["sku"] != sku:
                source_conflicts.add(normalized_name)
                return
            entries.setdefault(
                normalized_name,
                {
                    "product_name": product_name,
                    "normalized_name": normalized_name,
                    "sku": sku,
                    "source": source,
                },
            )

        for product_id, product_name in Product.objects.filter(active=True).values_list(
            "product_id",
            "product_name",
        ):
            add_entry(
                product_name,
                product_id,
                LandingProductMapping.SOURCE_PRODUCT_KNOWLEDGE,
            )

        for product_id, aliases in FitexpressProductMapping.objects.filter(active=True).values_list(
            "fitexpress_product_id",
            "aliases",
        ):
            for alias in aliases if isinstance(aliases, list) else []:
                add_entry(alias, product_id, LandingProductMapping.SOURCE_PRODUCT_ALIAS)

        for normalized_name in source_conflicts:
            entries.pop(normalized_name, None)

        stats = {
            "candidates": len(entries),
            "created": 0,
            "existing": 0,
            "database_conflicts": 0,
            "source_conflicts": len(source_conflicts),
        }
        if options["apply"]:
            with transaction.atomic():
                for normalized_name, values in sorted(entries.items()):
                    existing = LandingProductMapping.objects.filter(
                        normalized_name=normalized_name
                    ).first()
                    if existing:
                        if existing.sku != values["sku"]:
                            stats["database_conflicts"] += 1
                        else:
                            stats["existing"] += 1
                        continue
                    LandingProductMapping.objects.create(**values)
                    stats["created"] += 1

        mode = "APPLY" if options["apply"] else "DRY RUN"
        self.stdout.write(
            self.style.SUCCESS(
                f"{mode}: {stats['candidates']} candidates, {stats['created']} created, "
                f"{stats['existing']} existing, {stats['database_conflicts']} database conflicts, "
                f"{stats['source_conflicts']} source conflicts."
            )
        )
