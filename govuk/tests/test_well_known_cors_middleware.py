from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase

from govuk.middleware import WellKnownCorsMiddleware


class WellKnownCorsMiddlewareTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.middleware = WellKnownCorsMiddleware(lambda request: HttpResponse("ok"))

    def test_sets_wildcard_origin_header_for_well_known_paths(self):
        request = self.factory.get("/.well-known/jwks.json")

        response = self.middleware(request)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Access-Control-Allow-Origin"], "*")

    def test_leaves_other_paths_unchanged(self):
        request = self.factory.get("/search/")

        response = self.middleware(request)

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Access-Control-Allow-Origin", response)

    def test_preserves_existing_origin_header_for_well_known_paths(self):
        middleware = WellKnownCorsMiddleware(
            lambda request: HttpResponse(
                "ok",
                headers={"Access-Control-Allow-Origin": "https://example.com"},
            )
        )
        request = self.factory.get("/.well-known/jwks.json")

        response = middleware(request)

        self.assertEqual(
            response["Access-Control-Allow-Origin"],
            "https://example.com",
        )
