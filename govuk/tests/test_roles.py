import re
from datetime import date

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from wagtail.models import Page, Site

from govuk.capability_framework import split_leadership_examples
from govuk.models import (
    ContentPage,
    FrameworkMainPage,
    FrameworkSkillsPage,
    GovukChangelogEntry,
    GovukRole,
    GovukSkill,
    GovukTag,
)
from govuk.tests.helpers import feature_flags, make_framework_main_page, role_url


def _level(title: str, skills: list[tuple[GovukSkill, str]]) -> dict:
    return {
        "type": "level",
        "value": {
            "title": title,
            "description": "",
            "skills": [{"skill": skill.pk, "level": level} for skill, level in skills],
        },
    }


@override_settings(FEATURE_FLAGS=feature_flags(skills=True))
class FrameworkTestCase(TestCase):
    """The default site with a framework main page to serve the roles."""

    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific
        self.main_page = make_framework_main_page(self.root_page)

    def _add_skills_page(self, parent=None) -> FrameworkSkillsPage:
        page = (parent or self.root_page).add_child(
            instance=FrameworkSkillsPage(title="Skills A to Z", slug="skills")
        )
        page.save_revision().publish()
        return page.specific


class RolePageTests(FrameworkTestCase):
    def setUp(self):
        super().setUp()
        self.forensics = GovukSkill.objects.create(
            title="Forensics",
            body="<p>Forensics refers to the capture, analysis and reporting of evidence.</p>",
            working_points=[
                {"type": "point", "value": "Analyses digital evidence and investigates incidents."},
                {"type": "point", "value": "Undertakes real-time analysis on live systems."},
            ],
            practitioner_points=[
                {"type": "point", "value": "Leads forensic investigations and advises wider teams."}
            ],
        )
        self.protective_security = GovukSkill.objects.create(
            title="Protective security",
            body="<p>Protective security reduces risk across people, places and systems.</p>",
            awareness_points=[
                {"type": "point", "value": "Maintains an understanding of security fundamentals."}
            ],
        )
        self.digital_forensics_role = GovukRole.objects.create(
            title="Digital forensics analyst",
            body="<p>A digital forensics analyst acquires and analyses forensic evidence.</p>",
            levels=[
                {
                    "type": "level",
                    "value": {
                        "title": "Associate digital forensics analyst",
                        "description": "<p>An associate supports digital aspects of investigations.</p>",
                        "skills": [
                            {"skill": self.protective_security.pk, "level": "awareness"},
                            {"skill": self.forensics.pk, "level": "working"},
                        ],
                    },
                },
                {
                    "type": "level",
                    "value": {
                        "title": "Lead digital forensics analyst",
                        "description": "<p>A lead drives complex forensic investigations.</p>",
                        "skills": [{"skill": self.forensics.pk, "level": "practitioner"}],
                    },
                },
            ],
        )
        self.role_url = role_url(self.main_page, self.digital_forensics_role)

    def test_skill_points_for_level_accepts_choice_value_or_label(self):
        for level in ("working", "Working"):
            with self.subTest(level=level):
                self.assertEqual(
                    self.forensics.points_for_level(level),
                    [
                        "Analyses digital evidence and investigates incidents.",
                        "Undertakes real-time analysis on live systems.",
                    ],
                )

    def test_role_get_levels_with_skills_returns_skill_rows(self):
        role_levels = self.digital_forensics_role.get_levels_with_skills()

        self.assertEqual(len(role_levels), 2)
        self.assertEqual(role_levels[0]["title"], "Associate digital forensics analyst")
        self.assertEqual(role_levels[0]["skills"][0]["skill"], self.forensics)
        self.assertEqual(role_levels[0]["skills"][1]["skill"], self.protective_security)
        self.assertEqual(role_levels[0]["skills"][0]["required_level"], "working")
        self.assertEqual(role_levels[0]["skills"][0]["required_level_label"], "Working")
        self.assertIn(
            "Analyses digital evidence and investigates incidents.",
            role_levels[0]["skills"][0]["points"],
        )

    def test_role_page_renders_selected_roles_levels_and_skill_points(self):
        response = self.client.get(self.role_url)

        self.assertEqual(response.status_code, 200)
        for text in (
            "digital forensics analyst",
            "Associate digital forensics analyst",
            "Lead digital forensics analyst",
            "Forensics",
            "Protective security",
            "Level: Working",
            "Level: Awareness",
            "Analyses digital evidence and investigates incidents.",
            # The points are written as the ends of "You can:", so it is printed.
            "You can:",
        ):
            with self.subTest(text=text):
                self.assertContains(response, text)

    def test_a_skill_level_marked_not_defined_is_printed_as_the_bare_sentence(self):
        """The "not defined" sentence does not finish "You can:", so it stands alone."""
        undefined_skill = GovukSkill.objects.create(
            title="Undefined skill",
            body="<p>Undefined body.</p>",
            awareness_points=[
                {"type": "point", "value": "This skill level is currently not defined."}
            ],
        )
        sparse_role = GovukRole.objects.create(
            title="Sparse role",
            body="<p>A sparse role.</p>",
            levels=[
                {
                    "type": "level",
                    "value": {
                        "title": "Sparse associate",
                        "description": "<p>An associate with one undefined skill.</p>",
                        "skills": [{"skill": undefined_skill.pk, "level": "awareness"}],
                    },
                }
            ],
        )

        response = self.client.get(role_url(self.main_page, sparse_role))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "You can:")
        self.assertNotContains(response, "<li>This skill level is currently not defined.</li>")
        self.assertContains(
            response,
            '<p class="govuk-body govuk-!-margin-bottom-0">'
            "This skill level is currently not defined.</p>",
        )

    def test_the_sidebar_home_link_names_the_framework_and_is_not_current(self):
        response = self.client.get(self.role_url)

        home = response.context["framework_home"]
        self.assertEqual(home["title"], "Home")
        self.assertEqual(home["url"], self.main_page.url)
        self.assertFalse(home["is_current"])

    def test_a_delegated_grade_role_has_none_of_the_senior_civil_service_wording(self):
        response = self.client.get(self.role_url)

        self.assertNotContains(response, "A specific digital forensics analyst job")
        self.assertNotContains(response, "will need to use digital and data skills to")

    def test_framework_main_page_supports_tags(self):
        policy_tag = GovukTag.objects.create(slug="policy", name="Policy")
        security_tag = GovukTag.objects.create(slug="security", name="Security")

        self.main_page.tags.set([security_tag, policy_tag])

        self.assertEqual(
            list(self.main_page.tags.order_by("slug").values_list("slug", flat=True)),
            ["policy", "security"],
        )

    @override_settings(FEATURE_FLAGS=feature_flags())
    def test_framework_main_page_is_not_creatable_when_skills_feature_is_disabled(self):
        self.assertFalse(FrameworkMainPage.can_create_at(self.root_page))

    @override_settings(FEATURE_FLAGS=feature_flags())
    def test_a_role_route_does_not_serve_without_the_framework(self):
        """Not creatable is not the same as not public: the page import does not
        go through the admin, so a framework main page can exist without the flag."""
        response = self.client.get(self.role_url)

        self.assertEqual(response.status_code, 404)


