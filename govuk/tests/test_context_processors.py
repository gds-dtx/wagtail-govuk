import os
from unittest.mock import patch

from django.conf import settings
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from wagtail.models import Site

from govuk.context_processors import navigation_and_breadcrumbs
from govuk.models import ContentPage, FrameworkSkillsPage
from govuk.tests.helpers import feature_flags


class NavigationAndBreadcrumbsContextTests(SimpleTestCase):
    def _context(self):
        """The context for a request that matches no site."""
        request = RequestFactory().get("/")
        with patch("govuk.context_processors.Site.find_for_request", return_value=None) as find_for_request:
            context = navigation_and_breadcrumbs(request)
        find_for_request.assert_called_once_with(request)
        return context

    @override_settings(ADDITIONAL_CSS=[])
    def test_exposes_debug_version_and_settings_module(self):
        with patch.dict(os.environ, {"DJANGO_SETTINGS_MODULE": "govuk.settings.development"}, clear=False):
            context = self._context()

        self.assertEqual(context["app_debug"], settings.DEBUG)
        self.assertEqual(context["app_version"], settings.VERSION)
        self.assertEqual(context["additional_css"], [])
        self.assertEqual(context["noindex"], settings.NOINDEX)
        self.assertEqual(context["django_settings_module"], "govuk.settings.development")
        self.assertEqual(context["service_navigation_items"], [])
        self.assertEqual(context["breadcrumbs"], [])
        self.assertIsNone(context["phase_banner_settings"])
        self.assertIsNone(context["footer_settings"])
        self.assertIsNone(context["customise_settings"])

    def test_falls_back_to_loaded_settings_module_when_env_missing(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DJANGO_SETTINGS_MODULE", None)
            context = self._context()

        self.assertEqual(context["django_settings_module"], settings.SETTINGS_MODULE)

    def test_exposes_the_configured_settings(self):
        for setting, key, value in (
            ("ADDITIONAL_CSS", "additional_css", ["/static/cyber.css", "/static/other.css"]),
            ("NOINDEX", "noindex", False),
        ):
            with self.subTest(setting), override_settings(**{setting: value}):
                self.assertEqual(self._context()[key], value)


class ServiceNavigationWithoutTheFrameworkTests(TestCase):
    """The navigation menu is on every page of the site, including the errors.

    A framework page such as ``FrameworkSkillsPage`` can be a child of the site
    root with ``show_in_menus`` set (the page import carries the field along).
    It 404s on a site without the framework, so a header link to it would not work.
    """

    def setUp(self):
        root_page = Site.objects.get(is_default_site=True).root_page.specific
        for page in (
            ContentPage(title="Guidance", slug="guidance", body="", show_in_menus=True),
            FrameworkSkillsPage(title="Skills A-Z", slug="skills-az", show_in_menus=True),
        ):
            root_page.add_child(instance=page).save_revision().publish()

    def test_a_framework_page_is_a_navigation_item_only_with_the_framework(self):
        for skills, titles in ((True, ["Guidance", "Skills A-Z"]), (False, ["Guidance"])):
            with self.subTest(skills=skills), override_settings(FEATURE_FLAGS=feature_flags(skills=skills)):
                items = navigation_and_breadcrumbs(RequestFactory().get("/"))["service_navigation_items"]

                self.assertEqual([item["title"] for item in items], titles)
