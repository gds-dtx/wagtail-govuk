"""Rich text keeps the Design System's type scale on a framework site only.

main.js runs on every page of every service this image serves, and every
service renders the same ``.rich-text-content``. Giving an editor's headings,
paragraphs and lists the Design System's sizes is the Capability Framework's
house style (review round 2, T18 and T57); it is not a change other services
asked for. base.html marks the body on a framework instance and the script
reads the mark, so this is the boundary those two halves meet at.
"""

from django.test import TestCase, override_settings
from wagtail.models import Site

from govuk.models import ContentPage


def _flags(*, skills: bool) -> dict[str, bool]:
    return {
        "SKILLS": skills,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
    }


class RichTextTypeScaleScopeTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.page = self.site.root_page.add_child(
            instance=ContentPage(
                title="Guidance",
                slug="guidance",
                body="<h2>A heading</h2><p>Some prose.</p>",
            )
        )
        self.page.save_revision().publish()

    @override_settings(FEATURE_FLAGS=_flags(skills=True))
    def test_a_framework_instance_marks_the_body(self):
        response = self.client.get(self.page.url)

        self.assertContains(response, "data-rich-text-type-scale")

    @override_settings(FEATURE_FLAGS=_flags(skills=False))
    def test_a_service_without_the_framework_does_not(self):
        """The other services on this image render the same rich text and must
        keep the sizing they had before the framework was built."""
        response = self.client.get(self.page.url)

        self.assertNotContains(response, "data-rich-text-type-scale")


class RichTextTypeScaleScriptTests(TestCase):
    """The script half of the same boundary, read off the file.

    Nothing in the suite runs a browser, so these assert that the sizing is
    behind the mark and that ``govuk-list`` is not -- every service had that
    before and keeps it.
    """

    def setUp(self):
        from pathlib import Path

        self.js = (
            Path(__file__).resolve().parents[1] / "static" / "main.js"
        ).read_text()

    def test_the_sizing_reads_the_mark(self):
        self.assertIn(
            'const typeScale = document.body.hasAttribute("data-rich-text-type-scale")',
            self.js,
        )
        self.assertIn("if (!typeScale || el.className.trim())", self.js)
        self.assertIn(
            "const wrapperSize = typeScale ? wrapperTextSize(el) : null;", self.js
        )

    def test_lists_still_become_govuk_lists_everywhere(self):
        """Unconditional before the framework existed, and still unconditional."""
        self.assertIn(
            'el.classList.add("govuk-list", listModifier[el.tagName]);', self.js
        )
