"""CSV downloads shown as the GOV.UK attachment component -- CS32-3313.

The acceptance criterion is that "attachments follow GOV.UK's attachment
component". The live service does not: its download page is three bare links
to S3 files rebuilt on a schedule, which were 16 days stale when this was
written. So these tests are against the component as GOV.UK defines it rather
than against what is there now.

The size is the part worth testing hardest. It is a claim made to a reader
about a file they have not downloaded yet, on a page that offers a 2.4 MB
spreadsheet, and it is computed rather than stored -- so a test that it
matches the bytes actually served is the one that keeps it honest.
"""

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from wagtail.models import Site

from govuk.attachments import (
    csv_download_size,
    format_file_size,
    measure_csv_download,
    rewrite_csv_download_links,
)
from govuk.models import ContentPage


def _feature_flags() -> dict[str, bool]:
    return {
        "SKILLS": True,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
    }


class FileSizeWordingTests(TestCase):
    """GOV.UK writes a file size to three significant digits, in binary units."""

    def test_a_small_file_is_counted_in_bytes(self):
        self.assertEqual(format_file_size(0), "0 Bytes")
        self.assertEqual(format_file_size(1), "1 Byte")
        self.assertEqual(format_file_size(999), "999 Bytes")

    def test_a_kilobyte_is_1024_bytes_not_1000(self):
        self.assertEqual(format_file_size(1024), "1.00 KB")
        self.assertEqual(format_file_size(1023), "1023 Bytes")

    def test_three_significant_digits(self):
        self.assertEqual(format_file_size(213095), "208 KB")
        self.assertEqual(format_file_size(90183), "88.1 KB")
        self.assertEqual(format_file_size(2498642), "2.38 MB")

    def test_a_missing_size_says_nothing_rather_than_zero(self):
        """A file whose size could not be worked out must not claim to be empty."""
        self.assertEqual(format_file_size(None), "")


@override_settings(FEATURE_FLAGS=_feature_flags())
class DownloadSizeTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_the_size_shown_is_the_size_served(self):
        """The whole point of the metadata line, so it is measured both ways."""
        for name in ("roles", "skills", "changelog"):
            with self.subTest(name=name):
                served = self.client.get(
                    reverse("govuk_framework_csv", kwargs={"name": name})
                )
                self.assertEqual(served.status_code, 200)
                self.assertEqual(
                    measure_csv_download(name), len(served.getvalue())
                )

    def test_an_unknown_download_has_no_size(self):
        self.assertIsNone(measure_csv_download("payroll"))

    def test_the_size_is_cached_rather_than_generated_per_view(self):
        """Generating the roles CSV costs ~160ms; a page view should not."""
        first = csv_download_size("roles")

        with self.assertNumQueries(0):
            second = csv_download_size("roles")

        self.assertEqual(first, second)


@override_settings(FEATURE_FLAGS=_feature_flags())
class AttachmentRewriteTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_a_paragraph_holding_only_a_csv_link_becomes_the_component(self):
        html = rewrite_csv_download_links(
            '<p><a href="/download/roles.csv">Role content</a></p>'
        )

        self.assertIn('<section class="gem-c-attachment', html)
        self.assertIn('class="govuk-link gem-c-attachment__link"', html)
        self.assertIn(">Role content</a>", html)
        self.assertNotIn("<p><a", html)

    def test_the_component_says_what_kind_of_file_and_how_big(self):
        html = rewrite_csv_download_links(
            '<p><a href="/download/changelog.csv">Change notes</a></p>'
        )

        self.assertIn('title="Comma-separated Values"', html)
        self.assertIn(">CSV</abbr>", html)
        self.assertIn(format_file_size(measure_csv_download("changelog")), html)

    def test_a_trailing_csv_in_the_title_is_dropped(self):
        """The editor wrote it because nothing else said so. Now the card does."""
        html = rewrite_csv_download_links(
            '<p><a href="/download/skills.csv">Skill content (CSV)</a></p>'
        )

        self.assertIn(">Skill content</a>", html)
        self.assertNotIn("Skill content (CSV)", html)

    def test_a_link_inside_a_sentence_stays_a_link(self):
        """A file card wedged mid-sentence reads as neither one thing nor the other."""
        html = '<p>You can also <a href="/download/roles.csv">download the roles</a> today.</p>'

        self.assertEqual(rewrite_csv_download_links(html), html)

    def test_a_csv_that_is_not_one_of_ours_is_left_alone(self):
        html = '<p><a href="/download/payroll.csv">Payroll</a></p>'

        self.assertEqual(rewrite_csv_download_links(html), html)

    def test_an_ordinary_link_is_left_alone(self):
        html = '<p><a href="/skills">Skills A to Z</a></p>'

        self.assertEqual(rewrite_csv_download_links(html), html)

    def test_html_with_no_downloads_is_returned_unchanged(self):
        html = "<p>Nothing to download here.</p>"

        self.assertIs(rewrite_csv_download_links(html), html)

    def test_every_download_on_a_page_is_rewritten(self):
        html = rewrite_csv_download_links(
            '<p><a href="/download/roles.csv">Roles</a></p>'
            "<h3>File details</h3>"
            '<p><a href="/download/skills.csv">Skills</a></p>'
            '<p><a href="/download/changelog.csv">Changes</a></p>'
        )

        self.assertEqual(html.count('<section class="gem-c-attachment'), 3)


@override_settings(FEATURE_FLAGS=_feature_flags())
class DownloadPageTests(TestCase):
    """The component reaching a real page through the template, not just the function."""

    def setUp(self):
        cache.clear()
        site = Site.objects.get(is_default_site=True)
        self.page = site.root_page.specific.add_child(
            instance=ContentPage(
                title="Download framework content",
                slug="download",
                body=(
                    "<h2>Role descriptions</h2>"
                    '<p><a href="/download/roles.csv">'
                    "Role content - Capability Framework (CSV)</a></p>"
                    "<h3>File details</h3>"
                ),
            )
        )
        self.page.save_revision().publish()

    def test_the_page_signposts_the_file_as_an_attachment(self):
        html = self.client.get(self.page.url).content.decode()

        self.assertIn('<section class="gem-c-attachment', html)
        self.assertIn("Role content - Capability Framework", html)
        self.assertIn("CSV", html)

    def test_the_title_sits_under_the_section_heading_not_beside_it(self):
        """h2 "Role descriptions" then h3 for the file, so h3 "File details" follows."""
        html = self.client.get(self.page.url).content.decode()

        self.assertIn('<h3 class="gem-c-attachment__title">', html)

    def test_the_thumbnail_is_not_a_second_stop_for_a_keyboard(self):
        """It is the same link as the title, so it is skipped rather than repeated."""
        html = self.client.get(self.page.url).content.decode()

        self.assertIn('tabindex="-1" aria-hidden="true"', html)

    def test_the_rest_of_the_body_still_renders(self):
        html = self.client.get(self.page.url).content.decode()

        self.assertIn("<h2>Role descriptions</h2>", html)
        self.assertIn("<h3>File details</h3>", html)
