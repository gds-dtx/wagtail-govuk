import base64
import re

from django.urls import reverse_lazy
from django.utils.html import escape, strip_tags
from draftjs_exporter.dom import DOM
from wagtail import hooks
from wagtail.admin.rich_text.converters.contentstate_models import Entity
from wagtail.admin.rich_text.converters.html_to_contentstate import (
    AtomicBlockEntityElementHandler,
    BlockElementHandler,
    PageLinkElementHandler,
)
from wagtail.admin.rich_text.editors.draftail import features as draftail_features
from wagtail.rich_text import EmbedHandler
from wagtail.rich_text.pages import PageLinkHandler
from wagtail.whitelist import check_url

GOVUK_BUTTON_FEATURE = "govuk-button"
GOVUK_START_BUTTON_FEATURE = "govuk-start-button"
GOVUK_BUTTON_ENTITY_TYPE = "GOVUK_BUTTON_LINK"
GOVUK_START_BUTTON_ENTITY_TYPE = "GOVUK_START_BUTTON_LINK"
GOVUK_BUTTON_LINKTYPE = "govuk-button"
GOVUK_START_BUTTON_LINKTYPE = "govuk-start-button"
GOVUK_BUTTON_STYLE_ATTR = "data-govuk-button-style"
GOVUK_BUTTON_STYLE_DEFAULT = "default"
GOVUK_BUTTON_STYLE_START = "start"
RAW_HTML_FEATURE = "raw-html"
RAW_HTML_ENTITY_TYPE = "RAW_HTML"
RAW_HTML_EMBEDTYPE = "raw_html"
INSET_TEXT_FEATURE = "inset-text"
INSET_TEXT_BLOCK_TYPE = "inset-text"
LINE_BREAK_FEATURE = "line-break"


def _encode_raw_html(raw_html: str | None) -> str:
    normalised_html = (raw_html or "").strip()
    if not normalised_html:
        return ""
    return base64.urlsafe_b64encode(normalised_html.encode("utf-8")).decode("ascii")


def _decode_raw_html(encoded_html: str | None) -> str:
    normalised_encoded_html = (encoded_html or "").strip()
    if not normalised_encoded_html:
        return ""

    # Base64 strings in embed attributes can be copied without padding.
    padding = "=" * (-len(normalised_encoded_html) % 4)
    try:
        return base64.urlsafe_b64decode(
            (normalised_encoded_html + padding).encode("ascii")
        ).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return ""


def _get_govuk_button_attributes(*, is_start: bool) -> dict[str, str]:
    classes = "govuk-button govuk-button--start" if is_start else "govuk-button"
    style = GOVUK_BUTTON_STYLE_START if is_start else GOVUK_BUTTON_STYLE_DEFAULT
    return {
        "class": classes,
        "role": "button",
        "draggable": "false",
        "data-module": "govuk-button",
        GOVUK_BUTTON_STYLE_ATTR: style,
    }


def _build_govuk_button_opening_tag(*, href: str | None, is_start: bool) -> str:
    attrs = _get_govuk_button_attributes(is_start=is_start)
    ordered_attrs: list[str] = []
    if href:
        ordered_attrs.append(f'href="{escape(href)}"')
    ordered_attrs.extend(
        [
            f'class="{escape(attrs["class"])}"',
            f'role="{escape(attrs["role"])}"',
            f'draggable="{escape(attrs["draggable"])}"',
            f'data-module="{escape(attrs["data-module"])}"',
            f'{GOVUK_BUTTON_STYLE_ATTR}="{escape(attrs[GOVUK_BUTTON_STYLE_ATTR])}"',
        ]
    )
    return "<a " + " ".join(ordered_attrs) + ">"


def _govuk_button_entity(props: dict, *, is_start: bool):
    id_ = props.get("id")
    link_props = {}
    link_props["linktype"] = (
        GOVUK_START_BUTTON_LINKTYPE if is_start else GOVUK_BUTTON_LINKTYPE
    )
    if id_ is not None:
        link_props["id"] = id_
    else:
        link_props["url"] = check_url(props.get("url") or "") or "#"
    return DOM.create_element("a", link_props, props["children"])


def govuk_button_entity(props: dict):
    return _govuk_button_entity(props, is_start=False)


def govuk_start_button_entity(props: dict):
    return _govuk_button_entity(props, is_start=True)


