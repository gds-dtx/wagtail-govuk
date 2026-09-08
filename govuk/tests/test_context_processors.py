import os
from unittest.mock import patch

from django.conf import settings
from django.test import (
    RequestFactory,
    SimpleTestCase,
    TestCase,
    override_settings,
)
from wagtail.models import Site

from govuk.context_processors import navigation_and_breadcrumbs
from govuk.models import ContentPage, RolePage


def _feature_flags(*, skills_enabled: bool) -> dict[str, bool]:
    return {
        "SKILLS": skills_enabled,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
    }


class NavigationAndBreadcrumbsContextTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

    @override_settings(ADDITIONAL_CSS=[])
    @patch("govuk.context_processors.Site.find_for_request", return_value=None)
    def test_exposes_debug_version_and_settings_module(self, mocked_find_for_request):
        request = self.factory.get("/")

        with patch.dict(
            os.environ,
            {"DJANGO_SETTINGS_MODULE": "govuk.settings.local"},
            clear=False,
        ):
            context = navigation_and_breadcrumbs(request)

        self.assertEqual(context["app_debug"], settings.DEBUG)
        self.assertEqual(context["app_version"], settings.VERSION)
        self.assertEqual(context["additional_css"], [])
        self.assertEqual(context["noindex"], settings.NOINDEX)
        self.assertEqual(context["django_settings_module"], "govuk.settings.local")
        self.assertEqual(context["service_navigation_items"], [])
        self.assertEqual(context["breadcrumbs"], [])
        self.assertIsNone(context["phase_banner_settings"])
        self.assertIsNone(context["footer_settings"])
        self.assertIsNone(context["customise_settings"])
        mocked_find_for_request.assert_called_once_with(request)

    @patch("govuk.context_processors.Site.find_for_request", return_value=None)
    def test_falls_back_to_loaded_settings_module_when_env_missing(
        self, mocked_find_for_request
    ):
        request = self.factory.get("/")

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DJANGO_SETTINGS_MODULE", None)
            context = navigation_and_breadcrumbs(request)

        self.assertEqual(context["django_settings_module"], settings.SETTINGS_MODULE)
        mocked_find_for_request.assert_called_once_with(request)

    @override_settings(ADDITIONAL_CSS=["/static/cyber.css", "/static/other.css"])
    @patch("govuk.context_processors.Site.find_for_request", return_value=None)
    def test_exposes_additional_css_when_configured(self, mocked_find_for_request):
        request = self.factory.get("/")

        context = navigation_and_breadcrumbs(request)

        self.assertEqual(
            context["additional_css"], ["/static/cyber.css", "/static/other.css"]
        )
        mocked_find_for_request.assert_called_once_with(request)

    @override_settings(NOINDEX=False)
    @patch("govuk.context_processors.Site.find_for_request", return_value=None)
    def test_exposes_noindex_setting_value(self, mocked_find_for_request):
        request = self.factory.get("/")

        context = navigation_and_breadcrumbs(request)

        self.assertFalse(context["noindex"])
        mocked_find_for_request.assert_called_once_with(request)


class ServiceNavigationWithoutTheFrameworkTests(TestCase):
    """The navigation menu is on every page of the site, including the errors.

    A ``RolePage`` can be a child of the site root with ``show_in_menus`` set:
    the page import carries the field along with the rest of the payload. It
    404s when fetched, so a link to it in the header is a link that does not
    work, in the one place a reader sees on every page.
    """

    def setUp(self):
        self.factory = RequestFactory()
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific

        self.content_page = self.root_page.add_child(
            instance=ContentPage(
                title="Guidance",
                slug="guidance",
                body="",
                show_in_menus=True,
            )
        )
        self.content_page.save_revision().publish()

        self.role_page = self.root_page.add_child(
            instance=RolePage(
                title="Data analyst",
                slug="data-analyst",
                body="",
                show_in_menus=True,
            )
        )
        self.role_page.save_revision().publish()

    def _menu_titles(self):
        return [
            item["title"]
            for item in navigation_and_breadcrumbs(self.factory.get("/"))[
                "service_navigation_items"
            ]
        ]

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=True))
    def test_the_framework_site_still_gets_the_whole_menu(self):
        self.assertEqual(self._menu_titles(), ["Guidance", "Data analyst"])

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=False))
    def test_a_role_page_is_not_a_navigation_item(self):
        titles = self._menu_titles()

        self.assertNotIn("Data analyst", titles)
        self.assertEqual(titles, ["Guidance"])
