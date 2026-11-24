"""The framework boilerplate an editor can change.

Two things have to hold: an instance nobody has edited reads exactly as it did
when the words were in the templates, and an edit reaches the page.
"""

from django.test import TestCase, override_settings
from wagtail.models import Site

from govuk.models import (
    CapabilityFrameworkWordingSettings,
    FrameworkContentPage,
    FrameworkSkillsPage,
    GovukChangelogEntry,
    GovukRole,
    GovukSkill,
)
from govuk.tests.helpers import feature_flags, make_framework_main_page, role_url


@override_settings(FEATURE_FLAGS=feature_flags(skills=True))
class FrameworkWordingTestCase(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific
        self.wording = CapabilityFrameworkWordingSettings.for_site(self.site)
        self.main_page = make_framework_main_page(self.root_page)

    def _set(self, **fields):
        for name, value in fields.items():
            setattr(self.wording, name, value)
        self.wording.save()

    def _skills_page(self) -> FrameworkSkillsPage:
        page = self.root_page.add_child(instance=FrameworkSkillsPage(title="Skills", slug="skills"))
        page.save_revision().publish()
        return page.specific

    def assert_edits_reach(self, url, cases):
        """Each case is (fields, expected, unexpected); its fields are put back
        to their defaults before the next."""
        for fields, expected, unexpected in cases:
            with self.subTest(fields=sorted(fields)):
                self._set(**fields)
                try:
                    response = self.client.get(url)
                    for text in expected:
                        self.assertContains(response, text)
                    for text in unexpected:
                        self.assertNotContains(response, text)
                finally:
                    self._set(
                        **{
                            name: self.wording._meta.get_field(name).get_default()
                            for name in fields
                        }
                    )


class DefaultWordingTests(FrameworkWordingTestCase):
    """A site that has never opened the form reads as the templates did.

    Each default is checked against the words the template used to hold, so a
    typo made moving them into the database is caught here.
    """

    def setUp(self):
        super().setUp()
        self.skill = GovukSkill.objects.create(
            title="Data modelling",
            working_points=[{"type": "point", "value": "produce data models"}],
        )
        self.leadership_skill = GovukSkill.objects.create(
            title="Capability building",
            is_senior_civil_service=True,
            leadership_points=[{"type": "point", "value": "prioritising needs"}],
        )
        self.analyst = GovukRole.objects.create(
            title="Data analyst",
            family="Data",
            body="<p>What the job is.</p>",
            levels=[
                {
                    "type": "level",
                    "value": {
                        "title": "Data analyst",
                        "description": "",
                        "grades": ["heo"],
                        "skills": [{"skill": self.skill.pk, "level": "working"}],
                    },
                }
            ],
        )
        self.cto = GovukRole.objects.create(
            title="Chief technology officer",
            family="Chief digital and data",
            body="<p>What the job is.</p>",
            is_senior_civil_service=True,
            scs_grades=[{"type": "grade", "value": "scs1"}],
            scs_skills=[{"type": "skill", "value": self.leadership_skill.pk}],
            roles_that_could_lead_here=[{"type": "role", "value": self.analyst.pk}],
        )
        for role in (self.cto, self.analyst):
            GovukChangelogEntry.objects.create(
                date="2026-04-01", role=role, note="<p>First published.</p>"
            )

    def test_each_page_reads_as_it_did_before(self):
        for page, url_for, wordings in (
            (
                "role level page",
                lambda: role_url(self.main_page, self.analyst),
                (
                    "Contents",
                    "Data analyst role levels",
                    "This role level is most often performed at the",
                    "Civil Service job grade",
                    "Level: Working",
                    "Working is the second of four ascending skill levels",
                    "You can:",
                    "Senior Civil Service roles that data analyst could lead to",
                ),
            ),
            (
                "senior role page",
                lambda: role_url(self.main_page, self.cto),
                (
                    "A specific chief technology officer job can vary depending on the",
                    "context and challenges in your organisation",
                    "This role is often performed at the",
                    "Skills for chief technology officer",
                    "The chief technology officer role will need to use digital and "
                    "data skills to:",
                    "be an effective digital and data leader",
                    "operate in different contexts, depending",
                    "Description, including examples of leadership",
                    "Examples of leadership using this skill:",
                    "Roles that could lead to chief technology officer",
                    "Updates",
                ),
            ),
            (
                "skills page",
                lambda: self._skills_page().url,
                (
                    "Senior Civil Service",
                    "Description, including examples of leadership",
                    "Examples of leadership using this skill:",
                    "Skill level",
                    "Working is the second of four ascending skill levels",
                    "No description provided.",
                    "Roles that require this skill",
                ),
            ),
        ):
            response = self.client.get(url_for())
            for wording in wordings:
                with self.subTest(page=page, wording=wording):
                    self.assertContains(response, wording)

    def test_the_skills_page_with_no_skills_reads_as_it_did_before(self):
        GovukSkill.objects.all().delete()

        response = self.client.get(self._skills_page().url)

        self.assertContains(response, "No skills found.")


class EditedWordingTests(FrameworkWordingTestCase):
    def setUp(self):
        super().setUp()
        self.skill = GovukSkill.objects.create(
            title="Data modelling",
            working_points=[{"type": "point", "value": "produce data models"}],
        )
        self.role = GovukRole.objects.create(
            title="Development operations (DevOps) engineer",
            family="Technical",
            levels=[
                {
                    "type": "level",
                    "value": {
                        "title": "Senior DevOps engineer",
                        "description": "",
                        "grades": [],
                        "skills": [{"skill": self.skill.pk, "level": "expert"}],
                    },
                }
            ],
        )
        self.role_url = role_url(self.main_page, self.role)

    def test_an_edit_reaches_the_role_page(self):
        self.assert_edits_reach(
            self.role_url,
            [
                (
                    {"role_levels_heading": "Levels of {role}"},
                    ["Levels of Development operations (DevOps) engineer"],
                    ["role levels</h2>"],
                ),
                (
                    {"skill_level_scale_text": "{level}, {ordinal} of four"},
                    ["Expert, fourth of four"],
                    ["ascending skill levels"],
                ),
                # Both tables open with a Skill column; renaming one must not
                # rename the other.
                (
                    {
                        "level_skills_table_skill_heading": "Capability",
                        "scs_skills_table_skill_heading": "Leadership skill",
                    },
                    ["Capability"],
                    ["Leadership skill"],
                ),
            ],
        )

    def test_the_contents_link_says_what_the_heading_it_points_at_says(self):
        self._set(role_levels_heading="Levels of {role}")

        response = self.client.get(self.role_url)
        body = response.content.decode()

        anchor = self.main_page.build_role_context(response.wsgi_request, self.role)[
            "role_sections"
        ][0]["anchors"]["levels"]
        self.assertIn(
            f'href="#{anchor}" class="contents-list-links govuk-link">'
            "Levels of Development operations (DevOps) engineer",
            body,
        )

    def test_a_heading_keeps_the_capitals_inside_a_role_title(self):
        response = self.client.get(self.role_url)

        self.assertContains(response, "Development operations (DevOps) engineer role levels")

    def test_a_role_named_mid_sentence_is_lowercased_but_keeps_acronyms(self):
        """ "IT" is said letter by letter, so it stays as it is."""
        self._set(progression_scs_roles_heading="Where {role} leads")
        manager = GovukRole.objects.create(title="IT service manager", family="Tech")
        GovukRole.objects.create(
            title="Chief technology officer",
            is_senior_civil_service=True,
            roles_that_could_lead_here=[{"type": "role", "value": manager.pk}],
        )

        response = self.client.get(role_url(self.main_page, manager))

        self.assertContains(response, "Where IT service manager leads")

    def test_the_wording_is_held_per_site(self):
        other_site = Site.objects.create(
            hostname="other.example.com", port=80, root_page=self.site.root_page
        )
        other_wording = CapabilityFrameworkWordingSettings.for_site(other_site)
        other_wording.role_levels_heading = "Grades of {role}"
        other_wording.save()

        response = self.client.get(self.role_url)

        self.assertContains(response, "Development operations (DevOps) engineer role levels")

    def test_an_edit_reaches_the_skills_page(self):
        GovukRole.objects.create(
            title="Data analyst",
            family="Data",
            levels=[
                {
                    "type": "level",
                    "value": {
                        "title": "Data analyst",
                        "description": "",
                        "grades": [],
                        "skills": [{"skill": self.skill.pk, "level": "working"}],
                    },
                }
            ],
        )

        self.assert_edits_reach(
            self._skills_page().url,
            [
                (
                    {"skill_roles_heading": "Who needs this"},
                    ["Who needs this"],
                    ["Roles that require this skill"],
                ),
                (
                    {"skill_level_scale_text": "{level}, {ordinal} of four"},
                    ["Working, second of four"],
                    ["ascending skill levels"],
                ),
            ],
        )


class FrameworkLanguageEditableTests(FrameworkWordingTestCase):
    """The sentences that were still assembled from string literals in code:
    the role page introductions, navigation headings, breadcrumb and the home
    page's updates block."""

    def setUp(self):
        super().setUp()
        self.role = GovukRole.objects.create(
            title="Business architect",
            family="Architecture",
            body="<p>Develops the view.</p>",
            levels=[
                {
                    "type": "level",
                    "value": {
                        "title": "Trainee business architect",
                        "description": "<p>Learns.</p>",
                        "skills": [],
                    },
                },
                {
                    "type": "level",
                    "value": {
                        "title": "Lead business architect",
                        "description": "<p>Leads.</p>",
                        "skills": [],
                    },
                },
            ],
        )
        self.role_url = role_url(self.main_page, self.role)
        # Gives the navigation a further resources group to head.
        self.main_page.add_child(
            instance=FrameworkContentPage(title="Privacy", slug="privacy", body="")
        ).save_revision().publish()

    def test_the_role_page_wording_is_the_editors_to_change(self):
        self.assert_edits_reach(
            self.role_url,
            [
                (
                    {"overview_heading_text": "The {role} job, in short"},
                    ["The business architect job, in short"],
                    ["What a business architect does"],
                ),
                (
                    {"role_lead_text": "Meet {article} {role} and their skills."},
                    ["Meet a business architect and their skills."],
                    [],
                ),
                (
                    {
                        "role_levels_opening_many": "{role} has {count} steps{levels_range}.",
                        "role_levels_range_text": " ({first} up to {last})",
                        "role_levels_described_many": "Each is set out below.",
                        "role_levels_purpose_text": "Use them to plan.",
                    },
                    [
                        "business architect has 2 steps (trainee business architect up to "
                        "lead business architect).",
                        "Each is set out below. Use them to plan.",
                    ],
                    [],
                ),
                # The narrow-screen breadcrumb links the family group to its
                # section on the home page, so both read from the same field.
                (
                    {
                        "role_family_group_title": "The {family} family",
                        "further_resources_heading": "Everything else",
                    },
                    ["The Architecture family", 'href="/#the-architecture-family"'],
                    [],
                ),
            ],
        )

    def test_the_breadcrumb_home_label_is_the_editors_to_change(self):
        self._set(breadcrumb_home_label="Start")

        response = self.client.get(self.role_url)

        self.assertContains(
            response, '<a class="govuk-breadcrumbs__link" href="/">Start</a>', html=True
        )

    def test_the_home_updates_block_reads_the_same_wording_as_a_role_page(self):
        GovukChangelogEntry.objects.create(date="2026-08-01", note="<p>Framework refreshed</p>")
        self._set(
            updates_heading="What changed",
            published_prefix="First seen",
            last_updated_prefix="Freshest",
            see_all_updates_link_text="Everything that moved",
            show_all_updates_link_text="open the history",
            hide_all_updates_link_text="close the history",
        )

        response = self.client.get(self.main_page.url)

        for text in (
            ">What changed</h2>",
            "First seen",
            "Freshest",
            "Everything that moved",
            'data-show-text="open the history"',
            'data-hide-text="close the history"',
        ):
            with self.subTest(text=text):
                self.assertContains(response, text)
