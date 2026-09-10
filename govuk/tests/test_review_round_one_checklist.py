"""Antony's first review of the migration, 11 August to 7 September 2026.

The review compared this build against the live DDaT Capability Framework page
by page and asked, for 48 numbered items, for "the exact same functionality"
unless a difference was stated. Every item he marked "Fix needed" was fixed
somewhere between PRs #44 and #88, but the fixes are scattered across
templates, CSS and the JavaScript bundle, and the re-architecture in #72
rewrote most of those templates. A fix nobody is testing is a fix the next
rewrite silently drops.

So this module is the checklist itself, executable: one test per item that can
be decided from what the site renders or ships, named after the item and
quoting what was asked for.

What is asserted elsewhere rather than here, so that the review's rows are all
accounted for: the feedback journey and the record it writes (T46, T47) in
``test_page_feedback.py`` and ``test_feedback_view.py``; who may edit which
snippet (T50 to T55) in ``test_editor_snippet_permissions.py``; the CSV
contents and sizes behind the download page (T49) in
``test_csv_attachments.py`` and ``test_framework_download.py``; the search
endpoint's ranking and shape (T42 to T44) in ``test_search_suggest.py`` and
``test_search_backend.py``; tables (T19) in ``test_content_page_tables.py``;
and the automated part of WCAG (T35) by axe-core against a running instance,
which no unit test can stand in for.

What is not asserted at all, because it is a judgement about content or a
question the review left with the designer rather than anything the code
decides: T11, T13, T39 and T45 (wording that matches live, checked by reading
both), T40 (withdrawn -- the fallback is now editorial), and the three
designer questions under T11.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from django.test import TestCase, override_settings
from django.urls import reverse
from wagtail.models import Site

from govuk.models import (
    CapabilityFrameworkWordingSettings,
    ContentPage,
    CustomiseSettings,
    FrameworkContentPage,
    FrameworkSkillsPage,
    GovukChangelogEntry,
    GovukRole,
    GovukSkill,
)
from govuk.tests.framework_helpers import make_framework_main_page, role_url

STATIC = Path(__file__).resolve().parents[1] / "static"
MAIN_CSS = (STATIC / "main.css").read_text()
# Comments sit between the closing brace of one rule and the selector of the
# next, so they have to come out before a selector can be read off.
CSS = re.sub(r"/\*.*?\*/", "", MAIN_CSS, flags=re.S)
MAIN_JS = (STATIC / "main.js").read_text()


def _feature_flags() -> dict[str, bool]:
    return {
        "SKILLS": True,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
    }


def _rules(css: str, selector: str) -> str:
    """Every declaration block whose selector list names this selector.

    Joined rather than taken one at a time because the stylesheet states some
    of these twice, once for the desktop layout and once inside a media query,
    and either may be the one carrying the declaration being asserted on.
    """
    found = []
    for match in re.finditer(r"([^{}]*)\{([^{}]*)\}", css):
        head = match.group(1).split("{")[-1]
        if selector in [part.strip() for part in head.split(",")]:
            found.append(match.group(2))
    return "\n".join(found)


def _strip_tags(html: str) -> str:
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


class ReviewChecklistTestCase(TestCase):
    """One framework with two roles, two skills and a supporting page."""

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific
        self.main_page = make_framework_main_page(self.root_page)

        self.skill = GovukSkill.objects.create(
            title="Data visualisation",
            body="<p>Presenting data so it can be understood.</p>",
            working_points=[{"type": "point", "value": "Presents data clearly."}],
            expert_points=[{"type": "point", "value": "Sets the standard for data."}],
        )
        self.analyst = GovukRole.objects.create(
            title="Data analyst",
            family="Data",
            body="<p>A data analyst collects, manages and shares data.</p>",
            levels=[
                {
                    "type": "level",
                    "value": {
                        "title": "Associate data analyst",
                        "description": "<p>An associate supports data analysis.</p>",
                        "skills": [{"skill": self.skill.pk, "level": "working"}],
                    },
                },
                {
                    "type": "level",
                    "value": {
                        "title": "Lead data analyst",
                        "description": "<p>A lead runs data analysis.</p>",
                        "skills": [{"skill": self.skill.pk, "level": "expert"}],
                    },
                },
            ],
        )
        self.chief = GovukRole.objects.create(
            title="Chief data officer",
            family="Chief digital and data",
            is_senior_civil_service=True,
            body="<p>The chief data officer leads the data function.</p>",
            scs_skills=[{"type": "skill", "value": self.skill.pk}],
        )
        self.skills_page = self.main_page.add_child(
            instance=FrameworkSkillsPage(title="Skills A to Z", slug="skills")
        )
        self.skills_page.save_revision().publish()

    def role_html(self, role) -> str:
        response = self.client.get(role_url(self.main_page, role))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()


@override_settings(FEATURE_FLAGS=_feature_flags())
class RolePageChecklistTests(ReviewChecklistTestCase):
    """T12 and T14 to T17, T30 to T32: what a role page has to show."""

    def test_t14_1_the_role_lead_says_what_the_role_does(self):
        """"Find out what a {role} in government does and the skills you need
        to do the role at each level.\""""
        html = self.role_html(self.analyst)

        self.assertIn(
            "Find out what a data analyst in government does and the skills "
            "you need to do the role at each level.",
            html,
        )

    def test_t14_2_the_role_levels_intro_counts_the_levels(self):
        """"There are 4 [role] role levels, from [first] to [last]." -- the
        hardcoded sentence the review found missing from the template."""
        html = self.role_html(self.analyst)

        self.assertIn("There are 2 data analyst role levels", html)
        self.assertIn("from associate data analyst to lead data analyst", html)

    def test_t14_3_an_scs_role_introduces_its_skills_table(self):
        """The sentence above an SCS role's skills, missing at review time."""
        html = self.role_html(self.chief)

        self.assertIn("context and challenges in your organisation", html)

    def test_t15_a_role_lists_the_skills_it_shares_with_others(self):
        html = self.role_html(self.analyst)

        self.assertIn("Data visualisation", html)

    def test_t16_1_an_scs_role_names_the_roles_that_lead_to_it(self):
        """"Roles that could lead to {SCS role name}\"."""
        self.chief.roles_that_could_lead_here = [
            {"type": "role", "value": self.analyst.pk}
        ]
        self.chief.save()

        html = self.role_html(self.chief)

        self.assertIn("Roles that could lead to chief data officer", html)
        self.assertIn(role_url(self.main_page, self.analyst), html)

    def test_t16_2_a_role_names_the_scs_roles_it_could_lead_to(self):
        """"Senior Civil Service roles that {role name} could lead to\"."""
        self.chief.roles_that_could_lead_here = [
            {"type": "role", "value": self.analyst.pk}
        ]
        self.chief.save()

        html = self.role_html(self.analyst)

        self.assertIn(
            "Senior Civil Service roles that data analyst could lead to", html
        )
        self.assertIn(role_url(self.main_page, self.chief), html)

    def test_t17_changelog_entries_are_a_size_down_from_the_role_content(self):
        """"Text size for changelog entries should be smaller, using
        'class=govuk-body-s', to help differentiate from main role content\"."""
        GovukChangelogEntry.objects.create(
            role=self.analyst,
            date=date(2026, 8, 28),
            note="<p>Updated the levels.</p><ul><li>A bullet.</li></ul>",
        )

        html = self.role_html(self.analyst)
        block = re.search(
            r'<div class="govuk-body-s">(.*?)</div>', html, re.S
        )

        self.assertIsNotNone(block, "the changelog note is in a govuk-body-s block")
        self.assertIn("Updated the levels.", block.group(1))
        self.assertIn("A bullet.", block.group(1))

    def test_t30_the_contents_links_point_at_anchors_that_exist(self):
        html = self.role_html(self.analyst)
        anchors = set(re.findall(r'id="([^"]+)"', html))
        contents = re.findall(r'href="#([^"]+)"', html)

        self.assertTrue(contents, "the role page has a contents list")
        self.assertEqual(
            [target for target in contents if target not in anchors],
            [],
            "every contents link lands on an id that is on the page",
        )

    def test_t31_1_skill_names_are_links_on_a_role_that_is_not_scs(self):
        """"Skill names should be clickable - currently only SCS roles have
        clickable skills\"."""
        html = self.role_html(self.analyst)

        self.assertIn(
            f'href="{self.skills_page.url}#{self.skill.slug}"',
            html,
            "the skill in a role level's table deep-links to Skills A to Z",
        )

    def test_t31_2_a_link_in_a_table_is_not_underlined_until_it_is_used(self):
        """"Links in tables (skill names and role names) should not be
        underlined when not focused or clicked", with a thicker underline on
        hover."""
        self.assertIn(
            "text-decoration-line: none", _rules(CSS, ".govuk-table a.skill-name")
        )
        self.assertIn(
            "text-decoration-line: underline",
            _rules(CSS, ".govuk-table a.skill-name:hover"),
        )

    def test_t32_see_all_updates_jumps_to_the_updates_at_the_foot(self):
        GovukChangelogEntry.objects.create(
            role=self.analyst, date=date(2026, 8, 28), note="<p>Updated.</p>"
        )

        html = self.role_html(self.analyst)
        link = re.search(r'href="#([^"]*update[^"]*)"', html)

        self.assertIsNotNone(link, "the role page links to its own updates")
        self.assertIn(f'id="{link.group(1)}"', html)

    def test_t32_a_role_never_updated_has_no_see_all_updates_link(self):
        html = self.role_html(self.analyst)

        self.assertNotIn("See all updates", html)


