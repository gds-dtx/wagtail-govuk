
from django.db import models
from modelcluster.fields import ParentalKey
from taggit.models import TagBase, TaggedItemBase
from wagtail.admin.panels import FieldPanel


class GovukTag(TagBase):
    """Tag dictionary entry where slug is the key and name is the display value."""

    def clean(self):
        super().clean()
        if self.slug:
            self.slug = self.slug.strip().lower()

    @property
    def key(self) -> str:
        return self.slug

    @property
    def value(self) -> str:
        return self.name

    class Meta:
        verbose_name = "Tag"
        verbose_name_plural = "Tags"
        ordering = ["slug"]




class ExternalContentItemTag(TaggedItemBase):
    content_object = ParentalKey(
        "govuk.ExternalContentItem",
        related_name="tagged_items",
        on_delete=models.CASCADE,
    )
    tag = models.ForeignKey(
        "govuk.GovukTag",
        related_name="external_content_item_tagged_items",
        on_delete=models.CASCADE,
    )

    panels = [
        FieldPanel("tag"),
    ]




class ContentPageTag(TaggedItemBase):
    content_object = ParentalKey(
        "govuk.ContentPage",
        related_name="tagged_items",
        on_delete=models.CASCADE,
    )
    tag = models.ForeignKey(
        "govuk.GovukTag",
        related_name="content_page_tagged_items",
        on_delete=models.CASCADE,
    )

    panels = [
        FieldPanel("tag"),
    ]


class FrameworkMainPageTag(TaggedItemBase):
    content_object = ParentalKey(
        "govuk.FrameworkMainPage",
        related_name="tagged_items",
        on_delete=models.CASCADE,
    )
    tag = models.ForeignKey(
        "govuk.GovukTag",
        related_name="framework_main_page_tagged_items",
        on_delete=models.CASCADE,
    )

    panels = [
        FieldPanel("tag"),
    ]


class FrameworkContentPageTag(TaggedItemBase):
    content_object = ParentalKey(
        "govuk.FrameworkContentPage",
        related_name="tagged_items",
        on_delete=models.CASCADE,
    )
    tag = models.ForeignKey(
        "govuk.GovukTag",
        related_name="framework_content_page_tagged_items",
        on_delete=models.CASCADE,
    )

    panels = [
        FieldPanel("tag"),
    ]


class SectionPageTag(TaggedItemBase):
    content_object = ParentalKey(
        "govuk.SectionPage",
        related_name="tagged_items",
        on_delete=models.CASCADE,
    )
    tag = models.ForeignKey(
        "govuk.GovukTag",
        related_name="section_page_tagged_items",
        on_delete=models.CASCADE,
    )

    panels = [
        FieldPanel("tag"),
    ]


class TagListingsPageTag(TaggedItemBase):
    content_object = ParentalKey(
        "govuk.TagListingsPage",
        related_name="tagged_items",
        on_delete=models.CASCADE,
    )
    tag = models.ForeignKey(
        "govuk.GovukTag",
        related_name="tag_listings_page_tagged_items",
        on_delete=models.CASCADE,
    )

    panels = [
        FieldPanel("tag"),
    ]




