"""The head and body chrome every page inherits from base.html.

This is a public repository and the base template is the first file anyone
reuses, so the example attributes that ship with the GOV.UK Frontend template
docs -- `data-test="My value"` and a `theme-color` of the CSS named colour
blue -- should not be shipping on all seventy pages of the service.
"""

from django.test import TestCase
from wagtail.models import Site

from govuk.models import ContentPage


class BaseTemplateChromeTests(TestCase):
    def setUp(self):
        root_page = Site.objects.get(is_default_site=True).root_page.specific
        page = root_page.add_child(
            instance=ContentPage(
                title="Chrome",
                slug="chrome",
                hero_title="Chrome",
                body="<p>Some body copy.</p>",
            )
        )
        page.save_revision().publish()
        self.html = self.client.get(page.url).content.decode()

    def test_theme_colour_is_the_design_system_black(self):
        self.assertIn('<meta name="theme-color" content="#0b0c0c">', self.html)

    def test_no_element_is_tinted_the_css_named_blue(self):
        """`blue` is #0000FF, which is in no GOV.UK palette."""
        self.assertNotIn('"blue"', self.html)

    def test_the_body_carries_no_example_data_attributes(self):
        self.assertNotIn("data-test=", self.html)
        self.assertNotIn("data-other=", self.html)
