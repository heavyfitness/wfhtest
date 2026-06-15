# WFH Connect Lead Scanner — Runbook

Pipeline repo: `wfh-connect-site-in-a-box/wfhtest/wfhtest/`  
Branch: `claude/great-pasteur-8i5xcv`  
Last verified: 2026-06-13

---

## Pre-flight checklist (do once before first live run)

### 1. Fix the Authorization header on Hostinger (REQUIRED before any live run)

Hostinger/LiteSpeed strips the `Authorization` header from incoming HTTP requests by default,
which breaks WordPress Application Password auth. Add these two lines **at the very top** of
your site's `.htaccess` (before `# BEGIN WordPress`):

```apache
RewriteEngine On
RewriteRule .* - [E=HTTP_AUTHORIZATION:%{HTTP:Authorization}]
```

**How to get there:**  
Hostinger hPanel → Files → File Manager → navigate to `public_html/` → right-click `.htaccess`
→ Edit. Save. That's it.

Until this is done, the pipeline's WordPress writes will fail with `HTTP 401 rest_cannot_create`
even with valid credentials.

### 2. Install the mu-plugin (REQUIRED for RankMath SEO meta)

WordPress only exposes RankMath's meta fields over REST when they're explicitly registered.
The repo ships a tiny must-use plugin to do this:

1. Hostinger File Manager → `public_html/wp-content/` → create folder `mu-plugins` (if it doesn't exist).
2. Upload `wfhtest/mu-plugins/wfh-rest-meta.php` into that folder.

Without it the pipeline still publishes posts — it just loses the RankMath focus keyword,
SEO title, and meta description fields (it logs a warning and retries without them).

### 3. Verify .env credentials

File: `wfhtest/.env`

```
WP_URL=https://thewfhconnect.com
WP_USERNAME=benmusick54gmail-com   # your WordPress login username
WP_APP_PASSWORD=xxxx xxxx xxxx xxxx xxxx xxxx   # from Users → Profile → Application Passwords
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-4-6
DEFAULT_POST_STATUS=draft          # NEVER change to "publish" without manual review
```

**To create / rotate the Application Password:**
WP Admin → Users → Profile → scroll to "Application Passwords" → name: `wfh-pipeline`
→ Add New Application Password → copy the password (spaces included).

### 4. Smoke test (offline, no API cost)

```bash
cd wfhtest/wfhtest
DB_PATH=/tmp/wfh_test.db LOG_FILE=/tmp/wfh_test.log \
  PYTHONPATH=. python3 -m pytest -v
```

All 37 tests must pass. If any fail, do not run live.

---

## Running the pipeline

### Dry run (safe — calls LLM but never touches WordPress)

```bash
cd wfhtest/wfhtest
DB_PATH=/tmp/wfh_dry.db LOG_FILE=/tmp/wfh_dry.log \
  PYTHONPATH=. python3 -m wfh_pipeline \
    --source csv --csv-path sample_leads.csv \
    --dry-run --limit 4 -v
```

Prints the full generated post for each lead. Nothing is written to WordPress.
The LLM is called (~$0.01–$0.05 per lead). Leads are NOT marked as posted.

### RSS lead fetch (manual step before each batch)

Fetches fresh leads from RemoteOK and We Work Remotely and saves them with `verified=FALSE`:

```bash
cd wfhtest/wfhtest
python3 fetch_rss_leads.py --limit 10 --output rss_leads.csv
```

**Ben reviews `rss_leads.csv` before every run:**
- Delete rows that aren't entry-level or don't fit the audience
- Update `apply_url` to the **company's own careers page** (not the WWR/RemoteOK listing URL)
  — this is the direct-apply pledge on the site
- Add pay/requirements if visible on the company's page
- Set `verified=TRUE` on rows that pass your manual check

Only rows with `verified=TRUE` are ever processed.

### Draft run (standard daily operation — after .htaccess fix is in place)

```bash
cd wfhtest/wfhtest
DB_PATH=/tmp/wfh_live.db LOG_FILE=/tmp/wfh_live.log \
  PYTHONPATH=. python3 -m wfh_pipeline \
    --source csv --csv-path rss_leads.csv \
    --status draft --limit 5 -v
```

This calls the LLM, runs the QC gate, and creates **draft posts** in WordPress. Nothing publishes.
Posts land in WP Admin → Posts → Drafts for your review before you click Publish.

The dedup table (SQLite at `DB_PATH`) prevents the same lead from being processed twice
(keyed by `sha256(apply_url)[:16]`).

---

## Review gate — check every draft before publishing

Open WP Admin → Posts → Drafts. For each draft:

| Check | Must pass |
|-------|-----------|
| Apply link goes to the company's own site | Direct URL, no affiliate wrapper, no WWR/RemoteOK redirect |
| Pay range is accurate | Matches what's on the company's listing |
| No income claims | Body must not say "earn up to", "make $X", "income potential" |
| Scam warning paragraph present | Generator always includes it — verify it wasn't trimmed |
| FTC disclosure at bottom | "Affiliate disclosure: some links…" — must be present |
| Verify disclaimer at bottom | "always do your own due diligence…" — must be present |
| JSON-LD visible in post source | View Source → search `application/ld+json` |
| No MLM / opportunity framing | This pipeline is for job leads only; never mention LegalShield here |

After review: **Publish** or **Delete**. Never leave drafts accumulating without action.

---

## Lead rules (hard rules, not suggestions)

1. **verified=TRUE only** — if you haven't clicked the apply link yourself, don't mark it TRUE.
2. **Direct apply URL** — must go to the company's own site or a major ATS (Greenhouse, Lever,
   Workday, Taleo). Not WWR, not Indeed, not RemoteOK.
