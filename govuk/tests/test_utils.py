from django.test import TestCase

from govuk.utils import normalised_text


class NormalisedTextTests(TestCase):
    """The plain text a search summary is built from."""

    def test_one_block_is_separated_from_the_next(self):
        self.assertEqual(
            normalised_text("<p>You can:</p><ul><li>guide the organisation</li></ul>"),
            "You can: guide the organisation",
        )

    def test_each_list_item_is_separated_from_the_one_after_it(self):
        self.assertEqual(
            normalised_text("<ul><li>first thing</li><li>second thing</li></ul>"),
            "first thing second thing",
        )

    def test_a_line_break_separates_the_lines_it_divides(self):
        self.assertEqual(
            normalised_text("<p>first line<br>second line</p>"),
            "first line second line",
        )

    def test_character_references_come_back_as_the_characters_they_name(self):
        self.assertEqual(
            normalised_text("<p>the organisation&#x27;s data</p>"),
            "the organisation's data",
        )
        self.assertEqual(
            normalised_text("<p>research &amp; analysis</p>"),
            "research & analysis",
        )

    def test_the_separating_space_does_not_double_up(self):
        self.assertEqual(
            normalised_text("<p>a paragraph</p>\n<p>and another</p>"),
            "a paragraph and another",
        )

    def test_nothing_at_all_gives_an_empty_string(self):
        self.assertEqual(normalised_text(None), "")
        self.assertEqual(normalised_text(""), "")
