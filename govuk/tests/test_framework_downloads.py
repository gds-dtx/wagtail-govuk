"""The framework's three CSVs, downloadable from the site and shown as the
GOV.UK attachment component.

The size on the component is a claim about a file the reader has not
downloaded yet, computed rather than stored, so it is checked against the bytes
actually served.
"""

import codecs
import csv
import io
import tempfile
import time
from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from wagtail.models import Site

from govuk.capability_framework import csv_downloads
from govuk.capability_framework.attachments import (
    csv_download_size,
    format_file_size,
    measure_csv_download,
    rewrite_csv_download_links,
)
from govuk.capability_framework.csv_downloads import (
    CHANGELOG_COLUMNS,
    ROLE_COLUMNS,
    SKILL_COLUMNS,
)
from govuk.models import (
    FrameworkContentPage,
    GovukChangelogEntry,
    GovukRole,
    GovukSkill,
)
from govuk.tests.helpers import (
    feature_flags,
    make_framework_main_page,
    publish_page,
    text_body,
)


@override_settings(FEATURE_FLAGS=feature_flags(skills=True))
class FrameworkCsvDownloadTests(TestCase):
    """Built from the published content at the moment of asking, so what a
    reader downloads is what the site says."""

    def setUp(self):
        self.skill = GovukSkill.objects.create(
            title="Prototyping",
            body="<p>Building throwaway versions to test an idea.</p>",
            working_points=[{"type": "point", "value": "build a prototype"}],
        )
        self.role = GovukRole.objects.create(
            title="Interaction designer",
            family="User-centred design",
            body="<p>Designs interactions.</p>",
            levels=[
                {
                    "type": "level",
                    "value": {
                        "title": "Senior interaction designer",
                        "description": "<p>Leads design.</p>",
                        "skills": [{"skill": self.skill.pk, "level": "working"}],
                    },
                }
            ],
        )
        GovukChangelogEntry.objects.create(
            date="2026-08-01", note="<p>Prototyping guidance updated</p>", skill=self.skill
        )

    def _rows(self, name):
        response = self.client.get(reverse("govuk_framework_csv", args=[name]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertIn(".csv", response["Content-Disposition"])
        # utf-8-sig: the file carries a byte order mark for Excel, and a
        # reader that does not want it strips it the way this one does.
        return list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))

    def test_the_roles_csv_carries_the_published_columns_and_the_role(self):
        rows = self._rows("roles")

        self.assertEqual(rows[0], ROLE_COLUMNS)
        data = {tuple(row[:2]) for row in rows[1:]}
        self.assertIn(("User-centred design", "Interaction designer"), data)

    def test_the_skills_csv_carries_the_published_columns_and_the_skill(self):
        rows = self._rows("skills")

        self.assertEqual(rows[0], SKILL_COLUMNS)
        by_name = {row[0]: row for row in rows[1:]}
        self.assertIn("Prototyping", by_name)
        # The roles-that-require column comes from the content model, so it is
        # in step with the role created above by construction.
        self.assertIn("Interaction designer", by_name["Prototyping"][-1])

    def test_the_changelog_csv_names_the_skill_the_entry_belongs_to(self):
        rows = self._rows("changelog")

        self.assertEqual(rows[0], CHANGELOG_COLUMNS)
        self.assertIn(["2026-08-01", "Prototyping", "Prototyping guidance updated"], rows[1:])

    def test_a_download_is_the_content_at_the_moment_of_asking(self):
        before = self._rows("skills")
        GovukSkill.objects.create(title="Zonal networking", body="<p>New skill.</p>")

        after = self._rows("skills")

        self.assertEqual(len(after), len(before) + 1)
        self.assertIn("Zonal networking", {row[0] for row in after[1:]})

    def test_every_download_opens_as_utf_8_in_excel(self):
        """Excel guesses a CSV's encoding from its first bytes, and without a
        byte order mark falls back to Mac Roman on macOS."""
        for name in ("roles", "skills", "changelog"):
            with self.subTest(name=name):
                response = self.client.get(
                    reverse("govuk_framework_csv", args=[name])
                )
                self.assertTrue(
                    response.content.startswith(codecs.BOM_UTF8),
                    f"{name}.csv has no byte order mark",
                )

    def test_the_curly_quotes_in_a_change_note_survive_the_round_trip(self):
        """Grade changes are written with curly quotes, the characters that
        came out mangled."""
        GovukChangelogEntry.objects.create(
            date="2026-08-02",
            skill=self.skill,
            note="<p>from \u2018HEO\u2019 to \u2018HEO and SEO\u2019</p>",
        )

        response = self.client.get(reverse("govuk_framework_csv", args=["changelog"]))
        text = response.content.decode("utf-8-sig")

        self.assertIn("\u2018HEO\u2019", text)
        self.assertNotIn("\u2018HEO\u2019".encode().decode("mac-roman"), text)

    def test_a_name_that_is_not_a_download_is_not_found(self):
        response = self.client.get("/download/passwords.csv")
        self.assertEqual(response.status_code, 404)

    @override_settings(FEATURE_FLAGS=feature_flags())
    def test_without_the_skills_feature_there_is_no_download(self):
        response = self.client.get(reverse("govuk_framework_csv", args=["roles"]))
        self.assertEqual(response.status_code, 404)

    def test_a_shared_skill_body_is_converted_once_not_once_per_row(self):
        # The roles CSV writes a skill's description on every row requiring
        # it, and converting rich text back to prose is most of the file's cost.
        for title in ("Content designer", "Service designer"):
            GovukRole.objects.create(
                title=title,
                family="User-centred design",
                body="<p>Designs things.</p>",
                levels=[
                    {
                        "type": "level",
                        "value": {
                            "title": "Senior",
                            "description": "<p>Leads design.</p>",
                            "skills": [{"skill": self.skill.pk, "level": "working"}],
                        },
                    }
                ],
            )

        real = csv_downloads.rich_html_to_text
        with mock.patch.object(csv_downloads, "rich_html_to_text", side_effect=real) as convert:
            rows = self._rows("roles")

        prototyping_rows = [row for row in rows[1:] if row[5] == "Prototyping"]
        self.assertEqual(len(prototyping_rows), 3)
        bodies = [call.args[0] for call in convert.call_args_list]
        self.assertEqual(bodies.count(self.skill.body), 1)

    def test_the_command_and_the_download_write_the_same_file(self):
        with tempfile.TemporaryDirectory() as outdir:
            call_command("export_capability_framework", outdir, stdout=io.StringIO())
            # Bytes, not read_text(): text mode would fold the \r\n line
            # endings the csv module writes and hide a real difference.
            on_disk = (Path(outdir) / "skills.csv").read_bytes()

        response = self.client.get(reverse("govuk_framework_csv", args=["skills"]))
        self.assertEqual(response.content, on_disk)


