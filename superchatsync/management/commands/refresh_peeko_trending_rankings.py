import time
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin, urlparse

import requests
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.text import slugify

from superchatsync.models import (
    BusinessClient,
    BusinessCrawlPage,
    BusinessProduct,
    BusinessProductRanking,
)


USER_AGENT = "StorwyzKnowledgeBot/1.0 (+https://storwyz.com)"
DEFAULT_COLLECTIONS = [
    "trending-deals",
    "groceries-trending",
    "beauty-trending",
    "health-trending",
    "household-trending",
    "specials-trending",
]


def decimal_or_none(value):
    try:
        if value in (None, ""):
            return None
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def clean_text(value):
    return " ".join(str(value or "").split()).strip()


class Command(BaseCommand):
    help = "Refresh Peeko best-seller ranking table from Shopify trending collections."

    def add_arguments(self, parser):
        parser.add_argument("--business-slug", default="peeko")
        parser.add_argument("--rank-type", default="best_seller")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--sleep-seconds", type=float, default=0.35)
        parser.add_argument("--limit-per-collection", type=int, default=80)

    def handle(self, *args, **options):
        business = BusinessClient.objects.get(slug=options["business_slug"])
        rank_type = options["rank_type"]
        apply = options["apply"]
        limit_per_collection = max(1, options["limit_per_collection"])
        sleep_seconds = max(0, options["sleep_seconds"])
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json,text/html;q=0.8,*/*;q=0.6"})

        collections = self.discover_trending_collections(business)
        if not collections:
            collections = [
                {
                    "slug": slug,
                    "title": slug.replace("-", " ").title(),
                    "url": f"https://{business.domain}/collections/{slug}",
                }
                for slug in DEFAULT_COLLECTIONS
            ]

        if apply:
            BusinessProductRanking.objects.filter(business=business, rank_type=rank_type).update(active=False)

        total_seen = 0
        total_ranked = 0
        missing = 0

        for collection in collections:
            products = self.fetch_collection_products(
                session,
                business,
                collection["slug"],
                limit_per_collection,
            )
            for index, payload in enumerate(products, start=1):
                total_seen += 1
                product = self.upsert_product_if_needed(business, payload) if apply else None
                if apply and not product:
                    missing += 1
                    continue
                if apply:
                    _, created = BusinessProductRanking.objects.update_or_create(
                        business=business,
                        product=product,
                        rank_type=rank_type,
                        collection_slug=collection["slug"],
                        defaults={
                            "collection_title": collection["title"],
                            "source_url": collection["url"],
                            "rank": index,
                            "score": Decimal(max(1, 10000 - index)),
                            "active": True,
                            "metadata": {
                                "source": "shopify_trending_collection",
                                "shopify_product_id": payload.get("id"),
                                "handle": payload.get("handle"),
                                "collection": collection,
                            },
                            "last_seen_at": timezone.now(),
                        },
                    )
                    total_ranked += 1
            if sleep_seconds:
                time.sleep(sleep_seconds)

        self.stdout.write(
            self.style.SUCCESS(
                f"business={business.slug} collections={len(collections)} seen={total_seen} ranked={total_ranked} missing={missing} apply={apply}"
            )
        )

    def discover_trending_collections(self, business):
        collections = []
        pages = (
            BusinessCrawlPage.objects.filter(business=business, page_type="collection")
            .filter(url__icontains="trending")
            .order_by("url")
        )
        for page in pages:
            slug = urlparse(page.url).path.rstrip("/").split("/")[-1]
            if not slug:
                continue
            collections.append(
                {
                    "slug": slug,
                    "title": clean_text(page.title) or slug.replace("-", " ").title(),
                    "url": page.url,
                }
            )
        seen = set()
        unique = []
        for collection in collections:
            if collection["slug"] in seen:
                continue
            seen.add(collection["slug"])
            unique.append(collection)
        return unique

    def fetch_collection_products(self, session, business, collection_slug, limit):
        products = []
        page = 1
        base_url = f"https://{business.domain}/collections/{collection_slug}/products.json"
        while len(products) < limit:
            response = session.get(base_url, params={"limit": min(250, limit), "page": page}, timeout=35)
            if response.status_code == 429:
                time.sleep(4)
                response = session.get(base_url, params={"limit": min(250, limit), "page": page}, timeout=35)
            response.raise_for_status()
            batch = (response.json() or {}).get("products") or []
            if not batch:
                break
            products.extend(batch[: max(0, limit - len(products))])
            if len(batch) < min(250, limit):
                break
            page += 1
        return products[:limit]

    def upsert_product_if_needed(self, business, payload):
        handle = clean_text(payload.get("handle"))
        name = clean_text(payload.get("title"))
        if not handle or not name:
            return None
        external_id = str(payload.get("id") or "")
        product = (
            BusinessProduct.objects.filter(business=business, external_id=external_id).first()
            if external_id
            else None
        )
        if not product:
            product = BusinessProduct.objects.filter(business=business, slug=slugify(handle)[:220]).first()
        variants = payload.get("variants") or []
        prices = [decimal_or_none(variant.get("price")) for variant in variants]
        prices = [price for price in prices if price is not None]
        defaults = {
            "external_id": external_id,
            "name": name,
            "url": urljoin(f"https://{business.domain}", f"/products/{handle}"),
            "vendor": clean_text(payload.get("vendor")),
            "product_type": clean_text(payload.get("product_type")),
            "description": clean_text(payload.get("body_html")),
            "tags": payload.get("tags") or [],
            "options": payload.get("options") or [],
            "variants": variants,
            "currency": business.default_currency or "GBP",
            "min_price": min(prices) if prices else None,
            "max_price": max(prices) if prices else None,
            "status": "active",
            "source_payload": payload,
            "last_seen_at": timezone.now(),
        }
        if product:
            for field, value in defaults.items():
                setattr(product, field, value)
            product.save(update_fields=list(defaults.keys()) + ["updated_at"])
            return product
        return BusinessProduct.objects.create(
            business=business,
            slug=slugify(handle)[:220],
            **defaults,
        )
