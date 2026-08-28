"""The live service publishes /role/<slug> and /skill/<slug>; this site does
not. The command redirects the one scheme onto the other, so that cutover
breaks neither a bookmark nor the migrated content's own links -- the welcome
copy links roles the live way.
"""

import json
from io import StringIO

from django.core.management import call_command
from django.core.management.base import CommandError
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


class LiveServiceFixture(TestCase):
    """One role with a page that renders it, one skill with an A to Z to sit in."""

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


@override_settings(FEATURE_FLAGS=_feature_flags())
class SeedLiveServiceRedirectsTests(LiveServiceFixture):
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


@override_settings(FEATURE_FLAGS=_feature_flags())
class CheckLiveServiceRedirectsTests(LiveServiceFixture):
    """--check, for step 6 of the cutover: before DNS, while it is still free.

    CS32-1579's third acceptance criterion is that each old URL is tested and
    returns the expected redirect, so this has to fail rather than report. A
    command that prints "3 URLs have no redirect" and exits 0 is a line in a
    log nobody reads at four in the afternoon on a cutover day.
    """

    def _check(self):
        out = StringIO()
        call_command("seed_live_service_redirects", "--check", stdout=out)
        return out.getvalue()

    def test_a_fully_seeded_site_passes(self):
        self._run()

        self.assertIn("Every live service URL redirects", self._check())

    def test_a_missing_redirect_fails_and_names_the_url(self):
        with self.assertRaises(CommandError) as refusal:
            self._check()

        self.assertIn("have no redirect", str(refusal.exception))
        self.assertIn("Run this command without --check", str(refusal.exception))

    def test_checking_writes_nothing(self):
        """Otherwise the check and the fix are the same command, and a check
        that repairs what it is checking can never fail twice running."""
        with self.assertRaises(CommandError):
            self._check()

        self.assertFalse(Redirect.objects.exists())

    def test_a_redirect_pointing_somewhere_else_fails(self):
        """Seeded once, then the page moved, or the row was edited by hand."""
        self._run()
        redirect = Redirect.objects.get(
            old_path=Redirect.normalise_path(f"/role/{self.role.slug}")
        )
        redirect.redirect_page = self.skills_page
        redirect.save(update_fields=["redirect_page"])

        with self.assertRaises(CommandError) as refusal:
            self._check()

        self.assertIn("have no redirect", str(refusal.exception))

    def test_a_temporary_redirect_fails(self):
        """The acceptance criterion says 301, and says so twice."""
        self._run()
        Redirect.objects.filter(
            old_path=Redirect.normalise_path(f"/role/{self.role.slug}")
        ).update(is_permanent=False)

        with self.assertRaises(CommandError):
            self._check()

    def test_a_role_with_no_page_fails_differently(self):
        """Seeding cannot fix this one, so the message does not suggest it.

        The rule produces no target for a role no live page renders, so there
        is nothing to create and nothing to report as missing -- and the URL is
        one the live service publishes today.
        """
        GovukRole.objects.create(title="Unpublished role")
        self._run()

        with self.assertRaises(CommandError) as refusal:
            self._check()

        self.assertIn("nothing to point at", str(refusal.exception))
        self.assertNotIn("have no redirect", str(refusal.exception))

    def test_a_skill_with_no_a_to_z_fails(self):
        self.skills_page.unpublish()
        self._run()

        with self.assertRaises(CommandError) as refusal:
            self._check()

        self.assertIn("nothing to point at", str(refusal.exception))
