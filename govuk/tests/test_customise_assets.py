from django.test import TestCase
from django.urls import reverse
from wagtail.models import Site

from govuk.models import CustomiseSettings


class CustomiseAssetsTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.site.hostname = "testserver"
        self.site.port = 80
        self.site.save(update_fields=["hostname", "port"])
        self.customise_settings = CustomiseSettings.for_site(self.site)

    def test_custom_css_view_renders_extra_css(self):
        self.customise_settings.extra_css = (
            ".hero__title { text-transform: uppercase; }"
        )
        self.customise_settings.save()

        response = self.client.get("/gen/custom.css")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/css; charset=utf-8")
        body = response.content.decode("utf-8")
        self.assertIn(".hero__title { text-transform: uppercase; }", body)

    def test_custom_asset_views_return_404_when_empty(self):
        self.assertEqual(self.client.get("/gen/custom.css").status_code, 404)

    def test_content_max_width_sets_the_variable_and_shifts_the_breakpoint(self):
        self.customise_settings.content_max_width = 1200
        self.customise_settings.save()

        body = self.client.get("/gen/custom.css").content.decode("utf-8")

        self.assertIn(":root { --govuk-content-width: 1200px; }", body)
        # Centring breakpoint moves to width + 80 = 1280px, with the fixed
        # margins kept in the gap above the old 1030px breakpoint.
        self.assertIn("@media (min-width: 1030px) and (max-width: 1279px)", body)
        self.assertIn("@media (min-width: 1280px)", body)

    def test_a_narrower_content_width_centres_earlier(self):
        self.customise_settings.content_max_width = 800
        self.customise_settings.save()

        body = self.client.get("/gen/custom.css").content.decode("utf-8")

        self.assertIn(":root { --govuk-content-width: 800px; }", body)
        self.assertIn("@media (min-width: 880px)", body)
        # Narrower than the default: no gap to bridge above 1030px.
        self.assertNotIn("max-width: 879px", body)

    def test_no_width_css_when_left_blank(self):
        self.customise_settings.extra_css = ".x { color: red; }"
        self.customise_settings.save()

        body = self.client.get("/gen/custom.css").content.decode("utf-8")

        self.assertNotIn("--govuk-content-width", body)

    def test_base_template_only_includes_custom_assets_when_present(self):
        search_url = reverse("search")

        no_custom_response = self.client.get(search_url, data={"query": "service"})
        self.assertEqual(no_custom_response.status_code, 200)
        self.assertNotContains(no_custom_response, "/gen/custom.css")

        self.customise_settings.extra_css = ".hero__title { color: red; }"
        self.customise_settings.save()

        custom_response = self.client.get(search_url, data={"query": "service"})
        self.assertEqual(custom_response.status_code, 200)
        self.assertContains(custom_response, "/gen/custom.css")

    def test_base_template_uses_embedded_govuk_logo_by_default(self):
        response = self.client.get(reverse("search"), data={"query": "service"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'aria-label="GOV.UK"')
        self.assertNotContains(response, "/static/crown.svg")

    def test_base_template_can_render_uk_government_crown_logo(self):
        self.customise_settings.header_logo = "uk-government"
        self.customise_settings.save()

        response = self.client.get(reverse("search"), data={"query": "service"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'class="govuk-header__logo--ukgov"')
        self.assertNotContains(response, 'aria-label="GOV.UK"')

    def test_base_template_search_placeholder_hides_site_name_by_default(self):
        self.site.site_name = "Example Service"
        self.site.save(update_fields=["site_name"])

        response = self.client.get(reverse("search"), data={"query": "service"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'placeholder="Search"')

    def test_base_template_search_placeholder_can_show_site_name(self):
        self.site.site_name = "Example Service"
        self.site.save(update_fields=["site_name"])
        self.customise_settings.show_site_name_in_search_box = True
        self.customise_settings.save()

        response = self.client.get(reverse("search"), data={"query": "service"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'placeholder="Search Example Service"')

    def test_base_template_search_placeholder_can_fallback_to_this_site(self):
        self.site.site_name = ""
        self.site.save(update_fields=["site_name"])
        self.customise_settings.show_site_name_in_search_box = True
        self.customise_settings.save()

        response = self.client.get(reverse("search"), data={"query": "service"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'placeholder="Search this site"')
