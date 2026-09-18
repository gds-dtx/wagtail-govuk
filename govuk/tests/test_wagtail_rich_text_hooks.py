import importlib
import json
from unittest.mock import ANY, Mock, patch

from django.test import SimpleTestCase, override_settings


def _feature_flags() -> dict[str, bool]:
    return {
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
    }


def _reload_wagtail_hooks():
    import govuk.wagtail_hooks as hooks_module

    return importlib.reload(hooks_module)


class WagtailRichTextHooksTests(SimpleTestCase):
    @override_settings(FEATURE_FLAGS=_feature_flags())
    @patch("wagtail.snippets.models.register_snippet")
    def test_registers_raw_html_feature_for_draftail(self, _mock_register_snippet):
        hooks_module = _reload_wagtail_hooks()
        features = Mock()
        features.default_features = []

        hooks_module.register_govuk_button_rich_text_features(features)

        features.register_embed_type.assert_any_call(hooks_module.RawHtmlEmbedHandler)
        self.assertIn(hooks_module.RAW_HTML_FEATURE, features.default_features)
        features.register_editor_plugin.assert_any_call(
            "draftail",
            hooks_module.RAW_HTML_FEATURE,
            ANY,
        )

        raw_html_converter_call = next(
            call
            for call in features.register_converter_rule.call_args_list
            if call.args[1] == hooks_module.RAW_HTML_FEATURE
        )
        raw_html_converter_rule = raw_html_converter_call.args[2]
        self.assertIn(
            f'embed[embedtype="{hooks_module.RAW_HTML_EMBEDTYPE}"]',
            raw_html_converter_rule["from_database_format"],
        )
        self.assertIn(
            hooks_module.RAW_HTML_ENTITY_TYPE,
            raw_html_converter_rule["to_database_format"]["entity_decorators"],
        )

    @override_settings(FEATURE_FLAGS=_feature_flags())
    @patch("wagtail.snippets.models.register_snippet")
    def test_registers_inset_text_block_feature(self, _mock_register_snippet):
        hooks_module = _reload_wagtail_hooks()
        features = Mock()
        features.default_features = []

        hooks_module.register_govuk_button_rich_text_features(features)

        self.assertIn(hooks_module.INSET_TEXT_FEATURE, features.default_features)
        features.register_editor_plugin.assert_any_call(
            "draftail",
            hooks_module.INSET_TEXT_FEATURE,
            ANY,
        )

        inset_converter_call = next(
            call
            for call in features.register_converter_rule.call_args_list
            if call.args[1] == hooks_module.INSET_TEXT_FEATURE
        )
        inset_converter_rule = inset_converter_call.args[2]
        self.assertIn(
            'div[class="govuk-inset-text"]',
            inset_converter_rule["from_database_format"],
        )
        self.assertIn(
            hooks_module.INSET_TEXT_BLOCK_TYPE,
            inset_converter_rule["to_database_format"]["block_map"],
        )
        self.assertEqual(
            inset_converter_rule["to_database_format"]["block_map"][
                hooks_module.INSET_TEXT_BLOCK_TYPE
            ],
            {"element": "div", "props": {"class": "govuk-inset-text"}},
        )

    @override_settings(FEATURE_FLAGS=_feature_flags())
    @patch("wagtail.snippets.models.register_snippet")
    def test_registers_line_break_feature(self, _mock_register_snippet):
        """Without it, a return inside a quote starts a second bordered box."""
        hooks_module = _reload_wagtail_hooks()
        features = Mock()
        features.default_features = []

        hooks_module.register_govuk_button_rich_text_features(features)

        self.assertIn(hooks_module.LINE_BREAK_FEATURE, features.default_features)

        line_break_call = next(
            call
            for call in features.register_editor_plugin.call_args_list
            if call.args[1] == hooks_module.LINE_BREAK_FEATURE
        )
        plugin = line_break_call.args[2]
        self.assertEqual(plugin.option_name, "enableLineBreak")

    @override_settings(FEATURE_FLAGS=_feature_flags())
    @patch("wagtail.snippets.models.register_snippet")
    def test_a_line_break_survives_a_round_trip_through_inset_text(
        self, _mock_register_snippet
    ):
        """One quote on two lines has to come back as one block, not two.

        Wagtail carries the break both ways of its own accord, which is why
        the feature registers no converter rule. This is the assertion that
        says so, so that a Wagtail upgrade that changed it would be caught
        here rather than by an editor.
        """
        from wagtail.admin.rich_text.converters.contentstate import (
            ContentstateConverter,
        )

        hooks_module = _reload_wagtail_hooks()
        converter = ContentstateConverter(
            features=[
                "bold",
                "italic",
                "link",
                hooks_module.INSET_TEXT_FEATURE,
                hooks_module.LINE_BREAK_FEATURE,
            ]
        )

        contentstate = converter.from_database_format(
            '<div class="govuk-inset-text">line one<br/>line two</div>'
        )
        blocks = json.loads(contentstate)["blocks"]

        self.assertEqual(len(blocks), 1)
        self.assertEqual(blocks[0]["type"], hooks_module.INSET_TEXT_BLOCK_TYPE)
        self.assertEqual(blocks[0]["text"], "line one\nline two")

        html = converter.to_database_format(contentstate)
        self.assertEqual(html.count("govuk-inset-text"), 1)
        self.assertIn("line one<br/>line two", html)

    @override_settings(FEATURE_FLAGS=_feature_flags())
    @patch("wagtail.snippets.models.register_snippet")
    def test_raw_html_embed_handler_decodes_base64_html(self, _mock_register_snippet):
        hooks_module = _reload_wagtail_hooks()
        raw_html = '<script src="https://example.com/widget.js"></script>'
        encoded_html = hooks_module._encode_raw_html(raw_html)

        expanded = hooks_module.RawHtmlEmbedHandler.expand_db_attributes_many(
            [{"html": encoded_html}]
        )

        self.assertEqual(expanded, [raw_html])

    @override_settings(FEATURE_FLAGS=_feature_flags())
    @patch("wagtail.snippets.models.register_snippet")
    def test_raw_html_embed_handler_ignores_invalid_payloads(self, _mock_register_snippet):
        hooks_module = _reload_wagtail_hooks()

        expanded = hooks_module.RawHtmlEmbedHandler.expand_db_attributes_many(
            [{"html": "%%%not-base64%%%"}]
        )

        self.assertEqual(expanded, [""])
