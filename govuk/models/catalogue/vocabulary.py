"""The content catalogue's vocabularies: the values its entries are described by.

Each is a short, ordered list an editor manages under "Content catalogue" in
the admin, so a filter on the site only ever offers values that mean the same
thing on every entry. Topics are not one of these: they are the site's own tags
(``GovukTag``), so a catalogue entry's topics are the same tags content pages
use.
"""

from django.db import models
from wagtail.admin.panels import FieldPanel


class CatalogueLayout(models.TextChoices):
    """How a type of entry is laid out: which entry page type holds it."""

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
        help_text="Lower case and hyphens. Used in filter addresses and in the catalogue's export.",
    )
    label = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    sort_order = models.PositiveIntegerField(
        default=0, help_text="Listed in this order, lowest first, in filters and on group pages."
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
    """A kind of entry (standard, policy, guidance...), and the layout it uses."""

    plural = models.CharField(
        max_length=120,
        blank=True,
        help_text="The heading for a group page's section of these, such as 'Standards'.",
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
    """How widely an entry is meant to be used, from the public sector to one organisation."""

    class Meta(CatalogueVocabulary.Meta):
        verbose_name = "catalogue scope"


class CatalogueProfession(CatalogueVocabulary):
    """A profession an entry is for."""

    class Meta(CatalogueVocabulary.Meta):
        verbose_name = "catalogue profession"