@override_settings(FEATURE_FLAGS=_feature_flags())
class NavigationChecklistTests(ReviewChecklistTestCase):
    """T24 to T29, T34 and T41: the side navigation and what replaces it."""

    def test_t25_the_security_architect_link_is_the_role_not_security_txt(self):
        """The reviewer found this link going to a security.txt disclosure page.
        That was an edge redirect in front of the service, but the navigation
        the application renders has to be right for fixing the edge to help."""
        GovukRole.objects.create(title="Security architect", family="Architecture")

        html = self.role_html(self.analyst)

        self.assertIn(role_url(self.main_page, "security-architect"), html)
        self.assertNotIn("security.txt", html)

    def test_t26_the_further_resources_group_links_the_supporting_pages(self):
        page = self.main_page.add_child(
            instance=FrameworkContentPage(title="Job grades", slug="job-grades")
        )
        page.save_revision().publish()

        html = self.role_html(self.analyst)
        group = re.search(
            r'aria-label="Further resources">(.*?)</nav>', html, re.S
        )

        self.assertIsNotNone(group)
        self.assertIn(page.url, group.group(1))
        self.assertIn(self.skills_page.url, group.group(1))

    def test_t27_the_home_link_in_the_navigation_points_at_the_home_page(self):
        html = self.role_html(self.analyst)
        group = re.search(r'aria-label="Home">(.*?)</nav>', html, re.S)

        self.assertIsNotNone(group, "the navigation opens with a Home group")
        self.assertIn(f'href="{self.main_page.url}"', group.group(1))

    def test_t28_1_a_role_reads_home_then_family_then_role_on_a_small_screen(self):
        """"Home > {Role family} > {Role name}", the family linking to its
        heading on the home page. Different from the live service on purpose:
        it replaces the navigation the small screen hides."""
        html = self.role_html(self.analyst)
        crumbs = re.search(
            r'<ol class="govuk-breadcrumbs__list">(.*?)</ol>', html, re.S
        )

        self.assertIsNotNone(crumbs)
        titles = [
            _strip_tags(item)
            for item in re.findall(r"<li[^>]*>(.*?)</li>", crumbs.group(1), re.S)
        ]
        self.assertEqual(titles, ["Home", "Data roles", "Data analyst"])
        home_url = self.site.root_page.get_url()
        self.assertIn(f'href="{home_url}#data-roles"', crumbs.group(1))

    def test_t28_2_a_supporting_page_reads_home_then_its_own_title(self):
        page = self.main_page.add_child(
            instance=FrameworkContentPage(title="Job grades", slug="job-grades")
        )
        page.save_revision().publish()

        html = self.client.get(page.url).content.decode()
        crumbs = re.search(
            r'<ol class="govuk-breadcrumbs__list">(.*?)</ol>', html, re.S
        )

        self.assertIsNotNone(crumbs)
        titles = [
            _strip_tags(item)
            for item in re.findall(r"<li[^>]*>(.*?)</li>", crumbs.group(1), re.S)
        ]
        self.assertEqual(titles, ["Home", "Job grades"])

    def test_t28_3_the_breadcrumbs_are_hidden_where_the_navigation_shows(self):
        """They stand in for the navigation, so the two never show at once."""
        html = self.role_html(self.analyst)

        self.assertIn("app-breadcrumbs--mobile-only", html)
        self.assertIn("display: none", _rules(CSS, ".app-breadcrumbs--mobile-only"))

    def test_t29_a_back_to_top_control_is_on_the_page_and_shown_by_scrolling(self):
        html = self.role_html(self.analyst)

        self.assertIn('id="back-to-top"', html)
        self.assertIn("back-to-top--visible", MAIN_JS)
        self.assertIn("back-to-top--visible", MAIN_CSS)

    def test_t34_the_service_name_in_the_navigation_links_to_the_home_page(self):
        settings = CustomiseSettings.for_site(self.site)
        settings.service_name_location = "navigation"
        settings.save()
        self.site.site_name = "Capability Framework"
        self.site.save()

        html = self.role_html(self.analyst)
        name = re.search(
            r'<span class="govuk-service-navigation__service-name">(.*?)</span>',
            html,
            re.S,
        )

        self.assertIsNotNone(name, "the service name sits in the service navigation")
        self.assertIn('href="/"', name.group(1))

    def test_t41_the_navigation_is_on_the_skills_a_to_z_page(self):
        html = self.client.get(self.skills_page.url).content.decode()

        self.assertIn('class="role-nav', html)
        self.assertIn("Data roles", html)

    def test_t24_1_the_navigation_group_headings_are_a_size_up_from_the_items(self):
        """"Menu item text should be slightly larger... Can be same as Design
        System site sub menu." The Design System's sub-navigation, and the live
        service at desktop, put the group headings at 1.1875rem and the items
        at 1rem."""
        self.assertIn("font-size: 1.1875rem", _rules(CSS, ".role-nav__title"))
        self.assertIn("font-size: 1rem", _rules(CSS, ".role-nav"))

    def test_t24_2_a_navigation_item_takes_the_design_systems_focus_state(self):
        """"Focus state appearance for items should match links in main page."
        Yellow behind, black text, and the black bar along the bottom edge."""
        focus = _rules(CSS, ".role-nav__item a:focus")

        self.assertIn("background-color: #fd0", focus)
        self.assertIn("color: #0b0c0c", focus)
        self.assertIn("box-shadow: 0 -2px #fd0, 0 4px #0b0c0c", focus)


