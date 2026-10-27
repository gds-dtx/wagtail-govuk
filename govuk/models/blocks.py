
from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.utils.safestring import mark_safe
from wagtail import blocks
from wagtail.blocks import (
    ListBlockValidationError,
    StructBlockValidationError,
    StructValue,
)
from wagtail.contrib.table_block.blocks import DEFAULT_TABLE_OPTIONS, TableBlock
from wagtail.images.blocks import ImageChooserBlock
from wagtail.snippets.blocks import SnippetChooserBlock

from .constants import FRAMEWORK_WELCOME_RICH_TEXT_FEATURES
from .raw_html import sanitise_raw_html

# The name a row of cards is stored under in a content page's body.
CARD_ROW_BLOCK_TYPE = "cards"


class LinkStructValue(StructValue):
    @property
    def url(self):
        external_url = (self.get("external_url") or "").strip()
        if external_url:
            return external_url
        page = self.get("page")
        return (page.url or "") if page else ""


class LinkBlock(blocks.StructBlock):
    title = blocks.CharBlock(
        required=True,
        max_length=120,
    )
    page = blocks.PageChooserBlock(
        required=False,
    )
    external_url = blocks.URLBlock(
        required=False,
        help_text="Use an absolute external URL like https://www.gov.uk/help.",
    )

    class Meta:
        icon = "link"
        label = "Link"
        value_class = LinkStructValue



class InsetTextBlock(blocks.RichTextBlock):
    class Meta:
        icon = "warning"
        label = "Inset text"
        template = "blocks/framework_welcome_inset_text.html"


class GovukTableBlock(TableBlock):
    """A table an editor builds in a grid, rendered as a GOV.UK table.

    Wagtail's rich text has no table feature, so before this the only way to
    put one on a content page was to hand-write the HTML into a raw HTML
    embed. That is not a formatting option a content designer has.

    Cells hold text, not HTML: the renderer stays on Wagtail's default, so
    whatever is typed is escaped. A table is a place a paste from a document
    would otherwise carry markup straight onto a public page.

    The grid's menu also aligns cells and merges them. Both are stored per
    cell in the block's value (``cell`` and ``mergeCells``), which Wagtail
    keeps already; the template turns them into GOV.UK markup. The alignment
    menu offers vertical alignment too -- it is the grid's own submenu, which
    plain options cannot trim -- but only the horizontal choice is rendered.
    """

    def __init__(self, *args, table_options=None, **kwargs):
        # Wagtail hands handsontable whatever LANGUAGE_CODE is, and ours is
        # en-gb. The vendored handsontable 6.2.2 ships one locale, en-US, so
        # it logs a console error and falls back to it anyway. The strings
        # this picks are the grid's own context menu ("Insert row above"),
        # where the two spellings do not differ. Naming the locale it has
        # keeps the admin console clean.
        table_options = {
            "language": "en-US",
            "contextMenu": [
                *DEFAULT_TABLE_OPTIONS["contextMenu"],
                "---------",
                "alignment",
                "mergeCells",
            ],
            "mergeCells": True,
            **(table_options or {}),
        }
        super().__init__(*args, table_options=table_options, **kwargs)

    class Meta:
        icon = "table"
        label = "Table"
        template = "blocks/govuk_table.html"
        help_text = (
            "Right-click a cell to add or remove rows and columns, align its "
            "text, or merge it with the cells next to it. Give the table a "
            "caption: it is how somebody using a screen reader knows what the "
            "table is before reading it."
        )


# The widths a table column can be given, each a fraction of the table. Not the
# Design System's width classes: those go to the full width below the tablet
# breakpoint, which on a table's columns asks every sized one for the whole
# table. These hold at every width, and a table too wide for the screen scrolls.
TABLE_COLUMN_WIDTHS = {
    "one-quarter": ("One quarter", 1 / 4),
    "one-third": ("One third", 1 / 3),
    "one-half": ("One half", 1 / 2),
    "two-thirds": ("Two thirds", 2 / 3),
    "three-quarters": ("Three quarters", 3 / 4),
}


def _table_column_count(table_value) -> int:
    data = (table_value or {}).get("data") or []
    return len(data[0]) if data and data[0] else 0


