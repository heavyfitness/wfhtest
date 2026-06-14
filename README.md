# WFH Content Pipeline

Automated content pipeline for [thewfhconnect.com](https://thewfhconnect.com) — a remote-jobs
blog (Kadence + RankMath + LiteSpeed on Hostinger). It reads **verified job leads** from a
Google Sheet (or a local CSV), uses **Claude** to write SEO-optimized posts in a trustworthy,
no-hype voice, attaches **schema.org JobPosting JSON-LD** (Google for Jobs), and publishes to
WordPress via the **REST API** — on a schedule, with zero manual writing.

```
Google Sheet / CSV ──► LeadSource ──► ContentGenerator (Claude / Ollama)
                                          │  JSON → validated GeneratedPost
                                          ▼
                                      QC gate ──► JobPosting JSON-LD appended
                                          │
                                          ▼
                          WordPressClient (REST + RankMath meta)
                                          │
                                          ▼
                       SQLite posted-table (dedup, keyed by lead hash)
```

## Project layout

```
wfh_pipeline/
├── cli.py                  # typer CLI: python -m wfh_pipeline [OPTIONS]
├── config.py               # .env-driven Settings (+ per-operation validation)
├── models.py               # Lead, GeneratedPost, AffiliateLink (pydantic)
├── pipeline.py             # orchestrator + scheduling slots
├── qc.py                   # quality gate before anything reaches WordPress
├── schema.py               # schema.org JobPosting JSON-LD builder
├── state.py                # SQLite posted-store (dedup)
├── wordpress.py            # WP REST client (Basic Auth, retries, RankMath meta)
├── logging_setup.py        # console + rotating file logging
├── utils.py
├── sources/                # pluggable lead adapters (LeadSource interface)
│   ├── base.py             #   fetch_new_leads() contract
│   ├── csv_source.py       #   CSVLeadSource (testing / local)
│   ├── sheets.py           #   GoogleSheetsLeadSource (production)
│   └── parsing.py          #   shared row → Lead parsing
└── generation/             # LLM content generation
    ├── base.py             #   LLMBackend interface (swappable)
    ├── anthropic_backend.py#   default: Anthropic Messages API
    ├── ollama_backend.py   #   local Ollama (e.g. your RTX 3090)
    ├── prompts.py          #   system + user prompts
    └── generator.py        #   JSON parsing, affiliate section, disclosures

mu-plugins/wfh-rest-meta.php  # WordPress companion plugin (RankMath REST meta)
sample_leads.csv              # 5 fake leads (4 verified, 1 not) for dry runs
tests/                        # pytest suite — runs fully offline
```

## Requirements

- Python 3.11+
- A WordPress site with [Application Passwords](https://make.wordpress.org/core/2020/11/05/application-passwords-integration-guide/)
  (built into WP 5.6+, requires HTTPS)
- An [Anthropic API key](https://platform.claude.com/) — or a local Ollama install
- Optional: a Google Cloud service account for the Sheets source

## Setup

```bash
git clone <this repo> && cd wfhtest
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill it in — see below
```

### 1. WordPress Application Password

1. WP Admin → **Users → Profile** (the user the pipeline will post as — an Editor is enough).
2. Scroll to **Application Passwords**, name it `wfh-pipeline`, click **Add New**.
3. Copy the generated password (spaces and all) into `WP_APP_PASSWORD` in `.env`.

> **Hostinger/LiteSpeed note:** if every API call returns 401 even with correct credentials,
> the `Authorization` header is probably being stripped. Add this to the top of your site's
> `.htaccess`:
> ```apache
> RewriteEngine On
> RewriteRule .* - [E=HTTP_AUTHORIZATION:%{HTTP:Authorization}]
> ```

### 2. Install the mu-plugin (RankMath SEO meta over REST)

WordPress only accepts post meta over REST for keys explicitly registered with
`show_in_rest`. RankMath does not register its keys, so the pipeline ships a tiny
**must-use plugin**:

1. Open Hostinger's File Manager (or SFTP) and go to `wp-content/`.
2. Create the folder `mu-plugins/` if it doesn't exist.
3. Upload [`mu-plugins/wfh-rest-meta.php`](mu-plugins/wfh-rest-meta.php) into it.

That's it — mu-plugins are always active, no activation step. Without it the pipeline
**still publishes** (it retries without meta and logs a warning); you'd just lose the
RankMath title/description/focus-keyword fields.

### 3. Google Sheets source (production)

1. In Google Cloud Console: create a project → enable the **Google Sheets API** →
   create a **service account** → create a JSON key. Save it as `service_account.json`
   in the repo root (it's gitignored).
2. Share your leads sheet with the service account's `client_email` (Viewer is enough).
3. Set `GOOGLE_SHEETS_ID` (from the sheet URL) and `GOOGLE_SHEETS_WORKSHEET` in `.env`.

Expected columns (header row, case/spacing-insensitive — `Apply URL` works too):

| column | notes |
|---|---|
| `id` | optional — a stable hash of `apply_url` is derived when blank |
| `company`, `title` | required |
| `pay` | optional free text, e.g. `$17-$19/hr` (parsed best-effort into JSON-LD `baseSalary`) |
| `remote` | TRUE/FALSE (default TRUE) |
| `employment_type` | `FULL_TIME`, `part-time`, `contract`… normalized for schema.org |
| `requirements` | one cell, items separated by `\|`, `;`, or newlines |
| `description` | plain text |
| `apply_url` | required, absolute http(s) URL — also the dedup key |
| `source`, `date_found`, `category` | optional (`category` becomes the WP category) |
| `verified` | **only `TRUE`/`yes`/`1` rows are ever published** |

## Usage

The CLI is a single command; `--help` shows everything.

```bash
# First run — full pipeline incl. the LLM call, prints everything, touches nothing:
python -m wfh_pipeline --source csv --csv-path sample_leads.csv --dry-run -v

# Real run, as drafts (the default status), 2 posts max:
python -m wfh_pipeline --source csv --csv-path sample_leads.csv --limit 2

# Production: read the sheet, schedule posts across the day (8am/12pm/4pm ET):
python -m wfh_pipeline --source sheets --schedule --limit 3

# Publish immediately instead:
python -m wfh_pipeline --source sheets --status publish --limit 1
```

| flag | meaning |
|---|---|
| `--source [sheets\|csv]` | lead source (default `csv`) |
| `--csv-path PATH` | CSV file for `--source csv` (default `sample_leads.csv`) |
| `--limit N` | max posts this run (default `POSTS_PER_RUN`) |
| `--status [draft\|publish\|future]` | WP status (default `DEFAULT_POST_STATUS` = draft) |
| `--schedule` | implies `future`; spreads posts across `SCHEDULE_TIMES` in `TIMEZONE` |
| `--dry-run` | run everything **including the LLM** but never call WordPress |
| `-v` | debug logging |

Notes:

- `--dry-run` is the safe path for first runs. It does call the Anthropic API
  (a few cents per post) but never writes to WordPress and never marks leads as posted.
- Already-posted leads (by hash of `apply_url`) are skipped automatically — re-running
  is always safe and idempotent.
- `--status future` without `--schedule` also spreads posts across the schedule slots;
  `--schedule` with another status forces `future`.
- QC gate: posts with empty/oversized titles, short bodies, broken slugs, or a missing
  apply link are **logged and skipped**, never published.

## Scheduling

### cron (recommended)

Run once daily and let `--schedule` spread that day's posts across 8am/12pm/4pm ET:

```cron
# m h dom mon dow  command
15 7 * * * cd /home/youruser/wfh-content-pipeline && .venv/bin/python -m wfh_pipeline --source sheets --schedule --limit 3 >> logs/cron.log 2>&1
```

The rotating file log (`LOG_FILE`) captures the same output with DEBUG detail.

### n8n (alternative)

If you already run n8n: a **Schedule Trigger** node → **Execute Command** node running the
same command works fine, and gives you retry/alerting UX for free. Keep `--limit` modest and
let the SQLite dedup table make overlapping runs harmless.

## Swapping the LLM backend (Ollama on your 3090)

The generator only depends on the tiny `LLMBackend` interface. To use a local model:

```env
LLM_BACKEND=ollama
OLLAMA_BASE_URL=http://192.168.1.50:11434
OLLAMA_MODEL=llama3.1
```

The Ollama backend uses `/api/chat` with `format=json`, so the same JSON contract and
QC gate apply. Everything else is unchanged.

## Tests

```bash
pytest
```

The suite runs fully offline: the LLM is stubbed, WordPress is mocked with
`httpx.MockTransport`, and the end-to-end test drives a real `--dry-run`-equivalent
pipeline against `sample_leads.csv` (4 verified leads in, 4 dry-run results out,
nothing recorded as posted).

## Reliability notes

- **HTTP retries:** WordPress calls retry transport errors and 429/5xx with exponential
  backoff (2s/4s/8s). The Anthropic SDK retries internally; the Ollama backend has its own
  backoff loop.
- **Per-lead isolation:** one bad lead (LLM failure, QC rejection, WP error) is logged and
  skipped; the run continues.
- **Term failures don't block posts:** if category/tag resolution fails, the post is
  published without taxonomy and a warning is logged.
- **Meta failures don't block posts:** if RankMath meta is rejected (mu-plugin missing),
  the post is retried without meta and a warning is logged.
- **Logging:** console (INFO, DEBUG with `-v`) + rotating file (`LOG_FILE`, 1 MB × 5, DEBUG).

## Assumptions & design decisions

- **Model:** defaults to `claude-sonnet-4-6` (current Sonnet); change `ANTHROPIC_MODEL`
  in `.env` any time — no code change needed.
- **Affiliate links & disclosures are appended in code, not written by the LLM**, so URLs
  can never be hallucinated or mangled. Every post ends with an FTC affiliate disclosure
  and a "verify independently / do your own due diligence" disclaimer.
- If the LLM forgets the apply link, the generator appends a standard "How to apply"
  button itself rather than discarding the post.
- **WP post title = `seo_title`** (so H1 matches the meta title); RankMath meta is set as
  well. Category = lead `category` (title-cased); tags = company + category.
- **JobPosting JSON-LD:** `validThrough` = `date_found` + 45 days, `jobLocationType` =
  `TELECOMMUTE`, `applicantLocationRequirements` = USA, `directApply` = true. Schema is
  skipped (and logged) only when a lead has neither description nor requirements. Free-text
  pay is parsed best-effort into `baseSalary` and omitted when unparseable — Google treats
  it as recommended, not required.
- **Dedup:** the stable lead id is `sha256(apply_url)[:16]` (case/trailing-slash
  normalized). Editing a lead's title in the sheet won't cause a duplicate post; changing
  its apply URL will be treated as a new lead.
- Scheduling slots earlier than *now + 5 min* roll to the next slot/day, so an afternoon
  run never tries to schedule a post in the past.

## Troubleshooting

| symptom | likely fix |
|---|---|
| `401 Unauthorized` from WP | wrong app password, or Hostinger stripping the `Authorization` header — see the `.htaccess` snippet above |
| log: "WordPress rejected the post meta" | the mu-plugin isn't installed in `wp-content/mu-plugins/` |
| `Config error: Missing required settings: …` | fill in the named keys in `.env` (only the features you use are validated) |
| Google Sheets `PERMISSION_DENIED` | share the sheet with the service account's `client_email` |
| posts publish but Google for Jobs ignores them | give leads real descriptions/requirements; check the JSON-LD with Google's Rich Results Test |
| LiteSpeed cache shows stale archive pages | LiteSpeed Cache → purge on post publish is on by default; re-check **Cache → Purge** settings |

## Direct-apply pledge & two-lane publishing

The WFH Connect pledges to label every apply link honestly.
The pipeline enforces this in code — not just in an LLM prompt.

### How it works

Every lead's `apply_url` is classified at instantiation time:

| `link_type` | Meaning |
|---|---|
| `direct` | URL domain is a known ATS (Greenhouse, Lever, Workday, iCIMS, …) |
| `aggregator` | URL domain is a job board (RemoteOK, We Work Remotely, Indeed, …) |
| `unknown` | Neither list matched |

Two computed fields are always derived from the URL, never stored:
- `lead.link_type` — `"direct"` / `"aggregator"` / `"unknown"`
- `lead.is_direct` — `True` only when `link_type == "direct"`

### Two publishing lanes

| Lane | `--lane` value | What runs | Default? |
|---|---|---|---|
| **Direct** | `direct` | ATS-sourced leads only (`is_direct=True`) | Yes |
| **Aggregator** | `aggregator` | Job-board leads only (`is_direct=False`) | No |
| **All** | `all` | Every verified lead | No |

```bash
# Default: only ATS-direct leads — safe to auto-publish
python -m wfh_pipeline

# Review aggregator listings manually before they go live
python -m wfh_pipeline --lane aggregator --status draft

# Process everything (direct + aggregator)
python -m wfh_pipeline --lane all
```

### Honest-labeling rule

The post generator receives an explicit `LINK FRAMING` instruction:

- **Direct lead** — anchor text reads "Apply directly on their site"
- **Aggregator lead** — anchor text reads "View this listing on \<Board Name\>"

The QC gate then **rejects** any post that contains "apply directly" or "direct apply"
language when the URL is classified as aggregator or unknown. This is a hard code check,
not a prompt-drift risk.

### Aggregator auto-publish guard

By default (`ALLOW_AGGREGATOR_AUTOPUBLISH=false`), any aggregator lead that reaches the
publish step is forced to `status=draft` and logged:

```
AGGREGATOR — needs manual direct-link review before publishing (<Company> — <Title>)
```

Set `ALLOW_AGGREGATOR_AUTOPUBLISH=true` in `.env` only when you've verified the listing
links directly to the employer's own ATS.

### Aggregator review session

1. Run `python -m wfh_pipeline --lane aggregator --status draft` to collect drafts.
2. In WordPress → Posts, filter by status=Draft and look for the aggregator log tag.
3. For each draft: click through to the job board, find the real employer apply URL.
4. If it resolves to a known ATS domain, update the draft's apply link and publish.
5. Otherwise, leave as draft or delete.

**Shortcut — resolve source links:**
```bash
python -m wfh_pipeline --lane aggregator --resolve-source-links --status draft
```
This attempts to follow each aggregator listing's outbound apply link. If it resolves to a
known ATS domain, the lead's URL is upgraded automatically before content generation.
Only confirmed-direct upgrades are accepted; unknown destinations are left unchanged.

### Extending the domain lists

Add domains to the built-in lists without touching code:

```dotenv
# .env
EXTRA_DIRECT_DOMAINS=jobs.mycompany.com,*.myats.io
EXTRA_AGGREGATOR_DOMAINS=jobs.internal-board.example.org
```

Wildcard prefixes (`*.domain.com`) match any subdomain.

---

## Multi-source lead intake (Part 5)

The pipeline supports pulling from multiple lead sources simultaneously, deduplicated across runs via the SQLite store.

### Source types

| Source | File | `source_trust` | Default lane | Live? |
|--------|------|----------------|-------------|-------|
| Greenhouse Job Board API | `sources/greenhouse.py` | `direct` | direct | ✅ |
| Lever postings API | `sources/lever.py` | `direct` | direct | ✅ (if token valid) |
| RemoteOK RSS | `sources/rss.py` | `aggregator` | aggregator | ✅ |
| We Work Remotely RSS | `sources/rss.py` | `aggregator` | aggregator | ✅ |
| Remotive JSON API | `sources/rss.py` | `aggregator` | aggregator | ✅ |
| JSearch/RapidAPI | `sources/job_api.py` | `unknown` (per URL) | varies | off by default |
| CSV file | `sources/csv_source.py` | `aggregator` | — | legacy |
| Google Sheets | `sources/sheets.py` | `aggregator` | — | legacy |

### boards.yaml

ATS board tokens and RSS toggles are configured in `boards.yaml` (repo root):

```yaml
greenhouse:
  - anthropic      # verified: 35 remote roles
  - stripe         # verified: 92 remote roles
  - gitlab         # verified: 137 remote roles (all-remote company)
  - twilio         # verified: 153 remote roles
  - vercel         # verified: 17 remote roles
  - airtable       # verified: 14 remote roles
  - discord        # verified: 9 remote roles
  - datadog        # verified: 50 remote roles

lever:
  - whereby        # example; add slugs from jobs.lever.co/<slug>

rss:
  remoteok: true
  weworkremotely: true
  remotive: true
```

**Adding a Greenhouse board:** find the board token in the URL `https://boards.greenhouse.io/<token>/jobs` and add it to the `greenhouse` list. The pipeline will automatically include it on the next run.

**Adding a Lever board:** the token is the slug in `https://jobs.lever.co/<slug>`. Note that many companies have migrated off Lever's v0 public API — the source logs a warning and returns an empty list on 404.

**Greenhouse boards that returned 404 or 0 remote jobs (as of 2026-06-13, do not add):** openai, notion, figma, linearapp, retool, zapier, shopify, github, automattic, hashicorp.

### Source group CLI flag

```bash
# All sources (default) — ATS + RSS, then lane filter applied
python -m wfh_pipeline run --source-group all --lane direct

# ATS boards only (direct lane; recommended for automated daily runs)
python -m wfh_pipeline run --source-group direct --lane direct

# RSS feeds only (aggregator lane; posts as draft for manual review)
python -m wfh_pipeline run --source-group aggregator --lane aggregator --status draft

# Legacy single-file modes (backward compatible)
python -m wfh_pipeline run --source csv --csv-path my_leads.csv
python -m wfh_pipeline run --source sheets
```

### Recommended daily automated run

```bash
# Morning run: pull from all ATS boards, publish direct-apply leads as drafts
python -m wfh_pipeline run \
  --source-group direct \
  --lane direct \
  --status draft \
  --limit 5

# Weekly aggregator review: pull RSS, flag for manual direct-link review
python -m wfh_pipeline run \
  --source-group aggregator \
  --lane aggregator \
  --status draft \
  --resolve-source-links
```

### Google Alerts → RSS routing

Google Alerts does not provide a direct API, but it can emit an RSS feed for any search query. To route a Google Alert into the pipeline:

1. Create an alert at https://www.google.com/alerts for e.g. `"remote software engineer" site:greenhouse.io`
2. Set delivery to **RSS feed** and copy the feed URL
3. Add a custom `RSSLeadSource` subclass pointing to that URL:

```python
from wfh_pipeline.sources.rss import RSSLeadSource
from wfh_pipeline.models import Lead

class GoogleAlertSource(RSSLeadSource):
    name = "google_alert"
    feed_url = "https://www.google.com/alerts/feeds/YOUR_ALERT_ID/YOUR_TOKEN"

    def _parse_entry(self, entry):
        # Google Alerts entries have title and link; extract company from title
        title = entry.get("title", "")
        link = entry.get("link", "")
        if not link:
            return None
        return Lead(
            company="Unknown",  # Refine with --resolve-source-links
            title=title,
            apply_url=link,
            source="google_alert",
            source_trust=self.trust,
            verified=True,
        )
```

Add the source to `build_sources()` in `sources/multi.py` or instantiate it directly.

### Optional JSearch/RapidAPI source

Sign up at [RapidAPI](https://rapidapi.com) and subscribe to [JSearch](https://rapidapi.com/letscrape-6bRBa3QguO5/api/jsearch) (free tier available), then set:

```dotenv
# .env
ENABLE_JOB_API=true
JOB_API_KEY=your-rapidapi-key

# Comma-separated queries — each runs independently, results are combined and
# deduplicated by job ID. Defaults below target WFH Connect's audience.
JOB_API_QUERIES=remote customer service no experience,remote data entry no experience,remote chat support entry level,virtual assistant remote,remote customer support associate

# Max results per query (20 = 2 API pages per query)
JOB_API_MAX_RESULTS=20
```

JSearch results include direct employer ATS URLs for many roles — these classify as `link_type="direct"` and enter the direct lane automatically. Others classify as aggregator. The pipeline's lane filter handles routing correctly.

> **Note on BPO employers:** The biggest remote CS employers (Concentrix, TTEC, Teleperformance, Sitel, Sutherland, Conduent, BroadPath, Alorica, TaskUs, LiveOps, Arise, Working Solutions) are NOT on Greenhouse — they use iCIMS, Taleo, Workday, or proprietary portals. JSearch is the primary volume driver for entry-level CS / data-entry leads from these employers.

### Relevance / quality filter

Applied automatically after lane filtering to skip roles that don't fit WFH Connect's audience (senior engineering titles, director-level posts, talent-community placeholders, etc.).

```dotenv
# .env — all optional; built-in defaults are tuned for WFH Connect

# At least ONE must appear in title or description to keep the lead.
# Leave blank to use built-in defaults (customer service, data entry, etc.)
RELEVANCE_INCLUDE_KEYWORDS=

# If ANY appears in the job TITLE, the lead is rejected.
# Leave blank to use built-in defaults (senior, staff, principal, engineer…)
RELEVANCE_EXCLUDE_TITLE=

# Set true to disable the include-keyword requirement (only exclude applies).
# Useful for small, curated boards where you trust all titles.
RELEVANCE_PERMISSIVE=false
```

Disable the filter entirely for a single run with `--no-relevance-filter`:

```bash
python -m wfh_pipeline run --dry-run --no-relevance-filter
```

The filter is a **pre-generation gate** — it saves Anthropic API cost by never generating copy for roles outside your audience. It does not affect QC, lane policy, or the direct-apply pledge.
