"""Write what Ask shows for each evaluation question, summary and all, for a person to read.

    manage.py evaluate_ask_answers [file] [--out ask-answers.html] [--only paraphrase]

Runs each question as the /ask/ page does with both uses of Claude on
(re-ranking and the summary), whatever the admin switches say, and writes
one HTML page: the question, the cards, the summary, and three checks:

- **expected shown**: at least one expected answer is among the cards;
- **expected cited**: the summary links to at least one of them, or to an
  expected page within one;
- **out of scope**: a question the site cannot answer shows no cards.

Two calls to Claude a question: it costs money, and counts towards the day's
budget. Summaries cached in the last day are reused.
"""

import html
import re
from collections import Counter

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from govuk.ask import rerank as reranking, summary
from govuk.ask.cards import MAX_CARDS, cards_for
from govuk.ask.claude import get_claude
from govuk.content_index import FLAGS_OFF, content_index_enabled, evaluation as ev
from govuk.content_index.search import HYBRID, SearchError, is_close, search
from govuk.models import IndexedDocument


def _expected_card(card: dict, question) -> bool:
    """Whether a card is for an expected answer, by the documents it was found by."""
    if not question.expect:
        return False
    documents = IndexedDocument.objects.filter(pk__in=card.get("source_documents") or [])
    return any(ev.answers(document, path) for document in documents for path in question.expect)


class Command(BaseCommand):
    help = "Write Ask's cards and AI summary for each evaluation question to an HTML page."

    def add_arguments(self, parser):
        parser.add_argument("file", nargs="?", default=str(ev.QUESTIONS_FILE), help="The questions (YAML).")
        parser.add_argument("--out", default="ask-answers.html", help="The page to write.")
        parser.add_argument("--only", choices=ev.CATEGORIES, help="Only questions of this category.")

    def handle(self, *args, **options):
        if not content_index_enabled():
            raise CommandError(FLAGS_OFF)
        claude = get_claude()
        if claude is None:
            raise CommandError("Claude is not set up (ASK_CLAUDE).")
        try:
            questions = ev.load_questions(options["file"])
        except (OSError, ev.QuestionsError) as exc:
            raise CommandError(str(exc)) from exc
        if options["only"]:
            questions = [question for question in questions if question.category == options["only"]]

        rows, totals = [], Counter()
        for question in questions:
            row = self._run(question, claude)
            rows.append(row)
            for check, passed in row["checks"].items():
                totals[check, passed] += 1
            self.stdout.write(
                f"{'ok' if all(row['checks'].values()) else 'x '} [{question.category}] {question.question}"
            )
        with open(options["out"], "w", encoding="utf-8") as out:
            out.write(self._page(rows, claude.model))
        summary_line = ", ".join(
            f"{name}: {totals[name, True]}/{totals[name, True] + totals[name, False]}"
            for name in ("expected shown", "expected cited", "out of scope")
            if totals[name, True] + totals[name, False]
        )
        self.stdout.write(f"{summary_line}. Written to {options['out']}.")

    def _run(self, question, claude) -> dict:
        row = {"question": question, "cards": [], "summary": "", "ranking": "", "error": ""}
        try:
            outcome = search(
                question.question,
                mode=HYBRID,
                limit=settings.ASK_RERANK_CANDIDATES,
                min_similarity=settings.ASK_RERANK_FLOOR,
                max_length=ev.QUESTION_MAX_LENGTH,
            )
        except SearchError as exc:
            row["error"] = str(exc)
            row["checks"] = {}
            return row
        hits = None
        if outcome.hits:
            reranked = reranking.rerank(question.question, outcome.hits, claude)
            row["ranking"] = reranked.status
            if reranked.status == reranking.OK:
                hits = reranked.hits
        if hits is None:
            threshold = settings.CONTENT_INDEX_MIN_SIMILARITY
            hits = [hit for hit in outcome.hits if is_close(hit, threshold, semantic=outcome.semantic)]
        cards = cards_for(hits, None, limit=MAX_CARDS)
        row["cards"] = cards
        if cards:
            result = summary.summarise(summary.make_ticket(question.question, cards))
            row["summary"] = result.html if result.status == summary.OK else f"({result.status})"
        for card in cards:
            card["expected"] = _expected_card(card, question)
        if question.category == ev.OUT_OF_SCOPE:
            row["checks"] = {"out of scope": not cards}
        else:
            expected = [card for card in cards if card["expected"]]
            # A link to an expected card, or straight to an expected page within one.
            links = {html.unescape(href) for href in re.findall(r'href="([^"]*)"', row["summary"])}
            cited = any(card["url"] in links for card in expected) or any(
                "/" + path.strip("/") + "/" in link for link in links for path in question.expect
            )
            row["checks"] = {"expected shown": bool(expected), "expected cited": cited}
        return row

    def _page(self, rows, model: str) -> str:
        parts = [
            "<!doctype html><html lang='en'><head><meta charset='utf-8'><title>Ask answers</title>",
            "<style>body{font:16px/1.5 arial,sans-serif;max-width:960px;margin:2em auto;padding:0 1em}"
            "section{border-top:2px solid #b1b4b6;padding:1em 0}.pass{color:#00703c}.fail{color:#d4351c}"
            ".summary{background:#f3f2f1;border-left:5px solid #1d70b8;padding:.5em 1em}small{color:#505a5f}</style>",
            "</head><body>",
            f"<h1>Ask answers</h1><p><small>{html.escape(model)}, "
            f"{timezone.now():%d %B %Y %H:%M} UTC. Summaries are AI-written: read them against the cards.</small></p>",
        ]
        for row in rows:
            question = row["question"]
            parts.append("<section>")
            parts.append(f"<h2>{html.escape(question.question)}</h2>")
            parts.append(f"<p><small>{question.category}; re-ranking: {html.escape(row['ranking'] or 'none')}</small></p>")
            if question.note:
                parts.append(f"<p><small>Note: {html.escape(question.note)}</small></p>")
            for check, passed in row["checks"].items():
                parts.append(f"<p class='{'pass' if passed else 'fail'}'>{check}: {'yes' if passed else 'no'}</p>")
            if row["error"]:
                parts.append(f"<p class='fail'>{html.escape(row['error'])}</p>")
            if row["summary"]:
                # Built by govuk.ask.summary.render from a fixed set of tags.
                parts.append(f"<div class='summary'>{row['summary']}</div>")
            if row["cards"]:
                parts.append("<ol>")
                for card in row["cards"]:
                    mark = " (expected)" if card["expected"] else ""
                    parts.append(
                        f"<li><a href='{html.escape(card['url'], quote=True)}'>{html.escape(card['title'])}</a>"
                        f" <small>{html.escape(card.get('type') or '')}{mark}</small></li>"
                    )
                parts.append("</ol>")
            else:
                parts.append("<p>No cards.</p>")
            if question.expect:
                parts.append(f"<p><small>Expected: {html.escape(', '.join(question.expect))}</small></p>")
            parts.append("</section>")
        parts.append("</body></html>")
        return "\n".join(parts)
