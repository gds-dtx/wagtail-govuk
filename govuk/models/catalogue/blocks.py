"""Blocks for the content catalogue's pages.

An entry's body is a content page's body (text, tables, cards, banners, inset
text, raw HTML) plus three components guidance often needs: warning text,
details, and code. A policy's sections, and a tool's links, are blocks too.

These are also what the catalogue's import converts Markdown into, and what
its export converts back; ``govuk/catalogue/markdown_blocks.py`` keeps the
block type names in step.
"""

from wagtail import blocks

from ..blocks import (
    BodyRawHtmlBlock,
    BodyTextBlock,
    ContentBodyBlock,
    InsetTextBlock,
    SizedTableBlock,
)

# A content page's text (the site's default rich text features), with inline
# code, which technical guidance is full of: without it an editor's first save
# would strip every ``<code>`` the import made.
CATALOGUE_TEXT_FEATURES = [
    "h2", "h3", "h4", "bold", "italic", "code", "link", "ol", "ul", "hr",
    "line-break", "document-link", "image", "embed", "inset-text", "govuk-button",
]
# The rich text a component's own text allows: no headings, which belong to the
# page's outline rather than inside a warning or a details component.
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
    """A content page's body blocks, and the catalogue's own components."""

    text = CatalogueTextBlock()
    inset_text = InsetTextBlock(features=COMPONENT_RICH_TEXT_FEATURES)
    warning_text = WarningTextBlock()
    details = DetailsBlock()
    code = CodeBlock()

    class Meta:
        required = False
        template = "blocks/content_body.html"


class SectionBodyBlock(blocks.StreamBlock):
    """What a policy section holds: prose and components, without cards or banners."""

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
    """One numbered section of a policy, shown as a section of its accordion."""

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
