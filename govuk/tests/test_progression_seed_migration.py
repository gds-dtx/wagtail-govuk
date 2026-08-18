"""The data migration that seeds the published progression mapping.

Run against the current models rather than through the migration executor: the
historical model 0061 asks for is this one, the field having been added in the
migration immediately before it.
"""

from importlib import import_module

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase
from wagtail.models import ReferenceIndex

from govuk.models import GovukRole

# Imported by name because a module starting with a digit cannot be written in
# an import statement.
migration = import_module("govuk.migrations.0061_seed_roles_that_could_lead_here")
seed = migration.seed
PROGRESSION_BY_SENIOR_ROLE = migration.PROGRESSION_BY_SENIOR_ROLE


class ProgressionSeedMigrationTests(TestCase):
    def _create(self, slug: str, *, senior: bool = False) -> GovukRole:
        return GovukRole.objects.create(
            title=slug.replace("-", " ").capitalize(),
            slug=slug,
            is_senior_civil_service=senior,
        )

    def _create_every_role_in_the_mapping(self):
        for senior_slug, source_slugs in PROGRESSION_BY_SENIOR_ROLE.items():
            for slug in [senior_slug, *source_slugs]:
                if not GovukRole.objects.filter(slug=slug).exists():
                    self._create(slug, senior=slug in PROGRESSION_BY_SENIOR_ROLE)

    def _seeded(self, slug: str) -> list[str]:
        role = GovukRole.objects.get(slug=slug)
        return [entry.slug for entry in role.get_roles_that_could_lead_here()]

    def test_every_senior_role_is_given_the_published_mapping_in_order(self):
        self._create_every_role_in_the_mapping()

        seed(apps, None)

        for senior_slug, source_slugs in PROGRESSION_BY_SENIOR_ROLE.items():
            with self.subTest(role=senior_slug):
                self.assertEqual(self._seeded(senior_slug), source_slugs)

    def test_running_it_twice_changes_nothing(self):
        self._create_every_role_in_the_mapping()

        seed(apps, None)
        first_pass = {
            slug: self._seeded(slug) for slug in PROGRESSION_BY_SENIOR_ROLE
        }
        seed(apps, None)

        self.assertEqual(
            {slug: self._seeded(slug) for slug in PROGRESSION_BY_SENIOR_ROLE},
            first_pass,
        )

    def test_a_role_an_editor_has_already_filled_in_is_left_alone(self):
        self._create_every_role_in_the_mapping()
        cto = GovukRole.objects.get(slug="chief-technology-officer")
        only_one = GovukRole.objects.get(slug="software-developer")
        cto.roles_that_could_lead_here = [{"type": "role", "value": only_one.pk}]
        cto.save()

        seed(apps, None)

        self.assertEqual(self._seeded("chief-technology-officer"), ["software-developer"])

    def test_a_site_without_these_roles_is_left_untouched(self):
        """A Wagtail instance serving another profession has none of them, and
        the migration runs there too."""
        self._create("cyber-security-architect")

        seed(apps, None)

        self.assertEqual(GovukRole.objects.count(), 1)
        self.assertEqual(self._seeded("cyber-security-architect"), [])

    def test_a_role_the_mapping_names_but_this_site_lacks_is_passed_over(self):
        self._create("chief-technology-officer", senior=True)
        self._create("software-developer")
        self._create("technical-architect")

        seed(apps, None)

        self.assertEqual(
            self._seeded("chief-technology-officer"),
            ["software-developer", "technical-architect"],
        )


class ProgressionSeedReferenceIndexTests(TestCase):
    """What Wagtail is told about the roles the seeded blocks point at.

    Run against the historical model this time, which is the one a deployment
    hands the migration and the only one that shows the gap: saving through it
    updates no reference index, so the admin would offer to delete a role four
    senior roles name while reporting no usage of it anywhere.
    """

    def setUp(self):
        for slug in ("chief-technology-officer", "software-developer"):
            GovukRole.objects.create(
                title=slug.replace("-", " ").capitalize(),
                slug=slug,
                is_senior_civil_service=slug == "chief-technology-officer",
            )

    def _references(self):
        return ReferenceIndex.objects.filter(
            content_type=ContentType.objects.get_for_model(GovukRole),
            to_content_type=ContentType.objects.get_for_model(GovukRole),
        )

    def _historical_apps(self):
        """The models as they stood at 0060, which is what 0061 is given."""
        loader = MigrationLoader(connection)
        state = loader.project_state(
            ("govuk", "0060_govukrole_roles_that_could_lead_here")
        )
        return state.apps

    def test_the_seeded_blocks_are_written_to_the_reference_index(self):
        seed(self._historical_apps(), None)

        self.assertEqual(
            [reference.to_object_id for reference in self._references()],
            [str(GovukRole.objects.get(slug="software-developer").pk)],
        )

    def test_it_records_no_more_than_saving_the_role_by_hand_would(self):
        seed(self._historical_apps(), None)
        seeded = {
            (reference.to_object_id, reference.content_path)
            for reference in self._references()
        }

        senior = GovukRole.objects.get(slug="chief-technology-officer")
        senior.save()

        self.assertEqual(
            {
                (reference.to_object_id, reference.content_path)
                for reference in self._references()
            },
            seeded,
        )
