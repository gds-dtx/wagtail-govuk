"""Measuring the content index's search against questions with known answers.

``questions.yaml`` holds the questions (its header says how). ``evaluate``
runs each through ``search`` in one mode and records where each expected
answer came; ``summarise`` turns that into the numbers
``manage.py evaluate_content_search`` prints:

- **hit@1** and **hit@5**: for a question with ``match: any``, whether an
  expected answer is the first, or among the first five, of what Ask would
  show;
- **recall**: for ``match: all``, the share of the expected answers shown;
- **MRR**: the mean of 1 / the rank of the first expected answer shown (0 when
  none is), over every question with answers;
- **none close**: for an ``out_of_scope`` question, whether nothing at all
  was close enough to be shown;
- **hidden**: expected answers search found but the similarity threshold hid,
  the sign it is set too high.

"Shown" is what Ask shows: the hits ``search.is_close`` passes, in order;
or, re-ranked (``rerank_with``), the hits Claude chose, in its order.
Matches are counted by document, before Ask folds an entry's parts onto its
card; a part of an entry counts for the entry.

Reads only. Nothing here writes to the database.
"""

import statistics
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from govuk.content_index.search import (
    KEYWORD,
    SEMANTIC_OK,
    SearchError,
    is_close,
    search,
)
from govuk.models import IndexedDocument

QUESTIONS_FILE = Path(__file__).with_name("questions.yaml")
CATEGORIES = ("find", "requirement", "broad", "paraphrase", "shorthand", "out_of_scope")
OUT_OF_SCOPE = "out_of_scope"
DEFAULT_LIMIT = 8
QUESTION_MAX_LENGTH = 300


class QuestionsError(ValueError):
    pass


@dataclass
class Question:
    question: str
    category: str
    expect: list[str]
    match: str = "any"
    note: str = ""


def load_questions(path=QUESTIONS_FILE) -> list[Question]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    if not isinstance(data, list):
        raise QuestionsError("The questions file must be a list of questions.")
    questions = []
    for number, item in enumerate(data, start=1):
        where = f"Question {number}"
        if not isinstance(item, dict) or not str(item.get("question") or "").strip():
            raise QuestionsError(f"{where} has no question.")
        category = item.get("category")
        if category not in CATEGORIES:
            raise QuestionsError(f"{where}: category must be one of {', '.join(CATEGORIES)}.")
        expect = item.get("expect") or []
        if not isinstance(expect, list) or not all(isinstance(path, str) and path.strip() for path in expect):
            raise QuestionsError(f"{where}: expect must be a list of paths.")
        if (category == OUT_OF_SCOPE) != (not expect):
            raise QuestionsError(f"{where}: an out_of_scope question expects nothing, and only it.")
        match = item.get("match", "any")
        if match not in ("any", "all"):
            raise QuestionsError(f"{where}: match must be any or all.")
        questions.append(
            Question(
                question=str(item["question"]).strip(),
                category=category,
                expect=[path.strip() for path in expect],
                match=match,
                note=str(item.get("note") or ""),
            )
        )
    return questions


def answers(document: IndexedDocument, expected: str) -> bool:
    """Whether ``document`` is the expected answer, or a part of it."""
    tail = "/" + expected.strip("/") + "/"
    if tail in document.url_path:
        return True
    return bool(document.source_url) and document.source_url.rstrip("/") == expected.rstrip("/")


def unknown_answers(questions) -> list[str]:
    """Expected answers no document in the index is: a typo, or a moved page."""
    documents = list(IndexedDocument.objects.only("url_path", "source_url"))
    expected = {path for question in questions for path in question.expect}
    return sorted(path for path in expected if not any(answers(document, path) for document in documents))


@dataclass
class Shown:
    title: str
    url: str
    matched_words: str | None
    similarity: float | None


@dataclass
class QuestionResult:
    question: str
    category: str
    match: str
    expect: list[str]
    # Each expected answer's rank among what was shown, or None.
    ranks: dict[str, int | None] = field(default_factory=dict)
    # Expected answers found, but not shown: below the similarity threshold.
    hidden: list[str] = field(default_factory=list)
    shown: list[Shown] = field(default_factory=list)
    # The similarity of each expected answer found, shown or not.
    answer_similarity: list[float] = field(default_factory=list)
    best_similarity: float | None = None
    semantic: str = ""
    seconds: float = 0.0
    error: str = ""
    # Re-ranked: how it went ("ok", "refused", "failed"), and the tokens used.
    rerank: str = ""
    tokens: int = 0

    @property
    def first_rank(self) -> int | None:
        found = [rank for rank in self.ranks.values() if rank]
        return min(found) if found else None


