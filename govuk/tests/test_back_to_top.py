"""The "Back to top" link, gated by a Customise setting.

Every site on the platform shares base.html, so the link is opt-in: off by
default, and switched on for the capability framework by
``import_capability_framework``. These tests cover the gate itself -- whether
the button is in the markup -- not the JavaScript that reveals it on scroll,
which lives in main.js.
"""

from django.test import TestCase, override_settings
from wagtail.models import Site

from govuk.models import ContentPage, CustomiseSettings


def _feature_flags() -> dict[str, bool]:
    return {
        "SKILLS": True,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
    }


@override_settings(FEATURE_FLAGS=_feature_flags())
class BackToTopGateTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        page = self.site.root_page.add_child(
            instance=ContentPage(
                title="Civil Service job grades",
                slug="job-grades",
                body="<p>The grades.</p>",
            )
        )
        page.save_revision().publish()
        self.url = "/job-grades/"

    def test_the_link_is_off_until_a_site_switches_it_on(self):
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'id="back-to-top"')

    def test_the_link_appears_once_a_site_switches_it_on(self):
        customise = CustomiseSettings.for_site(self.site)
        customise.show_back_to_top = True
        customise.save()

        response = self.client.get(self.url)

        self.assertContains(response, 'id="back-to-top"')
        self.assertContains(response, "Back to top")

    def test_the_setting_is_off_by_default(self):
        """A fresh site gets the link off; only the framework asks for it."""
        customise = CustomiseSettings.for_site(self.site)

        self.assertFalse(customise.show_back_to_top)