@override_settings(FEATURE_FLAGS=_feature_flags())
class StylingChecklistTests(ReviewChecklistTestCase):
    """T18, T20, T22 and T57: sizes and states, decided in the stylesheet."""

    def test_t18_1_a_heading_2_an_editor_types_is_sized_as_a_heading(self):
        """"Heading 2 should use 'govuk-heading-l' to match CF site and
        differentiate from H3." The bundle styles the classes, not the tags, so
        the script adds the class an editor cannot."""
        self.assertRegex(MAIN_JS, r'H2:\s*"govuk-heading-l"')
        self.assertRegex(MAIN_JS, r'H3:\s*"govuk-heading-m"')

    def test_t18_2_a_heading_2_in_the_templates_is_already_the_right_size(self):
        html = self.role_html(self.analyst)

        for heading in re.findall(r"<h2[^>]*>", html):
            if "govuk-visually-hidden" in heading:
                continue  # there to structure the page, not to be read at a size
            self.assertIn("govuk-heading-l", heading, heading)

    def test_t20_a_link_takes_the_design_systems_focus_state(self):
        """Checked in the stylesheet rather than the browser: the Design
        System's own rule is what the bundle ships, so this is the local
        overrides not undoing it."""
        for selector in (".role-nav__item a:focus", ".role-nav__item--active a:focus"):
            self.assertIn("background-color: #fd0", _rules(CSS, selector), selector)

    def test_t22_a_skill_level_bar_is_a_fixed_size_not_the_cell_width(self):
        """"The 'skill level indicator' bars need to be smaller, and a fixed
        size. Currently they are trying to fill the cell width."

        Fixed in the sense the framework itself uses: the bar is a share of the
        column set by the stylesheet rather than whatever the cell's contents
        make it, and the cells inside it are equal. The share steps up as the
        column narrows, which is what the live service does, so the widest
        screen is the one to assert the size on.
        """
        bar = _rules(CSS, ".progress-bar__container")

        self.assertIn("width: 50%", bar)
        self.assertIn("table-layout: fixed", bar)
        self.assertNotIn("width: auto", bar)

    def test_t57_a_bullet_inside_a_changelog_note_is_the_smaller_size_too(self):
        """"Text in bullet points should also be smaller - currently appearing
        as the same size as page body text." The size is on the wrapper, so it
        has to be a rule that reaches the list inside it."""
        self.assertIn("wrapperTextSize", MAIN_JS)
        self.assertRegex(
            MAIN_JS,
            r"querySelectorAll\(\s*\n?\s*\"\.rich-text-content ul, \.rich-text-content ol\"",
        )
        self.assertIn("el.classList.add(wrapperSize)", MAIN_JS)