@override_settings(FEATURE_FLAGS=feature_flags(skills=True))
class FrameworkMainPagePlacementTests(TestCase):
    """The framework main page can sit anywhere, but only one per site."""

    def setUp(self):
        self.root_page = Site.objects.get(is_default_site=True).root_page.specific
        self.content = self.root_page.add_child(
            instance=ContentPage(title="Guidance", slug="guidance")
        )
        self.content.save_revision().publish()

    def test_it_can_be_created_below_another_page(self):
        self.assertTrue(FrameworkMainPage.can_create_at(self.content))

    def test_only_one_is_allowed_per_site(self):
        make_framework_main_page(self.root_page)

        self.assertFalse(FrameworkMainPage.can_create_at(self.root_page))
        self.assertFalse(FrameworkMainPage.can_create_at(self.content))


class RoleLevelGradeTests(FrameworkTestCase):
    """Indicative Civil Service job grades shown against each role level."""

    def setUp(self):
        super().setUp()
        self.skill = GovukSkill.objects.create(
            title="Data modelling",
            working_points=[{"type": "point", "value": "produce data models"}],
        )
        self.role = GovukRole.objects.create(
            title="Data analyst",
            family="Data",
            levels=[
                {
                    "type": "level",
                    "value": {
                        "title": "Associate data analyst",
                        "description": "",
                        "grades": ["eo", "heo"],
                        "skills": [{"skill": self.skill.pk, "level": "working"}],
                    },
                },
                {
                    "type": "level",
                    "value": {
                        "title": "Lead data analyst - management",
                        "description": "",
                        "grades": [],
                        "skills": [],
                    },
                },
            ],
        )

    def test_level_grades_render_as_labels(self):
        levels = self.role.get_levels_with_skills()

        self.assertEqual(
            levels[0]["grades"],
            ["EO (Executive Officer)", "HEO (Higher Executive Officer)"],
        )

    def test_level_grades_are_ordered_by_seniority_not_authoring_order(self):
        self.role.levels = [
            {
                "type": "level",
                "value": {
                    "title": "Associate data analyst",
                    "description": "",
                    "grades": ["heo", "eo"],
                    "skills": [],
                },
            }
        ]
        self.role.save()

        self.assertEqual(
            self.role.get_levels_with_skills()[0]["grades"],
            ["EO (Executive Officer)", "HEO (Higher Executive Officer)"],
        )

    def test_level_with_no_grades_is_empty(self):
        self.assertEqual(self.role.get_levels_with_skills()[1]["grades"], [])

    def test_role_page_renders_the_grade_sentence_for_graded_levels_only(self):
        response = self.client.get(role_url(self.main_page, self.role))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "EO (Executive Officer)")
        self.assertContains(response, "HEO (Higher Executive Officer)")
        self.assertContains(response, "This role level is most often performed at the", count=1)


