from django.test import TestCase
from wagtail.models import Site

from govuk.models import (
    ContentPage,
    RolePage,
    SectionPage,
    SkillsAZPage,
    TagListingsPage,
)


class PageContentMetadataToggleTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific

    def test_show_page_content_metadata_defaults_to_false_on_relevant_page_models(self):
        for model in (
            ContentPage,
            SectionPage,
            TagListingsPage,
            RolePage,
            SkillsAZPage,
        ):
            self.assertFalse(
                model._meta.get_field("show_page_content_metadata").default
            )

    def test_content_page_metadata_is_hidden_by_default_and_can_be_enabled(self):
        page = self.root_page.add_child(
            instance=ContentPage(
                title="Metadata content page",
                slug="metadata-content-page",
                body="<p>Body</p>",
                author="Example Author",
                show_last_updated_date=True,
            )
        )
        page.save_revision().publish()

        response_without_metadata = self.client.get(page.url)
        self.assertEqual(response_without_metadata.status_code, 200)
        self.assertNotContains(response_without_metadata, "page-content-metadata")

        page.show_page_content_metadata = True
        page.save_revision().publish()

        response_with_metadata = self.client.get(page.url)
        self.assertEqual(response_with_metadata.status_code, 200)
        self.assertContains(response_with_metadata, "page-content-metadata")

    def test_section_page_metadata_is_hidden_by_default_and_can_be_enabled(self):
        page = self.root_page.add_child(
            instance=SectionPage(
                title="Metadata section page",
                slug="metadata-section-page",
                author="Example Author",
                show_last_updated_date=True,
                free_text="",
                rows=[],
            )
        )
        page.save_revision().publish()

        response_without_metadata = self.client.get(page.url)
        self.assertEqual(response_without_metadata.status_code, 200)
        self.assertNotContains(response_without_metadata, "page-content-metadata")

        page.show_page_content_metadata = True
        page.save_revision().publish()

        response_with_metadata = self.client.get(page.url)
        self.assertEqual(response_with_metadata.status_code, 200)
        self.assertContains(response_with_metadata, "page-content-metadata")