def evaluate(
    questions,
    *,
    mode: str,
    limit: int = DEFAULT_LIMIT,
    min_similarity: float,
    rerank_with=None,
    candidates: int = 20,
    floor: float = 0.2,
) -> list[QuestionResult]:
    """``rerank_with`` a Claude (``govuk.ask.claude``) re-ranks each question's
    matches as Ask does: ``candidates`` of them, down to ``floor``. Each call
    costs money, and counts towards the day's budget."""
    from govuk.ask.rerank import OK, rerank

    results = []
    for question in questions:
        result = QuestionResult(question.question, question.category, question.match, question.expect)
        started = time.monotonic()
        try:
            outcome = search(
                question.question,
                mode=mode,
                limit=candidates if rerank_with else limit,
                min_similarity=None if mode == KEYWORD else (floor if rerank_with else min_similarity),
                max_length=QUESTION_MAX_LENGTH,
            )
        except SearchError as exc:
            result.error = str(exc)
            results.append(result)
            continue
        shown = None
        if rerank_with is not None and outcome.hits:
            reranked = rerank(question.question, outcome.hits, rerank_with)
            result.rerank, result.tokens = reranked.status, reranked.tokens
            if reranked.status == OK:
                shown = reranked.hits
        result.seconds = time.monotonic() - started
        result.semantic = outcome.semantic
        if shown is None:
            shown = [hit for hit in outcome.hits if is_close(hit, min_similarity, semantic=outcome.semantic)]
            shown = shown[:limit]
        result.shown = [Shown(hit.document.title, hit.url, hit.matched_words, hit.similarity) for hit in shown]
        similarities = [hit.similarity for hit in outcome.hits if hit.similarity is not None]
        result.best_similarity = max(similarities) if similarities else None
        for expected in question.expect:
            rank = next((n for n, hit in enumerate(shown, start=1) if answers(hit.document, expected)), None)
            result.ranks[expected] = rank
            found = next((hit for hit in outcome.hits if answers(hit.document, expected)), None)
            if found is not None:
                if found.similarity is not None:
                    result.answer_similarity.append(found.similarity)
                if rank is None:
                    result.hidden.append(expected)
        results.append(result)
    return results


@dataclass
class Summary:
    questions: int = 0
    hit_at_1: float | None = None
    hit_at_5: float | None = None
    recall: float | None = None
    mrr: float | None = None
    none_close: float | None = None
    hidden: int = 0
    errors: int = 0


def _mean(values) -> float | None:
    values = list(values)
    return sum(values) / len(values) if values else None


def summarise(results) -> Summary:
    ran = [result for result in results if not result.error]
    in_scope = [result for result in ran if result.category != OUT_OF_SCOPE]
    any_match = [result for result in in_scope if result.match == "any"]
    all_match = [result for result in in_scope if result.match == "all"]
    out = [result for result in ran if result.category == OUT_OF_SCOPE]
    return Summary(
        questions=len(results),
        hit_at_1=_mean(result.first_rank == 1 for result in any_match),
        hit_at_5=_mean(bool(result.first_rank and result.first_rank <= 5) for result in any_match),
        recall=_mean(
            sum(1 for rank in result.ranks.values() if rank) / len(result.ranks) for result in all_match
        ),
        mrr=_mean((1 / result.first_rank if result.first_rank else 0.0) for result in in_scope),
        none_close=_mean(not result.shown for result in out),
        hidden=sum(len(result.hidden) for result in in_scope),
        errors=len(results) - len(ran),
    )


def similarity_guide(results) -> dict:
    """What the similarity threshold should sit between: how alike the expected
    answers are to their questions, and how alike the best match to a question
    the site cannot answer is."""
    answers_found = [value for result in results for value in result.answer_similarity]
    out = [
        result.best_similarity
        for result in results
        if result.category == OUT_OF_SCOPE and result.best_similarity is not None
    ]

    def spread(values):
        if not values:
            return None
        values = sorted(values)
        return {
            "min": values[0],
            "p10": values[max(0, int(len(values) * 0.1) - 1)] if len(values) >= 10 else values[0],
            "median": statistics.median(values),
            "max": values[-1],
        }

    return {"answers": spread(answers_found), "out_of_scope_best": spread(out)}


def latency(results) -> dict | None:
    seconds = sorted(result.seconds for result in results if not result.error)
    if not seconds:
        return None
    p95 = seconds[min(len(seconds) - 1, int(round(len(seconds) * 0.95)) - 1)]
    return {"p50_ms": round(statistics.median(seconds) * 1000), "p95_ms": round(p95 * 1000)}


def semantic_status(results) -> str:
    statuses = {result.semantic for result in results if result.semantic}
    if statuses == {SEMANTIC_OK}:
        return SEMANTIC_OK
    return ", ".join(sorted(statuses)) or "none"


def as_json(mode: str, results) -> dict:
    return {
        "mode": mode,
        "summary": asdict(summarise(results)),
        "by_category": {
            category: asdict(summarise([r for r in results if r.category == category]))
            for category in CATEGORIES
            if any(r.category == category for r in results)
        },
        "similarity": similarity_guide(results),
        "latency": latency(results),
        "results": [asdict(result) for result in results],
    }
