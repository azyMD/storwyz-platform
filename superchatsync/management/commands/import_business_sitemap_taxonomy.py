import json
from urllib.parse import urlparse

from django.core.management.base import BaseCommand
from django.utils import timezone

from superchatsync.management.commands.crawl_business_website import (
    hash_value,
    is_allowed_url,
    normalize_url,
    page_type_for_url,
)
from superchatsync.models import (
    BusinessClient,
    BusinessCrawlPage,
    BusinessKnowledgeImportRun,
    BusinessKnowledgeItem,
)


def title_from_url(url):
    path = urlparse(url).path.strip("/")
    slug = path.split("/")[-1] if path else "home"
    return slug.replace("-", " ").replace("_", " ").title()


class Command(BaseCommand):
    help = "Create draft business knowledge from sitemap taxonomy without fetching page HTML."

    def add_arguments(self, parser):
        parser.add_argument("--business-slug", required=True)
        parser.add_argument("--base-url", required=True)
        parser.add_argument("--page-type", default="collection")
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        business = BusinessClient.objects.get(slug=options["business_slug"])
        base_url = normalize_url(options["base_url"])
        base_netloc = urlparse(base_url).netloc.lower()
        target_page_type = options["page_type"]

        from superchatsync.management.commands.crawl_business_website import Command as CrawlerCommand

        crawler = CrawlerCommand()
        crawler.base_url = base_url
        crawler.base_netloc = base_netloc
        import requests

        crawler.session = requests.Session()
        crawler.session.headers.update({"User-Agent": "StorwyzKnowledgeBot/1.0 (+https://storwyz.com)"})
        crawler.max_retries = 1
        crawler.retry_backoff_seconds = 5

        urls = sorted(
            url
            for url in crawler.discover_sitemap_urls()
            if is_allowed_url(url, base_netloc) and page_type_for_url(url) == target_page_type
        )

        run = BusinessKnowledgeImportRun.objects.create(
            business=business,
            source_url=base_url,
            source_type="sitemap_taxonomy",
            status="running" if options["apply"] else "draft",
            pages_found=len(urls),
            metadata={"page_type": target_page_type, "apply": options["apply"]},
        )

        created_pages = 0
        created_items = 0
        updated_pages = 0

        try:
            if options["apply"]:
                for url in urls:
                    existing = BusinessCrawlPage.objects.filter(
                        business=business,
                        url_hash=hash_value(url),
                        status="crawled",
                    ).first()
                    if existing:
                        continue

                    title = title_from_url(url)
                    body = f"Peeko {target_page_type}: {title}. Source URL: {url}"
                    page, created = BusinessCrawlPage.objects.update_or_create(
                        business=business,
                        url_hash=hash_value(url),
                        defaults={
                            "import_run": run,
                            "url": url,
                            "canonical_url": url,
                            "page_type": target_page_type,
                            "title": title,
                            "language": business.default_language,
                            "extracted_text": body,
                            "extracted_char_count": len(body),
                            "text_hash": hash_value(body),
                            "status": "metadata_only",
                            "source_payload": {"source": "sitemap_taxonomy", "page_type": target_page_type},
                            "crawled_at": timezone.now(),
                        },
                    )
                    created_pages += 1 if created else 0
                    updated_pages += 0 if created else 1

                    item, item_created = BusinessKnowledgeItem.objects.update_or_create(
                        business=business,
                        source_url_hash=hash_value(url),
                        item_type=f"{target_page_type}_taxonomy",
                        content_hash=hash_value(body),
                        defaults={
                            "import_run": run,
                            "page": page,
                            "scope": "general",
                            "title": title,
                            "body": body,
                            "evidence": body,
                            "source_url": url,
                            "source_section": "sitemap_taxonomy",
                            "language": business.default_language,
                            "confidence_score": 55,
                            "priority": 35,
                            "status": "draft",
                            "metadata": {"source": "sitemap_taxonomy", "raw": json.dumps({"url": url})},
                        },
                    )
                    if item_created:
                        created_items += 1

            run.status = "completed"
            run.pages_crawled = created_pages + updated_pages
            run.knowledge_items_created = created_items
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "pages_crawled", "knowledge_items_created", "finished_at", "updated_at"])
        except Exception as exc:
            run.status = "failed"
            run.error = str(exc)
            run.finished_at = timezone.now()
            run.save(update_fields=["status", "error", "finished_at", "updated_at"])
            raise

        self.stdout.write(
            self.style.SUCCESS(
                f"business={business.slug} page_type={target_page_type} urls={len(urls)} pages={run.pages_crawled} knowledge={created_items}"
            )
        )
