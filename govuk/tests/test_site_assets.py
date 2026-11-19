"""The site's own CSS and logo, and the vendored static files the image build collects."""

import re
from pathlib import Path

from django.contrib.staticfiles import finders
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from wagtail.models import Site

from govuk.models import CustomiseSettings

STATIC_ROOT = Path(__file__).resolve().parents[1] / "static"
SOURCE_MAP = re.compile(r"sourceMappingURL=([^\s*]+)")


class CustomiseAssetsTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.site.hostname = "testserver"
        self.site.port = 80
        self.site.save(update_fields=["hostname", "port"])
        self.customise_settings = CustomiseSettings.for_site(self.site)

    def _custom_css(self) -> str:
        return self.client.get("/gen/custom.css").content.decode("utf-8")

    def _search_page(self):
        response = self.client.get(reverse("search"), data={"query": "service"})
        self.assertEqual(response.status_code, 200)
        return response

    def test_custom_css_view_renders_extra_css(self):
        self.customise_settings.extra_css = ".hero__title { text-transform: uppercase; }"
        self.customise_settings.save()

        response = self.client.get("/gen/custom.css")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/css; charset=utf-8")
        self.assertIn(
            ".hero__title { text-transform: uppercase; }", response.content.decode("utf-8")
        )

    def test_custom_asset_views_return_404_when_empty(self):
        self.assertEqual(self.client.get("/gen/custom.css").status_code, 404)

    def test_content_max_width_sets_the_variable_and_shifts_the_breakpoint(self):
        self.customise_settings.content_max_width = 1200
        self.customise_settings.save()

        body = self._custom_css()

        self.assertIn(":root { --govuk-content-width: 1200px; }", body)
        # Centring breakpoint moves to width + 80 = 1280px, with the fixed
        # margins kept in the gap above the old 1030px breakpoint.
        self.assertIn("@media (min-width: 1030px) and (max-width: 1279px)", body)
        self.assertIn("@media (min-width: 1280px)", body)

    def test_a_narrower_content_width_centres_earlier(self):
        self.customise_settings.content_max_width = 800
        self.customise_settings.save()

        body = self._custom_css()

        self.assertIn(":root { --govuk-content-width: 800px; }", body)
        self.assertIn("@media (min-width: 880px)", body)
        # Narrower than the default: no gap to bridge above 1030px.
        self.assertNotIn("max-width: 879px", body)

    def test_no_width_css_when_left_blank(self):
        self.customise_settings.extra_css = ".x { color: red; }"
        self.customise_settings.save()

        self.assertNotIn("--govuk-content-width", self._custom_css())

    def test_base_template_only_includes_custom_assets_when_present(self):
        self.assertNotContains(self._search_page(), "/gen/custom.css")

        self.customise_settings.extra_css = ".hero__title { color: red; }"
        self.customise_settings.save()

        self.assertContains(self._search_page(), "/gen/custom.css")

    def test_base_template_uses_embedded_govuk_logo_by_default(self):
        response = self._search_page()

        self.assertContains(response, 'aria-label="GOV.UK"')
        self.assertNotContains(response, "/static/crown.svg")

    def test_base_template_can_render_uk_government_crown_logo(self):
        self.customise_settings.header_logo = "uk-government"
        self.customise_settings.save()

        response = self._search_page()

        self.assertContains(response, 'class="govuk-header__logo--ukgov"')
        self.assertNotContains(response, 'aria-label="GOV.UK"')

class VendoredSourceMapsTests(SimpleTestCase):
    """Every vendored file that names a source map ships with it.

    The image build's ``collectstatic`` runs whitenoise's manifest storage,
    which refuses to build when a named map is missing; the tests run under the
    plain storage, which does not look.
    """

    def test_every_named_source_map_is_present(self):
        missing = []
        for path in list(STATIC_ROOT.rglob("*.js")) + list(STATIC_ROOT.rglob("*.css")):
            tail = path.read_text(errors="ignore")[-400:]
            for name in SOURCE_MAP.findall(tail):
                relative = str(path.relative_to(STATIC_ROOT).parent / name).lstrip("./")
                if finders.find(relative) is None:
                    missing.append(f"{path.relative_to(STATIC_ROOT)} -> {name}")
        self.assertEqual(missing, [], "source maps the build would fail on")

    def test_the_autocomplete_library_names_its_own_versioned_map(self):
        """Upstream names an unversioned map; ours carries the version, so the comment must too."""
        library = (STATIC_ROOT / "accessible-autocomplete-3.0.1.min.js").read_text()
        self.assertIn("sourceMappingURL=accessible-autocomplete-3.0.1.min.js.map", library)
        self.assertIsNotNone(finders.find("accessible-autocomplete-3.0.1.min.js.map"))
