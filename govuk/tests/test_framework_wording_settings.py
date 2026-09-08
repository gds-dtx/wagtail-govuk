"""The framework boilerplate an editor can now change.

Headings, column headings and the sentence lead-ins used to be written into
the templates. Two things have to hold: an instance nobody has edited must
read exactly as it did before, and an edit must reach the page.
"""

from django.test import TestCase, override_settings
from wagtail.models import Site

from govuk.models import (
    CapabilityFrameworkWordingSettings,
    GovukChangelogEntry,
    GovukRole,
    GovukSkill,
    RolePage,
    SkillsAZPage,
)


def _feature_flags(*, skills_enabled: bool = True) -> dict[str, bool]:
    return {
        "SKILLS": skills_enabled,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
    }


@override_settings(FEATURE_FLAGS=_feature_flags())
class FrameworkWordingTestCase(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific
        self.wording = CapabilityFrameworkWordingSettings.for_site(self.site)

    def _set(self, **fields):
        for name, value in fields.items():
            setattr(self.wording, name, value)
        self.wording.save()


class DefaultWordingTests(FrameworkWordingTestCase):
    """A site that has never opened the form.

    Each default is checked against the words the template used to hold, so
    that a typo introduced while moving them into the database is caught here
    rather than by a reader of the published framework.
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
            roles_that_could_lead_here=[
                {"type": "role", "value": self.analyst.pk}
            ],
        )
        GovukChangelogEntry.objects.create(
            date="2026-04-01", role=self.cto, note="<p>First published.</p>"
        )
        GovukChangelogEntry.objects.create(
            date="2026-04-01", role=self.analyst, note="<p>First published.</p>"
        )

    def _publish(self, role: GovukRole) -> RolePage:
        page = self.root_page.add_child(
            instance=RolePage(
                title=role.title,
                slug=role.slug,
                selected_roles=[{"type": "role", "value": role.pk}],
            )
        )
        page.save_revision().publish()
        return page.specific

    def test_a_role_level_page_reads_as_it_did_before(self):
        page = self._publish(self.analyst)

        response = self.client.get(page.url)

        for wording in (
            "Contents",
            "Data analyst role levels",
            "This role level is most often performed at the",
            "Civil Service job grade",
            "Level: Working",
            "Working is the second of four ascending skill levels",
            "You can:",
            "Senior Civil Service roles that data analyst could lead to",
        ):
            with self.subTest(wording=wording):
                self.assertContains(response, wording)

    def test_a_senior_role_page_reads_as_it_did_before(self):
        page = self._publish(self.cto)

        response = self.client.get(page.url)

        for wording in (
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
        ):
            with self.subTest(wording=wording):
                self.assertContains(response, wording)

    def test_the_skills_page_reads_as_it_did_before(self):
        self._publish(self.analyst)
        page = self.root_page.add_child(
            instance=SkillsAZPage(title="Skills", slug="skills")
        )
        page.save_revision().publish()

        response = self.client.get(page.specific.url)

        for wording in (
            "Senior Civil Service",
            "Examples of leadership using this skill",
            "Skill level",
            "Working is the second of four ascending skill levels",
            "No description provided.",
            "Roles that require this skill",
        ):
            with self.subTest(wording=wording):
                self.assertContains(response, wording)

    def test_a_role_page_with_no_role_chosen_reads_as_it_did_before(self):
        # An author is what brings the content column into the page at all,
        # so a page with nothing whatever on it never reaches this message.
        page = self.root_page.add_child(
            instance=RolePage(title="Empty", slug="empty", author="Someone")
        )
        page.save_revision().publish()

        response = self.client.get(page.specific.url)

        self.assertContains(response, "No roles selected.")

    def test_the_skills_page_with_no_skills_reads_as_it_did_before(self):
        GovukSkill.objects.all().delete()
        page = self.root_page.add_child(
            instance=SkillsAZPage(title="Skills", slug="skills")
        )
        page.save_revision().publish()

        response = self.client.get(page.specific.url)

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
        page = self.root_page.add_child(
            instance=RolePage(
                title=self.role.title,
                slug="devops-engineer",
                selected_roles=[{"type": "role", "value": self.role.pk}],
            )
        )
        page.save_revision().publish()
        self.page = page.specific

    def test_an_edited_heading_reaches_the_page(self):
        self._set(role_levels_heading="Levels of {role}")

        response = self.client.get(self.page.url)

        self.assertContains(
            response, "Levels of Development operations (DevOps) engineer"
        )
        self.assertNotContains(response, "role levels</h2>")

    def test_the_contents_link_says_what_the_heading_it_points_at_says(self):
        """The two are the same words twice over, so an editor changing one
        and not the other would leave a link that names a section it lands on
        under a different name."""
        self._set(role_levels_heading="Levels of {role}")

        response = self.client.get(self.page.url)
        body = response.content.decode()

        anchor = self.page.get_context(response.wsgi_request)["role_sections"][0][
            "anchors"
        ]["levels"]
        self.assertIn(
            f'href="#{anchor}" class="contents-list-links govuk-link">'
            "Levels of Development operations (DevOps) engineer",
            body,
        )

    def test_a_heading_keeps_the_capitals_inside_a_role_title(self):
        """"Development operations (DevOps) engineer" opens a heading, so it
        is not lowercased the way the mid-sentence form is."""
        response = self.client.get(self.page.url)

        self.assertContains(
            response, "Development operations (DevOps) engineer role levels"
        )

    def test_a_role_named_mid_sentence_is_lowercased_but_keeps_acronyms(self):
        """The same name reads differently in the two places, which is why the
        heading and the sentence are given it separately: "IT" stays as it is
        because it is said letter by letter, and the rest comes down."""
        self._set(progression_scs_roles_heading="Where {role} leads")
        manager = GovukRole.objects.create(title="IT service manager", family="Tech")
        GovukRole.objects.create(
            title="Chief technology officer",
            is_senior_civil_service=True,
            roles_that_could_lead_here=[{"type": "role", "value": manager.pk}],
        )
        page = self.root_page.add_child(
            instance=RolePage(
                title=manager.title,
                slug="it-service-manager",
                selected_roles=[{"type": "role", "value": manager.pk}],
            )
        )
        page.save_revision().publish()

        response = self.client.get(page.specific.url)

        self.assertContains(response, "Where IT service manager leads")

    def test_the_screen_reader_wording_for_a_skill_level_is_editable(self):
        self._set(skill_level_scale_text="{level}, {ordinal} of four")

        response = self.client.get(self.page.url)

        self.assertContains(response, "Expert, fourth of four")
        self.assertNotContains(response, "ascending skill levels")

    def test_the_two_column_headings_are_set_apart_from_each_other(self):
        """A role level's skills table and the Senior Civil Service one both
        open with a Skill column, and an editor renaming one must not rename
        the other."""
        self._set(
            level_skills_table_skill_heading="Capability",
            scs_skills_table_skill_heading="Leadership skill",
        )

        response = self.client.get(self.page.url)

        self.assertContains(response, "Capability")
        self.assertNotContains(response, "Leadership skill")

    def test_the_wording_is_held_per_site(self):
        """Another profession running on the same instance writes its own."""
        other_site = Site.objects.create(
            hostname="other.example.com", port=80, root_page=self.site.root_page
        )
        other_wording = CapabilityFrameworkWordingSettings.for_site(other_site)
        other_wording.role_levels_heading = "Grades of {role}"
        other_wording.save()

        response = self.client.get(self.page.url)

        self.assertContains(
            response, "Development operations (DevOps) engineer role levels"
        )


class SkillsPageEditedWordingTests(FrameworkWordingTestCase):
    def setUp(self):
        super().setUp()
        skill = GovukSkill.objects.create(
            title="Data modelling",
            working_points=[{"type": "point", "value": "produce data models"}],
        )
        role = GovukRole.objects.create(
            title="Data analyst",
            family="Data",
            levels=[
                {
                    "type": "level",
                    "value": {
                        "title": "Data analyst",
                        "description": "",
                        "grades": [],
                        "skills": [{"skill": skill.pk, "level": "working"}],
                    },
                }
            ],
        )
        role_page = self.root_page.add_child(
            instance=RolePage(
                title=role.title,
                slug="data-analyst",
                selected_roles=[{"type": "role", "value": role.pk}],
            )
        )
        role_page.save_revision().publish()
        page = self.root_page.add_child(
            instance=SkillsAZPage(title="Skills", slug="skills")
        )
        page.save_revision().publish()
        self.page = page.specific

    def test_an_edited_heading_reaches_the_skills_page(self):
        self._set(skill_roles_heading="Who needs this")

        response = self.client.get(self.page.url)

        self.assertContains(response, "Who needs this")
        self.assertNotContains(response, "Roles that require this skill")

    def test_the_screen_reader_wording_reaches_the_skills_page(self):
        self._set(skill_level_scale_text="{level}, {ordinal} of four")

        response = self.client.get(self.page.url)

        self.assertContains(response, "Working, second of four")
        self.assertNotContains(response, "ascending skill levels")
