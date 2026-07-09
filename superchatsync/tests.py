from django.test import SimpleTestCase


class HealthEndpointTests(SimpleTestCase):
    def test_healthz_is_public_and_minimal(self):
        response = self.client.get("/healthz/", secure=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertEqual(response.headers["Cache-Control"], "max-age=0, no-cache, no-store, must-revalidate, private")

    def test_healthz_rejects_post(self):
        response = self.client.post("/healthz/", secure=True)

        self.assertEqual(response.status_code, 405)
