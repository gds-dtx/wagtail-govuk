import os
from unittest.mock import patch

from django.conf import settings
from django.test import RequestFactory, SimpleTestCase

from govuk.context_processors import navigation_and_breadcrumbs


class NavigationAndBreadcrumbsContextTests(SimpleTestCase):
    def setUp(self):
        self.factory = RequestFactory()

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
