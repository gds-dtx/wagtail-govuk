from django.test import TestCase, override_settings
from django.urls import reverse


class RobotsTxtViewTests(TestCase):
    @override_settings(NOINDEX=True)
    def test_disallows_all_crawlers_when_noindex_is_enabled(self):
        response = self.client.get(reverse("govuk_robots_txt"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/plain; charset=utf-8")
        self.assertEqual(
            response.content.decode("utf-8"),
            (
                "User-agent: *\n"
                "User-agent: Googlebot\n"
                "User-agent: AdsBot-Google\n"
                "Disallow: /\n"
            ),
        )

    @override_settings(NOINDEX=False)
    def test_allows_crawlers_when_noindex_is_disabled(self):
        response = self.client.get(reverse("govuk_robots_txt"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/plain; charset=utf-8")
        self.assertEqual(
            response.content.decode("utf-8"),
            (
                "User-agent: *\n"
                "User-agent: Googlebot\n"
                "User-agent: AdsBot-Google\n"
                "Disallow:\n\n\n"
                "User-agent: *\n"
                "User-agent: Googlebot\n"
                "User-agent: AdsBot-Google\n"
                "Allow:\n"
            ),
        )

    def test_head_request_is_allowed(self):
        response = self.client.head(reverse("govuk_robots_txt"))

        self.assertEqual(response.status_code, 200)
