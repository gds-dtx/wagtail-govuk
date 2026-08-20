"""The live service publishes /role/<slug> and /skill/<slug>; this site does
not. The command redirects the one scheme onto the other, so that cutover
breaks neither a bookmark nor the migrated content's own links -- the welcome
copy links roles the live way.
"""

import json
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings
from wagtail.contrib.redirects.models import Redirect
from wagtail.models import Site

from govuk.models import GovukRole, GovukSkill, RolePage, SkillsAZPage


def _feature_flags() -> dict[str, bool]:
    return {
        "SKILLS": True,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
    }


@override_settings(FEATURE_FLAGS=_feature_flags())
class SeedLiveServiceRedirectsTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific

        self.role = GovukRole.objects.create(title="Business architect")
        self.role_page = self.root_page.add_child(
            instance=RolePage(
                title="Business architect",
                slug="business-architect",
                selected_roles=json.dumps([{"type": "role", "value": self.role.pk}]),
            )
        )
        self.role_page.save_revision().publish()

        self.skill = GovukSkill.objects.create(title="Prototyping")
        self.skills_page = self.root_page.add_child(
            instance=SkillsAZPage(title="Skills A to Z", slug="skills")
        )
        self.skills_page.save_revision().publish()

    def _run(self):
        out = StringIO()
        call_command("seed_live_service_redirects", stdout=out)
        return out.getvalue()

    def test_a_live_role_url_reaches_the_page_that_renders_the_role(self):
        self._run()

        response = self.client.get(f"/role/{self.role.slug}")

        self.assertEqual(response.status_code, 301)
        self.assertEqual(response["Location"], self.role_page.url)

    def test_a_live_skill_url_reaches_the_skills_section_it_names(self):
        self._run()

        response = self.client.get(f"/skill/{self.skill.slug}")

        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response["Location"], f"{self.skills_page.url}#{self.skill.slug}"
        )

    def test_the_redirect_follows_the_page_not_the_address_it_had(self):
        """redirect_page rather than a pasted URL: a page moved or reslugged
        in the admin keeps its inbound redirect without the command rerunning."""
        self._run()

        redirect = Redirect.objects.get(
            old_path=Redirect.normalise_path(f"/role/{self.role.slug}")
        )

        self.assertEqual(redirect.redirect_page_id, self.role_page.pk)
        self.assertTrue(redirect.is_permanent)
        self.assertEqual(redirect.site_id, self.site.pk)

    def test_running_twice_changes_nothing(self):
        self._run()
        before = sorted(
            Redirect.objects.values_list("old_path", "redirect_page_id", "redirect_link")
        )

        output = self._run()

        after = sorted(
            Redirect.objects.values_list("old_path", "redirect_page_id", "redirect_link")
        )
        self.assertEqual(before, after)
        self.assertIn("0 created", output)

    def test_a_role_no_live_page_renders_gets_no_redirect(self):
        homeless = GovukRole.objects.create(title="Unpublished role")

        self._run()

        self.assertFalse(
            Redirect.objects.filter(
                old_path=Redirect.normalise_path(f"/role/{homeless.slug}")
            ).exists()
        )

    def test_the_first_page_in_tree_order_keeps_a_role_two_pages_carry(self):
        second_page = self.root_page.add_child(
            instance=RolePage(
                title="Business architect again",
                slug="business-architect-again",
                selected_roles=json.dumps([{"type": "role", "value": self.role.pk}]),
            )
        )
        second_page.save_revision().publish()

        self._run()

        redirect = Redirect.objects.get(
            old_path=Redirect.normalise_path(f"/role/{self.role.slug}")
        )
        self.assertEqual(redirect.redirect_page_id, self.role_page.pk)

    def test_without_a_skills_page_the_skill_redirects_are_skipped_and_said(self):
        self.skills_page.unpublish()

        output = self._run()

        self.assertIn("skill redirects were not seeded", output)
        self.assertFalse(
            Redirect.objects.filter(old_path__startswith="/skill/").exists()
        )
        # The role redirects still arrive: half a seeding beats none, and the
        # message says which half is missing.
        self.assertTrue(
            Redirect.objects.filter(old_path__startswith="/role/").exists()
        )
