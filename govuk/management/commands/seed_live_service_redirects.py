from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from wagtail.models import Site

from govuk.live_service_links import (
    seed_live_service_redirects,
    unanswerable_live_service_urls,
    unseeded_live_service_redirects,
)


class Command(BaseCommand):
    """Redirect the live service's URLs onto the pages this site serves.

    The live framework publishes a role at /role/<slug> and a skill at
    /skill/<slug>; Wagtail serves a role page at /<slug> and every skill as a
    section of the skills A to Z. Cutting over without these leaves every
    bookmark, every search result and every link in the migrated content
    itself answering 404 -- the welcome copy alone links 37 roles the live
    way.

    An import now seeds these itself, so on a fresh instance this command is a
    second line rather than the only one. It stays because the mapping is a
    rule, not content: each redirect points at whatever page carries the role
    or skill at the time it is run, so a page moved or reslugged in the admin
    afterwards is a reason to run it again. Nothing is deleted.

    --check writes nothing and fails if any live-service URL would not reach
    the right page, which is what CS32-1579 asks be tested before cutover.

    The rule itself lives in govuk.live_service_links, which the changelog
    notes are also rewritten through, so the three cannot drift apart.
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
        parser.add_argument(
            "--check",
            action="store_true",
            help=(
                "Report what is missing and exit non-zero, without writing "
                "anything. For the cutover check and for CI."
            ),
        )

    def handle(self, *args, **options):
        site = self._site(options.get("hostname"))

        if not settings.FEATURE_FLAGS.get("SKILLS"):
            # Said out loud rather than left to the numbers. With no targets
            # the write path prints three zeros and --check prints its
            # all-clear, and an operator running this from a shared runbook
            # would read either as "the redirects are in place" when the truth
            # is that these URLs are not this service's to answer.
            self.stdout.write(
                "FEATURE_SKILLS is off on this instance. The live service's "
                "/role/ and /skill/ URLs belong to the Capability Framework, "
                "so there is nothing here to redirect."
            )
            return

        if options.get("check"):
            self._check(site)
            return

        seeded = seed_live_service_redirects(site)
        if seeded.skills_have_nowhere_to_go:
            self.stdout.write(
                "No live skills A to Z page: skill redirects were not seeded."
            )
        self.stdout.write(
            f"Redirects for {site.hostname}: {seeded.created} created, "
            f"{seeded.updated} updated, {seeded.unchanged} already correct."
        )

    def _check(self, site):
        """Fail loudly rather than report a number nobody reads.

        Two separate failures. A path with no redirect is one this command
        would fix, so the message says to run it. A URL with no target at all
        is content -- a role no live page renders -- and running the command
        again would not touch it, so it is named separately to save somebody
        trying.
        """
        unseeded = unseeded_live_service_redirects(site)
        unanswerable = unanswerable_live_service_urls(site)

        for path in unseeded:
            self.stdout.write(f"  no redirect: {path}")
        for url in unanswerable:
            self.stdout.write(f"  nothing to point at: {url}")

        problems = []
        if unseeded:
            problems.append(
                f"{len(unseeded)} live service URLs have no redirect on "
                f"{site.hostname}. Run this command without --check."
            )
        if unanswerable:
            problems.append(
                f"{len(unanswerable)} live service URLs have nothing to point "
                "at: no live page on this site carries that role or skill."
            )
        if problems:
            raise CommandError(" ".join(problems))

        self.stdout.write(
            f"Every live service URL redirects to a page on {site.hostname}."
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
