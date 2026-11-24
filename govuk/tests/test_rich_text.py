"""Rich text: the editor's Draftail features and the type scale it renders at."""

import importlib
import json
from contextlib import contextmanager
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, TestCase, override_settings

from govuk.tests.helpers import feature_flags, publish_page


@contextmanager
def _hooks_context():
    with override_settings(FEATURE_FLAGS={"FEEDBACK": False}):
        with patch("wagtail.snippets.models.register_snippet"):
            yield


def _reload_rich_text_hooks():
    # Reloaded so the test sees the module's symbols, private helpers included.
    import govuk.wagtail_hooks.rich_text as rich_text_module

    with _hooks_context():
        return importlib.reload(rich_text_module)


def _registered_features(hooks) -> Mock:
    features = Mock()
    features.default_features = []
    with _hooks_context():
        hooks.register_govuk_button_rich_text_features(features)
    return features


class RichTextFeatureRegistrationTests(SimpleTestCase):
    def setUp(self):
        self.hooks = _reload_rich_text_hooks()
        self.features = _registered_features(self.hooks)

    def test_rich_text_offers_no_raw_html(self):
        """Hand-written HTML is a Raw HTML block in the body stream, not rich text."""
        registered = [call.args[1] for call in self.features.register_editor_plugin.call_args_list]
        self.assertNotIn("raw-html", registered)
        self.assertNotIn("raw-html", self.features.default_features)
        self.features.register_embed_type.assert_not_called()

    def test_a_line_break_survives_a_round_trip_through_inset_text(self):
        """One quote on two lines has to come back as one block, not two.

        Wagtail carries the break both ways itself, which is why the feature
        registers no converter rule; this catches an upgrade that changed that.
        """
        from wagtail.admin.rich_text.converters.contentstate import (
            ContentstateConverter,
        )

        converter = ContentstateConverter(
            features=[
                "bold",
                "italic",
                "link",
                self.hooks.INSET_TEXT_FEATURE,
                self.hooks.LINE_BREAK_FEATURE,
            ]
        )

        contentstate = converter.from_database_format(
            '<div class="govuk-inset-text">line one<br/>line two</div>'
        )
        blocks = json.loads(contentstate)["blocks"]

        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["type"], self.hooks.INSET_TEXT_BLOCK_TYPE)
        self.assertEqual(blocks[0]["text"], "line one\nline two")

        html = converter.to_database_format(contentstate)
        self.assertEqual(html.count("govuk-inset-text"), 1)
        self.assertIn("line one<br/>line two", html)

class GovukButtonFeatureTests(SimpleTestCase):
    def setUp(self):
        self.hooks = _reload_rich_text_hooks()
        # Wagtail's contentstate exporter picks the DOM engine before entity
        # decorators run; the tests below call DOM directly, so pick it here.
        from draftjs_exporter.dom import DOM

        DOM.use(DOM.STRING)

    def test_default_style_renders_plain_button(self):
        html = self.hooks._build_govuk_button_opening_tag(
            href="/start", style="default", new_tab=False
        )
        self.assertIn('href="/start"', html)
        self.assertIn('class="govuk-button"', html)
        self.assertIn('data-govuk-button-style="default"', html)
        self.assertNotIn("govuk-button--", html)
        self.assertNotIn("target=", html)

    def test_each_style_adds_its_modifier_class(self):
        cases = {
            "start": "govuk-button--start",
            "secondary": "govuk-button--secondary",
            "warning": "govuk-button--warning",
        }
        for style, modifier in cases.items():
            with self.subTest(style=style):
                html = self.hooks._build_govuk_button_opening_tag(
                    href="/x", style=style, new_tab=False
                )
                self.assertIn(f'class="govuk-button {modifier}"', html)
                self.assertIn(f'data-govuk-button-style="{style}"', html)

    def test_unknown_style_falls_back_to_default(self):
        html = self.hooks._build_govuk_button_opening_tag(
            href="/x", style="rainbow", new_tab=False
        )
        self.assertIn('class="govuk-button"', html)
        self.assertIn('data-govuk-button-style="default"', html)

    def test_new_tab_adds_target_and_rel_and_marker_attribute(self):
        html = self.hooks._build_govuk_button_opening_tag(
            href="/x", style="default", new_tab=True
        )
        self.assertIn('target="_blank"', html)
        self.assertIn('rel="noreferrer noopener"', html)
        self.assertIn('data-govuk-button-new-tab="true"', html)

    def test_expand_db_attributes_reads_style_and_new_tab(self):
        [html] = self.hooks.GovukButtonLinkHandler.expand_db_attributes_many(
            [
                {
                    "url": "https://example.gov.uk",
                    "data-govuk-button-style": "warning",
                    "data-govuk-button-new-tab": "true",
                }
            ]
        )
        self.assertIn('class="govuk-button govuk-button--warning"', html)
        self.assertIn('target="_blank"', html)

    def test_entity_decorator_serialises_non_default_options(self):
        from draftjs_exporter.dom import DOM

        element = self.hooks.govuk_button_entity(
            {
                "url": "https://example.gov.uk",
                "style": "secondary",
                "newTab": True,
                "children": "Apply",
            }
        )
        html = DOM.render(element)
        self.assertIn(f'linktype="{self.hooks.GOVUK_BUTTON_LINKTYPE}"', html)
        self.assertIn('data-govuk-button-style="secondary"', html)
        self.assertIn('data-govuk-button-new-tab="true"', html)

    def test_entity_decorator_omits_default_options(self):
        from draftjs_exporter.dom import DOM

        element = self.hooks.govuk_button_entity(
            {"url": "https://example.gov.uk", "style": "default", "children": "Apply"}
        )
        html = DOM.render(element)
        self.assertNotIn("data-govuk-button-style", html)
        self.assertNotIn("data-govuk-button-new-tab", html)

    def test_element_handler_round_trips_options_into_editor(self):
        handler = self.hooks.GovukButtonLinkElementHandler(
            self.hooks.GOVUK_BUTTON_ENTITY_TYPE
        )
        data = handler.get_attribute_data(
            {
                "url": "https://example.gov.uk",
                "data-govuk-button-style": "secondary",
                "data-govuk-button-new-tab": "true",
            }
        )
        self.assertEqual(data["style"], "secondary")
        self.assertTrue(data["newTab"])


class RichTextTypeScaleScopeTests(TestCase):
    """The Design System's type scale for rich text is the framework's house style only.

    main.js runs on every service this image serves, all rendering the same
    ``.rich-text-content``; base.html marks the body on a framework instance
    and the script reads the mark.
    """

    def test_only_a_framework_instance_marks_the_body(self):
        page = publish_page("Guidance", "<h2>A heading</h2><p>Some prose.</p>")
        for skills in (True, False):
            with self.subTest(skills=skills), override_settings(
                FEATURE_FLAGS=feature_flags(skills=skills)
            ):
                response = self.client.get(page.url)

                if skills:
                    self.assertContains(response, "data-rich-text-type-scale")
                else:
                    self.assertNotContains(response, "data-rich-text-type-scale")


