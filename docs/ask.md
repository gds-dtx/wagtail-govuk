# Ask

Ask lets someone type what they want to do in their own words ("I'm trying to
find cloud policy", "What standards do I have to meet?") and see the site's
closest matches, as the cards the content catalogue's section pages use.

It is built in steps:

1. **An evaluation set** (done): questions with known answers, and a command
   that measures search against them.
2. **Search by meaning, and the cards-only page** (done): the content index's
   sections embedded, a hybrid search, the question box block and `/ask/`.
3. A short written answer above the cards, drawn only from them.

Nothing generates text yet: every word on the page is the site's own.

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

### Logging

Each search is logged with how many cards it showed, whether meaning was
available (`ok`, `off` or `unavailable`) and how long it took. The question
is never logged or stored.

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
- **On the first deployment,** run `rebuild_content_index` once: it fills in
  the hashes (`INDEX_VERSION` 3), then embeds the whole index. That is about
  1,900 requests to Bedrock, a few minutes, and well under a penny.

## Code map

| Path | What it holds |
| --- | --- |
| `govuk/ask/views.py` | The `/ask/` page: checks, the rate limit, the search, logging |
| `govuk/ask/cards.py` | Hits as cards, with an entry's parts folded onto it |
| `govuk/models/blocks.py` (`QuestionBoxBlock`) | The question box |
| `govuk/templates/ask/`, `blocks/question_box.html` | Their templates |
| `govuk/templates/catalogue/includes/card.html` | The card, with its optional type badge and "Most relevant part" |
| `govuk/content_index/search.py` | Hybrid search (see [docs/mcp-server.md](mcp-server.md#search)) |
| `govuk/content_index/embeddings.py` | Embeddings (see [docs/mcp-server.md](mcp-server.md#embeddings)) |
| `govuk/rate_limit.py` | The per-client count, shared with the MCP server |
| `govuk/content_index/evaluation/` | The questions, and the measuring |
| `govuk/management/commands/evaluate_content_search.py` | The command |
| `govuk/tests/test_ask.py`, `test_content_index_embeddings.py` | Tests |