class GovukButtonLinkElementHandler(PageLinkElementHandler):
    def get_attribute_data(self, attrs):
        if "id" in attrs:
            return super().get_attribute_data(attrs)
        return {"url": attrs.get("url", "")}


class GovukButtonLinkHandler(PageLinkHandler):
    identifier = GOVUK_BUTTON_LINKTYPE
    is_start = False

    @classmethod
    def expand_db_attributes_many(cls, attrs_list: list[dict]) -> list[str]:
        return [
            _build_govuk_button_opening_tag(
                href=(
                    page.localized.url
                    if page
                    else (check_url(attrs.get("url") or "") or "#")
                ),
                is_start=cls.is_start,
            )
            for attrs, page in zip(attrs_list, cls.get_many(attrs_list))
        ]

    @classmethod
    def extract_references(cls, attrs):
        if attrs.get("id"):
            yield from super().extract_references(attrs)


class GovukStartButtonLinkHandler(GovukButtonLinkHandler):
    identifier = GOVUK_START_BUTTON_LINKTYPE
    is_start = True


class RawHtmlElementHandler(AtomicBlockEntityElementHandler):
    def create_entity(self, name, attrs, state, contentstate):
        return Entity(
            RAW_HTML_ENTITY_TYPE,
            "MUTABLE",
            {"html": _decode_raw_html(attrs.get("html", ""))},
        )


_TABLE_FRAGMENT_RE = re.compile(r"^\s*<table\b", re.IGNORECASE)
_CAPTION_RE = re.compile(r"<caption\b[^>]*>(.*?)</caption>", re.IGNORECASE | re.DOTALL)


def _wrap_table_in_scroll_region(html: str) -> str:
    """Give a hand-written table the scrollable region main.css styles.

    A table of four columns of full sentences cannot be made to fit 320px, and
    left alone it drags the whole document sideways -- WCAG 1.4.10 Reflow. The
    criterion exempts content that genuinely needs two dimensions, so the table
    keeps its shape and scrolls inside a region of its own. Focusable, so a
    keyboard can scroll it, and named from the caption where there is one.

    The test is whether the fragment *starts* with a table, which is what an
    editor pasting one produces. Anything else is left alone: a fragment with
    prose around a table is the editor laying the page out themselves.
    """
    if not _TABLE_FRAGMENT_RE.match(html):
        return html

    caption_match = _CAPTION_RE.search(html)
    caption = strip_tags(caption_match.group(1)).strip() if caption_match else ""
    label = escape(caption) if caption else "Table"
    return (
        f'<div class="table-scroll" tabindex="0" role="region" '
        f'aria-label="{label}">{html}</div>'
    )


class RawHtmlEmbedHandler(EmbedHandler):
    identifier = RAW_HTML_EMBEDTYPE

    @classmethod
    def expand_db_attributes_many(cls, attrs_list: list[dict]) -> list[str]:
        return [
            _wrap_table_in_scroll_region(_decode_raw_html(attrs.get("html", "")))
            for attrs in attrs_list
        ]


def raw_html_entity(props: dict):
    return DOM.create_element(
        "embed",
        {
            "embedtype": RAW_HTML_EMBEDTYPE,
            "html": _encode_raw_html(props.get("html", "")),
        },
    )


