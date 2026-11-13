# Ask

Ask lets someone type what they want to do in their own words ("I'm trying to
find cloud policy", "What standards do I have to meet?") and see the site's
closest matches, as the cards the content catalogue's section pages use.

It is built in steps:

1. **An evaluation set** (done): questions with known answers, and a command
   that measures search against them.
2. **Search by meaning, and the cards-only page** (done): the content index's
   sections embedded, a hybrid search, the question box block and `/ask/`.
3. **Claude** (done): it re-ranks the matches, and writes a short summary
   above the cards from them alone.

Claude is optional. Without it (`ASK_CLAUDE` unset, or both switches off in
**Settings > Ask**), every word on the page is the site's own.

It is behind the `ASK` feature flag (`FEATURE_ASK`), which is on in
`govuk/settings/development.py`. It reads the content index
([docs/mcp-server.md](mcp-server.md)), which is kept while either `ASK` or
`MCP` is on. With `ASK` off, the question box renders nothing and `/ask/`
returns 404.

## Trying it locally

1. Build the index, with embeddings. Without AWS, the fake model embeds by
   words alone, which is enough to try the page:

   ```bash
   export CONTENT_INDEX_EMBEDDINGS=fake      # or bedrock, with AWS credentials
   .venv/bin/python manage.py rebuild_content_index
   ```

   With `bedrock`, boto3 needs credentials for an account with Titan Text
   Embeddings V2, for example `AWS_PROFILE=...`, and
   `CONTENT_INDEX_EMBEDDING_REGION` if it is not `eu-west-2`.

2. Add a **Question box** block to a page's body (often the home page), and
   publish it.

3. Ask it something, or go straight to `http://localhost:8000/ask/`.

To try Claude as well, set `ASK_CLAUDE` before `runserver`, and turn on
re-ranking and the summary in **Settings > Ask**:

```bash
export ASK_CLAUDE=fake      # answers without a model: search's order, a stock summary
export ASK_CLAUDE=bedrock   # Claude in Amazon Bedrock, with AWS credentials boto3 can find
```

Locally, Django serves the summary itself at `/ask/summary/`, so no uvicorn
is needed.

Vectors from different models are kept apart, so switching between `fake` and
`bedrock` leaves each model's vectors in place.

## The page

### The question box

A body block (`QuestionBoxBlock` in `govuk/models/blocks.py`) that editors can
place on any content page, and edit like any other block:

| Field | Default |
| --- | --- |
| Label | What are you wanting to do with technology? |
| Hint | For example, ‘What standards do I have to meet?’ |
| Button text | Search |

Under the hint it always says "Do not include personal or sensitive
information.", which editors cannot remove.

### /ask/

- **POST, not GET.** The question goes in the request body, so it is not kept
  in the address bar, the browser's history, or any access log on the way:
  CloudFront's, the load balancer's or the app's. The cost is that a refresh
  asks to send it again.
- **No CSRF token.** The post only searches and changes nothing, and a token
  would have to be in the page the box is on, which CloudFront may cache.
- **What it shows:** up to six cards, best first. A card is for a catalogue
  entry, or for any other page, role or skill, with its type as a badge.
  - **A match within an entry** (a point of a code, a step, a page of a
    guide) is shown on its entry's card, as "Most relevant part".
  - **An external entry's card** goes to where it is published, as on the
    section pages.
- **Which matches are shown** (`search.is_close`): those with every word
  asked, or alike enough in meaning (`CONTENT_INDEX_MIN_SIMILARITY`). If
  meaning is not available (embeddings off, or Bedrock unreachable), a match
  with some of the words will do.
- **Nothing close:** it says so, and offers the catalogue's list of every
  entry.
- **Errors:** an empty question, or one over 300 characters, is shown in the
  GOV.UK error pattern.
- **Rate limit:** `ASK_RATE_LIMIT_PER_MINUTE` questions a minute (20 by
  default) per client address, from `CLIENT_IP_HEADER` (CloudFront's
  `true-client-ip`) where set. Over it, a 429 with `Retry-After`.
- **Not cached or indexed:** every response is sent with
  `Cache-Control: no-store` and `X-Robots-Tag: noindex`.

## Claude

Two calls, both to Claude Opus 5.5 at low effort on Amazon Bedrock
(`govuk/ask/claude.py`). Both ask for JSON to a schema (structured outputs),
have a timeout and no retries, and are logged without the question or the
answer.

- **The endpoint:** `bedrock-runtime` (`ASK_CLAUDE=bedrock`, the SDK's
  `AnthropicBedrock` client).
- **The model:** the EU inference profile, `eu.anthropic.claude-opus-5-5`.
  Bedrock processes each request in one of the EU regions that profile lists,
  never outside the EU.
- **The other endpoint:** `bedrock-mantle` (`ASK_CLAUDE=bedrock-mantle`, with
  `anthropic.claude-opus-5-5`) also works, wherever an account and region
  offer the model there. In the dev account's eu-west-2 they do not: it
  answers that the model does not exist.

### Re-ranking

- **When:** while the `/ask/` page is drawn, with **Re-rank matches with AI**
  on.
- **What Claude reads:** the question, and up to `ASK_RERANK_CANDIDATES` (20)
  matches from hybrid search down to `ASK_RERANK_FLOOR` (0.2), lower than the
  page's own threshold, since Claude judges what is close. Each match is
  marked `d1`, `d2`… with its title, type, description, best section and a
  snippet.
- **What it answers:** whether the question is one the site is for, and the
  markers of up to six matches that help, best first, each with a reason.
  Markers it was not given are dropped. Out of scope means nothing close.
- **If it fails** (after `ASK_RERANK_TIMEOUT_SECONDS`, 6), refuses, or the
  budget is spent, the page ranks by search alone, as without Claude.

### The summary

- **When:** after the page has loaded, with **Show an AI summary** on. The
  page carries a ticket, signed with the site's secret key, holding the
  question and the cards. `main.js` posts it to `/ask/summary/`, served by
  the uvicorn (MCP) service deployed, and by Django locally. The summary
  never holds up the cards.
- **Why a ticket:** the endpoint only summarises what search found for a
  question asked on the site, and reads the pages' text from the content
  index, never from the request. A ticket lasts 10 minutes.
- **What Claude reads:** the question, and for each card its title, type,
  description and the text of up to three sections it was found by.
- **What it answers:** about 100 words in plain English, naming each page by
  its marker. Each marker becomes a link with the card's title; an unknown
  one is dropped. The text is rendered from a little Markdown (paragraphs,
  lists, bold) and cleaned with nh3 before the links go in.
- **On the page:** under "Summary", shown whole in a polite live region so it
  is read out once, labelled "Written by AI from the pages below. Check them
  before you act on it." Without JavaScript, a note says a summary needs it.
  If it fails, refuses or the budget is spent, the region goes away.
- **Cached** for a day (`ASK_SUMMARY_CACHE_SECONDS`) under a hash of the
  question, the cards, the model and `PROMPT_VERSION`.

### Prompts

In `govuk/ask/prompts.py`. Bump `PROMPT_VERSION` with any change. The question
and the pages are wrapped in tags with their text escaped, and the
instructions say both are data: a page copied from another site could hold
text written to look like instructions.

### Switches, budget and limits

- **Settings > Ask** (`AskSettings`, only while `FEATURE_ASK` is on):
  **Re-rank matches with AI** and **Show an AI summary**, both off by
  default. Turning either off takes effect at once.
- **Daily token budget:** `ASK_DAILY_TOKEN_BUDGET` (2 million) tokens, in
  and out, across every task: `AskUsage` keeps one row a day (UTC) of calls,
  tokens, refusals and failures. Past it, Ask uses no model until midnight,
  and logs one warning.
- **Rate limits:** `ASK_RATE_LIMIT_PER_MINUTE` questions (20) and
  `ASK_SUMMARY_RATE_LIMIT_PER_MINUTE` summaries (10) a client address a
  minute.
- **Nothing kept:** questions and summaries are never stored or logged. The
  only copy of a summary is the day's cache entry.

### Cost

At Anthropic's own prices, which Bedrock's differ from: about $0.02 to
re-rank a question and $0.05 for its summary. The budget's 2 million tokens a
day is about $10.

### Logging

Each search is logged with:

- how many cards it showed;
- whether meaning was available (`ok`, `off` or `unavailable`);
- how it was ranked (`ai`, `search`, `failed`, `refused` or `over_budget`);
- whether a summary was offered, and how long it took.

Each call to Claude is logged with its tokens, time taken and prompt
version. The question and the summary are never logged or stored.

## Measuring search

```bash
.venv/bin/python manage.py evaluate_content_search [file] [--mode keyword|semantic|hybrid|all] \
    [--limit 8] [--min-similarity 0.35] [--show-misses] [--json out.json]
```

The questions are in `govuk/content_index/evaluation/questions.yaml`, about
60 of them in six categories: find, requirement, broad, paraphrase,
shorthand and out of scope. Its header says how to write one.

**Its expected answers are a draft** and need review by someone who knows
the catalogue. The command warns about any expected answer no indexed page
is, which catches typos and pages not imported.

For each mode it prints, by category:

| Number | Meaning |
| --- | --- |
| hit@1, hit@5 | An expected answer is the first shown, or in the first five (`match: any` questions) |
| recall | The share of the expected answers shown (`match: all`) |
| MRR | Mean of 1 / the rank of the first expected answer shown |
| none close | An out-of-scope question showed nothing |

It also prints:

- **how many expected answers the threshold hid**;
- **the spread of similarities**, for the expected answers and for the best
  match to each out-of-scope question. Set `CONTENT_INDEX_MIN_SIMILARITY`
  between the two;
- **the time per search**.

It reads only, so it can run on a deployed instance as a one-off ECS task, as
the other commands can: `python manage.py evaluate_content_search --mode all`.
The numbers that count come from PostgreSQL. Locally, SQLite's word matching
is cruder.

### With Claude

```bash
.venv/bin/python manage.py evaluate_content_search --rerank          # the same numbers, re-ranked
.venv/bin/python manage.py evaluate_ask_answers [--only paraphrase] [--out ask-answers.html]
```

- **`--rerank`** re-ranks each question's matches as the page does, and adds
  the tokens used.
- **`evaluate_ask_answers`** writes one HTML page showing each question's
  cards and summary, with three checks:
  - an expected answer is among the cards;
  - the summary links to one;
  - an out-of-scope question shows nothing.

  Read the summaries themselves: the checks cannot tell whether one is right.

Both call Claude for real, regardless of the admin switches. They cost money
(about $1.50 and $4 for the bundled questions) and count towards the day's
budget.

### Baseline

Measured locally on 13 November 2026, keyword mode, against the catalogue as
imported then, before the expected answers were reviewed:

| Category | hit@5 |
| --- | --- |
| find | 93% |
| requirement | 30% |
| broad (recall) | 19% |
| paraphrase | 7% |
| shorthand | 62% |

A question in a person's own words rarely finds its answer by keyword alone.
Search requires every word, and "I'm trying to find cloud policy" requires
"trying" and "find" too. To do: measure on gds-techx-002 with Titan, once the
answers are reviewed.

## Deploying it

In `gds-dtx/wagtail-iac`:

- **Environment:** `FEATURE_ASK=true` and `CONTENT_INDEX_EMBEDDINGS=bedrock`
  in `wagtail_variables`, for the site, the MCP service and the scheduled
  tasks. `CLIENT_IP_HEADER` defaults to `MCP_CLIENT_IP_HEADER`, which is
  already set.
- **IAM:** the ECS task role allowed `bedrock:InvokeModel` on
  `arn:aws:bedrock:<region>::foundation-model/amazon.titan-embed-text-v2:0`.
  The site uses it to embed each question; the nightly rebuild, to embed the
  index.
- **Model access:** check that Titan Text Embeddings V2 is available in the
  instance's region, in the Bedrock console's model catalogue. If it is not,
  set `CONTENT_INDEX_EMBEDDING_REGION` to a region where it is.
- **pgvector:** migration 0098 creates the extension. On Aurora that needs
  the `rds_superuser` role, which the app's user has as the cluster's master
  user.
- **No new schedule:** the nightly `rebuild_content_index` embeds what is
  missing at its end.
- **Claude:** `ASK_CLAUDE=bedrock` with `ASK_CLAUDE_MODEL` the EU inference
  profile, and `/ask/summary/` sent to the MCP service by its listener rule.
  The task role needs `bedrock:InvokeModel` on two things:
  - the inference profile (`arn:aws:bedrock:<region>:<account>:inference-profile/eu.anthropic.claude-opus-5-5`);
  - the model in every region the profile routes to
    (`arn:aws:bedrock:*::foundation-model/anthropic.claude-opus-5-5`), allowed
    only through that profile.

`enable_ask` in wagtail-iac (branch `feat/ask`) does all of this but the
checks.
- **On the first deployment,** run `rebuild_content_index` once: it fills in
  the hashes (`INDEX_VERSION` 3), then embeds the whole index. That is about
  1,900 requests to Bedrock, a few minutes, and well under a penny.

## Code map

| Path | What it holds |
| --- | --- |
| `govuk/ask/views.py` | The `/ask/` page: checks, the rate limit, the search, logging |
| `govuk/ask/cards.py` | Hits as cards, with an entry's parts folded onto it |
| `govuk/ask/claude.py` | The Bedrock client and the fake, errors and usage |
| `govuk/ask/prompts.py` | Instructions, schemas, and how the matches read |
| `govuk/ask/rerank.py`, `summary.py` | Re-ranking; the ticket, the summary and its rendering |
| `govuk/ask/usage.py`, `govuk/models/ask.py` | The daily budget; `AskSettings` and `AskUsage` |
| `govuk/mcp/app.py` (`summary_endpoint`) | The summary on the uvicorn service |
| `govuk/management/commands/evaluate_ask_answers.py` | The answers page |
| `govuk/models/blocks.py` (`QuestionBoxBlock`) | The question box |
| `govuk/templates/ask/`, `blocks/question_box.html` | Their templates |
| `govuk/templates/catalogue/includes/card.html` | The card, with its optional type badge and "Most relevant part" |
| `govuk/content_index/search.py` | Hybrid search (see [docs/mcp-server.md](mcp-server.md#search)) |
| `govuk/content_index/embeddings.py` | Embeddings (see [docs/mcp-server.md](mcp-server.md#embeddings)) |
| `govuk/rate_limit.py` | The per-client count, shared with the MCP server |
| `govuk/content_index/evaluation/` | The questions, and the measuring |
| `govuk/management/commands/evaluate_content_search.py` | The command |
| `govuk/tests/test_ask.py`, `test_ask_claude.py`, `test_content_index_embeddings.py` | Tests |
