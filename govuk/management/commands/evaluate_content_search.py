"""Measure the content index's search against questions with known answers.

    manage.py evaluate_content_search                      # hybrid, the bundled questions
    manage.py evaluate_content_search --mode all           # keyword, semantic and hybrid
    manage.py evaluate_content_search my-questions.yaml --show-misses --json out.json

Reads only: run it locally, or on a deployed instance as a one-off ECS task,
where its output goes to the task's log. The questions are test questions,
so they are printed; nothing a reader asked is ever read here. See
``govuk.content_index.evaluation`` for what each number means.
"""

import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from govuk.content_index import FLAGS_OFF, content_index_enabled, evaluation as ev
from govuk.content_index.search import HYBRID, KEYWORD, MODES, SEMANTIC


def _pct(value) -> str:
    return "  -  " if value is None else f"{value * 100:4.0f}%"


def _num(value) -> str:
    return "  -  " if value is None else f"{value:5.2f}"


class Command(BaseCommand):
    help = "Measure how well search finds the expected answers to a set of questions."

    def add_arguments(self, parser):
        parser.add_argument("file", nargs="?", default=str(ev.QUESTIONS_FILE), help="The questions (YAML).")
        parser.add_argument("--mode", choices=(*MODES, "all"), default=HYBRID)
        parser.add_argument("--limit", type=int, default=ev.DEFAULT_LIMIT, help="Results read per question.")
        parser.add_argument(
            "--min-similarity",
            type=float,
            default=None,
            help="The threshold to try (default CONTENT_INDEX_MIN_SIMILARITY).",
        )
        parser.add_argument("--show-misses", action="store_true", help="List each question that missed.")
        parser.add_argument(
            "--rerank",
            action="store_true",
            help="Re-rank each question's matches with Claude, as Ask does (hybrid only). Costs money.",
        )
        parser.add_argument("--json", help="Also write every result to this file.")

    def handle(self, *args, **options):
        if not content_index_enabled():
            raise CommandError(FLAGS_OFF)
        try:
            questions = ev.load_questions(options["file"])
        except (OSError, ev.QuestionsError) as exc:
            raise CommandError(str(exc)) from exc
        threshold = (
            settings.CONTENT_INDEX_MIN_SIMILARITY if options["min_similarity"] is None else options["min_similarity"]
        )
        unknown = ev.unknown_answers(questions)
        if unknown:
            self.stderr.write("Expected answers no indexed document is (typo, or not imported here):")
            for path in unknown:
                self.stderr.write(f"  {path}")

        modes = (KEYWORD, SEMANTIC, HYBRID) if options["mode"] == "all" else (options["mode"],)
        claude = None
        if options["rerank"]:
            from govuk.ask.claude import get_claude

            claude = get_claude()
            if claude is None:
                raise CommandError("Claude is not set up (ASK_CLAUDE).")
            modes = (HYBRID,)
        report = {"file": options["file"], "limit": options["limit"], "min_similarity": threshold, "modes": []}
        for mode in modes:
            results = ev.evaluate(
                questions,
                mode=mode,
                limit=options["limit"],
                min_similarity=threshold,
                rerank_with=claude,
                candidates=settings.ASK_RERANK_CANDIDATES,
                floor=settings.ASK_RERANK_FLOOR,
            )
            label = f"{mode}, re-ranked by {claude.model}" if claude else mode
            self._print_mode(label, results, options, threshold)
            report["modes"].append(ev.as_json(label, results))
        if options["json"]:
            with open(options["json"], "w", encoding="utf-8") as out:
                json.dump(report, out, indent=2, default=str)
            self.stdout.write(f"Written to {options['json']}.")

    def _print_mode(self, mode, results, options, threshold):
        write = self.stdout.write
        write("")
        write(
            f"Mode {mode}: {len(results)} questions, top {options['limit']}, "
            f"similarity threshold {threshold:.2f}, meaning: {ev.semantic_status(results)}"
        )
        write(f"  {'category':<13} {'n':>3}  {'hit@1':>5}  {'hit@5':>5}  {'recall':>6}  {'MRR':>5}  {'none close':>10}")
        rows = [(c, [r for r in results if r.category == c]) for c in ev.CATEGORIES]
        rows.append(("all", results))
        for name, subset in rows:
            if not subset:
                continue
            s = ev.summarise(subset)
            write(
                f"  {name:<13} {s.questions:>3}  {_pct(s.hit_at_1)}  {_pct(s.hit_at_5)}  {_pct(s.recall):>6}"
                f"  {_num(s.mrr)}  {_pct(s.none_close):>10}"
            )
        overall = ev.summarise(results)
        if overall.hidden:
            write(f"  {overall.hidden} expected answers found but hidden by the similarity threshold.")
        if overall.errors:
            write(f"  {overall.errors} questions could not be searched.")
        guide = ev.similarity_guide(results)
        for label, key in (("expected answers", "answers"), ("best out-of-scope match", "out_of_scope_best")):
            spread = guide[key]
            if spread:
                write(
                    f"  Similarity of {label}: min {spread['min']:.2f}, p10 {spread['p10']:.2f}, "
                    f"median {spread['median']:.2f}, max {spread['max']:.2f}"
                )
        timing = ev.latency(results)
        if timing:
            write(f"  Time per search: median {timing['p50_ms']} ms, p95 {timing['p95_ms']} ms")
        reranked = [result for result in results if result.rerank]
        if reranked:
            statuses = ", ".join(
                f"{sum(1 for r in reranked if r.rerank == status)} {status}" for status in ("ok", "refused", "failed")
            )
            tokens = sum(result.tokens for result in reranked)
            write(f"  Re-ranking: {statuses}; {tokens:,} tokens ({tokens // max(1, len(reranked)):,} a question)")
        if options["show_misses"]:
            self._print_misses(results)

    def _print_misses(self, results):
        write = self.stdout.write
        for result in results:
            if result.error:
                write(f"  ! [{result.category}] {result.question}: {result.error}")
                continue
            if result.category == ev.OUT_OF_SCOPE:
                missed = bool(result.shown)
            elif result.match == "all":
                missed = not all(result.ranks.values())
            else:
                missed = not (result.first_rank and result.first_rank <= 5)
            if not missed:
                continue
            write(f"  x [{result.category}] {result.question}")
            for expected, rank in result.ranks.items():
                state = f"rank {rank}" if rank else ("hidden" if expected in result.hidden else "not found")
                write(f"      expected {expected}: {state}")
            for n, shown in enumerate(result.shown[:5], start=1):
                similarity = "" if shown.similarity is None else f" {shown.similarity:.2f}"
                write(f"      {n}. {shown.title} ({shown.matched_words or 'meaning'}{similarity})")
