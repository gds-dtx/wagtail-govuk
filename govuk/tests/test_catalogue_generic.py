"""The generic entry layout: an entry of any type, around a body laid out as a content page's is."""

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from wagtail.models import Site

from govuk.catalogue.catalogue import read_catalogue
from govuk.catalogue.export_catalogue import export_catalogue
from govuk.catalogue.import_catalogue import import_catalogue
from govuk.models import (
    CatalogueScope,
    CatalogueType,
    ContentEntryGenericPage,
    ContentEntryGuidePage,
    ContentGroupPage,
)
from govuk.tests.catalogue_helpers import (
    _dedent,
    _entry,
    catalogue_files,
    guidance_flags,
    import_fixture,
    make_content_main_page,
)

BODY = [
    {"type": "text", "value": "<h2>Why it exists</h2><p>Opening text.</p>"},
    {"type": "banner", "value": {"heading": "Join the community", "style": "light", "text": "<p>Every month.</p>"}},
    {"type": "warning_text", "value": "<p>Mind the gap.</p>"},
    {"type": "raw_html", "value": '<p><img src="https://assets.example.gov.uk/a.png" alt=""></p>'},
]


def _generic_files():
    """The fixture catalogue, with a product laid out generically, held here."""
    files = catalogue_files()
    files["entries/a-model.yml"] = _entry(
        "a-model",
        type="product",
        layout="generic",
        source="\n  type: hosted\n  name: tech.gov.uk\n  path: content/a-model",
    )
    files["content/a-model/guidance.yml"] = _dedent(
        """
        schema: guidance-as-code/v0
        title: A model
        content_dir: .
        index: index.md
        """
    )
    files["content/a-model/index.md"] = _dedent(
        """
        ---
        title: A model
        ---

        # A model

        Everything in one place.

        > [!WARNING]
        > Some of it is old.
        """
    )
    files["content/a-model/stray.md"] = b"# Stray\n"
    return files


@override_settings(FEATURE_FLAGS=guidance_flags())
class GenericEntryTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.main = make_content_main_page(self.site.root_page)
        import_fixture(self.main)
        self.group = ContentGroupPage.objects.get(slug="resources-and-tools")
        # A product: its type's own layout is the tool layout.
        self.type = CatalogueType.objects.get(key="product")
        self.page = self.group.add_child(
            instance=ContentEntryGenericPage(
                title="Local architecture model",
                slug="architecture-model",
                entry_type=self.type,
                scope=CatalogueScope.objects.get(key="public-sector"),
                short_description="The technology layers local services are built on.",
                long_description="A guide to the public technology layers that deliver local services.",
                owner_organisation="Government Digital Service",
                body=BODY,
            )
        )
        self.page.save_revision().publish()

    def get(self, page=None):
        return self.client.get((page or self.page).url)

    def test_any_type_fits_it(self):
        for key in ("product", "guidance", "standard", "policy"):
            with self.subTest(type=key):
                self.page.entry_type = CatalogueType.objects.get(key=key)
                self.page.clean()
        self.assertEqual(ContentEntryGenericPage.allowed_subpage_models(), [])
        self.assertIn(ContentEntryGenericPage, ContentGroupPage.allowed_subpage_models())

    def test_the_add_view_offers_every_type(self):
        admin = get_user_model().objects.create_superuser("admin", "a@example.gov.uk", "x")
        self.client.force_login(admin)
        response = self.client.get(
            reverse("wagtailadmin_pages:add", args=["govuk", "contententrygenericpage", self.group.pk])
        )
        for entry_type in CatalogueType.objects.all():
            self.assertContains(response, f'<option value="{entry_type.pk}">{entry_type.label}</option>', html=False)
        self.assertContains(response, "Any type: this page lays it out freely")

    def test_it_has_the_entrys_heading_and_details(self):
        response = self.get()
        self.assertTemplateUsed(response, "catalogue/layouts/generic.html")
        self.assertContains(response, '<h1 class="govuk-heading-xl">Local architecture model</h1>', html=False)
        self.assertContains(response, "A guide to the public technology layers that deliver local services.")
        self.assertContains(response, "About this product")
        # Free-form: no masthead or contents list, whatever its type's layout.
        self.assertNotContains(response, "app-catalogue-masthead")
        self.assertIsNone(response.context.get("contents"))

    def test_its_body_is_laid_out_as_a_content_pages(self):
        response = self.get()
        self.assertContains(response, "body-banner--light body-band", html=False)
        self.assertContains(response, '<div class="govuk-width-container body-segment">', html=False)
        self.assertContains(response, "Opening text.")
        self.assertContains(response, 'class="govuk-warning-text"')
        # The same blocks in a guide stay in its column.
        guide = ContentEntryGuidePage.objects.get(slug="repo-guide")
        guide.body = BODY
        guide.save_revision().publish()
        self.assertContains(self.get(guide), "body-panel")

    def test_its_images_widen_its_content_security_policy(self):
        self.assertIn("https://assets.example.gov.uk", self.get().headers["Content-Security-Policy"])

    def test_it_is_listed_and_searched_as_its_type(self):
        sections = self.client.get(self.group.url).context["type_sections"]
        products = next(s for s in sections if s["title"] == "Products")
        self.assertIn("Local architecture model", [c["title"] for c in products["cards"]])
        items = self.client.get("/search/suggest/", {"q": "architecture model"}).json()
        self.assertEqual(
            items[0], {"text": "Local architecture model", "link": self.page.url, "type": "Product"}
        )