class FileSizeWordingTests(TestCase):
    """GOV.UK writes a file size to three significant digits, in binary units."""

    def test_a_file_size_is_written_as_govuk_writes_it(self):
        for size, wording in (
            (0, "0 Bytes"),
            (1, "1 Byte"),
            (999, "999 Bytes"),
            (1023, "1023 Bytes"),
            (1024, "1.00 KB"),
            (213095, "208 KB"),
            (90183, "88.1 KB"),
            (2498642, "2.38 MB"),
            # A file whose size could not be worked out must not claim to be empty.
            (None, ""),
        ):
            with self.subTest(size=size):
                self.assertEqual(format_file_size(size), wording)


@override_settings(FEATURE_FLAGS=feature_flags(skills=True))
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


@override_settings(FEATURE_FLAGS=feature_flags(skills=True))
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

    def test_a_paragraph_wagtail_has_stamped_still_becomes_the_component(self):
        """Wagtail writes data-block-key onto every paragraph it re-saves.

        Matching only a bare <p> meant the cards turned back into plain links
        the first time anyone opened the download page in the editor, which is
        what happened when the role navigation was switched on for it.
        """
        html = rewrite_csv_download_links(
            '<p data-block-key="3a0kt">'
            '<a href="/download/roles.csv">Role content</a></p>'
        )

        self.assertIn('<section class="gem-c-attachment', html)
        self.assertNotIn("data-block-key", html)

    def test_html_that_is_not_a_lone_download_is_left_alone(self):
        for name, html in (
            (
                "preformatted block",
                '<pre><a href="/download/roles.csv">Role content</a></pre>',
            ),
            # The title used to run on to the last </a>, swallowing a second link.
            (
                "paragraph with a second link",
                '<p><a href="/download/roles.csv">Role content</a> '
                '<a href="/skills">and the skills</a></p>',
            ),
            # A file card wedged mid-sentence reads as neither one thing nor the other.
            (
                "link inside a sentence",
                '<p>You can also <a href="/download/roles.csv">download the roles</a> today.</p>',
            ),
            ("csv that is not ours", '<p><a href="/download/payroll.csv">Payroll</a></p>'),
            ("ordinary link", '<p><a href="/skills">Skills A to Z</a></p>'),
        ):
            with self.subTest(name):
                self.assertEqual(rewrite_csv_download_links(html), html)

    # Generous: the linear pattern takes a few milliseconds on these inputs and
    # the quadratic one took a second or more.
    BUDGET_SECONDS = 0.5

    def test_unclosed_tags_are_scanned_in_linear_time(self):
        """Raw HTML in a content page can carry anything, and this runs on
        every render."""
        for name, html in (
            # A tag interior written as [^>]* restarted its scan from each one.
            ("paragraph tags", "<p " * 80_000 + '<a href="/download/roles.csv">x</a></p>'),
            # A title written as .*? looked for its </a> from each one.
            ("download links", '<p><a href="/download/roles.csv">' * 20_000),
        ):
            with self.subTest(name):
                started = time.monotonic()
                rewrite_csv_download_links(html)
                self.assertLess(time.monotonic() - started, self.BUDGET_SECONDS)

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