3. **No fee-to-apply roles** — if the listing mentions paying for training, equipment, or a
   background check, delete the lead.
4. **No commission-only or MLM** — any listing that mentions "business opportunity", "build a team",
   or "unlimited income" is disqualified.
5. **English only** — non-English listings confuse the LLM prompt and produce poor content.
6. **US-based or global** — if a role requires specific non-US residency, flag it; the JSON-LD
   defaults to USA applicants.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `HTTP 401` on all WP calls | Add the `.htaccess` RewriteRule above |
| `HTTP 401` only on POST (not GET) | Same fix — GET is public, POST requires auth |
| `WordPress rejected the post meta` | Install `mu-plugins/wfh-rest-meta.php` |
| `Config error: Missing required settings` | Check `.env` — all required keys must be non-empty |
| LLM call times out | Reduce `--limit`; each lead takes ~15–20s |
| Post dedup skips a lead you want re-run | Delete the row from the SQLite DB: `sqlite3 $DB_PATH "DELETE FROM posted WHERE lead_hash='<hash>'"` |
| RSS fetch returns no results | Check internet access; feeds move occasionally — update URLs in `fetch_rss_leads.py` |
| Posts publish but Google for Jobs ignores them | Check JSON-LD with Google's Rich Results Test; leads need real descriptions/requirements |

---

## Proposed schedule (not yet enabled — review and approve before activating)

Run once each weekday morning. The pipeline fetches your pre-reviewed `rss_leads.csv` and
creates up to 5 drafts. You review in wp-admin and publish what passes.

