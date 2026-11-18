"""The content catalogue's vocabularies, managed under "Content catalogue" in the admin.

Topics are not one of these: they are the site's own ``GovukTag``s.
"""

from django.db import models
from wagtail.admin.panels import FieldPanel

# Not a type's layout: a generic page takes a type of any layout, so the
# catalogue format records it on the entry (``layout: generic``).
GENERIC_LAYOUT = "generic"


class CatalogueLayout(models.TextChoices):
    """Which entry page type holds a type of entry."""

    REQUIREMENTS = "requirements", "Requirements (numbered points)"
    POLICY = "policy", "Policy (numbered sections)"
    GUIDE = "guide", "Guide (a handbook of pages)"
    STEPS = "steps", "Steps (a numbered method)"
    TOOL = "tool", "Tool (links to use it)"
    MEDIA = "media", "Media (not listed)"


class CatalogueVocabulary(models.Model):
    key = models.SlugField(
        max_length=60,
        unique=True,
        help_text="Lower case and hyphens. Used in filter URLs and the export.",
    )
    label = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    sort_order = models.PositiveIntegerField(
        default=0, help_text="Lowest first, in filters and on group pages."
    )

    panels = [
        FieldPanel("label"),
        FieldPanel("key"),
        FieldPanel("description"),
        FieldPanel("sort_order"),
    ]

    class Meta:
        abstract = True
        ordering = ["sort_order", "label"]

    def __str__(self):
        return self.label


class CatalogueType(CatalogueVocabulary):
    """A kind of entry (standard, policy, guidance...) and its layout."""

    plural = models.CharField(
        max_length=120,
        blank=True,
        help_text="Heading on group pages, such as 'Standards'.",
    )
    layout = models.CharField(max_length=20, choices=CatalogueLayout.choices)

    panels = [
        FieldPanel("label"),
        FieldPanel("plural"),
        FieldPanel("key"),
        FieldPanel("layout"),
        FieldPanel("description"),
        FieldPanel("sort_order"),
    ]

    class Meta(CatalogueVocabulary.Meta):
        verbose_name = "catalogue type"

    @property
    def heading(self) -> str:
        return self.plural or self.label


class CatalogueScope(CatalogueVocabulary):
    """How widely an entry applies, from the public sector to one organisation."""

    class Meta(CatalogueVocabulary.Meta):
        verbose_name = "catalogue scope"


class CatalogueProfession(CatalogueVocabulary):
    class Meta(CatalogueVocabulary.Meta):
        verbose_name = "catalogue profession"