@override_settings(FEATURE_FLAGS=feature_flags())
class DownloadsBelongToTheFrameworkTests(TestCase):
    """The three CSVs are the framework's, and so is the card that offers them.

    ``page_body`` runs this over every content page on every site, and the
    download URL is registered unconditionally -- it is the view that 404s
    without the flag -- so nothing in the reverse or the regex stops a
    non-framework page being rewritten.
    """

    def setUp(self):
        cache.clear()

    def test_a_csv_link_stays_an_ordinary_link_without_the_framework(self):
        html = '<p><a href="/download/roles.csv">Role content</a></p>'

        self.assertEqual(rewrite_csv_download_links(html), html)

    def test_the_framework_tables_are_not_read_to_size_a_file(self):
        """Measuring one of these means running the writer over GovukRole."""
        with self.assertNumQueries(0):
            rewrite_csv_download_links(
                '<p><a href="/download/roles.csv">Roles</a></p>'
                '<p><a href="/download/skills.csv">Skills</a></p>'
            )


@override_settings(FEATURE_FLAGS=feature_flags(skills=True))
class DownloadPageTests(TestCase):
    """The component reaching a real page through the template, not just the function."""

    def setUp(self):
        cache.clear()
        page = publish_page(
            "Download framework content",
            "<h2>Role descriptions</h2>"
            '<p><a href="/download/roles.csv">'
            "Role content - Capability Framework (CSV)</a></p>"
            "<h3>File details</h3>",
        )
        self.html = self.client.get(page.url).content.decode()

    def test_the_page_signposts_the_file_as_an_attachment(self):
        self.assertIn('<section class="gem-c-attachment', self.html)
        self.assertIn("Role content - Capability Framework", self.html)
        self.assertIn("CSV", self.html)

    def test_the_title_sits_under_the_section_heading_not_beside_it(self):
        """h2 "Role descriptions" then h3 for the file, so h3 "File details" follows."""
        self.assertIn('<h3 class="gem-c-attachment__title">', self.html)

    def test_the_thumbnail_is_not_a_second_stop_for_a_keyboard(self):
        """It is the same link as the title, so it is skipped rather than repeated."""
        self.assertIn('tabindex="-1" aria-hidden="true"', self.html)

    def test_the_rest_of_the_body_still_renders(self):
        self.assertIn("<h2>Role descriptions</h2>", self.html)
        self.assertIn("<h3>File details</h3>", self.html)


@override_settings(FEATURE_FLAGS=feature_flags(skills=True))
class FrameworkDownloadPageTests(TestCase):
    def test_a_framework_content_page_shows_the_files_as_attachments(self):
        """The download page is a framework content page, for the role navigation."""
        main_page = make_framework_main_page(Site.objects.get(is_default_site=True).root_page)
        page = main_page.add_child(
            instance=FrameworkContentPage(
                title="Download",
                slug="download",
                body=text_body(
                    '<p><a href="/download/roles.csv">Role descriptions</a></p>'
                    '<p><a href="/download/skills.csv">Skills</a></p>'
                    '<p><a href="/download/changelog.csv">Changelog</a></p>'
                ),
            )
        )
        page.save_revision().publish()

        html = self.client.get(page.url).content.decode()

        self.assertEqual(html.count('<section class="gem-c-attachment'), 3)
        self.assertIn("role-nav", html)