**Cron line (for reference — do NOT add until you're ready to activate):**

```cron
# WFH Connect lead scanner — runs 7:15am ET weekdays, produces drafts for review
15 7 * * 1-5  cd /path/to/wfhtest/wfhtest && \
  DB_PATH=/home/user/wfh-pipeline.db \
  LOG_FILE=/home/user/wfh-pipeline.log \
  PYTHONPATH=. python3 -m wfh_pipeline \
    --source csv --csv-path rss_leads.csv \
    --status draft --limit 5 >> /home/user/wfh-cron.log 2>&1
```

**n8n alternative:** Schedule Trigger (weekdays 7:15am ET) → Execute Command node with the
same command. n8n gives you retry visibility and error notifications.

**Recommendation:** run manually for 2–3 weeks first to calibrate the LLM output and
your review process, then activate the cron once you trust the quality.

---

## Cost estimate

- Claude Sonnet 4.6: ~$0.01–$0.05 per lead post (input ~800 tokens, output ~1200 tokens)
- 5 leads/day × 5 days/week × 4 weeks = 100 posts/month ≈ **$1–$5/month**
- Anthropic API free tier covers early testing

---

## File locations

| File | Purpose |
|------|---------|
| `wfhtest/.env` | Secrets (never commit to git) |
| `wfhtest/wfhtest/sample_leads.csv` | 4 verified test leads + 1 unverified scam (for dry runs) |
| `wfhtest/wfhtest/rss_leads.csv` | Generated by `fetch_rss_leads.py` — your review queue |
| `wfhtest/wfhtest/rss_verified_5.csv` | Example: 5 leads ready to run |
| `wfhtest/mu-plugins/wfh-rest-meta.php` | Upload to `wp-content/mu-plugins/` on Hostinger |
| `/tmp/wfh_live.db` | SQLite dedup state (or set DB_PATH to a persistent path on your server) |

---

## Direct-apply pledge & lane routing

The pipeline classifies every lead's apply URL as `direct`, `aggregator`, or `unknown`
and routes it to the appropriate publishing lane. See README.md for the full explanation.

### Lane CLI flag

| Flag | What it does |
|---|---|
| `--lane direct` (default) | Only process leads with ATS-direct URLs |
| `--lane aggregator` | Only process aggregator/job-board leads |
| `--lane all` | Process every verified lead regardless of URL type |

Always use `--lane direct` for hands-free automated runs. Use `--lane aggregator` during
a manual review session (see below).

### Aggregator review session (weekly)

Run this to collect unresolved aggregator drafts:

```bash
cd wfhtest/wfhtest
DB_PATH=/tmp/wfh_live.db LOG_FILE=/tmp/wfh_live.log \
  python -m wfh_pipeline \
  --source csv --csv-path rss_leads.csv \
  --lane aggregator --status draft --limit 10
```

In WordPress -> Posts -> Drafts, find posts tagged as aggregator leads and:
1. Click through to the board listing
2. Find the employer's real apply URL
3. If it points to a known ATS (Greenhouse, Lever, Workday, etc.) -> update and publish
4. Otherwise -> leave as draft or delete

### Optional: auto-resolve source links

```bash
python -m wfh_pipeline --lane aggregator --resolve-source-links --status draft --limit 10
```

Follows each aggregator listing's outbound link. Upgrades the apply URL only when it
resolves to a known-direct ATS domain. The upgrade is logged so you can verify it.

### QC backstop

The QC gate hard-rejects any post that:
- Uses "apply directly" or "direct apply" language AND
- Has an apply_url classified as aggregator or unknown

If you see `skipped_qc: body uses direct-apply framing` in the logs, the LLM drifted
back to direct-apply language for an aggregator lead. Fix by re-running with
`--resolve-source-links`, or manually update the lead's apply_url to the real ATS link.

### Extending domain lists via .env

```dotenv
EXTRA_DIRECT_DOMAINS=jobs.mycompany.com,*.myats.io
EXTRA_AGGREGATOR_DOMAINS=jobs.internal-board.example.org
ALLOW_AGGREGATOR_AUTOPUBLISH=false
```

---

## Multi-source intake (Part 5)

### Source overview

The pipeline pulls from multiple lead sources in a single run, deduplicated via SQLite. Each source type has a fixed `source_trust` that feeds into lane routing:

- **ATS sources** (`greenhouse.py`, `lever.py`) — `source_trust="direct"`, enter the direct lane. Apply URLs go straight to Greenhouse/Lever job boards.
- **RSS sources** (`rss.py`) — `source_trust="aggregator"`, enter the aggregator lane. Posts as draft by default, receive "View on {Board}" honest labeling.
- **Job API** (`job_api.py`) — `source_trust="unknown"`, URLs classified per-lead by the existing classifier. Runs multiple search queries in one call (configured via `JOB_API_QUERIES` in `.env`), deduplicates results by job ID.

### Configuration: boards.yaml

`boards.yaml` lives at the repo root. Edit it to add/remove ATS board tokens:

```yaml
greenhouse:
  - anthropic      # add any token from boards.greenhouse.io/<token>/jobs
  - your-company   # ... etc

lever: []          # add slugs from jobs.lever.co/<slug>

rss:
  remoteok: true        # set false to disable
  weworkremotely: true
  remotive: true
```

### Daily run commands

The **recommended daily command** uses `--source-group all` so both the ATS direct lane (Greenhouse boards) and the Job API entry-level lane run together:

```bash
# ── Recommended daily run ────────────────────────────────────────────────────
# Sources: all Greenhouse boards + Job API entry-level queries
# Lane: direct only (ATS links) — aggregator leads skip to draft automatically
# Relevance filter: ON — skips senior/high-experience roles before LLM call
python -m wfh_pipeline run \
  --source-group all \
  --lane direct \
  --status draft \
  --limit 5

# With scheduling (spread across 08:00, 12:00, 16:00)
python -m wfh_pipeline run \
  --source-group all \
  --lane direct \
  --status draft \
  --schedule

# ── Dry-run preview (no WordPress writes) ───────────────────────────────────
python -m wfh_pipeline run --source-group all --lane direct --dry-run --limit 5 -v

# ── Weekly aggregator review — RSS feeds, draft only ────────────────────────
python -m wfh_pipeline run \
  --source-group aggregator \
  --lane aggregator \
  --status draft \
  --resolve-source-links \
  --limit 10

# ── Debug / triage — bypass all filters ─────────────────────────────────────
python -m wfh_pipeline run --source-group all --lane all --dry-run --no-relevance-filter
```

**Relevance filter** is on by default. It:
1. Rejects titles matching exclude keywords (senior, staff, principal, engineer, manager, …)
2. Rejects descriptions stating 3+ years of experience required
3. Requires at least one include keyword (customer service, call center, data entry, …)

Add `--no-relevance-filter` to bypass for a single run. Tune keywords in `.env` with `RELEVANCE_INCLUDE_KEYWORDS`, `RELEVANCE_EXCLUDE_TITLE`, `RELEVANCE_REJECT_EXPERIENCE_YEARS`.

### Adding a new Greenhouse board token

1. Find the token: go to the company's Greenhouse job board URL — it is `boards.greenhouse.io/<token>/jobs`
2. Verify it returns remote jobs: `curl "https://boards-api.greenhouse.io/v1/boards/<token>/jobs?content=false" | python3 -m json.tool | grep -i remote`
3. Add the token to `boards.yaml` under `greenhouse:`
4. Run a dry-run to confirm leads appear: `python -m wfh_pipeline run --source-group direct --lane direct --dry-run`

### Relevance / quality filter

The filter (in `wfh_pipeline/relevance.py`) runs **after** lane routing and **before** LLM generation. It skips titles that:
- match any `RELEVANCE_EXCLUDE_TITLE` keyword (default: senior, staff, principal, engineer, manager, director, vp, etc.)
- contain **no** `RELEVANCE_INCLUDE_KEYWORDS` match (default: customer service, data entry, chat support, virtual assistant, entry level, etc.)

Configure in `.env`:

```dotenv
# Leave blank to use built-in defaults (recommended)
RELEVANCE_INCLUDE_KEYWORDS=
RELEVANCE_EXCLUDE_TITLE=

# Override to keep only billing/collections roles, for example:
# RELEVANCE_INCLUDE_KEYWORDS=billing,collections,accounts receivable
# RELEVANCE_EXCLUDE_TITLE=senior,director,manager

# Disable include-keyword requirement (only exclude list applies):
RELEVANCE_PERMISSIVE=false
```

### Job API (JSearch / RapidAPI)

1. Sign up at [rapidapi.com](https://rapidapi.com) → subscribe to **JSearch** (free tier).
2. Set in `.env`:

```dotenv
ENABLE_JOB_API=true
JOB_API_KEY=your-rapidapi-key

# Multi-query: each query runs independently; results combined + deduped by job ID
JOB_API_QUERIES=remote customer service no experience,remote data entry no experience,remote chat support entry level,virtual assistant remote,remote customer support associate

JOB_API_MAX_RESULTS=20   # per query
```

3. The relevance filter runs on Job API results the same as ATS/RSS leads.
4. BPO employers (Concentrix, TTEC, etc.) are **not** on Greenhouse — Job API is the primary source for entry-level CS leads from these employers.

### Lever board tokens

Lever's v0 public postings API (`api.lever.co/v0/postings/<slug>`) is deprecated for many companies and returns 404. The source logs a warning and skips 404 boards — no action needed. To find valid slugs, look for `jobs.lever.co/<slug>` in a company's job board URL and test with `curl`.

### WordPress auth

- **WP_USERNAME**: must be your WP account **email address** (`benmusick54@gmail.com`), not the username slug. The slug (`benmusick54gmail-com`) does not work with REST API Basic Auth.
- **WP_APP_PASSWORD**: generate in WP Admin → Users → Your Profile → Application Passwords. Spaces in the generated password are fine — the client strips them automatically.
- **Cloudflare**: the client sets a browser-like User-Agent automatically to pass Bot Fight Mode. No manual config needed.
