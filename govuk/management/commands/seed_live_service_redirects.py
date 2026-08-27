from django.core.management.base import BaseCommand, CommandError
from wagtail.contrib.redirects.models import Redirect
from wagtail.models import Site

from govuk.live_service_links import role_page_targets, skill_targets


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

    The rule itself lives in govuk.live_service_links, which the changelog
    notes are also rewritten through, so the two cannot drift apart.
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

        for slug, target in role_page_targets(site):
            was_created = self._seed(site, f"/role/{slug}", redirect_page=target)
            created, updated = created + was_created, updated + (not was_created)

        skills, skills_page = skill_targets(site)
        for slug, link in skills:
            was_created = self._seed(site, f"/skill/{slug}", redirect_link=link)
            created, updated = created + was_created, updated + (not was_created)

        if skills_page is None:
            self.stdout.write(
                "No live skills A to Z page: skill redirects were not seeded."
            )
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
