
from wagtail import blocks
from wagtail.blocks import StructValue
from wagtail.contrib.table_block.blocks import TableBlock


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
    embed. That is not a formatting option a content designer has, which is
    what left "tables can be added to the page" as the last unticked box on
    CS32-3527 while everything around it was done.

    Cells hold text, not HTML: the renderer stays on Wagtail's default, so
    whatever is typed is escaped. A table is a place a paste from a document
    would otherwise carry markup straight onto a public page.
    """

    def __init__(self, *args, table_options=None, **kwargs):
        # Wagtail hands handsontable whatever LANGUAGE_CODE is, and ours is
        # en-gb. The vendored handsontable 6.2.2 ships one locale, en-US, so
        # it logs a console error and falls back to it anyway. The strings
        # this picks are the grid's own context menu ("Insert row above"),
        # where the two spellings do not differ. Naming the locale it has
        # keeps the admin console clean.
        table_options = {"language": "en-US", **(table_options or {})}
        super().__init__(*args, table_options=table_options, **kwargs)

    class Meta:
        icon = "table"
        label = "Table"
        template = "blocks/govuk_table.html"
        help_text = (
            "Right-click a cell to add or remove rows and columns. Give the "
            "table a caption: it is how somebody using a screen reader knows "
            "what the table is before reading it."
        )


class ContentBodyBlock(blocks.StreamBlock):
    """Prose and tables, in whatever order the page needs them.

    ``ContentPage.body`` is a rich text field and stays one -- 67 pages of
    live content are stored in it, and the verified export was taken with it
    that shape. This renders after it, so an editor adding a table to an
    existing page changes nothing about the page's existing text.

    A page that needs a table part-way through moves its body text into a Text
    block here, which offers the same formatting the body field does.
    """

    # No features argument, so this offers exactly what the body field above
    # offers -- both take the default set, which the rich text hooks extend
    # with the GOV.UK button, start button, inset text and raw HTML.
    text = blocks.RichTextBlock(label="Text", icon="pilcrow")
    table = GovukTableBlock()

    class Meta:
        required = False




