from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from productfeed.models import Product
from superchatsync.fitexpress_reference import resolve_fitexpress_country
from superchatsync.fitexpress_seed_data import (
    FITEXPRESS_COUNTRIES,
    FITEXPRESS_PRODUCT_MAPPINGS,
)
from superchatsync.models import (
    CustomerProfile,
    FitexpressCountry,
    FitexpressProductMapping,
)


def _digits(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


class Command(BaseCommand):
    help = "Seed Fitexpress country and product mapping reference data."

    def add_arguments(self, parser):
        parser.add_argument(
            "--skip-products",
            action="store_true",
            help="Do not create/update rows in the product knowledge products table.",
        )
        parser.add_argument(
            "--skip-profile-backfill",
            action="store_true",
            help="Do not backfill existing CRM customer profile country metadata from phone prefixes.",
        )

    def handle(self, *args, **options):
        with transaction.atomic():
            country_count = self._seed_countries()
            mapping_count = self._seed_product_mappings()
            product_count = 0 if options["skip_products"] else self._seed_products()

        profile_count = 0
        if not options["skip_profile_backfill"]:
            profile_count = self._backfill_profile_countries()

        self.stdout.write(
            self.style.SUCCESS(
                "Seed complete: "
                f"{country_count} countries, "
                f"{mapping_count} product mappings, "
                f"{product_count} products, "
                f"{profile_count} profiles backfilled."
            )
        )

    def _seed_countries(self):
        count = 0
        for row in FITEXPRESS_COUNTRIES:
            metadata = row.get("metadata") or {}
            FitexpressCountry.objects.update_or_create(
                country_id=row["country_id"],
                defaults={
                    "country_name": row["country_name"],
                    "iso2": row.get("iso2"),
                    "phone_prefixes": row.get("phone_prefixes") or [],
                    "default_language": row.get("default_language"),
                    "active": True,
                    "metadata": metadata,
                },
            )
            count += 1
        return count

    def _seed_product_mappings(self):
        count = 0
        for row in FITEXPRESS_PRODUCT_MAPPINGS:
            FitexpressProductMapping.objects.update_or_create(
                product_id=str(row["product_id"]),
                defaults={
                    "product_name": row["product_name"],
                    "fitexpress_product_id": str(row["fitexpress_product_id"]),
                    "aliases": row.get("aliases") or [],
                    "landing_url": row.get("landing_url"),
                    "match_status": row.get("match_status") or "exact",
                    "options": row.get("options") or {},
                    "active": True,
                    "metadata": row.get("metadata") or {},
                },
            )
            count += 1
        return count

    def _seed_products(self):
        count = 0
        for row in FITEXPRESS_PRODUCT_MAPPINGS:
            product, created = Product.objects.get_or_create(
                product_id=str(row["product_id"]),
                defaults={
                    "product_name": row["product_name"],
                    "brand": None,
                    "category": None,
                    "short_description": row["product_name"],
                    "active": True,
                },
            )
            changed = False
            if not product.product_name:
                product.product_name = row["product_name"]
                changed = True
            if not product.short_description:
                product.short_description = row["product_name"]
                changed = True
            if not product.active:
                product.active = True
                changed = True
            if changed:
                product.save(update_fields=["product_name", "short_description", "active"])
            count += 1
        return count

    def _backfill_profile_countries(self):
        updated = 0
        now = timezone.now()
        for profile in CustomerProfile.objects.exclude(phone__isnull=True).exclude(phone="").iterator():
            if not _digits(profile.phone):
                continue
            country = resolve_fitexpress_country(profile.phone)
            if not country:
                continue
            metadata = profile.metadata if isinstance(profile.metadata, dict) else {}
            before = dict(metadata)
            if not metadata.get("country_code") or metadata.get("country_code") == "unknown":
                metadata["country_code"] = country.iso2 or "unknown"
            if not metadata.get("country_name") or metadata.get("country_name") == "Unknown":
                metadata["country_name"] = country.country_name
            if not metadata.get("fitexpress_country_id"):
                metadata["fitexpress_country_id"] = country.country_id
            if country.default_language and not metadata.get("phone_default_language_code"):
                metadata["phone_default_language_code"] = country.default_language
            if metadata != before:
                profile.metadata = metadata
                profile.updated_at = now
                profile.save(update_fields=["metadata", "updated_at"])
                updated += 1
        return updated
