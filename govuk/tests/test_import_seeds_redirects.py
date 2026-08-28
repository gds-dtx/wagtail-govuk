"""An import seeds the live service's redirects -- CS32-1579.

The redirects are the one part of the migration that is neither in the export
file nor recreatable from it: they are rows in Wagtail's own redirects app, and
they name pages by primary key, which means nothing in another database. So the
runbook had a step for them, run by hand over `aws ecs execute-command` against
a task with `enable_execute_command` switched on for the occasion, after the
import and before DNS.

A cutover that misses it produces a site that looks finished. Every page is
there and every page is right, and every bookmark, every search result and
every GovSearch link into the old service answers 404.

The import is the moment the pages those redirects point at come into
existence, so these tests are that it is also the moment they are written --
and that running it twice, or as somebody who does not administer redirects,
does the honest thing.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase, override_settings
from wagtail.contrib.redirects.models import Redirect
from wagtail.models import GroupPagePermission, Page, Site

from govuk.models import GovukRole, GovukSkill, RolePage, SkillsAZPage
from govuk.page_import_export import PAGE_EXPORT_FORMAT, import_pages_from_payload


def _feature_flags(*, skills_enabled: bool = True) -> dict[str, bool]:
    return {
        "SKILLS": skills_enabled,
        "ORGANISATIONS": False,
        "PEOPLE_FINDER": False,
        "FEEDBACK": False,
    }


@override_settings(FEATURE_FLAGS=_feature_flags())
class ImportSeedsRedirectsTests(TestCase):
    def setUp(self):
        self.site = Site.objects.get(is_default_site=True)
        self.root_page = self.site.root_page.specific

        self.role = GovukRole.objects.create(title="Data analyst")
        self.skill = GovukSkill.objects.create(title="Prototyping")
        self.skills_page = self.root_page.add_child(
            instance=SkillsAZPage(title="Skills A to Z", slug="skills")
        )
        self.skills_page.save_revision().publish()

        self.user = get_user_model().objects.create_superuser(
            username="importer",
            email="importer@example.gov.uk",
            password="unused-password",
        )

    # -- helpers --------------------------------------------------------------

    def _payload(self, *, slug="data-analyst"):
        return {
            "format": PAGE_EXPORT_FORMAT,
            "pages": [
                {
                    "model": "govuk.RolePage",
                    "settings": {"slug": slug, "title": "Data analyst"},
                    "fields": {
                        "selected_roles": [{"type": "role", "value": self.role.slug}]
                    },
                }
            ],
        }

    def _import(self, user=None, payload=None):
        return import_pages_from_payload(
            payload=payload or self._payload(),
            site=self.site,
            user=self.user if user is None else user,
        )

    def _publisher_who_does_not_administer_redirects(self):
        """Everything needed to import pages, and nothing to do with redirects.

        The ordinary shape of an editor account: they publish content, someone
        else runs the redirects area of the admin.
        """
        group = Group.objects.create(name="publishers")
        group.permissions.set(Permission.objects.filter(codename="access_admin"))
        for codename in ("add_page", "change_page", "publish_page"):
            GroupPagePermission.objects.create(
                group=group,
                page=Page.objects.get(pk=1),
                permission=Permission.objects.get(
                    content_type__app_label="wagtailcore", codename=codename
                ),
            )
        user = get_user_model().objects.create_user(
            username="publisher",
            email="publisher@example.gov.uk",
            password="unused-password",
            is_staff=True,
        )
        user.groups.add(group)
        return user

    def _redirect(self, old_path):
        return Redirect.objects.get(old_path=Redirect.normalise_path(old_path))

    def _redirect_notes(self, result):
        return [note for note in result.notes if "live service" in note]

    # -- the redirects arrive with the content --------------------------------

    def test_a_live_role_url_works_without_the_runbook_step(self):
        self.assertFalse(Redirect.objects.exists())

        self._import()

        response = self.client.get(f"/role/{self.role.slug}")
        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response["Location"], RolePage.objects.get(slug="data-analyst").url
        )

    def test_a_live_skill_url_works_without_the_runbook_step(self):
        self._import()

        response = self.client.get(f"/skill/{self.skill.slug}")
        self.assertEqual(response.status_code, 301)
        self.assertEqual(
            response["Location"], f"{self.skills_page.url}#{self.skill.slug}"
        )

    def test_the_redirect_follows_the_page_rather_than_its_address(self):
        self._import()

        redirect = self._redirect(f"/role/{self.role.slug}")

        self.assertEqual(
            redirect.redirect_page_id, RolePage.objects.get(slug="data-analyst").pk
        )
        self.assertTrue(redirect.is_permanent)
        self.assertEqual(redirect.site_id, self.site.pk)

    def test_the_import_report_says_the_redirects_were_written(self):
        """It changes the site beyond what was in the file, so it is reported."""
        result = self._import()

        self.assertEqual(
            self._redirect_notes(result),
            [
                "Redirected 2 of the live service's URLs to the pages this "
                "site serves them on (2 new, 0 repointed)."
            ],
        )

    # -- running it again ------------------------------------------------------

    def test_importing_again_writes_nothing_and_says_nothing_about_redirects(self):
        self._import()
        before = sorted(
            Redirect.objects.values_list("old_path", "redirect_page_id", "redirect_link")
        )

        result = self._import()

        self.assertEqual(
            sorted(
                Redirect.objects.values_list(
                    "old_path", "redirect_page_id", "redirect_link"
                )
            ),
            before,
        )
        self.assertEqual(self._redirect_notes(result), [])

    def test_a_redirect_pointing_at_the_wrong_page_is_put_right(self):
        """A role moved to another page, or a row edited by hand in the admin."""
        Redirect.objects.create(
            old_path=Redirect.normalise_path(f"/role/{self.role.slug}"),
            site=self.site,
            redirect_page=self.skills_page,
            is_permanent=True,
        )

        result = self._import()

        self.assertEqual(
            self._redirect(f"/role/{self.role.slug}").redirect_page_id,
            RolePage.objects.get(slug="data-analyst").pk,
        )
        self.assertIn("1 repointed", self._redirect_notes(result)[0])

    def test_a_redirect_this_rule_does_not_produce_is_left_alone(self):
        """Somebody's hand-written redirect is still the only answer some URL has."""
        Redirect.objects.create(
            old_path=Redirect.normalise_path("/collections/digital-roles"),
            site=self.site,
            redirect_link="https://www.gov.uk/",
            is_permanent=True,
        )

        self._import()

        self.assertEqual(
            self._redirect("/collections/digital-roles").redirect_link,
            "https://www.gov.uk/",
        )

    # -- when it cannot, or should not ----------------------------------------

    def test_an_account_that_cannot_administer_redirects_is_told_so(self):
        """The URLs are missing whether or not this account could have fixed it.

        Reported rather than passed over, because the person running the import
        is the one who still has time to ask for the permission or for the
        command to be run.
        """
        result = self._import(self._publisher_who_does_not_administer_redirects())

        self.assertFalse(Redirect.objects.exists())
        self.assertIn(
            "Skipped seeding the live service's redirects because you do not "
            "have permission to add redirects (wagtailredirects.add_redirect).",
            result.errors,
        )
        # The pages themselves still came in: half an import beats none, and
        # the message says which half is missing.
        self.assertTrue(RolePage.objects.filter(slug="data-analyst").exists())

    def test_a_missing_skills_a_to_z_is_reported_rather_than_passed_over(self):
        self.skills_page.unpublish()

        result = self._import()

        self.assertIn(
            "The live service's /skill/ URLs were not redirected: this site "
            "has no live skills A to Z page for them to point into.",
            result.notes,
        )
        self.assertFalse(
            Redirect.objects.filter(old_path__startswith="/skill/").exists()
        )
        # The role redirects still arrive.
        self.assertTrue(
            Redirect.objects.filter(old_path__startswith="/role/").exists()
        )

    @override_settings(FEATURE_FLAGS=_feature_flags(skills_enabled=False))
    def test_an_instance_that_is_not_the_framework_seeds_nothing(self):
        """/role/ and /skill/ are the capability framework's URL shapes.

        This importer runs on every instance on the platform. Nowhere else has
        a live service publishing those paths to redirect from.
        """
        self._import()

        self.assertFalse(Redirect.objects.exists())