class SizedTableBlock(blocks.StructBlock):
    """A table, with an optional width for each of its columns.

    The grid stores only what its own editor saves, so the widths sit beside it
    as a list, one per column from the left. A column without one shares out
    what the others leave.

    A table stored before the widths existed is the grid's value on its own;
    it is read as a table with no widths, and stored in this shape the next
    time its page is saved.
    """

    table = GovukTableBlock()
    column_widths = blocks.ListBlock(
        blocks.ChoiceBlock(
            choices=[
                ("auto", "Automatic"),
                *((key, label) for key, (label, _) in TABLE_COLUMN_WIDTHS.items()),
            ],
            default="auto",
            label="Width",
        ),
        default=[],
        required=False,
        label="Column widths",
        help_text=(
            "Optional. One width for each column, from the left. A column "
            "without one, or set to Automatic, shares out what is left."
        ),
    )

    class Meta:
        icon = "table"
        label = "Table"
        template = "blocks/sized_table.html"

    @staticmethod
    def _with_widths(raw):
        if isinstance(raw, dict) and "table" not in raw and "data" in raw:
            return {"table": raw, "column_widths": []}
        return raw

    def to_python(self, value):
        return super().to_python(self._with_widths(value))

    def bulk_to_python(self, values):
        return super().bulk_to_python([self._with_widths(value) for value in values])

    def clean(self, value):
        value = super().clean(value)
        widths = list(value.get("column_widths") or [])
        columns = _table_column_count(value.get("table"))
        if columns and len(widths) > columns:
            raise StructBlockValidationError(
                block_errors={
                    "column_widths": ListBlockValidationError(
                        non_block_errors=[
                            ValidationError(
                                f"The table has {columns} "
                                f"column{'s' if columns != 1 else ''}, so give "
                                f"no more than {columns} "
                                f"width{'s' if columns != 1 else ''}."
                            )
                        ]
                    )
                }
            )
        total = sum(TABLE_COLUMN_WIDTHS[width][1] for width in widths if width in TABLE_COLUMN_WIDTHS)
        if total > 1 + 1e-9:
            raise StructBlockValidationError(
                block_errors={
                    "column_widths": ListBlockValidationError(
                        non_block_errors=[
                            ValidationError(
                                "These widths add up to more than the whole table."
                            )
                        ]
                    )
                }
            )
        return value

    def get_context(self, value, parent_context=None):
        context = super().get_context(value, parent_context=parent_context)
        # A class per column.
        # A page import writes stream data without running clean, so
        # neither the width nor the number of them can be trusted.
        columns = _table_column_count(value.get("table"))
        widths = list(value.get("column_widths") or [])[:columns]
        column_classes = [
            f"app-table-col--{width}" if width in TABLE_COLUMN_WIDTHS else ""
            for width in widths
        ]
        context["column_classes"] = column_classes if any(column_classes) else []
        return context


class BodyRawHtmlBlock(blocks.RawHTMLBlock):
    """HTML written by hand, for markup the other blocks cannot make.

    The one place an editor can write HTML: rich text has no raw HTML feature.
    It is cleaned to an allowlist (``govuk.models.raw_html``) when it is saved,
    so the editor sees what will be published, and again when it is rendered,
    since an import writes stream data without running ``clean``. A fragment
    that is a table is given the scrollable region a table block has (see
    ``raw_html_block`` in ``govuk_filters``). It sits in the text column, where
    main.js gives its links and lists the GOV.UK classes.
    """

    class Meta:
        icon = "code"
        label = "Raw HTML"
        template = "blocks/raw_html.html"
        help_text = (
            "For markup the other blocks cannot make. Scripts, styles, forms, "
            "iframes and anything else that is not page content are removed "
            "when you save. Style it with classes from Settings > Customise > "
            "Extra CSS."
        )

    def clean(self, value):
        return mark_safe(sanitise_raw_html(str(super().clean(value) or "")))


class BodyTextBlock(blocks.RichTextBlock):
    """Rich text in a page body.

    No features argument, so this offers exactly what the rich text body field
    it replaced did -- the default set, which the rich text hooks extend with
    the GOV.UK button, start button, inset text and line breaks. The template runs
    it through ``page_body``, so a link to a framework CSV becomes an
    attachment.
    """

    class Meta:
        icon = "pilcrow"
        label = "Text"
        template = "blocks/text.html"


class CardBlock(blocks.StructBlock):
    """One card in a row of cards.

    The children, their names and their order are the ones section pages'
    rows had, which migration 0089 moved into content page bodies unchanged.
    """

    title = blocks.CharBlock(
        required=True,
        max_length=120,
    )
    image = ImageChooserBlock(
        required=False,
        help_text="Optional header image for this card.",
    )
    image_fit = blocks.ChoiceBlock(
        choices=[
            ("cover", "Cover"),
            ("contain", "Contain"),
        ],
        default="cover",
        required=True,
        help_text="How the header image should fit inside the card.",
    )
    text = blocks.RichTextBlock(
        required=False,
        features=["bold", "italic", "link", "ul", "ol"],
    )
    link = LinkBlock(
        required=False,
    )
    tags = blocks.ListBlock(
        SnippetChooserBlock("govuk.GovukTag", required=False),
        required=False,
        help_text="Optional tags for this card.",
    )

    class Meta:
        icon = "doc-full"
        label = "Card"


