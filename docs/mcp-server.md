# MCP server

An MCP server lets an AI tool, such as Claude, search and read what this site
publishes. It is built in stages:

1. **The content index** (done): every published, public page, role and skill,
   held as Markdown and split into sections, kept current as editors publish.
2. **The MCP server** (done): tools to search the index and read a document,
   served at `/mcp`.
3. **External entries' source text** (done): the page each catalogue entry
   published elsewhere points at, fetched into the index.
4. Tools for the catalogue's entries and the framework's roles and skills.

It is behind the `MCP` feature flag (`FEATURE_MCP`). The content index is
shared with Ask ([docs/ask.md](ask.md), `FEATURE_ASK`), and is kept while
either flag is on. With both off:

- nothing is written to the index;
- the rebuild command refuses to run.

The same uvicorn service also serves Ask's summary at `/ask/summary/` when
`FEATURE_ASK` is on (see [docs/ask.md](ask.md#the-summary)). It starts with
either flag on, and serves `/mcp` only with `FEATURE_MCP`. The MCP rate limit
counts `/mcp` requests only.

The flag is on in `govuk/settings/development.py`.

## Trying it locally

1. Build the index once. After that it keeps itself current as you publish:

   ```bash
   .venv/bin/python manage.py rebuild_content_index
   ```

   To fetch the source pages of the catalogue's external entries too, which
   takes about a minute for 60 sources:

   ```bash
   .venv/bin/python manage.py harvest_external_sources
   ```

2. Start the server. It runs beside `runserver`, on its own port:

   ```bash
   .venv/bin/python manage.py run_mcp_server          # http://127.0.0.1:8001/mcp
   .venv/bin/python manage.py run_mcp_server --reload # restarts when the code changes
   ```

   `http://127.0.0.1:8001/mcp/health` answers when it is up.

3. Connect a client to it:

   - **Claude Code:**
     `claude mcp add --transport http govuk-local http://127.0.0.1:8001/mcp`,
     then ask it about the site's content.
   - **The MCP Inspector**, to call the tools by hand:
     `npx @modelcontextprotocol/inspector`. Choose the Streamable HTTP
     transport and the address above. Browsers on localhost are allowed in
     development.

The addresses the tools return are built from the default site's hostname and
port (**Settings › Sites**). For links that open on `runserver`, set the port
to 8000.

## The MCP server

### Tools

| Tool | Takes | Returns |
| --- | --- | --- |
| `search` | `query`; optionally `origin` (`site` or `external`), `kind` (`page`, `role` or `skill`), `type` (such as `Standard`) and `limit` (1 to 25, 10 by default) | The best matches, one per page. Each has its address, type, origin, description, the text around the match, the section that matched best with a link to it, other sections that matched, and when it was last published |
| `get_document` | `url`: an address from `search`, any address or path on the site, or an external entry's source address | The page in full as Markdown, with its sections, breadcrumbs, the site's details about it (a catalogue entry's owner, scope and review dates, for example) and, for an external entry, a notice that its content is elsewhere |

- **Read only:** both tools are marked as read-only and as reading only
  this site.
- **Search syntax:** queries use web search syntax: quote a phrase, or put
  `-` before a word to leave it out.
- **By meaning too:** `search` runs the index's hybrid search (see
  [Search](#search)), so a question in plain words finds what it means.
- **Long pages:** a page longer than 100,000 characters is cut short, with
  `truncated` set.

### Rules around it

- **Hosts:** it answers only the hosts the site does (`ALLOWED_HOSTS`).
  Requests from a browser on another site are refused.
- **Maintenance:** it is closed with a 503 whenever the site is, whether by
  `MAINTENANCE_MODE` or by the Maintenance mode setting. There is no
  signed-in staff member to let through.
- **Rate limit:** each client address may make `MCP_RATE_LIMIT_PER_MINUTE`
  requests a minute (60 by default). One question can take several requests.
  - **Which address:** behind CloudFront, the address counted is the one in
    the header `MCP_CLIENT_IP_HEADER` names (`true-client-ip`). CloudFront's
    viewer-request function sets that header from the viewer's own address,
    overwriting anything the client sent. Only CloudFront can reach the load
    balancer, because the load balancer forwards nothing without CloudFront's
    secret `X-ALB-Protection` header.
  - **Without the header setting,** the connection's address is counted.
    Behind CloudFront that would be a CloudFront edge address, so the setting
    is needed there.
  - **Per process:** the count is kept per process unless `CACHE_URL` names a
    shared cache, so treat this as a second line of defence behind the WAF's
    limit.
- **Health check:** `/mcp/health` is exempt from maintenance and the rate
  limit, as the site's own health check is.
- **Logging:** each tool call is logged with its tool, outcome, number of
  results and duration. What was asked is never logged: people may type
  their own circumstances into a question. The SDK's own logging is held at
  INFO or above for the same reason, because at DEBUG it logs the messages
  it receives.
- **Stateless:** there are no sessions, so any container can answer any
  request.

### Deploying it

The infrastructure is in `gds-dtx/wagtail-iac` (the module) and
`gds-dtx/wagtail-instances` (one module call per site), and does not run the
MCP server yet. A request reaches it through CloudFront (and its WAF, where it
is on), then the instance's load balancer listener, then ECS. CloudFront's
default behaviour already passes `POST` through with caching off and every
viewer header forwarded, so CloudFront needs no change. The module is used
unpinned, so everything below belongs behind an opt-in variable.

- **A separate ECS service**, from the same image and with the site's
  environment, running
  `python manage.py run_mcp_server --host 0.0.0.0 --port 8001`.
  - **Why separate:** ECS replaces a whole task when any of its target groups
    fails a health check, so the MCP server must not share the site's task.
  - **What it does not need:** no EFS mount and no `migrate`; the site's
    container still runs the migrations.
  - **Size:** 256 CPU and 512 MB to begin with. Set `--workers` to suit.
- **Its security group** lets port 8001 in from the load balancer.
- **A target group** on port 8001, with a health check of `GET /mcp/health`.
  It answers whatever the `Host` header, so the load balancer can probe by
  IP.
- **A listener rule** sending `/mcp` and `/mcp/*` to that target group. It
  carries the site rule's host and `X-ALB-Protection` conditions and is
  evaluated before the site's rule, which therefore needs a higher priority
  number than it has now (1).
- **`MCP_CLIENT_IP_HEADER=true-client-ip`**, so the rate limit counts each
  viewer rather than CloudFront's edges. `MCP_FORWARDED_ALLOW_IPS` cannot
  help behind CloudFront: the load balancer appends CloudFront's address.
- **A rate-based rule on the CloudFront WAF**, per IP, on URIs starting
  `/mcp`, with its own block action. This is the limit to rely on.
  - **Monitor mode:** the WAF's managed rules default to count only
    (`waf_monitor_mode`).
  - **The WAF itself** is off unless `enable_cloudfront_waf` is set.
- **For search by meaning:** `CONTENT_INDEX_EMBEDDINGS=bedrock`, and the
  task role allowed `bedrock:InvokeModel` on Titan Text Embeddings V2 (see
  [docs/ask.md](ask.md#deploying-it)).
- **`FEATURE_MCP=true`** in the instance's `wagtail_variables`, so the site,
  the MCP service and the scheduled tasks all have it. The site needs it to
  write the index as editors publish.
- **Two scheduled tasks**, built like `sync_external_content`'s:
  `rebuild_content_index` and `harvest_external_sources`, daily. They need
  outbound https, which the tasks' security group and subnets already give
  `sync_external_content`.
- **On the first deployment**, run `rebuild_content_index` and then
  `harvest_external_sources` once, with ECS Exec or a one-off task.

## The content index

### What it holds

| Source | What is indexed | Address |
| --- | --- | --- |
| Content pages, tag listings, the content main page | Hero introduction and body | The page |
| Framework main page, framework content pages, the skills A to Z | Hero introduction, body, and the main page's welcome content when it is shown | The page |
| Catalogue group pages | Description and body | The page |
| Catalogue entries | The entry's details (type, scope, professions, topics, owner, contact, version and review dates, related entries), its opening paragraph and its content in the order the page shows it: a standard's points, a policy's numbered sections, a method's steps, a tool's links | The page |
| Pages within an entry | Summary and body, with the point or step number | The page |
| External catalogue entries | The entry's details and opening paragraph, then a copy of its source page's text where one could be fetched (see [External entries' source text](#external-entries-source-text)) | The page, and the source in `source_url` |
| Roles | Description, each level (description, grades, skills with their level descriptions), senior civil service grades and skills, and both progression lists | `…/role/<slug>/` under the framework main page |
| Skills | Description, each level's points, and leadership examples | `…/skills/#<slug>` on the skills A to Z |

A page type without a converter is indexed with its title and search
description only.

### What it leaves out

The index holds only what a signed-out reader is served:

- pages that are not live, and drafts: a saved draft is in a revision until it
  is published, so the index never sees it;
- pages under a view restriction (sign-in, password or groups), on the page
  itself or on any page above it;
- page types whose flag is off: framework pages without `SKILLS`, catalogue
  pages without `GUIDANCE`;
- roles and skills that are not live, and all of them when their page (the
  framework main page, or the skills A to Z) is not live and public;
- the tree root, and pages of Wagtail's bare page type.

Content discovery items are not indexed.

### How it is split

Each document is split at its h2 and h3 headings. Each section records the
headings above it (`Testing › Screen readers`), and a section longer than about
1,500 characters is split between paragraphs, never inside a code block.

On catalogue pages a section also records the id of its heading on the page,
counted the way the page counts them, so a link can go straight to it. Other
pages make their heading ids in the browser, so their sections have none.

Links are written as full addresses, so a section read on its own can still
be followed.

### How it is kept current

After a publish commits, the index is updated:

| Event | What is re-indexed |
| --- | --- |
| A page is published or unpublished | The page. Its descendants too if its title or address changed. The whole entry if it is within a catalogue entry, because of the numbering |
| A page is moved or reordered | It and everything beneath it, and any catalogue entry it left or joined |
| A view restriction is added or removed | The page and everything beneath it |
| A role is published or unpublished | The role, and the roles whose progression lists name it |
| A skill is published, unpublished or deleted | The skill, and the roles that list it |
| The framework main page or the skills A to Z changes | Every role, or every skill and role, because their addresses change |

A deleted page, role or skill takes its rows with it.

An update that fails is logged and never stops the publish.

Some changes are only picked up by a rebuild:

- renamed tags or catalogue vocabulary;
- a feature flag switched;
- content written straight to the database by a migration or a script.

Run the rebuild on a timer for these.

### Rebuilding

```bash
.venv/bin/python manage.py rebuild_content_index [--only pages|roles|skills] [--force] [--dry-run]
```

- It indexes everything that should be in the index and removes everything
  that should not.
- Each item's row stores a hash of what it was built from, so items that have
  not changed are skipped. A rebuild that finds nothing to do takes a couple
  of seconds for a few hundred documents.
- `--force` rewrites every row.
- `--dry-run` reports the counts and writes nothing.
- One item that fails is reported and the rest still index. The command then
  exits with an error, so a scheduled run shows up as failed.

With embeddings on, the rebuild then embeds what has no vector yet (see
[Embeddings](#embeddings)); `--no-embed` leaves that.

**When you change how items are read** (`extract.py`, `markdown.py` or
`chunking.py`), or what is embedded for them (`section_text` and
`document_text` in `embeddings.py`), bump `INDEX_VERSION` in `indexer.py`.
Otherwise the next rebuild skips every row whose source has not changed.

### Embeddings

Each section, and each document as its card reads (title, type and
description), has a vector of what it means, so search can find text that
says what a question asks in other words. Turned on by
`CONTENT_INDEX_EMBEDDINGS=bedrock`: Amazon Titan Text Embeddings V2, 1024
dimensions, on Amazon Bedrock, with the ECS task's role.

- **Stored by the text's hash.** A row records the SHA-256 of what is
  embedded for it (`embedding_hash`), and `ContentEmbedding` holds one
  vector per hash and model. Sections are remade whenever their document
  changes, but text that did not change keeps its vector, so only new or
  changed text is sent to the model, and a forced rebuild sends nothing.
- **Never on publish.** Storing a row only hashes its text.
  `manage.py embed_content_index [--dry-run]`, which the nightly rebuild
  runs at its end, embeds what is missing and removes vectors nothing uses.
  Until then, a new section is found by its words alone.
- **A stale row** records the hash of text other than what it would embed
  now: `embeddings.py` changed without `INDEX_VERSION` being bumped. The
  command reports them; a forced rebuild mends them.
- **The vector type** is pgvector's, which Aurora PostgreSQL has built in;
  migration 0098 creates the extension. The column has no fixed length, so
  another length or model needs no migration. There is no approximate
  index: every search compares against every vector, which takes
  milliseconds at a few thousand rows. Add an HNSW index if the index grows
  past about 50,000 sections.
- **On SQLite**, which tests and local runs use, vectors are stored as text
  and compared in Python. Locally, `CONTENT_INDEX_EMBEDDINGS=fake` embeds by
  words alone, to try the code without AWS; with AWS credentials boto3 can
  find, `bedrock` works locally too.

### Search

`content_index.search.search` has three modes:

| Mode | What it matches | Used by |
| --- | --- | --- |
| `keyword` | Every word asked, ranked by PostgreSQL full-text search | `search_documents` by default; the evaluation's baseline |
| `hybrid` | Every word; any of the words, less those that say nothing ("how", "trying"); and the sections and documents closest in meaning. The ranked lists are merged by reciprocal rank fusion (`FUSION_WEIGHTS`) | The MCP server's `search` tool, and Ask |
| `semantic` | Meaning alone | The evaluation, to compare |

- **Each hit records how it matched:** `matched_words` (`every`, `some`,
  or `None` for meaning alone) and `similarity` (0 to 1).
- **A poor match by meaning alone is dropped** below
  `CONTENT_INDEX_MIN_SIMILARITY`, since the meaning lists always return
  something, even for a question the site cannot answer.
- **When the model cannot be reached**, a hybrid search runs on its words
  and says so (`SearchOutcome.semantic`). The warning it logs names the
  reason, never the query. A query's vector is cached for a day under the
  query's hash.

## External entries' source text

A catalogue entry published elsewhere has only a summary on this site. The
index also keeps a copy of the text of the page its source address points at,
so a question can be answered from the content itself.

### What is fetched

Only the source page itself, not the pages it links to. How it is read
depends on the source:

| Source | How it is read |
| --- | --- |
| `www.gov.uk` | From the GOV.UK Content API (`/api/content/<path>`), which gives the body without the page around it. A guide's parts come under their own titles. A publication's documents are listed as links. An index-style page with no body, such as the Service Manual, the Service Standard or the Service Toolkit, is the list of pages beneath it, with their descriptions |
| `github.com` | The repository page's rendered README |
| Any other site | The page's main content: `<main>`, or `role="main"`, or `#main-content`, or the body. Navigation, headers, footers, scripts, forms and the GOV.UK Design System's furniture (breadcrumbs, back links, banners, visually hidden text) are left out |

The HTML becomes Markdown with its headings, lists, tables, code and links,
and links are made absolute.

- **Too little text:** a page with under 200 characters of text has nothing
  to index. Most often it is drawn by JavaScript, which a fetch does not run.
- **Not a web page:** a PDF or other file is not read.
- **Size:** a copy is cut off at 200,000 characters.

### The rules it fetches by

- **Https only**, through the same guards as content discovery and the
  catalogue import: no private or loopback addresses, checked again on every
  redirect.
- **robots.txt is honoured**, for the agent `wagtail-govuk-content-index`.
  A missing robots.txt allows everything. One that cannot be read (a 5xx, or
  the host is down) means that host is skipped until the next run.
- **It identifies itself** with a User-Agent of the form
  `wagtail-govuk-content-index/1.0 (+<site address>)`, and does not pretend
  to be a browser. Sites behind bot protection may refuse it.
- **Conditional requests:** a page that has not changed answers 304 and
  costs almost nothing.
- **Politeness:** a second between two requests to the same host.
- **Limits:** a 10-second timeout and a 5 MB cap on each download.

### What happens when it cannot

Each source has a `HarvestedSource` row, shared by every entry that points at
it. Its status is how the last attempt went:

| Status | Meaning | The copy |
| --- | --- | --- |
| Fetched | Read, or confirmed unchanged | Replaced or kept |
| Refused by the source | A 401, 403 or 429, or robots.txt does not allow it | Kept |
| Could not be fetched | Any other failure | Kept |
| No text found | Read, with too little text | Removed |
| Not fetched | Not https, or not a web page | Removed |

A source that is briefly down keeps the text fetched before. One that is read
and has no text any more loses it.

### When it fetches

- **Not on publish.** Fetching while a page is published would hold up the
  publish, up to the 10-second timeout for each page, and an import publishes
  dozens of pages at once. A new entry, or one given a new source address,
  has no copy until the next timed run, unless someone uses Fetch now. Until
  then its notice says it has not been fetched yet.
- **On a timer:** `harvest_external_sources` fetches every source, re-indexes
  what changed, and removes rows that no live entry uses:

  ```bash
  .venv/bin/python manage.py harvest_external_sources [--url URL ...] [--force]
  ```

  It prints a line for each source and the totals. Refused and failed sources
  are expected, so they do not fail the run; only an unexpected error does.
  `--force` fetches every page in full, without the conditional request.
- **From the admin:** the external entry's edit view has a **Source text**
  panel. It shows the last attempt, the reason if it failed, and how much
  text is held, and has a **Fetch now** button. Fetch now needs permission
  to publish the entry.

### How the MCP server shows it

- **Search:** the copy is searched like any other content. A result for an
  external entry has `source_checked_at`, when its copy was last checked.
- **`get_document`:** the notice says whether the text includes a copy, and
  when it was last checked. The copy is third-party content that may have
  changed, and the source is authoritative. Where there is no copy, the
  notice says why.
- **Server instructions:** they tell clients the same, and to cite the
  `source_url`.

Sections of a copied page link to the source address. Its heading ids are not
carried over.

### Data

| Model | What it holds |
| --- | --- |
| `IndexedDocument` | One per page, role or skill. Its source (exactly one of `page`, `role`, `skill`), `kind`, `origin` (`site`, or `external` for a catalogue entry published elsewhere), `site` and `url_path`, `source_url`, `title`, `description`, `type_label`, `breadcrumbs`, `metadata`, the whole `markdown`, `last_updated`, and the hash and index version |
| `IndexedChunk` | One per section: `position`, `heading_path`, `anchor` and `text` |
| `HarvestedSource` | One per external source address: `status`, `reason`, `http_status`, the copy (`title`, `markdown`, `content_hash`), `etag` and `last_modified` for conditional requests, the source's own `source_updated_at`, `fetched_at` (the last attempt), `succeeded_at` and `failures` |

Addresses are stored as paths. The full address is the site's root URL
followed by the path, so a change of hostname needs no rebuild.

## Code map

| Path | What it holds |
| --- | --- |
| `govuk/models/content_index.py` | `IndexedDocument`, `IndexedChunk`, `HarvestedSource`, `ContentEmbedding` |
| `govuk/content_index/embeddings.py` | What is embedded, the Bedrock and fake models, filling the store |
| `govuk/management/commands/embed_content_index.py` | Embedding what is missing |
| `govuk/content_index/extract.py` | A converter for each page type, roles and skills; what is eligible |
| `govuk/content_index/markdown.py` | Blocks and rich text as Markdown, with full link addresses |
| `govuk/content_index/chunking.py` | Splitting at headings, and catalogue heading ids |
| `govuk/content_index/indexer.py` | Storing rows, the rebuild, and what each change re-indexes |
| `govuk/content_index/signals.py` | The publish, move and restriction handlers |
| `govuk/management/commands/rebuild_content_index.py` | The rebuild command |
| `govuk/content_index/search.py` | Searching the index by words and meaning, and finding a document by its address |
| `govuk/mcp/tools.py` | The `search` and `get_document` tools, and their logging |
| `govuk/mcp/server.py` | The tools registered with the MCP SDK, the instructions it gives clients, and the health check |
| `govuk/mcp/app.py` | The HTTP app: hosts, maintenance and the rate limit |
| `govuk/mcp/asgi.py` | What uvicorn runs |
| `govuk/management/commands/run_mcp_server.py` | Starts the server |
| `govuk/content_index/fetch.py` | The guarded fetch, and robots.txt |
| `govuk/content_index/sources.py` | Reading a source page as Markdown: GOV.UK's Content API, GitHub, any other page |
| `govuk/content_index/harvest.py` | Fetching a source, storing how it went, re-indexing its entries; fetching them all |
| `govuk/management/commands/harvest_external_sources.py` | Fetching every source |
| `SourceTextPanel` in `govuk/models/catalogue/pages.py`; `catalogue_fetch_source_view` in `govuk/wagtail_hooks/catalogue.py` | The Source text panel and Fetch now |
| `govuk/tests/test_content_index.py`, `test_content_index_search.py`, `test_content_index_harvest.py`, `test_content_index_embeddings.py`, `test_mcp_server.py` | Tests |