@override_settings(FEATURE_FLAGS=_feature_flags())
class SkillsAToZChecklistTests(ReviewChecklistTestCase):
    """T36 to T38: the skills index."""

    def test_t36_a_skill_has_an_id_a_role_page_can_deep_link_to(self):
        html = self.client.get(self.skills_page.url).content.decode()

        self.assertIn(f'id="{self.skill.slug}"', html)
        self.assertIn("openLinkedAccordionSection", MAIN_JS)
        self.assertIn("govuk-accordion__section-button", MAIN_JS)

    def test_t37_a_skill_level_reads_you_can_before_its_points(self):
        """"Each level description should start with 'You can:' then list the
        bullet points - as skills do on role pages\"."""
        html = self.client.get(self.skills_page.url).content.decode()
        wording = CapabilityFrameworkWordingSettings.for_site(self.site)

        self.assertEqual(wording.skill_points_intro, "You can:")
        self.assertIn("You can:", html)
        self.assertLess(
            html.index("You can:"), html.index("Presents data clearly."),
            "the lead-in comes before the points it leads into",
        )

    def test_t38_a_skill_names_the_roles_that_require_it_and_links_them(self):
        html = self.client.get(self.skills_page.url).content.decode()

        self.assertIn("Data analyst", html)
        self.assertIn(role_url(self.main_page, self.analyst), html)


