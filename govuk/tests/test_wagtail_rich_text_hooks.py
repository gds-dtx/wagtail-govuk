import importlib
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


class GovukButtonFeatureTests(SimpleTestCase):
    def setUp(self):
        with override_settings(FEATURE_FLAGS=_feature_flags()):
            with patch("wagtail.snippets.models.register_snippet"):
                self.hooks = _reload_wagtail_hooks()

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

    @override_settings(FEATURE_FLAGS=_feature_flags())
    @patch("wagtail.snippets.models.register_snippet")
    def test_registers_single_button_feature(self, _mock_register_snippet):
        features = Mock()
        features.default_features = []

        self.hooks.register_govuk_button_rich_text_features(features)

        registered_features = [
            call.args[1] for call in features.register_editor_plugin.call_args_list
        ]
        self.assertIn(self.hooks.GOVUK_BUTTON_FEATURE, registered_features)
        self.assertNotIn("govuk-start-button", registered_features)

        button_converter_call = next(
            call
            for call in features.register_converter_rule.call_args_list
            if call.args[1] == self.hooks.GOVUK_BUTTON_FEATURE
        )
        from_db = button_converter_call.args[2]["from_database_format"]
        # A single selector handles every variant now the legacy linktype is gone.
        self.assertEqual(
            list(from_db.keys()),
            [f'a[linktype="{self.hooks.GOVUK_BUTTON_LINKTYPE}"]'],
        )
