import json
import re
import uuid
from decimal import Decimal
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from productfeed.models import Offer, Product
from superchatsync.models import ProductKnowledgeImport, ProductKnowledgeItem


IMPORT_SOURCE = "google_drive_product_knowledge_20260627"
UUID_NAMESPACE = uuid.UUID("8d2c4e96-69a7-48d5-8df9-a5df3fb13f1a")


def stable_uuid(*parts):
    return uuid.uuid5(UUID_NAMESPACE, ":".join(str(part or "") for part in parts))


def slugify(value):
    text = re.sub(r"[^a-z0-9]+", "_", str(value or "").casefold()).strip("_")
    return text or "product"


def clean(value, limit=None):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit] if limit else text


def combined_source_text(product_data):
    blocks = []
    landing_text = product_data.get("landing_text") or ""
    doc_text = product_data.get("google_doc_text") or ""
    if landing_text:
        blocks.append(
            "=== LANDING SOURCE TEXT ===\n"
            f"URL: {product_data.get('landing_url') or ''}\n\n"
            f"{landing_text}"
        )
    if doc_text:
        blocks.append(
            "=== GOOGLE DOC / SHEET SOURCE TEXT ===\n"
            f"URL: {product_data.get('google_doc_url') or ''}\n"
            f"source_type: {product_data.get('doc_source_type') or ''}\n\n"
            f"{doc_text}"
        )
    return "\n\n".join(blocks).strip()


