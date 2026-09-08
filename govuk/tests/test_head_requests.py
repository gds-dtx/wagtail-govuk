"""HEAD answers wherever GET does.

Every page Wagtail serves already answers HEAD, but the views this app routes
itself were decorated `require_http_methods(["GET"])`, which rejects HEAD with
a 405. Link checkers and uptime monitors ask with HEAD by preference -- a
service that answers 405 on its own search page and its own downloads looks
broken to both. `view_robots` and `view_securitytxt` had it right from the
start; these tests hold the rest to the same behaviour.
"""

from django.test import TestCase, override_settings
from django.urls import reverse
from wagtail.models import Site

from govuk.models import CustomiseSettings, EdDSAKeyPair, EdDSAKeySettings


def _feature_flags() -> dict[str, bool]:
    return {
        "SKILLS": True,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
    }


@override_settings(FEATURE_FLAGS=_feature_flags())
class HeadRequestTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.site.hostname = "testserver"
        self.site.port = 80
        self.site.save(update_fields=["hostname", "port"])

    def assertHeadMatchesGet(self, url):
        """HEAD and GET agree on the status, and HEAD sends no body.

        Django strips the body from a HEAD response itself, so the assertion
        that matters is the status: it is the 405 that this guards against.
        """
        get_response = self.client.get(url)
        head_response = self.client.head(url)

        self.assertEqual(head_response.status_code, get_response.status_code)
        self.assertEqual(head_response.content, b"")

    def test_search_answers_head(self):
        self.assertHeadMatchesGet(reverse("search"))

    def test_search_with_a_query_answers_head(self):
        self.assertHeadMatchesGet(reverse("search") + "?query=designer")

    def test_custom_css_answers_head(self):
        customise = CustomiseSettings.for_site(self.site)
        customise.extra_css = ".masthead { background: #0b0c0c; }"
        customise.save()

        self.assertHeadMatchesGet(reverse("govuk_custom_css"))

    def test_jwks_answers_head(self):
        EdDSAKeyPair.generate_for_settings(
            settings_obj=EdDSAKeySettings.for_site(self.site)
        )

        self.assertHeadMatchesGet(reverse("govuk_jwks"))

    def test_framework_csv_answers_head(self):
        for name in ("roles", "skills", "changelog"):
            with self.subTest(name=name):
                self.assertHeadMatchesGet(
                    reverse("govuk_framework_csv", args=[name])
                )

    def test_head_on_a_missing_download_is_still_a_404(self):
        """Not a 405 dressed up as a 404: the view must reach its own Http404."""
        response = self.client.head(reverse("govuk_framework_csv", args=["nope"]))

        self.assertEqual(response.status_code, 404)

    def test_post_is_still_rejected(self):
        """Widening to HEAD must not widen to anything else."""
        for url in (
            reverse("search"),
            reverse("govuk_custom_css"),
            reverse("govuk_jwks"),
            reverse("govuk_framework_csv", args=["roles"]),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.post(url).status_code, 405)
