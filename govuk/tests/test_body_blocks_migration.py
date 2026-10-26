"""Migrations 0087-0088: each page's rich text body becomes its first block.

The body used to be a rich text field with a stream of further blocks after
it. These tests build pages, a draft and reference index rows in that shape at
0086, run the migrations forward, and check that the text arrives at the head
of the stream everywhere the old shape was stored -- the live row, the draft an
editor would open next, and the reference index the admin's usage listings
read -- and that the framework skills page, whose rich text body stays one, is
left alone.
"""

import json
import uuid

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone
from wagtail.models import ReferenceIndex, Revision, Site

from govuk.models import ContentPage, FrameworkSkillsPage
from govuk.tests.test_pagetypes_migration import _leaf, _step

BEFORE = [("govuk", "0086_body_blocks_cards_and_banners")]


class BodyBlocksMigrationTests(TransactionTestCase):
    # The flush after each test empties every table, including the root page
    # and the default site the other tests rely on; this puts them back.
    serialized_rollback = True

    def setUp(self):
        executor = MigrationExecutor(connection)
        executor.migrate(BEFORE)
        self.old_apps = executor.loader.project_state(BEFORE).apps
        try:
            self._build_pages_in_the_old_shape()
        except Exception:
            MigrationExecutor(connection).migrate(_leaf())
            raise
        MigrationExecutor(connection).migrate(_leaf())
        Site.clear_site_root_paths_cache()

    def tearDown(self):
        MigrationExecutor(connection).migrate(_leaf())
        Site.clear_site_root_paths_cache()

    # -- the old shape, at 0086 -------------------------------------------------

    def _content_type(self, model):
        ContentType = self.old_apps.get_model("contenttypes", "ContentType")
        content_type, _ = ContentType.objects.get_or_create(
            app_label=model._meta.app_label, model=model._meta.model_name
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
            content_type=self._content_type(model),
            locale=self.old_apps.get_model("wagtailcore", "Locale").objects.first(),
            live=True,
            **fields,
        )

    def _reference(self, page, *, model, to_page, path):
        self.old_apps.get_model("wagtailcore", "ReferenceIndex").objects.create(
            content_type=self._content_type(model),
            base_content_type=self._content_type(self.old_apps.get_model("wagtailcore", "Page")),
            object_id=str(page.pk),
            to_content_type=self._content_type(self.old_apps.get_model("wagtailcore", "Page")),
            to_object_id=str(to_page.pk),
            model_path=path[0],
            content_path=path[1],
            content_path_hash=uuid.uuid5(
                uuid.UUID("bdc70d8b-e7a2-4c2a-bf43-2a3e3fcbbe86"), path[1]
            ),
        )

    def _build_pages_in_the_old_shape(self):
        apps = self.old_apps
        OldContentPage = apps.get_model("govuk", "ContentPage")
        OldSkillsPage = apps.get_model("govuk", "FrameworkSkillsPage")
        OldRevision = apps.get_model("wagtailcore", "Revision")

        self.linked = self._add_page(OldContentPage, slug="linked")
        self.link_html = (
            f'<p>See <a linktype="page" id="{self.linked.pk}">the linked page</a>.</p>'
        )
        self.table_block_id = str(uuid.uuid4())
        self.text_block_id = str(uuid.uuid4())
        self.both = self._add_page(
            OldContentPage,
            slug="both",
            body=self.link_html,
            body_blocks=[
                {
                    "type": "text",
                    "value": f'<p><a linktype="page" id="{self.linked.pk}">Again</a></p>',
                    "id": self.text_block_id,
                },
                {
                    "type": "table",
                    "value": {
                        "data": [["a", "b"]],
                        "first_row_is_table_header": False,
                        "first_col_is_header": False,
                        "table_caption": "",
                    },
                    "id": self.table_block_id,
                },
            ],
        )
        self.blocks_only = self._add_page(
            OldContentPage,
            slug="blocks-only",
            body="",
            body_blocks=[{"type": "text", "value": "<p>Only a block.</p>", "id": str(uuid.uuid4())}],
        )
        self.skills_body = "<p>The skills intro stays rich text.</p>"
        self.skills = self._add_page(OldSkillsPage, slug="skills", body=self.skills_body)

        page_ct = self._content_type(apps.get_model("wagtailcore", "Page"))
        self.draft = OldRevision.objects.create(
            content_type=self._content_type(OldContentPage),
            base_content_type=page_ct,
            object_id=str(self.both.pk),
            object_str="both",
            content={
                "pk": self.both.pk,
                "title": "both",
                "body": "<p>Draft words.</p>",
                "body_blocks": json.dumps([]),
            },
            created_at=timezone.now(),
        )
        self.skills_draft = OldRevision.objects.create(
            content_type=self._content_type(OldSkillsPage),
            base_content_type=page_ct,
            object_id=str(self.skills.pk),
            object_str="skills",
            content={"pk": self.skills.pk, "title": "skills", "body": self.skills_body},
            created_at=timezone.now(),
        )

        # What Wagtail indexes the two fields' links under: the rich text body
        # at "body." and a stream block at "body_blocks.<block id>".
        self._reference(self.both, model=OldContentPage, to_page=self.linked, path=("body.", "body."))
        self._reference(
            self.both,
            model=OldContentPage,
            to_page=self.linked,
            path=("body_blocks.text", f"body_blocks.{self.text_block_id}"),
        )

    # -- what comes out ---------------------------------------------------------

    def test_the_rich_text_body_becomes_the_first_block(self):
        page = ContentPage.objects.get(pk=self.both.pk)

        self.assertEqual(
            [block.block_type for block in page.body], ["text", "text", "table"]
        )
        self.assertEqual(page.body[0].value.source, self.link_html)
        # The blocks already there keep their ids, which the reference index
        # and any open comment threads are keyed on.
        self.assertEqual(page.body[1].id, self.text_block_id)
        self.assertEqual(page.body[2].id, self.table_block_id)

    def test_a_blank_body_adds_no_block(self):
        page = ContentPage.objects.get(pk=self.blocks_only.pk)

        self.assertEqual([block.block_type for block in page.body], ["text"])
        self.assertEqual(page.body[0].value.source, "<p>Only a block.</p>")

    def test_a_draft_opens_with_its_words_in_the_first_block(self):
        """Left in the old shape, the draft would open empty and publish blank."""
        draft = Revision.objects.get(pk=self.draft.pk)

        self.assertNotIn("body_blocks", draft.content)
        page = draft.as_object()
        self.assertEqual([block.block_type for block in page.body], ["text"])
        self.assertEqual(page.body[0].value.source, "<p>Draft words.</p>")

    def test_the_skills_page_keeps_its_rich_text_body(self):
        self.assertEqual(
            FrameworkSkillsPage.objects.get(pk=self.skills.pk).body, self.skills_body
        )
        self.assertEqual(
            Revision.objects.get(pk=self.skills_draft.pk).content["body"],
            self.skills_body,
        )

    def test_the_reference_index_follows_the_text_to_its_block(self):
        page = ContentPage.objects.get(pk=self.both.pk)
        rows = ReferenceIndex.objects.filter(object_id=str(page.pk))

        self.assertEqual(
            sorted(rows.values_list("model_path", "content_path")),
            sorted(
                [
                    ("body.text", f"body.{page.body[0].id}"),
                    ("body.text", f"body.{self.text_block_id}"),
                ]
            ),
        )

    def test_reindexing_the_page_finds_nothing_to_change(self):
        """The rows are the ones Wagtail would write for the page as it now is."""
        page = ContentPage.objects.get(pk=self.both.pk)
        before = set(
            ReferenceIndex.objects.filter(object_id=str(page.pk)).values_list(
                "pk", "content_path_hash"
            )
        )

        ReferenceIndex.create_or_update_for_object(page)

        after = set(
            ReferenceIndex.objects.filter(object_id=str(page.pk)).values_list(
                "pk", "content_path_hash"
            )
        )
        self.assertEqual(after, before)
        self.assertEqual(len(after), 2)
