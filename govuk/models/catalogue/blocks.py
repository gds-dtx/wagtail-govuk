"""Blocks for the content catalogue's pages.

Block type names must match ``govuk/catalogue/markdown_blocks.py`` (import/export).
"""

from wagtail import blocks

from ..blocks import (
    BodyRawHtmlBlock,
    BodyTextBlock,
    ContentBodyBlock,
    InsetTextBlock,
    SizedTableBlock,
)

# The default features plus inline code, or an editor's first save strips the import's <code>.
CATALOGUE_TEXT_FEATURES = [
    "h2", "h3", "h4", "bold", "italic", "code", "link", "ol", "ul", "hr",
    "line-break", "document-link", "image", "embed", "inset-text", "govuk-button",
]
# No headings: they belong to the page outline, not inside a component.
COMPONENT_RICH_TEXT_FEATURES = ["bold", "italic", "code", "link", "ul", "ol", "line-break"]


class CatalogueTextBlock(BodyTextBlock):
    def __init__(self, **kwargs):
        kwargs.setdefault("features", CATALOGUE_TEXT_FEATURES)
        super().__init__(**kwargs)


class WarningTextBlock(blocks.RichTextBlock):
    def __init__(self, **kwargs):
        kwargs.setdefault("features", COMPONENT_RICH_TEXT_FEATURES)
        super().__init__(**kwargs)

    class Meta:
        icon = "warning"
        label = "Warning text"
        template = "blocks/catalogue/warning_text.html"


class DetailsBlock(blocks.StructBlock):
    summary = blocks.CharBlock(max_length=200, help_text="The link text that opens it.")
    text = blocks.RichTextBlock(features=COMPONENT_RICH_TEXT_FEATURES)

    class Meta:
        icon = "collapse-down"
        label = "Details"
        template = "blocks/catalogue/details.html"


class CodeBlock(blocks.StructBlock):
    language = blocks.CharBlock(
        required=False, max_length=40, help_text="Optional, such as python or yaml."
    )
    code = blocks.TextBlock()

    class Meta:
        icon = "code"
        label = "Code"
        template = "blocks/catalogue/code.html"


class CatalogueBodyBlock(ContentBodyBlock):
    text = CatalogueTextBlock()
    inset_text = InsetTextBlock(features=COMPONENT_RICH_TEXT_FEATURES)
    warning_text = WarningTextBlock()
    details = DetailsBlock()
    code = CodeBlock()

    class Meta:
        required = False
        template = "blocks/content_body.html"


class SectionBodyBlock(blocks.StreamBlock):
    """A policy section's body: no cards or banners."""

    text = CatalogueTextBlock()
    table = SizedTableBlock()
    inset_text = InsetTextBlock(features=COMPONENT_RICH_TEXT_FEATURES)
    warning_text = WarningTextBlock()
    details = DetailsBlock()
    code = CodeBlock()
    raw_html = BodyRawHtmlBlock()

    class Meta:
        required = False
        template = "blocks/content_body.html"


class PolicySectionBlock(blocks.StructBlock):
    title = blocks.CharBlock(max_length=200)
    body = SectionBodyBlock()

    class Meta:
        icon = "list-ol"
        label = "Section"


class ToolLinkBlock(blocks.StructBlock):
    title = blocks.CharBlock(max_length=80)
    url = blocks.URLBlock()
    description = blocks.CharBlock(required=False, max_length=300)

    class Meta:
        icon = "link"
        label = "Link"