@override_settings(FEATURE_FLAGS=guidance_flags())
class GenericImportExportTests(TestCase):
    def setUp(self):
        self.root = Site.objects.get(is_default_site=True).root_page
        self.main = make_content_main_page(self.root)
        self.result = import_fixture(self.main, _generic_files())

    def test_an_entrys_own_layout_makes_it_a_generic_page(self):
        page = ContentEntryGenericPage.objects.get(slug="a-model")
        self.assertEqual(page.entry_type.key, "product")
        self.assertEqual(page.get_parent().slug, "resources-and-tools")
        self.assertEqual([b.block_type for b in page.body], ["text", "warning_text"])
        self.assertFalse(page.get_children().exists())
        self.assertIn("a-model: a generic entry is one page; left out stray.md", self.result.warnings)

    def test_it_exports_with_its_layout_and_imports_again(self):
        exported = export_catalogue(self.main)
        self.assertIn("layout: generic", exported.files["entries/a-model.yml"])
        self.assertNotIn("layout: generic", exported.files["entries/hosted-standard.yml"])
        self.assertIn("Everything in one place.", exported.files["content/a-model/index.md"])
        other = make_content_main_page(self.root, title="Other", slug="other")
        result = import_catalogue(other, {name: text.encode() for name, text in exported.files.items()})
        self.assertEqual(result.warnings, [])
        again = ContentEntryGenericPage.objects.child_of(
            ContentGroupPage.objects.child_of(other).get(slug="resources-and-tools")
        ).get()
        self.assertEqual(again.body.raw_data[0]["value"], "<p>Everything in one place.</p>")


class GenericLayoutReadingTests(TestCase):
    def _warnings(self, **fields):
        files = catalogue_files()
        files["entries/odd.yml"] = _entry("odd", **fields)
        return read_catalogue(files).warnings

    def test_only_generic_and_only_with_a_page(self):
        external = "\n  type: external\n  name: GOV.UK\n  path: https://www.gov.uk/odd"
        self.assertTrue(any(
            "odd" in w and "external entry has no page" in w
            for w in self._warnings(type="guidance", layout="generic", source=external)
        ))
        self.assertTrue(any(
            "odd" in w and "layout 'guide' must be generic" in w
            for w in self._warnings(type="guidance", layout="guide", source=external)
        ))
