from datetime import date

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
)
from govuk.tests.framework_helpers import make_framework_main_page, role_url


def _feature_flags(*, skills_enabled: bool = True) -> dict[str, bool]:
    return {
        "SKILLS": skills_enabled,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
    }


@override_settings(FEATURE_FLAGS=_feature_flags())
class SeniorCivilServiceRoleTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific

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

        self.main_page = make_framework_main_page(self.root_page)
        self.cto_url = role_url(self.main_page, self.cto)

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
        self.assertEqual(
            [entry["role"] for entry in self.cto.get_related_roles()], [other]
        )

    def test_scs_skill_lists_the_roles_requiring_it(self):
        self.assertEqual(
            self.capability_building.get_roles_requiring_skill(), [self.cto]
        )

    def test_role_page_renders_scs_skills_and_grades(self):
        response = self.client.get(self.cto_url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Skills for chief technology officer")
        self.assertContains(response, "Strategic technology planning")
        self.assertContains(response, "Examples of leadership using this skill")
        self.assertContains(response, "inspiring people with your vision")
        self.assertContains(response, "SCS 1")
        self.assertContains(response, "SCS 2")
        # SCS roles have no proficiency levels
        self.assertNotContains(response, "Level: Working")

    def test_scs_role_page_carries_the_wording_every_scs_role_shares(self):
        """Two passages read the same on every Senior Civil Service page."""
        response = self.client.get(self.cto_url)

        self.assertContains(
            response,
            "A specific chief technology officer job can vary depending on the",
        )
        self.assertContains(
            response,
            "The chief technology officer role will need to use digital and data skills to:",
        )
        self.assertContains(response, "be an effective digital and data leader")

    def test_both_passages_link_to_the_context_and_challenges_page(self):
        context_page = self.root_page.add_child(
            instance=ContentPage(
                title=(
                    "Context and challenges for Senior Civil Service roles "
                    "in digital and data"
                ),
                slug=FrameworkMainPage.SCS_CONTEXT_SLUG,
                body="",
            )
        )
        context_page.save_revision().publish()

        response = self.client.get(self.cto_url)

        self.assertContains(
            response,
            f'<a class="govuk-link" href="{context_page.url}">'
            "context and challenges in your organisation</a>",
            html=True,
        )
        self.assertContains(
            response,
            f'<a class="govuk-link" href="{context_page.url}">'
            "on the context and challenges in your organisation</a>",
            html=True,
        )

    def test_the_wording_survives_the_context_page_not_existing(self):
        """That page has not been migrated across yet, and a link written out
        by hand would be a 404 on every Senior Civil Service role until it is."""
        response = self.client.get(self.cto_url)

        self.assertContains(response, "context and challenges in your organisation")
        self.assertNotContains(response, FrameworkMainPage.SCS_CONTEXT_SLUG)

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

        # Wagtail's URL carries its trailing slash, so the reader is not sent
        # through a redirect on the way.
        self.assertContains(
            response,
            f'<a class="govuk-link" href="{job_grades.url}">'
            "Civil Service job grade</a>",
            html=True,
        )

    def test_the_grade_wording_survives_the_job_grades_page_not_existing(self):
        """Another profession's framework need not publish that page, and a
        link written out by hand would be a 404 on every role there."""
        response = self.client.get(self.cto_url)

        self.assertContains(response, "Civil Service job grade of:")
        self.assertNotContains(response, 'href="/job-grades"')

    def test_another_sites_job_grades_page_is_not_linked_to(self):
        other_root = self._another_site()
        their_page = other_root.add_child(
            instance=ContentPage(
                title="Civil Service job grades",
                slug=FrameworkMainPage.JOB_GRADES_SLUG,
                body="",
            )
        )
        their_page.save_revision().publish()

        response = self.client.get(self.cto_url)

        self.assertContains(response, "Civil Service job grade of:")
        self.assertNotContains(response, "other.example.gov.uk")

    def _another_site(self) -> Page:
        """A second service in the same database, which one instance can host,
        and which a lookup across every page would reach into."""
        tree_root = Page.objects.get(depth=1)
        other_root = tree_root.add_child(
            instance=ContentPage(
                title="Another service", slug="another-service", body=""
            )
        )
        other_root.save_revision().publish()
        Site.objects.create(hostname="other.example.gov.uk", root_page=other_root)
        return other_root

    def test_another_sites_context_page_is_not_linked_to(self):
        other_root = self._another_site()
        their_page = other_root.add_child(
            instance=ContentPage(
                title="Context and challenges",
                slug=FrameworkMainPage.SCS_CONTEXT_SLUG,
                body="",
            )
        )
        their_page.save_revision().publish()

        response = self.client.get(self.cto_url)

        self.assertContains(response, "context and challenges in your organisation")
        self.assertNotContains(response, "other.example.gov.uk")

    def test_another_sites_skills_index_is_not_linked_to(self):
        other_root = self._another_site()
        their_index = other_root.add_child(
            instance=FrameworkSkillsPage(title="Skills A to Z", slug="skills")
        )
        their_index.save_revision().publish()

        response = self.client.get(self.cto_url)

        self.assertContains(response, "Strategic technology planning")
        self.assertNotContains(response, "other.example.gov.uk")

    def test_a_senior_role_page_keeps_the_anchor_ids_the_framework_publishes(self):
        """Anyone who has bookmarked or written down a link to a section of a
        senior role page should land on the same section here, so these ids are
        the live service's, not ones chosen to read tidily.

        Three of them are spent differently on a senior page than elsewhere in
        the framework: "role-levels" heads the skills, senior roles having no
        levels; "roles-that-shares" heads the roles sharing its skills; and
        "related-roles" heads the roles leading into it rather than beside it.
        """
        data_engineer = GovukRole.objects.create(title="Data engineer", family="Data")
        self.cto.roles_that_could_lead_here = [
            {"type": "role", "value": data_engineer.pk}
        ]
        self.cto.save()
        GovukRole.objects.create(
            title="Chief data officer",
            is_senior_civil_service=True,
            scs_skills=[{"type": "skill", "value": self.capability_building.pk}],
        )
        GovukChangelogEntry.objects.create(
            date=date(2026, 5, 29), role=self.cto, note="<p>First published.</p>"
        )

        response = self.client.get(self.cto_url)

        for anchor in (
            "what-a-chief-technology-officer-does",
            "role-levels",
            "roles-that-shares",
            "related-roles",
            "update-history",
        ):
            with self.subTest(anchor=anchor):
                self.assertContains(response, f'id="{anchor}"')
                self.assertContains(response, f'href="#{anchor}"')

    def test_a_senior_role_given_levels_does_not_repeat_the_role_levels_id(self):
        """"role-levels" heads a senior role's skills only because the
        framework gives senior roles no levels. Nothing in the model says so,
        and a page carrying both would otherwise print the id twice and send
        both contents links to whichever came first.
        """
        self.cto.levels = [
            {
                "type": "level",
                "value": {
                    "title": "Lead chief technology officer",
                    "description": "",
                    "grades": ["scs1"],
                    "skills": [
                        {"skill": self.delegated_skill.pk, "level": "working"}
                    ],
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
            self.cto.get_roles_that_could_lead_here(),
            [solution_architect, data_engineer],
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

    def test_the_progression_section_links_to_the_route_of_a_role(self):
        data_engineer = GovukRole.objects.create(title="Data engineer", family="Data")
        data_engineer_url = role_url(self.main_page, data_engineer)
        self.cto.roles_that_could_lead_here = [
            {"type": "role", "value": data_engineer.pk}
        ]
        self.cto.save()

        response = self.client.get(self.cto_url)

        self.assertContains(
            response, "Roles that could lead to chief technology officer"
        )
        self.assertContains(
            response,
            f'<a href="{data_engineer_url}" class="govuk-link">Data engineer</a>',
            html=True,
        )
        # and again in the in-page contents, where every section is listed.
        # A senior role uses "related-roles" for this, the framework having
        # spent that id differently on its senior pages.
        self.assertContains(response, 'href="#related-roles"')

    def test_every_progression_role_is_linked_to_its_own_route(self):
        """Every role now has a route under the framework main page, so a role
        that leads here is always linked -- there is no longer a role without a
        page of its own to render as plain text."""
        data_ethicist = GovukRole.objects.create(title="Data ethicist", family="Data")
        data_ethicist_url = role_url(self.main_page, data_ethicist)
        self.cto.roles_that_could_lead_here = [
            {"type": "role", "value": data_ethicist.pk}
        ]
        self.cto.save()

        response = self.client.get(self.cto_url)

        self.assertContains(
            response,
            f'<a href="{data_ethicist_url}" class="govuk-link">Data ethicist</a>',
            html=True,
        )

    def test_the_progression_section_is_left_out_when_no_roles_are_mapped(self):
        response = self.client.get(self.cto_url)

        self.assertNotContains(
            response, "Roles that could lead to chief technology officer"
        )

    def _map_data_engineer_into_the_cto(self):
        """The framework's career path, as the content team authors it: on the
        senior role, as the roles that could lead into it."""
        data_engineer = GovukRole.objects.create(title="Data engineer", family="Data")
        self.cto.roles_that_could_lead_here = [
            {"type": "role", "value": data_engineer.pk}
        ]
        self.cto.save()
        return data_engineer, role_url(self.main_page, data_engineer)

    def test_the_mapping_is_turned_around_for_the_role_it_leads_from(self):
        """The senior role names the ones that lead into it, but the framework
        also prints the path forwards, on the page of the role you start at."""
        _, data_engineer_url = self._map_data_engineer_into_the_cto()

        response = self.client.get(data_engineer_url)

        self.assertContains(
            response,
            "Senior Civil Service roles that data engineer could lead to",
        )
        self.assertContains(
            response,
            f'<a href="{self.cto_url}" class="govuk-link">'
            "Chief technology officer</a>",
            html=True,
        )
        # and again in the in-page contents, where every section is listed
        self.assertContains(response, 'href="#related-scs-roles"')

    def test_the_senior_roles_a_role_leads_to_are_listed_by_title(self):
        data_engineer, data_engineer_url = self._map_data_engineer_into_the_cto()
        GovukRole.objects.create(
            title="Chief data officer",
            family="Chief digital and data",
            is_senior_civil_service=True,
            roles_that_could_lead_here=[{"type": "role", "value": data_engineer.pk}],
        )

        body = self.client.get(data_engineer_url).content.decode()
        # Only within the section: the side navigation lists both roles too,
        # and searching the whole page would compare their order there.
        section = body[body.index('id="related-scs-roles"') :]
        section = section[: section.index("</ul>")]

        self.assertLess(
            section.index("Chief data officer</a>"),
            section.index("Chief technology officer</a>"),
        )

    def test_only_senior_roles_are_listed_as_somewhere_a_role_leads_to(self):
        """The heading says Senior Civil Service, so a delegated-grade role
        mapping the same way does not belong in it."""
        data_engineer, data_engineer_url = self._map_data_engineer_into_the_cto()
        GovukRole.objects.create(
            title="Lead data engineer",
            family="Data",
            roles_that_could_lead_here=[{"type": "role", "value": data_engineer.pk}],
        )

        body = self.client.get(data_engineer_url).content.decode()
        # Only within the section: the side navigation now lists every role,
        # "Lead data engineer" among them, so searching the whole page would
        # find it there regardless of the section it heads.
        section = body[body.index('id="related-scs-roles"') :]
        section = section[: section.index("</ul>")]

        self.assertIn("Chief technology officer", section)
        self.assertNotIn("Lead data engineer", section)

    def test_a_senior_role_is_not_told_which_senior_roles_it_leads_to(self):
        """The framework prints the path forwards only on the way up to the
        Senior Civil Service, not between roles already in it."""
        self._map_data_engineer_into_the_cto()
        GovukRole.objects.create(
            title="Chief digital and information officer",
            family="Chief digital and data",
            is_senior_civil_service=True,
            roles_that_could_lead_here=[{"type": "role", "value": self.cto.pk}],
        )

        response = self.client.get(self.cto_url)

        self.assertNotContains(
            response,
            "Senior Civil Service roles that chief technology officer could lead to",
        )
        self.assertNotContains(response, 'id="related-scs-roles"')

    def test_the_forward_section_is_left_out_when_nothing_leads_anywhere(self):
        data_engineer = GovukRole.objects.create(title="Data engineer", family="Data")

        response = self.client.get(role_url(self.main_page, data_engineer))

        self.assertNotContains(response, "Senior Civil Service roles that")

    def test_skills_index_marks_scs_skills_and_omits_the_level_table(self):
        skills_page = self.root_page.add_child(
            instance=FrameworkSkillsPage(title="Skills A to Z", slug="skills")
        )
        skills_page.save_revision().publish()

        response = self.client.get(skills_page.url)
        body = response.content.decode()

        self.assertIn("Senior Civil Service", body)
        self.assertIn("Examples of leadership using this skill", body)
        # the delegated-grade skill keeps its four-level table
        self.assertIn("produce data models", body)

    def test_delegated_role_is_unaffected(self):
        role = GovukRole.objects.create(
            title="Data engineer",
            levels=[
                {
                    "type": "level",
                    "value": {
                        "title": "Data engineer",
                        "description": "",
                        "skills": [
                            {"skill": self.delegated_skill.pk, "level": "working"}
                        ],
                    },
                }
            ],
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
        description, examples = split_leadership_examples(text)

        self.assertEqual(description, "You can:\n- guide the organisation")
        self.assertEqual(
            examples, ["prioritising capability needs", "growing communities"]
        )

    def test_text_without_examples_is_returned_unchanged(self):
        description, examples = split_leadership_examples("You can:\n- do a thing")

        self.assertEqual(description, "You can:\n- do a thing")
        self.assertEqual(examples, [])

    def test_empty_text(self):
        self.assertEqual(split_leadership_examples(""), ("", []))
