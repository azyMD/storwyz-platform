import json
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, SimpleTestCase, TestCase

from superchatsync import catalog_builder
from superchatsync.landing_leads import (
    _external_order_id,
    _fitspace_payload_for_lead,
    validate_landing_lead,
)
from superchatsync.landing_product_mapping import normalize_product_name
from superchatsync.management.commands.backfill_catalog_product_skus import ProductSkuResolver
from superchatsync.models import LandingLeadSubmission, LandingProductMapping


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

    def test_delete_removes_catalog_directory(self):
        with TemporaryDirectory() as temp_dir:
            catalog_path = Path(temp_dir) / "butchaxe-ro" / "ro"
            catalog_path.mkdir(parents=True)
            (catalog_path / "manifest.json").write_text(
                json.dumps(
                    {
                        "product_name": "ButchAxe RO",
                        "product_slug": "butchaxe-ro",
                        "country_code": "ro",
                        "pages": [{"filename": "assets/page-01.png"}],
                    }
                ),
                encoding="utf-8",
            )
            request = RequestFactory().post("/catalog-admin/delete/butchaxe-ro/ro/")
            request.session = {catalog_builder.CATALOG_SESSION_KEY: True}

            with patch.object(catalog_builder, "CATALOG_DIR", Path(temp_dir)):
                response = catalog_builder.catalog_delete(request, "butchaxe-ro", "ro")

            self.assertEqual(response.status_code, 302)
            self.assertFalse(catalog_path.exists())

    def test_admin_includes_column_filters(self):
        request = RequestFactory().get("/catalog-admin/")
        request.session = {catalog_builder.CATALOG_SESSION_KEY: True}
        brochures = [
            {
                "product_name": "ButchAxe RO",
                "product_sku": "2757",
                "product_slug": "butchaxe-ro",
                "country_code": "ro",
                "country_label": "Romania",
                "page_count": 2,
                "updated_at": "2026-07-22T10:00:00Z",
                "desired_url": "https://catalog.storwyz.com/butchaxe-ro/ro/",
                "local_path": "/catalog/butchaxe-ro/ro/",
            }
        ]

        with patch.object(catalog_builder, "_list_brochures", return_value=brochures):
            response = catalog_builder.catalog_admin(request)

        html = response.content.decode("utf-8")
        self.assertIn('id="catalogProductFilter"', html)
        self.assertIn('id="catalogSkuFilter"', html)
        self.assertIn('id="catalogCountryFilter"', html)
        self.assertIn("storwyz.catalogAdmin.filters", html)
        self.assertIn("<th>Actions</th>", html)
        self.assertIn('data-product="ButchAxe RO butchaxe-ro"', html)
        self.assertIn('data-sku="2757"', html)
        self.assertIn('data-country="Romania ro"', html)


class LandingLeadValidationTests(SimpleTestCase):
    def test_valid_payload_is_normalized_for_fitspace(self):
        payload, errors = validate_landing_lead(
            {
                "customer_name": "Test Customer",
                "customer_phone": "+373 68 200 969",
                "customer_region": "1044",
                "customer_address": "Test street 10",
                "quantity": "2",
                "cost": "258.00",
                "product": "ButchAxe",
                "referral": "Landing",
                "customer_comment": "Landing test",
            }
        )

        self.assertEqual(errors, {})
        self.assertEqual(payload["customer_region"], 1044)
        self.assertEqual(payload["quantity"], 2)
        self.assertEqual(payload["cost"], 258)
        self.assertEqual(payload["product"], "ButchAxe")

    def test_missing_required_fields_are_reported(self):
        _, errors = validate_landing_lead({"customer_name": "", "cost": "invalid"})

        self.assertEqual(
            set(errors),
            {
                "customer_name",
                "customer_phone",
                "customer_address",
                "customer_region",
                "quantity",
                "cost",
                "product",
            },
        )

    def test_external_order_id_is_extracted_from_fitspace_response(self):
        self.assertEqual(_external_order_id('{"order_id":"fs_123"}'), "fs_123")

    def test_product_name_normalization_is_exact_but_format_tolerant(self):
        self.assertEqual(normalize_product_name("  Butch-Axe™  "), "butchaxe")
        self.assertEqual(normalize_product_name("Cuțit Japonez"), "cutitjaponez")

    def test_fitspace_payload_uses_resolved_sku(self):
        lead = SimpleNamespace(
            customer_name="Test Customer",
            customer_phone="+37368000000",
            customer_region=1044,
            customer_address="Test address",
            quantity=1,
            cost=Decimal("129.00"),
            referral="Landing",
            customer_comment="Test",
        )

        payload = _fitspace_payload_for_lead(lead, "2757")

        self.assertEqual(payload["product"], "2757")
        self.assertEqual(payload["cost"], 129)


class LandingLeadProductMappingTests(TestCase):
    payload = {
        "customer_name": "Test Customer",
        "customer_phone": "+37368000000",
        "customer_region": 1044,
        "customer_address": "Test street 10",
        "quantity": 1,
        "cost": 129,
        "product": "Butch Axe",
        "referral": "Landing",
        "customer_comment": "Mapping test",
    }

    @patch("superchatsync.landing_leads.requests.post")
    def test_known_product_is_forwarded_with_mapped_sku(self, post):
        LandingProductMapping.objects.create(product_name="ButchAxe", sku="2757")
        post.return_value = SimpleNamespace(status_code=201, text='{"order_id":"fs_test"}')

        response = self.client.post(
            "/api/landing-leads/",
            data=json.dumps(self.payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["product_sku"], "2757")
        self.assertEqual(post.call_args.kwargs["json"]["product"], "2757")
        lead = LandingLeadSubmission.objects.get()
        self.assertEqual(lead.product, "Butch Axe")
        self.assertEqual(lead.product_sku, "2757")
        self.assertEqual(lead.status, LandingLeadSubmission.STATUS_SENT)

    @patch("superchatsync.landing_leads.requests.post")
    def test_unknown_product_is_stored_without_forwarding(self, post):
        payload = {**self.payload, "product": "Unknown landing product"}

        response = self.client.post(
            "/api/landing-leads/",
            data=json.dumps(payload),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 202)
        self.assertFalse(response.json()["forwarded"])
        post.assert_not_called()
        lead = LandingLeadSubmission.objects.get()
        self.assertEqual(lead.product, "Unknown landing product")
        self.assertEqual(lead.status, LandingLeadSubmission.STATUS_MAPPING_REQUIRED)
        self.assertFalse(lead.product_sku)
