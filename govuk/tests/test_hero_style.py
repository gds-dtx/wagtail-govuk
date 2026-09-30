from django.test import SimpleTestCase, TestCase
from wagtail.models import Site

from govuk.models import ContentPage, SectionPage
from govuk.models.pages import HeroStyle, HeroStyleMixin


class HeroStyleMixinPropertyTests(SimpleTestCase):
    """The base.html conditionals read these properties off the page."""

    def test_each_property_answers_for_its_style_only(self):
        cases = {
            HeroStyle.HIDDEN: (False, False, False, False),
            HeroStyle.NONE: (False, False, False, False),
            HeroStyle.LARGE_TEXT: (True, False, False, False),
            HeroStyle.STYLED: (False, True, False, True),
            HeroStyle.COMBINED: (False, False, True, True),
        }
        for style, expected in cases.items():
            page = ContentPage(title="x", hero_style=style)
            got = (
                page.hero_is_large_text,
                page.hero_is_styled,
                page.hero_is_combined,
                page.hero_uses_masthead,
            )
            self.assertEqual(got, expected, style)

    def test_the_mixin_is_abstract(self):
        self.assertTrue(HeroStyleMixin._meta.abstract)

    def test_the_default_is_no_styling(self):
        self.assertEqual(ContentPage().hero_style, HeroStyle.NONE)


class HeroStyleRenderingTests(TestCase):
    """Each hero_style produces its own top-of-page markup in base.html."""

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root = self.site.root_page.specific

    def _render(self, style, **kwargs):
        page = self.root.add_child(
            instance=ContentPage(
                title="Hero page",
                slug=f"hero-{style}",
                hero_style=style,
                **kwargs,
            )
        )
        page.save_revision().publish()
        return self.client.get(page.url)

    def test_combined_renders_the_combined_masthead_and_inverse_nav(self):
        response = self._render(HeroStyle.COMBINED)
        self.assertContains(response, "masthead--combined")
        self.assertContains(response, "govuk-service-navigation--inverse")

    def test_styled_renders_a_plain_masthead_band(self):
        response = self._render(HeroStyle.STYLED)
        self.assertContains(response, "masthead")
        self.assertNotContains(response, "masthead--combined")
        self.assertNotContains(response, "govuk-service-navigation--inverse")

    def test_large_text_renders_an_in_column_heading_not_a_band(self):
        response = self._render(
            HeroStyle.LARGE_TEXT, hero_intro="<p>Big intro</p>"
        )
        self.assertContains(response, "govuk-heading-xl")
        self.assertContains(response, "Big intro")
        self.assertNotContains(response, "masthead")
        self.assertNotContains(response, "hero__title")

    def test_no_styling_renders_the_plain_hero(self):
        response = self._render(HeroStyle.NONE)
        self.assertContains(response, "hero__title")
        self.assertNotContains(response, "masthead")

    def test_hidden_keeps_a_visually_hidden_h1_but_no_visible_hero(self):
        response = self._render(HeroStyle.HIDDEN)
        # The H1 stays in the document for structure/accessibility, visually hidden.
        self.assertContains(
            response, '<h1 class="govuk-visually-hidden">Hero page</h1>'
        )
        # ...but no visible hero band, styled title or large-text heading.
        self.assertNotContains(response, "hero__title")
        self.assertNotContains(response, "masthead")
        self.assertNotContains(response, "govuk-heading-xl")

    def test_section_page_also_honours_the_dropdown(self):
        page = self.root.add_child(
            instance=SectionPage(
                title="A section", slug="a-section", hero_style=HeroStyle.STYLED
            )
        )
        page.save_revision().publish()
        response = self.client.get(page.url)
        self.assertContains(response, "masthead")
        self.assertNotContains(response, "masthead--combined")