class Command(BaseCommand):
    help = "Import the scanned Google Drive product landing/doc texts into product knowledge tables."

    def add_arguments(self, parser):
        parser.add_argument(
            "--fixture",
            default="superchatsync/data/google_drive_product_knowledge_fixture.json",
            help="Path to generated Google Drive product knowledge fixture.",
        )
        parser.add_argument(
            "--keep-existing-offers",
            action="store_true",
            help="Do not deactivate existing product offers before inserting landing offers.",
        )

    def handle(self, *args, **options):
        fixture_path = Path(options["fixture"])
        if not fixture_path.is_absolute():
            fixture_path = Path.cwd() / fixture_path
        if not fixture_path.exists():
            raise CommandError(f"Fixture not found: {fixture_path}")

        data = json.loads(fixture_path.read_text(encoding="utf-8"))
        products = data.get("products") or []
        if not products:
            raise CommandError("Fixture has no products.")

        stats = {
            "products": 0,
            "imports": 0,
            "items": 0,
            "source_text_items": 0,
            "offers": 0,
            "offers_deactivated": 0,
        }

        with transaction.atomic():
            for product_data in products:
                self.import_product(product_data, stats, keep_existing_offers=options["keep_existing_offers"])

        self.stdout.write(
            self.style.SUCCESS(
                "Import complete: "
                f"{stats['products']} products, "
                f"{stats['imports']} imports, "
                f"{stats['items']} knowledge items "
                f"({stats['source_text_items']} source_text), "
                f"{stats['offers']} landing offers, "
                f"{stats['offers_deactivated']} old offers deactivated."
            )
        )

    def import_product(self, product_data, stats, keep_existing_offers=False):
        product_id = str(product_data["product_id"])
        product_fields = product_data.get("product_fields") or {}
        product, _ = Product.objects.update_or_create(
            product_id=product_id,
            defaults={
                "product_name": product_fields.get("product_name") or product_data.get("product_name") or product_id,
                "short_description": product_fields.get("short_description") or "",
                "main_benefits": product_fields.get("main_benefits") or "",
                "material": product_fields.get("material") or "",
                "delivery_info": product_fields.get("delivery_info") or "",
                "payment_info": product_fields.get("payment_info") or "",
                "active": bool(product_fields.get("active", True)),
            },
        )
        stats["products"] += 1

        now = timezone.now()
        import_id = stable_uuid(IMPORT_SOURCE, product_id, "combined_import")
        source_text = combined_source_text(product_data)
        knowledge_import, _ = ProductKnowledgeImport.objects.update_or_create(
            import_id=import_id,
            defaults={
                "product": product,
                "title": f"{product.product_name}: Google Drive + landing source",
                "source_file": f"google_drive_products/{product_id}_{slugify(product.product_name)}.json",
                "original_filename": product_data.get("landing_text_file") or "",
                "status": "processed",
                "notes": (
                    f"Imported from Google Drive scan. source_preference={product_data.get('source_preference') or ''}. "
                    "Full landing/doc text is preserved here; active AI items use curated landing facts/offers."
                ),
                "extracted_text": source_text,
                "extracted_char_count": len(source_text),
                "suggestions_created_count": len(product_data.get("knowledge_items") or []),
                "error": None,
                "created_by": "codex_import_google_drive_products",
                "processed_at": now,
                "knowledge_package_status": "not_created",
                "package_suggestions_created_count": 0,
                "created_at": now,
                "updated_at": now,
            },
        )
        stats["imports"] += 1

        for item in product_data.get("knowledge_items") or []:
            item_id = stable_uuid(IMPORT_SOURCE, product_id, item.get("slug"))
            status = item.get("status") or "approved"
            ProductKnowledgeItem.objects.update_or_create(
                item_id=item_id,
                defaults={
                    "knowledge_import": knowledge_import,
                    "product": product,
                    "category": item.get("category") or "source_text",
                    "title": item.get("title") or "",
                    "question": item.get("question") or None,
                    "answer": item.get("answer") or None,
                    "rule": item.get("rule") or None,
                    "keyword": item.get("keyword") or None,
                    "description": item.get("description") or None,
                    "price": item.get("price") or None,
                    "evidence": item.get("evidence") or None,
                    "confidence_score": int(item.get("confidence_score") or 70),
                    "priority": int(item.get("priority") or 50),
                    "status": status,
                    "applied_target_table": "product_knowledge_items" if status == "applied" else None,
                    "applied_target_id": str(item_id) if status == "applied" else None,
                    "apply_error": None,
                    "raw_payload": {
                        "source": IMPORT_SOURCE,
                        "source_kind": item.get("source_kind"),
                        "landing_url": product_data.get("landing_url"),
                        "google_doc_url": product_data.get("google_doc_url"),
                        "source_preference": product_data.get("source_preference"),
                    },
                    "reviewed_by": "codex_import",
                    "reviewed_at": now,
                    "applied_at": now if status == "applied" else None,
                    "created_at": now,
                    "updated_at": now,
                },
            )
            stats["items"] += 1
            if item.get("category") == "source_text":
                stats["source_text_items"] += 1

        self.import_landing_offers(product, product_data, stats, keep_existing_offers=keep_existing_offers)

    def import_landing_offers(self, product, product_data, stats, keep_existing_offers=False):
        offers = product_data.get("landing_offers") or []
        if not offers:
            return

        new_offer_ids = {
            f"{product.product_id}_landing_offer_{index + 1}_{offer.get('quantity') or 1}x"
            for index, offer in enumerate(offers)
        }
        if not keep_existing_offers:
            deactivated = (
                Offer.objects.filter(product=product, active=True)
                .exclude(offer_id__in=new_offer_ids)
                .update(active=False)
            )
            stats["offers_deactivated"] += deactivated

        for index, offer in enumerate(offers):
            quantity = int(offer.get("quantity") or 1)
            offer_id = f"{product.product_id}_landing_offer_{index + 1}_{quantity}x"
            price = Decimal(str(offer.get("price") or "0"))
            currency = offer.get("currency") or "RON"
            Offer.objects.update_or_create(
                offer_id=offer_id,
                defaults={
                    "product": product,
                    "offer_name": offer.get("offer_name") or f"{quantity}x {product.product_name}",
                    "variant": f"landing_{quantity}x",
                    "quantity": quantity,
                    "price": price,
                    "currency": currency,
                    "delivery_offer": "Landing page source",
                    "payment_method": "cash_on_delivery",
                    "active": True,
                },
            )
            stats["offers"] += 1