@hooks.register("register_rich_text_features")
def register_govuk_button_rich_text_features(features):
    features.register_link_type(GovukButtonLinkHandler)
    features.register_link_type(GovukStartButtonLinkHandler)
    features.register_embed_type(RawHtmlEmbedHandler)

    for feature_name in (
        GOVUK_BUTTON_FEATURE,
        GOVUK_START_BUTTON_FEATURE,
        RAW_HTML_FEATURE,
        INSET_TEXT_FEATURE,
        LINE_BREAK_FEATURE,
    ):
        if feature_name not in features.default_features:
            features.default_features.append(feature_name)

    link_chooser_urls = {
        "pageChooser": reverse_lazy("wagtailadmin_choose_page"),
        "externalLinkChooser": reverse_lazy("wagtailadmin_choose_page_external_link"),
        "emailLinkChooser": reverse_lazy("wagtailadmin_choose_page_email_link"),
        "phoneLinkChooser": reverse_lazy("wagtailadmin_choose_page_phone_link"),
        "anchorLinkChooser": reverse_lazy("wagtailadmin_choose_page_anchor_link"),
    }
    common_editor_plugin_args = {
        "attributes": ["url", "id", "parentId"],
        "allowlist": {
            "href": "^(http:|https:|mailto:|tel:|#|undefined$)",
        },
        "chooserUrls": link_chooser_urls,
    }

    features.register_editor_plugin(
        "draftail",
        GOVUK_BUTTON_FEATURE,
        draftail_features.EntityFeature(
            {
                "type": GOVUK_BUTTON_ENTITY_TYPE,
                "label": "Btn",
                "description": "Button link",
                **common_editor_plugin_args,
            },
            js=[
                "wagtailadmin/js/page-chooser-modal.js",
                "govuk/js/draftail-govuk-button.js",
            ],
        ),
    )
    features.register_converter_rule(
        "contentstate",
        GOVUK_BUTTON_FEATURE,
        {
            "from_database_format": {
                f'a[linktype="{GOVUK_BUTTON_LINKTYPE}"]': GovukButtonLinkElementHandler(
                    GOVUK_BUTTON_ENTITY_TYPE
                ),
            },
            "to_database_format": {
                "entity_decorators": {GOVUK_BUTTON_ENTITY_TYPE: govuk_button_entity}
            },
        },
    )

    features.register_editor_plugin(
        "draftail",
        GOVUK_START_BUTTON_FEATURE,
        draftail_features.EntityFeature(
            {
                "type": GOVUK_START_BUTTON_ENTITY_TYPE,
                "description": "Start button link",
                "icon": "login",
                **common_editor_plugin_args,
            },
            js=[
                "wagtailadmin/js/page-chooser-modal.js",
                "govuk/js/draftail-govuk-button.js",
            ],
        ),
    )
    features.register_converter_rule(
        "contentstate",
        GOVUK_START_BUTTON_FEATURE,
        {
            "from_database_format": {
                f'a[linktype="{GOVUK_START_BUTTON_LINKTYPE}"]': GovukButtonLinkElementHandler(
                    GOVUK_START_BUTTON_ENTITY_TYPE
                ),
            },
            "to_database_format": {
                "entity_decorators": {
                    GOVUK_START_BUTTON_ENTITY_TYPE: govuk_start_button_entity
                }
            },
        },
    )

    features.register_editor_plugin(
        "draftail",
        RAW_HTML_FEATURE,
        draftail_features.EntityFeature(
            {
                "type": RAW_HTML_ENTITY_TYPE,
                "description": "Raw HTML block",
                "icon": "code",
            },
            js=["govuk/js/draftail-raw-html.js"],
        ),
    )
    features.register_converter_rule(
        "contentstate",
        RAW_HTML_FEATURE,
        {
            "from_database_format": {
                f'embed[embedtype="{RAW_HTML_EMBEDTYPE}"]': RawHtmlElementHandler(),
            },
            "to_database_format": {
                "entity_decorators": {RAW_HTML_ENTITY_TYPE: raw_html_entity}
            },
        },
    )

    features.register_editor_plugin(
        "draftail",
        INSET_TEXT_FEATURE,
        draftail_features.BlockFeature(
            {
                "type": INSET_TEXT_BLOCK_TYPE,
                "description": "Inset text",
                "icon": "openquote",
            }
        ),
    )
    features.register_converter_rule(
        "contentstate",
        INSET_TEXT_FEATURE,
        {
            "from_database_format": {
                'div[class="govuk-inset-text"]': BlockElementHandler(
                    INSET_TEXT_BLOCK_TYPE
                ),
            },
            "to_database_format": {
                "block_map": {
                    INSET_TEXT_BLOCK_TYPE: {
                        "element": "div",
                        "props": {"class": "govuk-inset-text"},
                    }
                }
            },
        },
    )

    # Inset text is a block, so pressing return inside a quote ends the quote
    # and starts another one -- two bordered boxes where the editor wanted one
    # quote on two lines. A line break stays inside the block, and Wagtail
    # already carries it both ways of its own accord: the contentstate exporter
    # turns "\n" into <br>, and LineBreakHandler turns it back. All that was
    # missing was a way to type one, which is what enableLineBreak adds -- a
    # toolbar control and shift+return. No converter rule to register, and
    # nothing changes for text that has no line breaks in it.
    features.register_editor_plugin(
        "draftail",
        LINE_BREAK_FEATURE,
        draftail_features.BooleanFeature("enableLineBreak"),
    )

