from django.core.management.base import BaseCommand, CommandError
from wagtail.contrib.redirects.models import Redirect
from wagtail.models import Site

from govuk.models import GovukRole, GovukSkill, RolePage, SkillsAZPage


class Command(BaseCommand):
    """Redirect the live service's URLs onto the pages this site serves.

    The live framework publishes a role at /role/<slug> and a skill at
    /skill/<slug>; Wagtail serves a role page at /<slug> and every skill as a
    section of the skills A to Z. Cutting over without these leaves every
    bookmark, every search result and every link in the migrated content
    itself answering 404 -- the welcome copy alone links 37 roles the live
    way.

    The mapping is a rule, not content: each redirect points at whatever page
    carries the role or skill at the time the command is run, so it belongs
    in the runbook beside the import rather than in a migration. Run it again
    after content moves and the redirects follow; nothing is deleted.
    """

    help = (
        "Create or update redirects from the live service's /role/<slug> and "
        "/skill/<slug> URLs to the pages this site serves them on."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--hostname",
            help=(
                "Hostname of the site to seed. Defaults to the default site. "
                "Redirects are scoped to the one site, so a shared instance "
                "leaves its other sites alone."
            ),
        )

    def handle(self, *args, **options):
        site = self._site(options.get("hostname"))
        created = updated = 0

        for old_path, target in self._role_targets(site):
            was_created = self._seed(site, old_path, redirect_page=target)
            created, updated = created + was_created, updated + (not was_created)

        skill_targets, skills_note = self._skill_targets(site)
        for old_path, link in skill_targets:
            was_created = self._seed(site, old_path, redirect_link=link)
            created, updated = created + was_created, updated + (not was_created)

        if skills_note:
            self.stdout.write(skills_note)
        self.stdout.write(
            f"Redirects for {site.hostname}: {created} created, {updated} updated."
        )

    def _site(self, hostname):
        if hostname:
            site = Site.objects.filter(hostname=hostname).first()
            if site is None:
                raise CommandError(f"No site has the hostname '{hostname}'.")
            return site
        site = Site.objects.filter(is_default_site=True).first()
        if site is None:
            raise CommandError("There is no default site to seed redirects for.")
        return site

    def _role_targets(self, site):
        """(old_path, page) for every role a live page on this site renders.

        The first page in tree order keeps a role that several pages carry,
        matching the order the listings use.
        """
        slugs_by_id = dict(GovukRole.objects.values_list("pk", "slug"))
        seen: set[int] = set()
        targets = []
        pages = (
            RolePage.objects.live()
            .descendant_of(site.root_page, inclusive=True)
            .order_by("path")
        )
        for page in pages:
            for role_id in page.get_selected_role_ids():
                slug = slugs_by_id.get(role_id)
                if not slug or role_id in seen:
                    continue
                seen.add(role_id)
                targets.append((f"/role/{slug}", page))
        return targets

    def _skill_targets(self, site):
        """(old_path, link) for every skill, into its section of the A to Z.

        A skill is a snippet with no page of its own, so the redirect carries
        the fragment the search results already use.
        """
        skills_page = (
            SkillsAZPage.objects.live()
            .descendant_of(site.root_page, inclusive=True)
            .order_by("path")
            .first()
        )
        if skills_page is None:
            return [], "No live skills A to Z page: skill redirects were not seeded."
        page_url = skills_page.url or ""
        return [
            (f"/skill/{slug}", f"{page_url}#{slug}")
            for slug in GovukSkill.objects.values_list("slug", flat=True)
            if slug
        ], ""

    @staticmethod
    def _seed(site, old_path, *, redirect_page=None, redirect_link=""):
        _, was_created = Redirect.objects.update_or_create(
            old_path=Redirect.normalise_path(old_path),
            site=site,
            defaults={
                "redirect_page": redirect_page,
                "redirect_link": redirect_link,
                "is_permanent": True,
            },
        )
        return was_created
