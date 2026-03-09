from django.core.exceptions import ValidationError
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

    def test_custom_css_view_renders_masthead_overrides_and_extra_css(self):
        self.customise_settings.hero_background_color = "#112233"
        self.customise_settings.hero_text_color = "#fefefe"
        self.customise_settings.extra_css = (
            ".hero__title { text-transform: uppercase; }"
        )
        self.customise_settings.save()

        response = self.client.get("/gen/custom.css")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/css; charset=utf-8")
        body = response.content.decode("utf-8")
        self.assertIn(".masthead {", body)
        self.assertIn("background: #112233;", body)
        self.assertIn("color: #fefefe;", body)
        self.assertIn(".hero__title { text-transform: uppercase; }", body)

    def test_custom_js_view_renders_extra_js(self):
        self.customise_settings.extra_js = "window.customThemeReady = true;"
        self.customise_settings.save()

        response = self.client.get("/gen/custom.js")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response["Content-Type"], "application/javascript; charset=utf-8"
        )
        self.assertIn(
            "window.customThemeReady = true;", response.content.decode("utf-8")
        )

    def test_custom_asset_views_return_404_when_empty(self):
        self.assertEqual(self.client.get("/gen/custom.css").status_code, 404)
        self.assertEqual(self.client.get("/gen/custom.js").status_code, 404)

    def test_hero_colours_require_hex_values(self):
        self.customise_settings.hero_background_color = "green"

        with self.assertRaises(ValidationError):
            self.customise_settings.full_clean()

    def test_base_template_only_includes_custom_assets_when_present(self):
        search_url = reverse("search")

        no_custom_response = self.client.get(search_url, data={"query": "service"})
        self.assertEqual(no_custom_response.status_code, 200)
        self.assertNotContains(no_custom_response, "/gen/custom.css")
        self.assertNotContains(no_custom_response, "/gen/custom.js")

        self.customise_settings.hero_background_color = "#001122"
        self.customise_settings.extra_js = "window.enableCustom = true;"
        self.customise_settings.save()

        custom_response = self.client.get(search_url, data={"query": "service"})
        self.assertEqual(custom_response.status_code, 200)
        self.assertContains(custom_response, "/gen/custom.css")
        self.assertContains(custom_response, "/gen/custom.js")

    def test_base_template_uses_embedded_govuk_logo_by_default(self):
        response = self.client.get(reverse("search"), data={"query": "service"})

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'aria-label="GOV.UK"')
        self.assertNotContains(response, '/static/crown.svg')

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
