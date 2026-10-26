"""Migrations 0089-0090: every section page becomes a content page.

A section page was rows of cards and then free text. These tests build one at
0088 with everything that records its type or the shape of its content -- page
tags, a draft, a page log entry and reference index rows -- run the migrations
forward, and check that the page arrives as a content page with the same id
and URL, whose body is its rows as card blocks and then its free text.
"""

import uuid

from django.core.management.color import no_style
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone
from wagtail.models import Page, PageLogEntry, ReferenceIndex, Revision, Site

from govuk.models import ContentPage, ContentPageTag
from govuk.tests.test_pagetypes_migration import _leaf, _step

BEFORE = [("govuk", "0088_body_streamfield")]
CONTENT_PATH_HASH_NAMESPACE = uuid.UUID("bdc70d8b-e7a2-4c2a-bf43-2a3e3fcbbe86")


class SectionPageMigrationTests(TransactionTestCase):
    # The flush after each test empties every table, including the root page
    # and the default site the other tests rely on; this puts them back.
    serialized_rollback = True

    def setUp(self):
        executor = MigrationExecutor(connection)
        executor.migrate(BEFORE)
        self.old_apps = executor.loader.project_state(BEFORE).apps
        try:
            self._build_a_section_page()
        except Exception:
            MigrationExecutor(connection).migrate(_leaf())
            raise
        MigrationExecutor(connection).migrate(_leaf())
        Site.clear_site_root_paths_cache()

    def tearDown(self):
        MigrationExecutor(connection).migrate(_leaf())
        Site.clear_site_root_paths_cache()

    # -- a section page, at 0088 ------------------------------------------------

    def _content_type(self, app_label, model_name):
        ContentType = self.old_apps.get_model("contenttypes", "ContentType")
        content_type, _ = ContentType.objects.get_or_create(
            app_label=app_label, model=model_name
        )
        return content_type

    def _add_page(self, model, *, slug, **fields):
        OldPage = self.old_apps.get_model("wagtailcore", "Page")
        parent = OldPage.objects.get(depth=1)
        parent.numchild += 1
        parent.save(update_fields=["numchild"])
        return model.objects.create(
            title=slug,
            draft_title=slug,
            slug=slug,
            path=parent.path + _step(parent.numchild),
            depth=parent.depth + 1,
            numchild=0,
            url_path=f"/{slug}/",
            content_type=self._content_type("govuk", model._meta.model_name),
            locale=self.old_apps.get_model("wagtailcore", "Locale").objects.first(),
            live=True,
            **fields,
        )

    def _reference(self, page, *, to_model, to_id, model_path, content_path):
        self.old_apps.get_model("wagtailcore", "ReferenceIndex").objects.create(
            content_type=self._content_type("govuk", "sectionpage"),
            base_content_type=self._content_type("wagtailcore", "page"),
            object_id=str(page.pk),
            to_content_type=to_model,
            to_object_id=str(to_id),
            model_path=model_path,
            content_path=content_path,
            content_path_hash=uuid.uuid5(CONTENT_PATH_HASH_NAMESPACE, content_path),
        )

    def _build_a_section_page(self):
        apps = self.old_apps
        OldContentPage = apps.get_model("govuk", "ContentPage")
        OldSectionPage = apps.get_model("govuk", "SectionPage")
        OldContentPageTag = apps.get_model("govuk", "ContentPageTag")
        OldSectionPageTag = apps.get_model("govuk", "SectionPageTag")
        OldTag = apps.get_model("govuk", "GovukTag")
        page_ct = self._content_type("wagtailcore", "page")

        self.linked = self._add_page(OldContentPage, slug="linked", body=[])
        alpha = OldTag.objects.create(slug="alpha", name="Alpha")
        self.beta = OldTag.objects.create(slug="beta", name="Beta")

        self.row_id = str(uuid.uuid4())
        self.card_id = str(uuid.uuid4())
        self.free_text = (
            f'<p>See <a linktype="page" id="{self.linked.pk}">the linked page</a>.</p>'
        )
        self.section = self._add_page(
            OldSectionPage,
            slug="get-involved",
            hero_title="Get involved",
            enable_tag_filter=True,
            free_text=self.free_text,
            rows=[
                {
                    "type": "row",
                    "id": self.row_id,
                    "value": {
                        "heading": "Services",
                        "cards": [
                            {
                                "type": "item",
                                "id": self.card_id,
                                "value": {
                                    "title": "Alpha card",
                                    "image": None,
                                    "image_fit": "cover",
                                    "text": "<p>Summary</p>",
                                    "link": {
                                        "title": "More",
                                        "page": self.linked.pk,
                                        "external_url": "",
                                    },
                                    "tags": [],
                                },
                            }
                        ],
                    },
                }
            ],
        )
        self.section_tag = OldSectionPageTag.objects.create(
            content_object_id=self.section.pk, tag_id=self.beta.pk
        )
        # The collision the draft has to be kept from: the two through tables
        # number their rows separately, so another page's tag row can hold the
        # id the section page's has in its own table.
        self.bystander = self._add_page(OldContentPage, slug="bystander", body=[])
        self.bystander_tag = OldContentPageTag.objects.create(
            pk=self.section_tag.pk, content_object_id=self.bystander.pk, tag_id=alpha.pk
        )
        # An explicit id leaves Postgres's sequence where it was, so the
        # migration's own new tag row would be handed the same one.
        with connection.cursor() as cursor:
            for statement in connection.ops.sequence_reset_sql(
                no_style(), [OldContentPageTag]
            ):
                cursor.execute(statement)

        sectionpage_ct = self._content_type("govuk", "sectionpage")
        self.draft = apps.get_model("wagtailcore", "Revision").objects.create(
            content_type=sectionpage_ct,
            base_content_type=page_ct,
            object_id=str(self.section.pk),
            object_str="get-involved",
            content={
                "pk": self.section.pk,
                "title": "Get involved, drafted",
                "slug": "get-involved",
                "content_type": sectionpage_ct.pk,
                "rows": "[]",
                "free_text": "<p>Draft words.</p>",
                "enable_tag_filter": True,
                "tagged_items": [
                    {
                        "pk": self.section_tag.pk,
                        "tag": self.beta.pk,
                        "content_object": self.section.pk,
                    }
                ],
            },
            created_at=timezone.now(),
        )
        apps.get_model("wagtailcore", "PageLogEntry").objects.create(
            content_type=sectionpage_ct,
            page_id=self.section.pk,
            label="get-involved",
            action="wagtail.publish",
            timestamp=timezone.now(),
        )

        # What Wagtail indexes the page's references under: the free text at
        # "free_text.", a card's page link under its row and card, and the tag
        # row by its id.
        self._reference(
            self.section,
            to_model=page_ct,
            to_id=self.linked.pk,
            model_path="free_text.",
            content_path="free_text.",
        )
        self._reference(
            self.section,
            to_model=page_ct,
            to_id=self.linked.pk,
            model_path="rows.row.cards.item.link.page",
            content_path=f"rows.{self.row_id}.cards.{self.card_id}.link.page",
        )
        self._reference(
            self.section,
            to_model=self._content_type("govuk", "govuktag"),
            to_id=self.beta.pk,
            model_path="tagged_items.item.tag",
            content_path=f"tagged_items.{self.section_tag.pk}.tag",
        )

    # -- what comes out ---------------------------------------------------------

    def test_the_page_is_a_content_page_where_it_was(self):
        page = Page.objects.get(pk=self.section.pk)

        self.assertIs(page.specific_class, ContentPage)
        self.assertEqual(page.url_path, "/get-involved/")
        self.assertEqual(page.specific.hero_title, "Get involved")
        self.assertTrue(page.specific.enable_tag_filter)

    def test_its_rows_then_its_free_text_make_the_body(self):
        page = ContentPage.objects.get(pk=self.section.pk)

        self.assertEqual([block.block_type for block in page.body], ["cards", "text"])
        cards = page.body[0]
        self.assertEqual(cards.id, self.row_id)
        self.assertEqual(cards.value["heading"], "Services")
        self.assertEqual(cards.value["cards"][0]["title"], "Alpha card")
        self.assertEqual(page.body[1].value.source, self.free_text)

    def test_its_tags_move_with_it(self):
        page = ContentPage.objects.get(pk=self.section.pk)

        self.assertEqual(list(page.tags.values_list("slug", flat=True)), ["beta"])

    def test_its_history_stays_with_it(self):
        self.assertEqual(
            PageLogEntry.objects.filter(page_id=self.section.pk).get().content_type.model,
            "contentpage",
        )

    def test_its_draft_opens_as_a_content_page(self):
        draft = Revision.objects.get(pk=self.draft.pk)
        page = draft.as_object()

        self.assertIsInstance(page, ContentPage)
        self.assertEqual(page.title, "Get involved, drafted")
        self.assertEqual([block.block_type for block in page.body], ["text"])
        self.assertEqual(page.body[0].value.source, "<p>Draft words.</p>")

    def test_publishing_the_draft_leaves_another_pages_tag_alone(self):
        """The draft named its tag row by the old table's id, which in the new
        table is the bystander's row; publishing it unchanged would take it."""
        Revision.objects.get(pk=self.draft.pk).publish()

        bystander_tag = ContentPageTag.objects.get(pk=self.bystander_tag.pk)
        self.assertEqual(bystander_tag.content_object_id, self.bystander.pk)
        self.assertEqual(
            list(
                ContentPage.objects.get(pk=self.section.pk).tags.values_list(
                    "slug", flat=True
                )
            ),
            ["beta"],
        )

    def test_reindexing_the_page_finds_nothing_to_change(self):
        """The rewritten rows are the ones Wagtail writes for the page as it is."""
        page = ContentPage.objects.get(pk=self.section.pk)
        rows = ReferenceIndex.objects.filter(
            base_content_type__model="page", object_id=str(page.pk)
        )
        before = set(rows.values_list("pk", "model_path", "content_path"))

        ReferenceIndex.create_or_update_for_object(page)

        self.assertEqual(set(rows.values_list("pk", "model_path", "content_path")), before)
        self.assertEqual(
            sorted(model_path for _, model_path, _ in before),
            ["body.cards.cards.item.link.page", "body.text", "tagged_items.item.tag"],
        )
