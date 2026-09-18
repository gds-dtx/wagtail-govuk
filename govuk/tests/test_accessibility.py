"""Accessibility regression tests for the shared page baseline.

The service is built on the GOV.UK Design System, so most WCAG 2.2 AA
behaviour comes from Design System components. These tests lock down the
handful of page-level invariants that the base template is responsible for
and that a template change could silently break: a declared page language,
a working skip link, a single top-level heading, a named main landmark and
a non-empty document title. See docs/accessibility.md for the wider audit
context and documented Design System deviations.
"""

import re

from django.test import TestCase
from wagtail.models import Site

from govuk.models import ContentPage


class AccessibilityBaselineTests(TestCase):
    """Every rendered page inherits these WCAG-relevant guarantees."""

    def setUp(self):
        self.root_page = Site.objects.get(is_default_site=True).root_page.specific
        self.page = self.root_page.add_child(
            instance=ContentPage(
                title="Accessible page",
                slug="accessible-page",
                hero_title="Accessible page",
                body="<h2>A section</h2><p>Some body copy.</p>",
            )
        )
        self.page.save_revision().publish()
        self.html = self.client.get(self.page.url).content.decode()

    def test_html_declares_a_language(self):
        """WCAG 3.1.1 Language of Page."""
        self.assertIn('<html lang="en"', self.html)

    def test_skip_link_targets_the_main_landmark(self):
        """WCAG 2.4.1 Bypass Blocks: the skip link must point at real content."""
        self.assertIn('class="govuk-skip-link"', self.html)
        self.assertIn('href="#main-content"', self.html)
        self.assertIn('id="main-content"', self.html)

    def test_there_is_a_main_landmark(self):
        """WCAG 1.3.1 / 4.1.2: the main region is programmatically identifiable."""
        self.assertIn('role="main"', self.html)

    def test_there_is_exactly_one_top_level_heading(self):
        """WCAG 1.3.1: a single h1 anchors the heading structure."""
        self.assertEqual(len(re.findall(r"<h1\b", self.html)), 1)

    def test_the_document_has_a_non_empty_title(self):
        """WCAG 2.4.2 Page Titled."""
        match = re.search(r"<title>(.*?)</title>", self.html, re.DOTALL)
        self.assertIsNotNone(match)
        self.assertTrue(match.group(1).strip())