@override_settings(FEATURE_FLAGS=_feature_flags())
class SearchChecklistTests(ReviewChecklistTestCase):
    """T42 to T44: the search the review could not test, now built."""

    def test_t42_a_search_finds_roles_and_skills(self):
        response = self.client.get(reverse("search"), {"query": "data"})
        html = response.content.decode()

        self.assertEqual(response.status_code, 200)
        self.assertIn("Data analyst", html)
        self.assertIn("Data visualisation", html)

    def test_t43_a_result_links_to_the_role_or_the_skill_it_names(self):
        """"Verify that results deep link to individual role levels and
        skills\"."""
        html = self.client.get(reverse("search"), {"query": "data"}).content.decode()

        self.assertIn(role_url(self.main_page, self.analyst), html)
        self.assertIn(f"{self.skills_page.url}#{self.skill.slug}", html)

    def test_t44_a_result_says_whether_it_is_a_role_or_a_skill(self):
        """"Verify that results clearly differentiate roles and role levels (as
        they often have the same name)\"."""
        html = self.client.get(reverse("search"), {"query": "data"}).content.decode()
        tags = re.findall(r'<strong class="govuk-tag[^"]*">\s*(.*?)\s*</strong>', html)

        self.assertIn("Role", tags)
        self.assertIn("Skill", tags)

    def test_t44_the_suggestions_say_the_same(self):
        items = self.client.get("/search/suggest/", {"q": "data"}).json()

        self.assertEqual(
            {item["text"]: item["type"] for item in items}.get("Data analyst"), "Role"
        )
        self.assertEqual(
            {item["text"]: item["type"] for item in items}.get("Data visualisation"),
            "Skill",
        )