class CardRowBlock(blocks.StructBlock):
    """A row of cards under an optional heading.

    A block a content page body can hold between its text. Which cards show
    is not always the row's to say: a page with a tag filter passes the cards
    the filter left in as ``visible_cards``.
    """

    heading = blocks.CharBlock(
        required=False,
        help_text="Optional heading for this row section.",
    )
    cards = blocks.ListBlock(
        CardBlock(),
        min_num=1,
        max_num=60,
        help_text="Add between 1 and 60 cards in this row.",
    )

    # Spans the page rather than the text column where the page has no
    # sidebar. See body_layout.
    full_width = True

    class Meta:
        icon = "placeholder"
        label = "Row section"
        template = "blocks/card_row.html"

    def get_context(self, value, parent_context=None):
        context = super().get_context(value, parent_context=parent_context)
        visible_cards = context.get("visible_cards")
        context["heading"] = value.get("heading")
        context["cards"] = (
            value.get("cards") if visible_cards is None else visible_cards
        )
        return context


class BannerStructValue(StructValue):
    @property
    def button_url(self):
        external_url = (self.get("button_external_url") or "").strip()
        if external_url:
            return external_url
        page = self.get("button_page")
        return (page.url or "") if page else ""


class BannerBlock(blocks.StructBlock):
    """A coloured band with a heading, a line or two of text and a button.

    The button is optional, so its text and its destination are separate
    optional fields rather than a LinkBlock, whose title is always required.
    ``clean`` asks for both or neither.
    """

    heading = blocks.CharBlock(
        required=True,
        max_length=120,
    )
    text = blocks.RichTextBlock(
        required=False,
        features=["bold", "italic", "link"],
    )
    button_text = blocks.CharBlock(
        required=False,
        max_length=60,
        help_text="Optional. Leave blank for a banner without a button.",
    )
    button_page = blocks.PageChooserBlock(
        required=False,
    )
    button_external_url = blocks.URLBlock(
        required=False,
        help_text="Use an absolute external URL like https://www.gov.uk/help.",
    )
    style = blocks.ChoiceBlock(
        choices=[
            ("brand", "Brand colour"),
            ("light", "Light"),
        ],
        default="brand",
        required=True,
    )

    # See CardRowBlock.full_width.
    full_width = True

    class Meta:
        icon = "pick"
        label = "Banner"
        template = "blocks/banner.html"
        value_class = BannerStructValue

    def clean(self, value):
        value = super().clean(value)
        has_text = bool((value.get("button_text") or "").strip())
        has_destination = bool(
            value.get("button_page") or (value.get("button_external_url") or "").strip()
        )
        if has_text and not has_destination:
            raise StructBlockValidationError(
                block_errors={
                    "button_page": ValidationError(
                        "Choose a page or enter a URL for the button."
                    )
                }
            )
        if has_destination and not has_text:
            raise StructBlockValidationError(
                block_errors={
                    "button_text": ValidationError(
                        "Give the button some text, or remove where it goes."
                    )
                }
            )
        return value


class ContentBodyBlock(blocks.StreamBlock):
    """Prose, tables, cards and banners, in whatever order the page needs them.

    The whole of a content page's body. The block type names are stored with
    each block, so renaming one means migrating every page that uses it.
    """

    text = BodyTextBlock()
    table = SizedTableBlock()
    cards = CardRowBlock(label="Cards")
    banner = BannerBlock()
    inset_text = InsetTextBlock(features=FRAMEWORK_WELCOME_RICH_TEXT_FEATURES)
    raw_html = BodyRawHtmlBlock()

    class Meta:
        required = False
        template = "blocks/content_body.html"


@dataclass
class BodySegment:
    """A stretch of a page body laid out the same way.

    Either one full-width block, or a run of blocks that share the text column.
    Each item is ``{"block": <stream child>, "cards": <visible cards or None>,
    "first_cards": bool}``.
    """

    full_width: bool
    items: list[dict] = field(default_factory=list)


@dataclass
class BodyLayout:
    segments: list[BodySegment]

    @property
    def full_width(self) -> bool:
        return any(segment.full_width for segment in self.segments)


def body_layout(stream, *, allow_full_width: bool, visible_cards=None) -> BodyLayout:
    """Group a body stream into the segments its template lays out.

    A card row or banner spans the page only where ``allow_full_width`` says
    the page has no sidebar beside its text; next to one there is no room to
    break out, and every block stays in the column.

    ``visible_cards`` is the tag filter's answer, keyed by the row's position in
    the stream (see ``govuk.models.cards``). A row it leaves without a card is
    not laid out at all, heading and all. ``first_cards`` marks the
    first row that is, where the filter control goes.
    """
    segments: list[BodySegment] = []
    seen_cards = False
    for index, child in enumerate(stream or []):
        is_card_row = child.block_type == CARD_ROW_BLOCK_TYPE
        cards = None
        if is_card_row and visible_cards is not None:
            cards = visible_cards.get(index, [])
            if not cards:
                continue
        item = {
            "block": child,
            "cards": cards,
            "first_cards": is_card_row and not seen_cards,
        }
        seen_cards = seen_cards or is_card_row

        if allow_full_width and getattr(child.block, "full_width", False):
            segments.append(BodySegment(full_width=True, items=[item]))
        elif segments and not segments[-1].full_width:
            segments[-1].items.append(item)
        else:
            segments.append(BodySegment(full_width=False, items=[item]))
    return BodyLayout(segments=segments)

