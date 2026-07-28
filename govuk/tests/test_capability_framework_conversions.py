from datetime import date

from django.test import SimpleTestCase

from govuk.capability_framework import (
    LEVEL_NOT_DEFINED,
    NOT_IN_USE,
    changelog_html_to_note,
    changelog_note_to_html,
    parse_iso_date,
    parse_points,
    points_to_text,
    rich_html_to_text,
    text_to_rich_html,
)


class ParsePointsTests(SimpleTestCase):
    def test_extracts_bullets_after_you_can(self):
        self.assertEqual(
            parse_points("You can:\n- do a thing\n- do another thing"),
            ["do a thing", "do another thing"],
        )

    def test_returns_empty_for_blank_or_not_in_use(self):
        self.assertEqual(parse_points(""), [])
        self.assertEqual(parse_points(NOT_IN_USE), [])

    def test_unbulleted_text_becomes_a_single_point(self):
        self.assertEqual(parse_points(LEVEL_NOT_DEFINED), [LEVEL_NOT_DEFINED])

    def test_points_are_truncated_to_field_limit(self):
        self.assertEqual(len(parse_points("You can:\n- " + "x" * 900)[0]), 500)


class TextToRichHtmlTests(SimpleTestCase):
    def test_paragraphs_and_bullets(self):
        self.assertEqual(
            text_to_rich_html("Intro line.\n\n- first\n- second"),
            "<p>Intro line.</p><ul><li>first</li><li>second</li></ul>",
        )

    def test_blank_and_not_in_use_produce_no_html(self):
        self.assertEqual(text_to_rich_html(""), "")
        self.assertEqual(text_to_rich_html(NOT_IN_USE), "")

    def test_markup_in_source_is_escaped(self):
        self.assertNotIn("<script>", text_to_rich_html("<script>alert(1)</script>"))


class RichHtmlToTextTests(SimpleTestCase):
    def test_round_trips_the_published_prose_convention(self):
        original = (
            "A data engineer builds data products.\n"
            "\n"
            "In this role, you will:\n"
            "- build pipelines\n"
            "- write ETL scripts"
        )
        self.assertEqual(rich_html_to_text(text_to_rich_html(original)), original)

    def test_handles_plain_fragment_without_block_tags(self):
        self.assertEqual(rich_html_to_text("just text"), "just text")

    def test_entities_are_decoded(self):
        self.assertEqual(rich_html_to_text("<p>Hodgson&#x27;s rule</p>"), "Hodgson's rule")

    def test_empty_input(self):
        self.assertEqual(rich_html_to_text(""), "")
        self.assertEqual(rich_html_to_text(None), "")


class PointsToTextTests(SimpleTestCase):
    def test_renders_you_can_block(self):
        self.assertEqual(
            points_to_text(["first", "second"]),
            "You can:\n- first\n- second",
        )

    def test_round_trips_with_parse_points(self):
        original = "You can:\n- alpha\n- beta"
        self.assertEqual(points_to_text(parse_points(original)), original)

    def test_placeholder_is_written_without_prefix_or_bullet(self):
        self.assertEqual(points_to_text([LEVEL_NOT_DEFINED]), LEVEL_NOT_DEFINED)

    def test_empty_points_produce_empty_string(self):
        self.assertEqual(points_to_text([]), "")


class ChangelogConversionTests(SimpleTestCase):
    def test_note_round_trips_through_html(self):
        note = "[Data engineer](/role/data-engineer) has updated skills.\nA second change."
        self.assertEqual(changelog_html_to_note(changelog_note_to_html(note)), note)

    def test_links_become_anchors(self):
        self.assertEqual(
            changelog_note_to_html("[Role](/role/x) changed."),
            '<p><a href="/role/x">Role</a> changed.</p>',
        )

    def test_html_is_escaped(self):
        self.assertIn("&lt;b&gt;", changelog_note_to_html("<b>bold</b>"))

    def test_empty_values(self):
        self.assertEqual(changelog_note_to_html(""), "")
        self.assertEqual(changelog_html_to_note(""), "")


class ParseIsoDateTests(SimpleTestCase):
    def test_parses_iso_dates(self):
        self.assertEqual(parse_iso_date("2026-05-29"), date(2026, 5, 29))
        self.assertEqual(parse_iso_date("  2020-01-07 "), date(2020, 1, 7))

    def test_returns_none_for_invalid_dates(self):
        self.assertIsNone(parse_iso_date(""))
        self.assertIsNone(parse_iso_date("29/05/2026"))