@override_settings(FEATURE_FLAGS=_feature_flags())
class DownloadChecklistTests(ReviewChecklistTestCase):
    """T49: the download page's three files."""

    BODY = (
        '<p><a href="/download/roles.csv">Role descriptions</a></p>'
        '<p><a href="/download/skills.csv">Skills</a></p>'
        '<p><a href="/download/changelog.csv">Changelog</a></p>'
    )

    def test_t49_a_framework_page_still_shows_the_files_as_attachments(self):
        """"After giving the Download page the role navigation menu, the 3 file
        icons and accompanying text has reverted to a normal link." Giving it
        the navigation changed its page type, so the attachment rendering has
        to survive the change of type."""
        page = self.main_page.add_child(
            instance=FrameworkContentPage(
                title="Download", slug="download", body=self.BODY
            )
        )
        page.save_revision().publish()

        html = self.client.get(page.url).content.decode()

        self.assertEqual(html.count('<section class="gem-c-attachment'), 3)
        self.assertIn("role-nav", html)

    def test_t49_a_plain_page_shows_them_the_same_way(self):
        page = self.root_page.add_child(
            instance=ContentPage(title="Download", slug="download", body=self.BODY)
        )
        page.save_revision().publish()

        html = self.client.get(page.url).content.decode()

        self.assertEqual(html.count('<section class="gem-c-attachment'), 3)


@override_settings(FEATURE_FLAGS=_feature_flags())
class HomePageChecklistTests(ReviewChecklistTestCase):
    """T23, T33 and T58: the header and the home page's updates."""

    def test_t23_the_govuk_logo_links_to_govuk_not_to_this_service(self):
        """"The GOV.UK should link to gov.uk homepage\"."""
        html = self.client.get(self.main_page.url).content.decode()
        logo = re.search(r'<a href="([^"]*)" class="govuk-header__homepage-link"', html)

        self.assertIsNotNone(logo)
        self.assertEqual(logo.group(1), "https://www.gov.uk")

    def test_t23_the_service_name_is_in_the_service_navigation_not_the_header(self):
        settings = CustomiseSettings.for_site(self.site)
        settings.service_name_location = "navigation"
        settings.save()
        self.site.site_name = "Capability Framework"
        self.site.save()

        html = self.client.get(self.main_page.url).content.decode()

        self.assertIn("govuk-service-navigation__service-name", html)
        self.assertNotIn("govuk-header__service-name", html)

    def test_t58_the_site_updates_are_collapsed_until_the_reader_asks(self):
        """"Changelog entries on the homepage should be collapsed by default,
        then expand on click\"."""
        GovukChangelogEntry.objects.create(
            date=date(2026, 8, 28), note="<p>The framework was updated.</p>"
        )

        html = self.client.get(self.main_page.url).content.decode()

        self.assertIn("data-hide-text", html)
        self.assertIn("hidden", re.search(
            r"<[^>]*data-hide-text.*?>(.{0,600})", html, re.S
        ).group(0))

    def test_t33_see_all_updates_names_the_wording_that_closes_it_again(self):
        """"'hide all updates' then becomes visible, and should close it
        again\"."""
        wording = CapabilityFrameworkWordingSettings.for_site(self.site)

        self.assertTrue(wording.hide_all_updates_link_text)
        self.assertIn("hideText", MAIN_JS)


