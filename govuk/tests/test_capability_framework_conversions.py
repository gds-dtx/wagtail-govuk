import time
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
    repair_changelog_html,
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

    def test_the_placeholder_is_recognised_in_either_wording(self):
        """The published skills export carries both: 37 rows say "currently"
        and one does not. Matching only the first put that row out as
        "You can:\\n- This skill level is not defined." in the CSV this
        service publishes back."""
        variant = "This skill level is not defined."

        self.assertEqual(points_to_text([variant]), variant)

    def test_a_real_point_that_merely_mentions_the_words_is_left_alone(self):
        point = "This skill level is not defined by the role's seniority alone"

        self.assertEqual(points_to_text([point]), f"You can:\n- {point}")

    def test_the_placeholder_beside_real_points_stays_a_bullet(self):
        """One point is a placeholder standing in for a description; the same
        sentence in a list is a sentence in a list."""
        points = [LEVEL_NOT_DEFINED, "do a real thing"]

        self.assertEqual(
            points_to_text(points),
            f"You can:\n- {LEVEL_NOT_DEFINED}\n- do a real thing",
        )

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

    def test_hyphen_lines_become_a_bulleted_list(self):
        self.assertEqual(
            changelog_note_to_html("These roles changed:\n- Data analyst\n- Data engineer"),
            "<p>These roles changed:</p><ul><li>Data analyst</li><li>Data engineer</li></ul>",
        )

    def test_links_with_no_text_are_dropped(self):
        """The exports carry a stray "[](/skills)" where a link lost its text."""
        self.assertEqual(
            changelog_note_to_html("The skills [](/skills) were updated."),
            "<p>The skills  were updated.</p>",
        )
        self.assertEqual(changelog_note_to_html("[](/skills)"), "")

    def test_empty_values(self):
        self.assertEqual(changelog_note_to_html(""), "")
        self.assertEqual(changelog_html_to_note(""), "")


class ChangelogRepairTests(SimpleTestCase):
    """Notes stored before the import understood the exports' conventions."""

    def test_a_run_of_hyphen_paragraphs_becomes_one_list(self):
        self.assertEqual(
            repair_changelog_html(
                "<p>These roles changed:</p><p>- Data analyst</p><p>- Data engineer</p>"
            ),
            "<p>These roles changed:</p><ul><li>Data analyst</li><li>Data engineer</li></ul>",
        )

    def test_separate_runs_stay_separate_lists(self):
        self.assertEqual(
            repair_changelog_html("<p>- one</p><p>Then:</p><p>- two</p>"),
            "<ul><li>one</li></ul><p>Then:</p><ul><li>two</li></ul>",
        )

    def test_links_with_no_text_are_removed(self):
        self.assertEqual(
            repair_changelog_html("<p>The skills [](/skills) were updated.</p>"),
            "<p>The skills  were updated.</p>",
        )

    def test_a_paragraph_left_empty_by_the_repair_is_dropped(self):
        self.assertEqual(repair_changelog_html("<p>Real change.</p><p>[](/skills)</p>"), "<p>Real change.</p>")

    def test_a_note_that_needs_no_repair_is_left_alone(self):
        note = '<p><a href="/role/x">Role</a> changed.</p><ul><li>alpha</li></ul>'
        self.assertEqual(repair_changelog_html(note), note)

    def test_empty_values(self):
        self.assertEqual(repair_changelog_html(""), "")
        self.assertEqual(repair_changelog_html(None), "")


class ChangelogNoteScalingTests(SimpleTestCase):
    """A big note must not send the patterns quadratic.

    An imported note reaches these conversions before anything has looked at
    its size, and the 0058 data migration runs them at container boot, so a
    note that backtracks for minutes stops the site coming up.
    """

    # Each of these took minutes at this size before the patterns were
    # rewritten, and runs in milliseconds now. A whole second is far below
    # the old cost and far above the new one, so it will not flake.
    BUDGET_SECONDS = 1

    def assert_runs_quickly(self, convert, value):
        started = time.monotonic()
        convert(value)
        self.assertLess(time.monotonic() - started, self.BUDGET_SECONDS)

    def test_a_paragraph_that_never_closes_is_repaired_quickly(self):
        self.assert_runs_quickly(repair_changelog_html, "<p>-" + " " * 64_000)

    def test_unterminated_markdown_links_convert_quickly(self):
        self.assert_runs_quickly(changelog_note_to_html, "[a](" * 32_000)

    def test_unterminated_empty_markdown_links_convert_quickly(self):
        self.assert_runs_quickly(changelog_note_to_html, "[](" * 64_000)


class ParseIsoDateTests(SimpleTestCase):
    def test_parses_iso_dates(self):
        self.assertEqual(parse_iso_date("2026-05-29"), date(2026, 5, 29))
        self.assertEqual(parse_iso_date("  2020-01-07 "), date(2020, 1, 7))

    def test_parses_timestamps_by_taking_the_date(self):
        # The source column is a "Timestamp"; a future export may carry a time.
        self.assertEqual(parse_iso_date("2026-05-29T09:00:00"), date(2026, 5, 29))
        self.assertEqual(parse_iso_date("2026-05-29 09:00:00"), date(2026, 5, 29))
        self.assertEqual(parse_iso_date("2026-05-29T09:00:00Z"), date(2026, 5, 29))

    def test_returns_none_for_invalid_dates(self):
        self.assertIsNone(parse_iso_date(""))
        self.assertIsNone(parse_iso_date("29/05/2026"))