class RelatedRolesTests(FrameworkTestCase):
    def setUp(self):
        super().setUp()
        self.modelling = GovukSkill.objects.create(title="Data modelling")
        self.communicating = GovukSkill.objects.create(title="Communicating")
        self.problem_management = GovukSkill.objects.create(title="Problem management")

        self.data_engineer = GovukRole.objects.create(
            title="Data engineer",
            levels=[
                _level(
                    "Data engineer",
                    [(self.modelling, "working"), (self.communicating, "awareness")],
                ),
                _level(
                    "Senior data engineer",
                    [(self.modelling, "practitioner"), (self.problem_management, "working")],
                ),
            ],
        )
        # Shares two skills with the data engineer.
        self.data_architect = GovukRole.objects.create(
            title="Data architect",
            levels=[
                _level(
                    "Data architect",
                    [(self.modelling, "expert"), (self.communicating, "working")],
                )
            ],
        )
        # Shares one.
        self.incident_manager = GovukRole.objects.create(
            title="Incident manager",
            levels=[_level("Incident manager", [(self.problem_management, "expert")])],
        )
        # Shares none.
        self.unrelated_role = GovukRole.objects.create(title="Content designer", levels=[])

        self.role_url = role_url(self.main_page, self.data_engineer)
        self.architect_url = role_url(self.main_page, self.data_architect)

    def test_get_skill_ids_returns_distinct_skills_across_levels(self):
        self.assertEqual(
            self.data_engineer.get_skill_ids(),
            {self.modelling.pk, self.communicating.pk, self.problem_management.pk},
        )

    def test_related_roles_ordered_by_shared_count_then_title(self):
        related = self.data_engineer.get_related_roles()

        self.assertEqual(
            [entry["role"] for entry in related],
            [self.data_architect, self.incident_manager],
        )
        self.assertEqual(
            [skill.title for skill in related[0]["shared_skills"]],
            ["Communicating", "Data modelling"],
        )
        self.assertEqual(
            [skill.title for skill in related[1]["shared_skills"]],
            ["Problem management"],
        )

    def test_related_roles_excludes_roles_without_shared_skills(self):
        related_roles = [entry["role"] for entry in self.data_engineer.get_related_roles()]
        self.assertNotIn(self.unrelated_role, related_roles)

    def test_related_roles_respects_count_cap(self):
        related = self.data_engineer.get_related_roles(count=1)
        self.assertEqual([entry["role"] for entry in related], [self.data_architect])

    def test_role_without_skills_has_no_related_roles(self):
        self.assertEqual(self.unrelated_role.get_related_roles(), [])

    def test_role_page_renders_related_roles_with_link_to_role_page(self):
        response = self.client.get(self.role_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Roles that share data engineer skills")
        self.assertContains(response, self.architect_url)
        self.assertContains(response, "Incident manager")

    def test_related_role_heading_preserves_acronyms(self):
        self.assertEqual(
            FrameworkMainPage._display_role_name("IT service manager"), "IT service manager"
        )
        self.assertEqual(FrameworkMainPage._display_role_name("Data engineer"), "data engineer")

    def test_shared_skills_link_to_skills_index_when_present(self):
        skills_page = self._add_skills_page()

        response = self.client.get(self.role_url)
        self.assertContains(response, f"{skills_page.url}#{self.modelling.slug}")


class SkillRolesTests(FrameworkTestCase):
    """The roles that require a skill, listed on the skills index."""

    def setUp(self):
        super().setUp()
        self.modelling = GovukSkill.objects.create(title="Data modelling")
        self.unused_skill = GovukSkill.objects.create(title="Unused skill")

        self.data_engineer = GovukRole.objects.create(
            title="Data engineer",
            levels=[
                _level("Data engineer", [(self.modelling, "working")]),
                # A second level requiring the same skill must not duplicate the role.
                _level("Senior data engineer", [(self.modelling, "practitioner")]),
            ],
        )
        self.data_architect = GovukRole.objects.create(
            title="Data architect",
            levels=[_level("Data architect", [(self.modelling, "expert")])],
        )
        self.skills_page = self._add_skills_page()

    def test_roles_requiring_skill_sorted_by_title_without_duplicates(self):
        self.assertEqual(
            self.modelling.get_roles_requiring_skill(),
            [self.data_architect, self.data_engineer],
        )

    def test_skill_with_no_roles_returns_empty_list(self):
        self.assertEqual(self.unused_skill.get_roles_requiring_skill(), [])

    def test_roles_by_skill_id_indexes_every_skill(self):
        index = GovukRole.roles_by_skill_id()

        self.assertEqual(index[self.modelling.pk], [self.data_architect, self.data_engineer])
        self.assertNotIn(self.unused_skill.pk, index)

    def test_skills_page_lists_roles_and_links_to_role_pages(self):
        response = self.client.get(self.skills_page.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Roles that require this skill")
        self.assertContains(response, role_url(self.main_page, self.data_engineer))
        self.assertContains(response, role_url(self.main_page, self.data_architect))
        self.assertContains(response, "Data architect")

    def test_skills_page_omits_heading_for_skills_without_roles(self):
        GovukRole.objects.all().delete()

        response = self.client.get(self.skills_page.url)

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Roles that require this skill")


class SeniorCivilServiceRoleTests(FrameworkTestCase):
    def setUp(self):
        super().setUp()
        self.strategic_planning = GovukSkill.objects.create(
            title="Strategic technology planning",
            body="<p>You can:</p><ul><li>determine the right technologies</li></ul>",
            is_senior_civil_service=True,
            leadership_points=[
                {"type": "point", "value": "inspiring people with your vision"},
                {"type": "point", "value": "educating colleagues about benefits"},
            ],
        )
        self.capability_building = GovukSkill.objects.create(
            title="Capability building",
            body="<p>You can:</p><ul><li>guide the organisation</li></ul>",
            is_senior_civil_service=True,
            leadership_points=[{"type": "point", "value": "prioritising capability needs"}],
        )
        self.delegated_skill = GovukSkill.objects.create(
            title="Data modelling",
            working_points=[{"type": "point", "value": "produce data models"}],
        )
        self.cto = GovukRole.objects.create(
            title="Chief technology officer",
            family="Chief digital and data",
            body="<p>A chief technology officer is the technology strategist.</p>",
            is_senior_civil_service=True,
            scs_grades=[{"type": "grade", "value": "scs1"}, {"type": "grade", "value": "scs2"}],
            scs_skills=[
                {"type": "skill", "value": self.strategic_planning.pk},
                {"type": "skill", "value": self.capability_building.pk},
            ],
        )
        self.cto_url = role_url(self.main_page, self.cto)

    def _map_data_engineer_into_the_cto(self):
        """The framework's career path, as the content team authors it: on the
        senior role, as the roles that could lead into it."""
        data_engineer = GovukRole.objects.create(title="Data engineer", family="Data")
        self.cto.roles_that_could_lead_here = [{"type": "role", "value": data_engineer.pk}]
        self.cto.save()
        return data_engineer, role_url(self.main_page, data_engineer)

    def _scs_roles_section(self, url) -> str:
        # Only the section: the side navigation lists every role too.
        body = self.client.get(url).content.decode()
        section = body[body.index('id="related-scs-roles"') :]
        return section[: section.index("</ul>")]

    def test_scs_role_exposes_its_skills_with_leadership_examples(self):
        skills = self.cto.get_scs_skills()

        self.assertEqual(
            [row["skill"] for row in skills],
            [self.strategic_planning, self.capability_building],
        )
        self.assertEqual(
            skills[0]["leadership_points"],
            ["inspiring people with your vision", "educating colleagues about benefits"],
        )

    def test_scs_role_has_no_levels(self):
        self.assertEqual(self.cto.get_levels_with_skills(), [])

    def test_scs_grades_render_as_labels(self):
        self.assertEqual(
            self.cto.get_scs_grade_labels(),
            ["SCS 1 (Senior Civil Service 1)", "SCS 2 (Senior Civil Service 2)"],
        )

    def test_scs_skills_count_towards_related_roles(self):
        other = GovukRole.objects.create(
            title="Chief data officer",
            is_senior_civil_service=True,
            scs_skills=[{"type": "skill", "value": self.capability_building.pk}],
        )

        self.assertIn(self.capability_building.pk, self.cto.get_skill_ids())
        self.assertEqual([entry["role"] for entry in self.cto.get_related_roles()], [other])

    def test_scs_skill_lists_the_roles_requiring_it(self):
        self.assertEqual(self.capability_building.get_roles_requiring_skill(), [self.cto])

    def test_role_page_renders_scs_skills_and_grades(self):
        response = self.client.get(self.cto_url)

        self.assertEqual(response.status_code, 200)
        for text in (
            "Skills for chief technology officer",
            "Strategic technology planning",
            "Examples of leadership using this skill",
            "inspiring people with your vision",
            "SCS 1",
            "SCS 2",
        ):
            with self.subTest(text=text):
                self.assertContains(response, text)
        self.assertNotContains(response, "Level: Working")

    def test_scs_role_page_carries_the_wording_every_scs_role_shares(self):
        response = self.client.get(self.cto_url)

        self.assertContains(response, "A specific chief technology officer job can vary depending on the")
        self.assertContains(
            response,
            "The chief technology officer role will need to use digital and data skills to:",
        )
        self.assertContains(response, "be an effective digital and data leader")

    def test_both_passages_link_to_the_context_and_challenges_page(self):
        context_page = self.root_page.add_child(
            instance=ContentPage(
                title="Context and challenges for Senior Civil Service roles in digital and data",
                slug=FrameworkMainPage.SCS_CONTEXT_SLUG,
                body="",
            )
        )
        context_page.save_revision().publish()

        response = self.client.get(self.cto_url)

        for text in (
            "context and challenges in your organisation",
            "on the context and challenges in your organisation",
        ):
            with self.subTest(text=text):
                self.assertContains(
                    response,
                    f'<a class="govuk-link" href="{context_page.url}">{text}</a>',
                    html=True,
                )

    def test_the_grade_wording_links_to_the_job_grades_page(self):
        job_grades = self.root_page.add_child(
            instance=ContentPage(
                title="Civil Service job grades",
                slug=FrameworkMainPage.JOB_GRADES_SLUG,
                body="",
            )
        )
        job_grades.save_revision().publish()

        response = self.client.get(self.cto_url)

        # Wagtail's URL carries its trailing slash, so there is no redirect.
        self.assertContains(
            response,
            f'<a class="govuk-link" href="{job_grades.url}">Civil Service job grade</a>',
            html=True,
        )

    def test_the_wording_survives_the_pages_it_links_to_not_existing(self):
        """Neither page need be published, and a link written out by hand would
        be a 404 on every role until it was."""
        response = self.client.get(self.cto_url)

        self.assertContains(response, "context and challenges in your organisation")
        self.assertNotContains(response, FrameworkMainPage.SCS_CONTEXT_SLUG)
        self.assertContains(response, "Civil Service job grade of:")
        self.assertNotContains(response, 'href="/job-grades"')

    def test_another_sites_pages_are_not_linked_to(self):
        """One instance can host a second service, which a lookup across every
        page would reach into."""
        other_root = Page.objects.get(depth=1).add_child(
            instance=ContentPage(title="Another service", slug="another-service", body="")
        )
        other_root.save_revision().publish()
        Site.objects.create(hostname="other.example.gov.uk", root_page=other_root)

        for their_page, wording in (
            (
                ContentPage(
                    title="Civil Service job grades",
                    slug=FrameworkMainPage.JOB_GRADES_SLUG,
                    body="",
                ),
                "Civil Service job grade of:",
            ),
            (
                ContentPage(
                    title="Context and challenges",
                    slug=FrameworkMainPage.SCS_CONTEXT_SLUG,
                    body="",
                ),
                "context and challenges in your organisation",
            ),
            (
                FrameworkSkillsPage(title="Skills A to Z", slug="skills"),
                "Strategic technology planning",
            ),
        ):
            with self.subTest(page=their_page.title):
                other_root.add_child(instance=their_page).save_revision().publish()

                response = self.client.get(self.cto_url)

                self.assertContains(response, wording)
                self.assertNotContains(response, "other.example.gov.uk")

    def test_every_contents_link_on_a_senior_role_page_lands_on_its_section(self):
        self._map_data_engineer_into_the_cto()
        GovukRole.objects.create(
            title="Chief data officer",
            is_senior_civil_service=True,
            scs_skills=[{"type": "skill", "value": self.capability_building.pk}],
        )
        GovukChangelogEntry.objects.create(
            date=date(2026, 5, 29), role=self.cto, note="<p>First published.</p>"
        )

        response = self.client.get(self.cto_url)

        html = response.content.decode()
        links = re.findall(r'href="#([^"]+)"', html)
        ids = set(re.findall(r'id="([^"]+)"', html))

        self.assertGreaterEqual(len(links), 4)
        self.assertEqual([link for link in links if link not in ids], [])

    def test_a_senior_role_given_levels_does_not_repeat_the_role_levels_id(self):
        """Nothing in the model stops a senior role having levels, and printing
        the id twice would send both contents links to the first."""
        self.cto.levels = [
            {
                "type": "level",
                "value": {
                    "title": "Lead chief technology officer",
                    "description": "",
                    "grades": ["scs1"],
                    "skills": [{"skill": self.delegated_skill.pk, "level": "working"}],
                },
            }
        ]
        self.cto.save()

        content = self.client.get(self.cto_url).content.decode()

        self.assertEqual(content.count('id="role-levels"'), 1)
        self.assertEqual(content.count('id="skills"'), 1)

    def test_roles_that_could_lead_to_this_one_are_listed_in_the_order_given(self):
        data_engineer = GovukRole.objects.create(title="Data engineer", family="Data")
        solution_architect = GovukRole.objects.create(
            title="Solution architect", family="Architecture"
        )
        self.cto.roles_that_could_lead_here = [
            {"type": "role", "value": solution_architect.pk},
            {"type": "role", "value": data_engineer.pk},
        ]
        self.cto.save()

        self.assertEqual(
            self.cto.get_roles_that_could_lead_here(), [solution_architect, data_engineer]
        )

    def test_a_role_cannot_lead_to_itself_and_is_never_listed_twice(self):
        data_engineer = GovukRole.objects.create(title="Data engineer", family="Data")
        self.cto.roles_that_could_lead_here = [
            {"type": "role", "value": data_engineer.pk},
            {"type": "role", "value": self.cto.pk},
            {"type": "role", "value": data_engineer.pk},
        ]
        self.cto.save()

        self.assertEqual(self.cto.get_roles_that_could_lead_here(), [data_engineer])

    def test_the_progression_section_links_each_role_to_its_route(self):
        leading_roles = [
            GovukRole.objects.create(title=title, family="Data")
            for title in ("Data engineer", "Data ethicist")
        ]
        self.cto.roles_that_could_lead_here = [
            {"type": "role", "value": role.pk} for role in leading_roles
        ]
        self.cto.save()

        response = self.client.get(self.cto_url)

        self.assertContains(response, "Roles that could lead to chief technology officer")
        for role in leading_roles:
            with self.subTest(role=role.title):
                self.assertContains(
                    response,
                    f'<a href="{role_url(self.main_page, role)}" class="govuk-link">{role.title}</a>',
                    html=True,
                )
        # A senior role spends "related-roles" on this section's contents entry.
        self.assertContains(response, 'href="#related-roles"')

    def test_the_progression_section_is_left_out_when_no_roles_are_mapped(self):
        response = self.client.get(self.cto_url)

        self.assertNotContains(response, "Roles that could lead to chief technology officer")

    def test_the_mapping_is_turned_around_for_the_role_it_leads_from(self):
        """The framework also prints the path forwards, on the page of the role
        you start at."""
        _, data_engineer_url = self._map_data_engineer_into_the_cto()

        response = self.client.get(data_engineer_url)

        self.assertContains(response, "Senior Civil Service roles that data engineer could lead to")
        self.assertContains(
            response,
            f'<a href="{self.cto_url}" class="govuk-link">Chief technology officer</a>',
            html=True,
        )
        self.assertContains(response, 'href="#related-scs-roles"')

    def test_the_senior_roles_a_role_leads_to_are_listed_by_title(self):
        data_engineer, data_engineer_url = self._map_data_engineer_into_the_cto()
        GovukRole.objects.create(
            title="Chief data officer",
            family="Chief digital and data",
            is_senior_civil_service=True,
            roles_that_could_lead_here=[{"type": "role", "value": data_engineer.pk}],
        )

        section = self._scs_roles_section(data_engineer_url)

        self.assertLess(
            section.index("Chief data officer</a>"),
            section.index("Chief technology officer</a>"),
        )

    def test_only_senior_roles_are_listed_as_somewhere_a_role_leads_to(self):
        data_engineer, data_engineer_url = self._map_data_engineer_into_the_cto()
        GovukRole.objects.create(
            title="Lead data engineer",
            family="Data",
            roles_that_could_lead_here=[{"type": "role", "value": data_engineer.pk}],
        )

        section = self._scs_roles_section(data_engineer_url)

        self.assertIn("Chief technology officer", section)
        self.assertNotIn("Lead data engineer", section)

    def test_a_senior_role_is_not_told_which_senior_roles_it_leads_to(self):
        """The path forwards is printed only on the way up to the Senior Civil
        Service, not between roles already in it."""
        self._map_data_engineer_into_the_cto()
        GovukRole.objects.create(
            title="Chief digital and information officer",
            family="Chief digital and data",
            is_senior_civil_service=True,
            roles_that_could_lead_here=[{"type": "role", "value": self.cto.pk}],
        )

        response = self.client.get(self.cto_url)

        self.assertNotContains(
            response, "Senior Civil Service roles that chief technology officer could lead to"
        )
        self.assertNotContains(response, 'id="related-scs-roles"')

    def test_the_forward_section_is_left_out_when_nothing_leads_anywhere(self):
        data_engineer = GovukRole.objects.create(title="Data engineer", family="Data")

        response = self.client.get(role_url(self.main_page, data_engineer))

        self.assertNotContains(response, "Senior Civil Service roles that")

    def test_skills_index_marks_scs_skills_and_omits_the_level_table(self):
        skills_page = self._add_skills_page()

        body = self.client.get(skills_page.url).content.decode()

        self.assertIn("Senior Civil Service", body)
        self.assertIn("Examples of leadership using this skill", body)
        # The delegated-grade skill keeps its four-level table.
        self.assertIn("produce data models", body)

    def test_delegated_role_is_unaffected(self):
        role = GovukRole.objects.create(
            title="Data engineer",
            levels=[_level("Data engineer", [(self.delegated_skill, "working")])],
        )

        self.assertFalse(role.is_senior_civil_service)
        self.assertEqual(role.get_scs_skills(), [])
        self.assertEqual(len(role.get_levels_with_skills()), 1)


class SplitLeadershipExamplesTests(TestCase):
    def test_splits_description_from_examples(self):
        text = (
            "You can:\n"
            "- guide the organisation\n"
            "Examples of leadership using this skill:\n"
            "- prioritising capability needs\n"
            "- growing communities"
        )

        self.assertEqual(
            split_leadership_examples(text),
            (
                "You can:\n- guide the organisation",
                ["prioritising capability needs", "growing communities"],
            ),
        )

    def test_text_without_examples_is_returned_unchanged(self):
        self.assertEqual(
            split_leadership_examples("You can:\n- do a thing"), ("You can:\n- do a thing", [])
        )

    def test_empty_text(self):
        self.assertEqual(split_leadership_examples(""), ("", []))


class ChangelogEntryTests(FrameworkTestCase):
    def setUp(self):
        super().setUp()
        self.role = GovukRole.objects.create(title="Data engineer")
        self.skill = GovukSkill.objects.create(title="Data modelling")

        self.first_entry = GovukChangelogEntry.objects.create(
            date=date(2020, 1, 7), role=self.role, note="<p>First published.</p>"
        )
        self.latest_entry = GovukChangelogEntry.objects.create(
            date=date(2026, 5, 29), role=self.role, note="<p>Skills updated.</p>"
        )
        self.unpublished_entry = GovukChangelogEntry.objects.create(
            date=date(2026, 7, 1), role=self.role, note="<p>Draft note.</p>", live=False
        )
        self.site_wide_entry = GovukChangelogEntry.objects.create(
            date=date(2026, 5, 29), note="<p>A new role was added.</p>"
        )

    def test_role_changelog_returns_published_entries_newest_first(self):
        changelog = self.role.get_changelog()

        self.assertEqual(changelog["entries"], [self.latest_entry, self.first_entry])
        self.assertNotIn(self.unpublished_entry, changelog["entries"])
        self.assertNotIn(self.site_wide_entry, changelog["entries"])

    def test_role_changelog_dates_span_first_and_latest_entry(self):
        changelog = self.role.get_changelog()

        self.assertEqual(changelog["published_date"], date(2020, 1, 7))
        self.assertEqual(changelog["last_updated_date"], date(2026, 5, 29))

    def test_changelog_dates_are_none_without_entries(self):
        changelog = self.skill.get_changelog()

        self.assertEqual(changelog["entries"], [])
        self.assertIsNone(changelog["published_date"])
        self.assertIsNone(changelog["last_updated_date"])

    def test_skill_changelog_returns_its_own_entries(self):
        entry = GovukChangelogEntry.objects.create(
            date=date(2025, 3, 1), skill=self.skill, note="<p>Level descriptions updated.</p>"
        )
        self.assertEqual(self.skill.get_changelog()["entries"], [entry])

    def test_entry_cannot_target_both_a_role_and_a_skill(self):
        entry = GovukChangelogEntry(
            date=date(2026, 1, 1), role=self.role, skill=self.skill, note="<p>Ambiguous.</p>"
        )
        with self.assertRaises(ValidationError):
            entry.full_clean()

    def test_role_page_renders_updates_section(self):
        response = self.client.get(role_url(self.main_page, self.role))

        self.assertEqual(response.status_code, 200)
        for text in ("Updates", "7 January 2020", "29 May 2026", "Skills updated."):
            with self.subTest(text=text):
                self.assertContains(response, text)
        self.assertNotContains(response, "Draft note.")
        self.assertNotContains(response, "A new role was added.")
