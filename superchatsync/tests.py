import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory
from django.test import SimpleTestCase

from superchatsync import catalog_builder
from superchatsync.management.commands.backfill_catalog_product_skus import ProductSkuResolver


class HealthEndpointTests(SimpleTestCase):
    def test_healthz_is_public_and_minimal(self):
        response = self.client.get("/healthz/", secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertEqual(response.headers["Cache-Control"], "max-age=0, no-cache, no-store, must-revalidate, private")

    def test_healthz_rejects_post(self):
        response = self.client.post("/healthz/", secure=True)

        self.assertEqual(response.status_code, 405)


class CatalogSkuTests(SimpleTestCase):
    def test_resolver_prioritizes_product_knowledge_and_supports_aliases(self):
        resolver = ProductSkuResolver(
            products=[
                ("2658", "Windshield Wiper"),
                ("2884", "Scraper RainAway"),
                ("2888", "Nuvia Trimmer"),
                ("2896", "Glossy Comb"),
            ],
            mappings=[
                ("2658", "Windshield Wiper", ["Rainaway"]),
                ("2888", "Nuvia Trimmer", ["Nuvia 2 in 1"]),
            ],
            knowledge_product_ids={"2658", "2888"},
        )

        self.assertEqual(resolver.resolve("RainWay RO")[0], "2658")
        self.assertEqual(resolver.resolve("Nuvia BG")[0], "2888")
        self.assertEqual(resolver.resolve("Glossy Comb RO"), ("2896", "catalog_exact"))
        self.assertEqual(resolver.resolve("Peeko Best Sellers"), (None, "unmatched"))

    def test_create_stores_product_sku_in_manifest(self):
        with TemporaryDirectory() as temp_dir:
            upload = SimpleUploadedFile("page.png", b"catalog-page", content_type="image/png")
            request = RequestFactory().post(
                "/catalog-admin/create/",
                data={
                    "product_name": "ButchAxe RO",
                    "product_sku": "2757",
                    "country_code": "ro",
                    "pages": upload,
                },
            )
            request.session = {catalog_builder.CATALOG_SESSION_KEY: True}

            with patch.object(catalog_builder, "CATALOG_DIR", Path(temp_dir)):
                response = catalog_builder.catalog_create(request)

            self.assertEqual(response.status_code, 200)
            payload = json.loads(response.content)
            self.assertEqual(payload["product_sku"], "2757")
            manifest_path = Path(temp_dir) / "butchaxe-ro" / "ro" / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["product_sku"], "2757")
