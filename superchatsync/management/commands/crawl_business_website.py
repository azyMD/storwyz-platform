import hashlib
import json
import mimetypes
import os
import re
import time
import uuid
import xml.etree.ElementTree as ET
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlunparse

import requests
from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.utils.text import slugify
from lxml import html

from superchatsync.models import (
    BusinessClient,
    BusinessCrawlPage,
    BusinessKnowledgeImportRun,
    BusinessKnowledgeItem,
    BusinessMediaAsset,
    BusinessProduct,
)


USER_AGENT = "StorwyzKnowledgeBot/1.0 (+https://storwyz.com)"
BLOCKED_PATH_MARKERS = (
    "/admin",
    "/cart",
    "/checkout",
    "/checkouts",
    "/orders",
    "/account",
    "/services",
    "/recommendations/products",
)
BLOCKED_QUERY_MARKERS = (
    "sort_by=",
    "filter.",
    "preview_theme_id=",
    "preview_script_id=",
    "oseid=",
)
POLICY_MARKERS = (
    "shipping",
    "delivery",
    "return",
    "refund",
    "privacy",
    "terms",
    "contact",
    "faq",
    "help",
)


def clean_text(value):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text


def hash_value(value):
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def normalize_url(url):
    parsed = urlparse(str(url or "").strip())
    if not parsed.scheme:
        return ""
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((parsed.scheme, parsed.netloc.lower(), path, "", parsed.query, ""))


def is_allowed_url(url, base_netloc):
    parsed = urlparse(url)
    if parsed.netloc.lower() != base_netloc:
        return False
    path = parsed.path.lower()
    query = parsed.query.lower()
    if any(marker in path for marker in BLOCKED_PATH_MARKERS):
        return False
    if any(marker in query for marker in BLOCKED_QUERY_MARKERS):
        return False
    if "+" in path or "%2b" in path:
        return False
    return True


def page_type_for_url(url):
    path = urlparse(url).path.lower()
    if path in {"", "/"}:
        return "home"
    if path.startswith("/products/"):
        return "product"
    if path.startswith("/collections/"):
        return "collection"
    if path.startswith("/blogs/"):
        return "blog"
    if path.startswith("/policies/") or any(marker in path for marker in POLICY_MARKERS):
        return "policy"
    if path.startswith("/pages/"):
        return "page"
    return "page"


def knowledge_type_for_page(page_type, url, title):
    text = f"{url} {title or ''}".lower()
    if page_type == "policy" or any(marker in text for marker in POLICY_MARKERS):
        return "general_policy"
    if "about" in text or "story" in text:
        return "brand_voice"
    return "raw_section"


def decimal_or_none(value):
    try:
        if value in (None, ""):
            return None
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def html_to_text(markup):
    if not markup:
        return ""
    try:
        doc = html.fromstring(f"<div>{markup}</div>")
        return clean_text(doc.text_content())
    except Exception:
        return clean_text(re.sub(r"<[^>]+>", " ", str(markup)))


def html_page_text(markup):
    doc = html.fromstring(markup)
    for element in doc.xpath("//script|//style|//noscript|//svg|//form"):
        parent = element.getparent()
        if parent is not None:
            parent.remove(element)
    title = clean_text(doc.findtext(".//title"))
    meta_description = ""
    meta = doc.xpath("//meta[translate(@name, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='description']/@content")
    if meta:
        meta_description = clean_text(meta[0])
    canonical = ""
    canonical_nodes = doc.xpath("//link[translate(@rel, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='canonical']/@href")
    if canonical_nodes:
        canonical = clean_text(canonical_nodes[0])
    body_text = clean_text(doc.text_content())
    headings = [clean_text(item.text_content()) for item in doc.xpath("//h1|//h2|//h3") if clean_text(item.text_content())]
    images = []
    for img in doc.xpath("//img"):
        src = img.get("src") or img.get("data-src") or img.get("data-original")
        if not src:
            continue
        images.append(
            {
                "src": src,
                "alt": clean_text(img.get("alt")),
                "width": img.get("width"),
                "height": img.get("height"),
            }
        )
    json_ld = []
    for script in doc.xpath("//script[@type='application/ld+json']/text()"):
        try:
            json_ld.append(json.loads(script))
        except Exception:
            pass
    return {
        "title": title,
        "meta_description": meta_description,
        "canonical": canonical,
        "body_text": body_text,
        "headings": headings,
        "images": images,
        "json_ld": json_ld,
    }


