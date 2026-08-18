from django.test import SimpleTestCase

from govuk.utils import LARGEST_ROW_ID, normalised_text, row_id_from_text


class NormalisedTextTests(SimpleTestCase):
    def test_tags_are_removed_and_whitespace_collapsed(self):
        self.assertEqual(normalised_text(" <b>data</b>\n analyst "), "data analyst")

    def test_nothing_is_the_empty_string(self):
        self.assertEqual(normalised_text(None), "")


class RowIdFromTextTests(SimpleTestCase):
    """The three ways a filter can look like a number and not be one.

    Every one of them reached ``int`` or the database straight from a query
    string or an uploaded file, and every one of them was a 500 rather than a
    filter matching nothing.
    """

    def test_an_ordinary_id_is_read(self):
        self.assertEqual(row_id_from_text("42"), 42)
        self.assertEqual(row_id_from_text(" 42 "), 42)
        self.assertEqual(row_id_from_text(42), 42)

    def test_an_id_written_in_another_script_is_still_an_id(self):
        self.assertEqual(row_id_from_text("٣"), 3)
        self.assertEqual(row_id_from_text("੫"), 5)

    def test_a_digit_int_cannot_read_names_no_row(self):
        for value in ("²", "³", "₂", "½"):
            with self.subTest(value=value):
                self.assertIsNone(row_id_from_text(value))

    def test_more_digits_than_int_reads_names_no_row(self):
        self.assertIsNone(row_id_from_text("1" * 4301))

    def test_a_number_larger_than_a_row_id_names_no_row(self):
        self.assertEqual(row_id_from_text(str(LARGEST_ROW_ID)), LARGEST_ROW_ID)
        self.assertIsNone(row_id_from_text(str(LARGEST_ROW_ID + 1)))

    def test_anything_that_is_no_number_names_no_row(self):
        for value in ("", "  ", None, "abc", "1.5", "-1", "+1", "1_0", "12abc"):
            with self.subTest(value=value):
                self.assertIsNone(row_id_from_text(value))
