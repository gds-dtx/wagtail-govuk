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

    def test_a_nested_list_is_separated_from_the_item_it_sits_under(self):
        """Indenting a list item is one keystroke in the editor, and it nests
        the sublist inside the item above it. Nothing closes between the two,
        so only the opening tag stands where a space belongs."""
        self.assertEqual(
            normalised_text(
                "<ul><li>Lead a team<ul><li>and its budget</li></ul></li></ul>"
            ),
            "Lead a team and its budget",
        )

    def test_the_same_of_a_block_that_opens_straight_after_text(self):
        self.assertEqual(
            normalised_text("Introduction<ul><li>a point</li></ul>"),
            "Introduction a point",
        )
        self.assertEqual(
            normalised_text("As they said<blockquote><p>a quote</p></blockquote>"),
            "As they said a quote",
        )

    def test_what_the_editor_actually_writes_for_an_indented_item(self):
        """Not a hand-written approximation of it.

        Wagtail's converter is what turns the editor's list depth into HTML,
        so it is what decides whether this function ever meets the shape.
        """
        import json

        from wagtail.admin.rich_text.converters.contentstate import (
            ContentstateConverter,
        )

        converter = ContentstateConverter(features=["h2", "h3", "bold", "link", "ul", "ol"])
        html_from_editor = converter.to_database_format(
            json.dumps(
                {
                    "blocks": [
                        {
                            "key": "a",
                            "type": "unordered-list-item",
                            "text": "Lead a team",
                            "depth": 0,
                            "inlineStyleRanges": [],
                            "entityRanges": [],
                        },
                        {
                            "key": "b",
                            "type": "unordered-list-item",
                            "text": "and its budget",
                            "depth": 1,
                            "inlineStyleRanges": [],
                            "entityRanges": [],
                        },
                    ],
                    "entityMap": {},
                }
            )
        )

        self.assertIn("<ul><li", html_from_editor.replace("</li>", ""))
        self.assertEqual(
            normalised_text(html_from_editor), "Lead a team and its budget"
        )

    def test_a_word_broken_by_inline_markup_is_not_split_in_two(self):
        """The boundary is between blocks, not inside them: emphasis and links
        sit in the middle of a sentence and must not gain a space."""
        self.assertEqual(normalised_text("<p>hyper<b>text</b></p>"), "hypertext")
        self.assertEqual(
            normalised_text('<p>data<a href="/x">set</a></p>'), "dataset"
        )

    def test_a_tag_that_merely_starts_with_a_block_tags_letters_is_left_alone(self):
        """`<pre>` is not `<p>`, and matching it as one would put a space in
        the middle of preformatted text."""
        self.assertEqual(normalised_text("<p>a</p><pre>b</pre>"), "a b")
        self.assertEqual(normalised_text("<pre>one two</pre>"), "one two")

    def test_nothing_at_all_gives_an_empty_string(self):
        self.assertEqual(normalised_text(None), "")
        self.assertEqual(normalised_text(""), "")
