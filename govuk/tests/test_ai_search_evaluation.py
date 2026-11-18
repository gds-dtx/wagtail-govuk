"""The evaluation set and the commands that measure search and AI search against it."""

import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings

from govuk.ai_search import evaluation as ev
from govuk.content_index import indexer
from govuk.content_index.search import HYBRID, KEYWORD
from govuk.tests.test_ai_search import FLAGS, WithFakeModel
from govuk.tests.test_ai_search_claude import ON, fake, publish


class QuestionsFileTests(SimpleTestCase):
    def test_the_example_shows_every_category_and_is_well_formed(self):
        questions = ev.load_questions(ev.EXAMPLE_FILE)
        self.assertEqual({question.category for question in questions}, set(ev.CATEGORIES))

    def test_the_commands_need_a_questions_file(self):
        for command in ("evaluate_content_search", "evaluate_ai_search"):
            with self.subTest(command=command), self.assertRaises(CommandError):
                call_command(command)

    def test_a_badly_formed_question_is_refused(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / "q.yaml"
            for text in (
                "- {question: x, category: nonsense, expect: [a/]}",
                "- {question: x, category: out_of_scope, expect: [a/]}",
                "- {question: x, category: find, expect: []}",
                "- {question: x, category: find, expect: [a/], match: most}",
            ):
                path.write_text(text)
                with self.subTest(text=text), self.assertRaises(ev.QuestionsError):
                    ev.load_questions(path)

    def test_a_part_of_an_entry_counts_for_it(self):
        from govuk.models import IndexedDocument

        point = IndexedDocument(url_path="/content/standards/code/use-cloud/", source_url="")
        self.assertTrue(ev.answers(point, "standards/code/"))
        self.assertTrue(ev.answers(point, "code/use-cloud/"))
        self.assertFalse(ev.answers(IndexedDocument(url_path="/content/standards/code/", source_url=""), "code/use-cloud/"))


@override_settings(FEATURE_FLAGS=FLAGS, CONTENT_INDEX_EMBEDDINGS="fake", CONTENT_INDEX_MIN_SIMILARITY=0.3)
class EvaluationTests(WithFakeModel):
    def _questions(self, folder):
        path = Path(folder) / "q.yaml"
        path.write_text(
            "- {question: 'How do I take money from the public?', category: paraphrase, expect: [govuk-pay/]}\n"
            "- {question: 'assistive technology', category: find, expect: [accessibility/, no-such-page/], match: all}\n"
            "- {question: 'Write me a poem about cats', category: out_of_scope, expect: []}\n"
        )
        return path

    def test_the_numbers(self):
        with TemporaryDirectory() as folder:
            questions = ev.load_questions(self._questions(folder))
        keyword = ev.summarise(ev.evaluate(questions, mode=KEYWORD, min_similarity=0.3))
        hybrid = ev.summarise(ev.evaluate(questions, mode=HYBRID, min_similarity=0.3))
        self.assertEqual((keyword.hit_at_1, keyword.recall, keyword.none_close), (0.0, 0.5, 1.0))
        self.assertEqual((hybrid.hit_at_1, hybrid.recall, hybrid.none_close), (1.0, 0.5, 1.0))
        self.assertEqual(ev.unknown_answers(questions), ["no-such-page/"])

    def test_the_command(self):
        out, err = StringIO(), StringIO()
        with TemporaryDirectory() as folder:
            path = self._questions(folder)
            report = Path(folder) / "out.json"
            call_command(
                "evaluate_content_search", str(path), "--mode", "all", "--show-misses", "--json", str(report),
                stdout=out, stderr=err,
            )
            written = json.loads(report.read_text())
        output = out.getvalue()
        for mode in ("keyword", "semantic", "hybrid"):
            self.assertIn(f"Mode {mode}: 3 questions", output)
        self.assertIn("x [paraphrase] How do I take money from the public?", output)
        self.assertIn("no-such-page/", err.getvalue())
        self.assertEqual([mode["mode"] for mode in written["modes"]], ["keyword", "semantic", "hybrid"])


@override_settings(**ON)
class EvaluationWithClaudeTests(TestCase):
    def setUp(self):
        cache.clear()
        publish("Payments", "<p>Take card payments online.</p>")
        indexer.rebuild()
        self.fake, patcher = fake()
        self.addCleanup(patcher.stop)

    def _questions(self, folder):
        path = Path(folder) / "q.yaml"
        path.write_text(
            "- {question: 'card payments', category: find, expect: [payments/]}\n"
            "- {question: 'Write me a poem', category: out_of_scope, expect: []}\n"
        )
        return path

    def test_rerank_in_the_search_evaluation(self):
        out = StringIO()
        with TemporaryDirectory() as folder:
            call_command("evaluate_content_search", str(self._questions(folder)), "--rerank", stdout=out, stderr=StringIO())
        self.assertIn("re-ranked by fake", out.getvalue())
        self.assertIn("Re-ranking: 1 ok, 0 refused, 0 failed", out.getvalue())

    @override_settings(AI_CLAUDE="")
    def test_rerank_needs_claude(self):
        with TemporaryDirectory() as folder, self.assertRaisesMessage(CommandError, "AI_CLAUDE"):
            call_command("evaluate_content_search", str(self._questions(folder)), "--rerank")

    def test_the_answers_page(self):
        out = StringIO()
        with TemporaryDirectory() as folder:
            report = Path(folder) / "answers.html"
            call_command("evaluate_ai_search", str(self._questions(folder)), "--out", str(report), stdout=out)
            page = report.read_text()
        self.assertIn("<h2>card payments</h2>", page)
        self.assertIn("expected shown: yes", page)
        self.assertIn("expected cited: yes", page)
        self.assertIn("out of scope: yes", page)
        self.assertIn("expected shown: 1/1", out.getvalue())
        self.assertEqual(json.loads(json.dumps(self.fake.calls)), ["rerank", "summary"])