class Command(BaseCommand):
    help = "Crawl a business website into draft business knowledge/product/media tables."

    def add_arguments(self, parser):
        parser.add_argument("--business-slug", default="peeko")
        parser.add_argument("--business-name", default="Peeko")
        parser.add_argument("--base-url", default="https://peeko.co.uk/")
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--download-images", action="store_true")
        parser.add_argument("--max-pages", type=int, default=0)
        parser.add_argument("--sleep-seconds", type=float, default=0.15)
        parser.add_argument("--max-retries", type=int, default=3)
        parser.add_argument("--retry-backoff-seconds", type=float, default=30.0)
        parser.add_argument(
            "--skip-products-refresh",
            action="store_true",
            help="Do not call products.json; useful when only retrying non-product pages.",
        )
        parser.add_argument(
            "--skip-product-page-crawl",
            action="store_true",
            help="Use products.json for product knowledge/media and skip crawling product HTML pages.",
        )
        parser.add_argument(
            "--skip-existing-crawled-pages",
            action="store_true",
            help="Do not request pages that were already crawled successfully.",
        )

    def handle(self, *args, **options):
        self.apply = options["apply"]
        self.download_images = options["download_images"]
        self.sleep_seconds = options["sleep_seconds"]
        self.max_retries = options["max_retries"]
        self.retry_backoff_seconds = options["retry_backoff_seconds"]
        self.skip_existing_crawled_pages = options["skip_existing_crawled_pages"]
        self.skip_products_refresh = options["skip_products_refresh"]
        self.base_url = normalize_url(options["base_url"])
        self.base_netloc = urlparse(self.base_url).netloc.lower()
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})

        business, _ = BusinessClient.objects.get_or_create(
            slug=options["business_slug"],
            defaults={
                "name": options["business_name"],
                "domain": self.base_netloc,
                "default_language": "en",
                "default_currency": "GBP",
                "status": "draft",
                "metadata": {"source": "website_crawl", "base_url": self.base_url},
            },
        )
        if business.domain != self.base_netloc or business.default_currency != "GBP":
            business.domain = self.base_netloc
            business.default_currency = "GBP"
            business.save(update_fields=["domain", "default_currency", "updated_at"])

        run = BusinessKnowledgeImportRun.objects.create(
            business=business,
            source_url=self.base_url,
            source_type="website_crawl",
            status="running" if self.apply else "draft",
            metadata={"download_images": self.download_images, "apply": self.apply},
        )

        try:
            sitemap_urls = self.discover_sitemap_urls()
            product_payloads = [] if self.skip_products_refresh else self.fetch_products_json()
            product_urls = {normalize_url(urljoin(self.base_url, f"/products/{p.get('handle')}")) for p in product_payloads if p.get("handle")}
            all_urls = sorted({url for url in sitemap_urls | product_urls if is_allowed_url(url, self.base_netloc)})
            if options["skip_product_page_crawl"]:
                all_urls = [url for url in all_urls if page_type_for_url(url) != "product"]
            if options["max_pages"] and options["max_pages"] > 0:
                all_urls = all_urls[: options["max_pages"]]

            run.pages_found = len(all_urls)
            run.products_found = len(product_payloads)
            run.save(update_fields=["pages_found", "products_found", "updated_at"])

            products_by_slug = {}
            for payload in product_payloads:
                product = self.upsert_product(business, run, payload)
                if product:
                    products_by_slug[product.slug] = product

            for url in all_urls:
                page = self.crawl_page(business, run, url, products_by_slug)
                if page and page.page_type == "product":
                    slug = urlparse(url).path.rstrip("/").split("/")[-1]
                    product = products_by_slug.get(slug)
                    if product:
                        page.product = product
                        page.save(update_fields=["product", "updated_at"])
                if self.sleep_seconds:
                    time.sleep(self.sleep_seconds)

            run.status = "completed"
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "finished_at", "updated_at"])
        except Exception as exc:
            run.status = "failed"
            run.error = str(exc)
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "error", "finished_at", "updated_at"])
            raise

        self.stdout.write(
            self.style.SUCCESS(
                "business={business} pages={pages} products={products} knowledge={knowledge} media={media} errors={errors}".format(
                    business=business.slug,
                    pages=run.pages_crawled,
                    products=run.products_imported,
                    knowledge=run.knowledge_items_created,
                    media=run.media_assets_created,
                    errors=run.error_count,
                )
            )
        )

    def get(self, url, accept=None):
        headers = {}
        if accept:
            headers["Accept"] = accept
        for attempt in range(self.max_retries + 1):
            response = self.session.get(url, headers=headers, timeout=30)
            if response.status_code == 429 and attempt < self.max_retries:
                retry_after = response.headers.get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else self.retry_backoff_seconds * (attempt + 1)
                except ValueError:
                    delay = self.retry_backoff_seconds * (attempt + 1)
                time.sleep(delay)
                continue
            response.raise_for_status()
            return response
        response.raise_for_status()
        return response

    def discover_sitemap_urls(self):
        seen_sitemaps = set()
        discovered_urls = set()

        def walk(sitemap_url):
            sitemap_url = normalize_url(sitemap_url)
            if not sitemap_url or sitemap_url in seen_sitemaps:
                return
            seen_sitemaps.add(sitemap_url)
            try:
                response = self.get(sitemap_url, accept="application/xml,text/xml,*/*")
            except Exception:
                return
            root = ET.fromstring(response.content)
            namespace = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
            child_sitemaps = [loc.text for loc in root.findall(".//sm:sitemap/sm:loc", namespace) if loc.text]
            if child_sitemaps:
                for child in child_sitemaps:
                    walk(child)
                return
            for loc in root.findall(".//sm:url/sm:loc", namespace):
                if loc.text:
                    url = normalize_url(loc.text)
                    if is_allowed_url(url, self.base_netloc):
                        discovered_urls.add(url)

        walk(urljoin(self.base_url, "/sitemap.xml"))
        return discovered_urls

    def fetch_products_json(self):
        products = []
        seen_ids = set()
        for page_number in range(1, 200):
            url = urljoin(self.base_url, f"/products.json?limit=250&page={page_number}")
            try:
                data = self.get(url, accept="application/json").json()
            except Exception:
                break
            batch = data.get("products") or []
            if not batch:
                break
            for product in batch:
                product_id = str(product.get("id") or "")
                if product_id and product_id not in seen_ids:
                    seen_ids.add(product_id)
                    products.append(product)
            if len(batch) < 250:
                break
        return products

    def upsert_product(self, business, run, payload):
        handle = clean_text(payload.get("handle"))
        title = clean_text(payload.get("title"))
        if not handle or not title:
            return None
        description = html_to_text(payload.get("body_html"))
        variants = payload.get("variants") or []
        prices = [decimal_or_none(variant.get("price")) for variant in variants]
        prices = [price for price in prices if price is not None]
        product_url = normalize_url(urljoin(self.base_url, f"/products/{handle}"))
        defaults = {
            "external_id": str(payload.get("id") or ""),
            "name": title,
            "url": product_url,
            "vendor": clean_text(payload.get("vendor")),
            "product_type": clean_text(payload.get("product_type")),
            "description": description,
            "tags": payload.get("tags") or [],
            "options": payload.get("options") or [],
            "variants": variants,
            "currency": "GBP",
            "min_price": min(prices) if prices else None,
            "max_price": max(prices) if prices else None,
            "status": "draft",
            "source_payload": payload,
            "last_seen_at": timezone.now(),
        }
        if not self.apply:
            return None
        product, created = BusinessProduct.objects.update_or_create(
            business=business,
            slug=slugify(handle)[:220],
            defaults=defaults,
        )
        run.products_imported += 1 if created else 0
        self.create_product_knowledge(business, run, product, payload)
        self.create_product_media(business, run, product, payload)
        run.save(update_fields=["products_imported", "knowledge_items_created", "media_assets_created", "updated_at"])
        return product

    def create_product_knowledge(self, business, run, product, payload):
        body = html_to_text(payload.get("body_html"))
        if body:
            self.upsert_knowledge(
                business=business,
                run=run,
                product=product,
                scope="product",
                item_type="product_fact",
                title=product.name,
                body=body,
                source_url=product.url,
                source_section="products.json.body_html",
                confidence=90,
            )
        variants = payload.get("variants") or []
        if variants:
            lines = []
            for variant in variants:
                name = clean_text(variant.get("title")) or "Default"
                price = clean_text(variant.get("price"))
                available = variant.get("available")
                lines.append(f"{name}: £{price}; available={available}")
            self.upsert_knowledge(
                business=business,
                run=run,
                product=product,
                scope="product",
                item_type="pricing_offer",
                title=f"{product.name} pricing and variants",
                body="\n".join(lines),
                source_url=product.url,
                source_section="products.json.variants",
                confidence=95,
            )
        specs = {
            "product_type": payload.get("product_type"),
            "vendor": payload.get("vendor"),
            "tags": payload.get("tags") or [],
            "options": payload.get("options") or [],
        }
        if any(specs.values()):
            self.upsert_knowledge(
                business=business,
                run=run,
                product=product,
                scope="product",
                item_type="product_specs",
                title=f"{product.name} specs",
                body=json.dumps(specs, ensure_ascii=False, indent=2),
                source_url=product.url,
                source_section="products.json.specs",
                confidence=85,
            )

    def create_product_media(self, business, run, product, payload):
        images = payload.get("images") or []
        for index, image in enumerate(images):
            source = image.get("src")
            if not source:
                continue
            source_url = normalize_url(source)
            if not source_url:
                continue
            self.upsert_media(
                business=business,
                run=run,
                product=product,
                source_url=source_url,
                title=f"{product.name} image {index + 1}",
                alt_text=clean_text(image.get("alt")),
                image_role="main" if index == 0 else "gallery",
                source_page_url=product.url,
                metadata={"shopify_image": image, "source": "products.json"},
            )

    def crawl_page(self, business, run, url, products_by_slug):
        page_type = page_type_for_url(url)
        if self.skip_existing_crawled_pages:
            existing_page = BusinessCrawlPage.objects.filter(
                business=business,
                url_hash=hash_value(url),
                status="crawled",
            ).first()
            if existing_page:
                return existing_page
        product = None
        if page_type == "product":
            slug = urlparse(url).path.rstrip("/").split("/")[-1]
            product = products_by_slug.get(slug)
        try:
            response = self.get(url)
            extracted = html_page_text(response.text)
            text = extracted["body_text"]
            page, created = BusinessCrawlPage.objects.update_or_create(
                business=business,
                url_hash=hash_value(url),
                defaults={
                    "import_run": run,
                    "product": product,
                    "url": url,
                    "canonical_url": normalize_url(urljoin(url, extracted["canonical"])) if extracted["canonical"] else "",
                    "page_type": page_type,
                    "title": extracted["title"],
                    "meta_description": extracted["meta_description"],
                    "language": "en",
                    "extracted_text": text,
                    "extracted_char_count": len(text),
                    "text_hash": hash_value(text) if text else "",
                    "status": "crawled",
                    "http_status": response.status_code,
                    "source_payload": {
                        "headings": extracted["headings"][:80],
                        "json_ld": extracted["json_ld"][:20],
                        "images_found": len(extracted["images"]),
                    },
                    "crawled_at": timezone.now(),
                },
            )
            run.pages_crawled += 1
            if text:
                self.upsert_knowledge(
                    business=business,
                    run=run,
                    page=page,
                    product=product,
                    scope="product" if product else "general",
                    item_type=knowledge_type_for_page(page_type, url, extracted["title"]),
                    title=extracted["title"] or url,
                    body=text[:12000],
                    source_url=url,
                    source_section="visible_text",
                    confidence=70 if page_type != "policy" else 85,
                )
            run.save(update_fields=["pages_crawled", "knowledge_items_created", "updated_at"])
            return page
        except Exception as exc:
            BusinessCrawlPage.objects.update_or_create(
                business=business,
                url_hash=hash_value(url),
                defaults={
                    "import_run": run,
                    "url": url,
                    "page_type": page_type,
                    "status": "error",
                    "error": str(exc),
                    "crawled_at": timezone.now(),
                },
            )
            run.error_count += 1
            run.save(update_fields=["error_count", "updated_at"])
            return None

    def upsert_knowledge(self, *, business, run, scope, item_type, title, body, source_url, source_section, confidence, page=None, product=None):
        body = clean_text(body)
        if not body:
            return None
        content_hash = hash_value(f"{scope}|{item_type}|{body}")
        source_hash = hash_value(source_url)
        item, created = BusinessKnowledgeItem.objects.update_or_create(
            business=business,
            source_url_hash=source_hash,
            item_type=item_type,
            content_hash=content_hash,
            defaults={
                "import_run": run,
                "page": page,
                "product": product,
                "scope": scope,
                "title": clean_text(title)[:500],
                "body": body,
                "evidence": body[:1200],
                "source_url": source_url,
                "source_section": source_section,
                "language": "en",
                "confidence_score": confidence,
                "priority": 70 if scope == "product" else 50,
                "status": "draft",
                "metadata": {"source": "website_crawl"},
            },
        )
        if created:
            run.knowledge_items_created += 1
        return item

    def upsert_media(self, *, business, run, product, source_url, title, alt_text, image_role, source_page_url, metadata):
        source_hash = hash_value(source_url)
        existing = BusinessMediaAsset.objects.filter(business=business, source_url_hash=source_hash).first()
        local_path = existing.local_path if existing else ""
        mime_type = existing.mime_type if existing else ""
        file_size = existing.file_size_bytes if existing else None
        width = existing.width if existing else None
        height = existing.height if existing else None
        if self.download_images:
            local_path, mime_type, file_size, width, height = self.download_image(business.slug, product.slug, source_url)
        asset, created = BusinessMediaAsset.objects.update_or_create(
            business=business,
            source_url_hash=source_hash,
            defaults={
                "import_run": run,
                "product": product,
                "asset_type": "image",
                "image_role": image_role,
                "title": clean_text(title),
                "alt_text": alt_text,
                "source_url": source_url,
                "local_path": local_path,
                "mime_type": mime_type,
                "file_size_bytes": file_size,
                "width": width,
                "height": height,
                "source_page_url": source_page_url,
                "language": "en",
                "status": "draft",
                "metadata": metadata,
                "last_seen_at": timezone.now(),
            },
        )
        if created:
            run.media_assets_created += 1
        return asset

    def download_image(self, business_slug, product_slug, source_url):
        try:
            response = self.session.get(source_url, timeout=45)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
            ext = mimetypes.guess_extension(content_type) or Path(urlparse(source_url).path).suffix or ".img"
            image_dir = Path(settings.MEDIA_ROOT) / "business_clients" / business_slug / "product_images" / product_slug
            image_dir.mkdir(parents=True, exist_ok=True)
            filename = f"{hash_value(source_url)[:20]}{ext}"
            path = image_dir / filename
            if not path.exists():
                path.write_bytes(response.content)
            width = height = None
            try:
                from PIL import Image
                with Image.open(path) as image:
                    width, height = image.size
            except Exception:
                pass
            return str(path), content_type, len(response.content), width, height
        except Exception:
            return "", "", None, None, None