@override_settings(FEATURE_FLAGS=_feature_flags())
class EditingChecklistTests(ReviewChecklistTestCase):
    """T54 and T56: what the CMS lets an editor write."""

    def test_t56_a_quote_holds_a_line_break_without_starting_a_new_quote(self):
        """"Quote formatting (uses inset text component) should allow line
        breaks within a single quote. Currently a new line results in a new
        border\"."""
        from govuk.wagtail_hooks import LINE_BREAK_FEATURE

        page = self.main_page.add_child(
            instance=FrameworkContentPage(
                title="Upcoming changes",
                slug="upcoming-changes",
                body=(
                    '<div class="govuk-inset-text">'
                    "<p>First line.<br/>Second line.</p></div>"
                ),
            )
        )
        page.save_revision().publish()

        html = self.client.get(page.url).content.decode()
        insets = re.findall(r'<div class="govuk-inset-text">(.*?)</div>', html, re.S)

        self.assertEqual(LINE_BREAK_FEATURE, "line-break")
        self.assertEqual(len(insets), 1, "one quote, not one per line")
        self.assertIn("<br", insets[0])


@override_settings(FEATURE_FLAGS=_feature_flags())
class RemainingChecklistTests(ReviewChecklistTestCase):
    """T12, T19, T21, T48 and the CMS rows, which need one assertion each."""

    def test_t12_the_heading_stands_off_the_service_navigation(self):
        """"Space between page title and service navigation component should be
        larger... check current site for example." The live service leaves 25px
        between the phase banner and the heading."""
        self.assertIn("padding: 25px 0 0", _rules(CSS, ".govuk-main-wrapper"))

    def test_t19_a_table_an_editor_pastes_is_styled_like_a_design_system_one(self):
        """Wagtail's rich text has no table feature, so a table arrives as raw
        HTML without the Design System's classes. Both routes have to look the
        same."""
        page = self.main_page.add_child(
            instance=FrameworkContentPage(
                title="Job grades",
                slug="job-grades",
                body=(
                    "<table><thead><tr><th>Grade</th></tr></thead>"
                    "<tbody><tr><td>SCS1</td></tr></tbody></table>"
                ),
            )
        )
        page.save_revision().publish()

        html = self.client.get(page.url).content.decode()

        self.assertIn("<table", html)
        self.assertIn("SCS1", html)
        self.assertTrue(
            _rules(CSS, ".rich-text-content table:not(.govuk-table)")
            or _rules(CSS, ".rich-text-content table:not(.govuk-table) th"),
            "a bare table is styled to match govuk-table",
        )

    def test_t21_a_bullet_list_an_editor_writes_gets_the_govuk_list_classes(self):
        """The bundle styles govuk-list, not ul, so rich text needs them
        adding for the bullets to appear at all."""
        self.assertIn('listModifier = { UL: "govuk-list--bullet"', MAIN_JS)
        self.assertIn('el.classList.add("govuk-list", listModifier[el.tagName])', MAIN_JS)

    def test_t48_the_banner_text_takes_its_own_line_rather_than_wrapping(self):
        """"Test that the banner text doesn't wrap onto a second line at
        smaller screen sizes." Below the breakpoint the text becomes a block,
        so it starts on its own line instead of wrapping around the tag."""
        banner = _rules(CSS, ".govuk-phase-banner__text")

        self.assertIn("display: block", banner)

    def test_t50_to_t55_an_editor_can_reach_the_snippets_the_review_edited(self):
        """The review edited roles, skills, changelog entries and tags through
        the CMS. Their admin URLs are what it used; ``test_editor_snippet_
        permissions.py`` covers who may do it."""
        from django.contrib.auth import get_user_model

        User = get_user_model()
        User.objects.create_superuser("reviewer", "reviewer@example.gov.uk", "pw")
        self.client.force_login(User.objects.get(username="reviewer"))

        for url in (
            "/admin/snippets/govuk/govukrole/",
            "/admin/snippets/govuk/govukskill/",
            "/admin/snippets/govuk/govukchangelogentry/",
            "/admin/snippets/govuk/govuktag/",
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200, url)
