# MCP server

An MCP server lets an AI tool, such as Claude, search and read what this site
publishes. It is built in stages:

1. **The content index** (done): every published, public page, role and skill,
   held as Markdown and split into sections, kept current as editors publish.
2. **The MCP server** (done): tools to search the index and read a document,
   served at `/mcp`.
3. The text of catalogue entries published elsewhere, fetched from their
   sources into the index.
4. Tools for the catalogue's entries and the framework's roles and skills.

It is behind the `MCP` feature flag (`FEATURE_MCP`). With the flag off:

- nothing is written to the index;
- the rebuild command refuses to run;
- the MCP server will not start.

The flag is on in `govuk/settings/development.py`.

## Trying it locally

1. Build the index once. After that it keeps itself current as you publish:

   ```bash
   .venv/bin/python manage.py rebuild_content_index
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
  The count is kept per process unless `CACHE_URL` names a shared cache, so
  treat this as a second line of defence behind the load balancer's own
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

The infrastructure is in `gds-dtx/wagtail-iac` and `gds-dtx/wagtail-instances`,
and does not run the MCP server yet. It needs:

- **A container** from the same image, alongside the site's, running
  `python manage.py run_mcp_server --host 0.0.0.0 --port 8001`. It needs the
  site's environment, including `FEATURE_MCP=true`. Set `--workers` to suit
  the task's CPU.
- **A health check** of `GET /mcp/health` on port 8001. The image's
  `HEALTHCHECK` checks gunicorn's port, so the MCP container needs its own
  check in the task definition.
- **A load balancer rule** sending `/mcp` and `/mcp/*` to that container's
  target group, ahead of the site's default rule. The site stays on the same
  domain.
- **`MCP_FORWARDED_ALLOW_IPS`** set to the VPC's range. Then the address the
  rate limit counts is the client's, not the load balancer's, and an address
  a client writes into `X-Forwarded-For` itself is ignored.
- **A rate limit at the load balancer or web application firewall**, per
  client IP, on `/mcp`. This is the limit to rely on.
- **`FEATURE_MCP=true`** on the site's own container too, so the index is
  written as editors publish.
- **The index rebuilt on deployment** (`rebuild_content_index`), and on a
  timer afterwards.

## The content index

### What it holds

| Source | What is indexed | Address |
| --- | --- | --- |
| Content pages, tag listings, the content main page | Hero introduction and body | The page |
| Framework main page, framework content pages, the skills A to Z | Hero introduction, body, and the main page's welcome content when it is shown | The page |
| Catalogue group pages | Description and body | The page |
| Catalogue entries | The entry's details (type, scope, professions, topics, owner, contact, version and review dates, related entries), its opening paragraph and its content in the order the page shows it: a standard's points, a policy's numbered sections, a method's steps, a tool's links | The page |
| Pages within an entry | Summary and body, with the point or step number | The page |
| External catalogue entries | The entry's details and opening paragraph. Their source's own text arrives in stage 3 | The page, and the source in `source_url` |
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

**When you change how items are read** (`extract.py`, `markdown.py` or
`chunking.py`), bump `INDEX_VERSION` in `indexer.py`. Otherwise the next
rebuild skips every row whose source has not changed.

### Data

| Model | What it holds |
| --- | --- |
| `IndexedDocument` | One per page, role or skill. Its source (exactly one of `page`, `role`, `skill`), `kind`, `origin` (`site`, or `external` for a catalogue entry published elsewhere), `site` and `url_path`, `source_url`, `title`, `description`, `type_label`, `breadcrumbs`, `metadata`, the whole `markdown`, `last_updated`, and the hash and index version |
| `IndexedChunk` | One per section: `position`, `heading_path`, `anchor` and `text` |

Addresses are stored as paths. The full address is the site's root URL
followed by the path, so a change of hostname needs no rebuild.

## Code map

| Path | What it holds |
| --- | --- |
| `govuk/models/content_index.py` | `IndexedDocument`, `IndexedChunk` |
| `govuk/content_index/extract.py` | A converter for each page type, roles and skills; what is eligible |
| `govuk/content_index/markdown.py` | Blocks and rich text as Markdown, with full link addresses |
| `govuk/content_index/chunking.py` | Splitting at headings, and catalogue heading ids |
| `govuk/content_index/indexer.py` | Storing rows, the rebuild, and what each change re-indexes |
| `govuk/content_index/signals.py` | The publish, move and restriction handlers |
| `govuk/management/commands/rebuild_content_index.py` | The rebuild command |
| `govuk/content_index/search.py` | Searching the index, and finding a document by its address |
| `govuk/mcp/tools.py` | The `search` and `get_document` tools, and their logging |
| `govuk/mcp/server.py` | The tools registered with the MCP SDK, the instructions it gives clients, and the health check |
| `govuk/mcp/app.py` | The HTTP app: hosts, maintenance and the rate limit |
| `govuk/mcp/asgi.py` | What uvicorn runs |
| `govuk/management/commands/run_mcp_server.py` | Starts the server |
| `govuk/tests/test_content_index.py`, `test_content_index_search.py`, `test_mcp_server.py` | Tests |
