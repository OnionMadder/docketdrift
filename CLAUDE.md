# DocketDrift — notes for Claude sessions

Survival kit for any Claude session working on this repo. Read once,
re-read whenever a recurring gotcha bites. The goal of this document is
to make the next session productive within the first 5 minutes.

## Where things stand right now

Three states live, all on subdomains of `docketdrift.com`:

| State | Subdomain | Opinions | Judges | Panel votes | Date range | Notes |
|---|---|---|---|---|---|---|
| MN (flagship) | `mn.docketdrift.com` | 60,375 | 124 | 9,914 | 1851 to current | Full statute graph (124K cites), 21K tag suggestions, full editorial pipeline |
| NH (beta) | `nh.docketdrift.com` | 20,693 | 69 | 17,032 | Through 2025-11-07 | CL lag; courts.nh.gov is Akamai-blocked, can't scrape directly |
| AZ (beta) | `az.docketdrift.com` | 38,066 | 133 | 40 → growing | Through 2026-06-05 | 7 AZ Supreme justices have full bios + photos via azcourts.gov scraper. AZ Supreme byline-extractor pattern needs work (low coverage) |

The apex `docketdrift.com` shows three live state tiles. About page
documents the editorial-review posture + the "we do not generate text"
anti-hallucination disclosure. A dedicated `/how-we-differ/` page
expands on the AI-tools distinction.

**Right after this session:** see *Open work, ranked* below.

## When asked to bring a new state online

Read `docs/STATE_ROLLOUT.md` first. It's a 12-phase end-to-end runbook
distilled from the MN/NH/AZ rollouts with explicit commands and gates
for each phase. Don't improvise a new sequence — the runbook captures
the failure modes (CL API 21-hour-cooldown trap, alias-cert timing,
parser scope split, Akamai-blocked court sites) and the universal-vs-per-state
matrix.

## The repo and its shape

- Django 5.2 + PyMySQL + MariaDB 11.7 on NFSN. Local dev defaults to SQLite.
- Frontend = Django templates + minimal JS + HTMX where it earns its keep
  (only the bulk tag-review admin uses it). No SPA, no build pipeline.
- State subdomain (`mn.docketdrift.com`, ...) is resolved to a `State` row
  by `opinions/middleware.py:StateRouterMiddleware` and attached as
  `request.state`. Apex has `request.state = None`.
- Before the state router, `opinions/middleware.py:CrawlerBlockMiddleware`
  hard-blocks aggressive SEO crawlers (SemrushBot, AhrefsBot, etc.) with a
  429 BEFORE any view or DB query runs.
- Per-state landing template is `opinions/templates/opinions/state_landing.html`,
  rendered by `views.home()` when `request.state` is set. Auto-disclosure
  banner fires when the most recent opinion is > 30 days old.
- Production deploys via `git push origin main` then SSH-driven NFSN-side
  pull + `nfsn -j signal-daemon gunicorn TERM`. Onion prefers I drive the
  full deploy loop (her SSH config has the `docketdrift` alias).

## Recurring gotchas — DO NOT MAKE THESE AGAIN

### Django template comments are single-line only

`{# this is fine #}` works **only on a single line**. Multi-line `{# ... #}`
renders as raw page text — Onion has caught it bleeding into the apex
hero multiple times. **Always** use `{% comment %} ... {% endcomment %}`
for any block longer than a single line.

This is now enforced by `opinions/checks.py:opinions.E001`, a deploy-blocking
Django system check that walks every `.html` under `opinions/templates/` and
the TEMPLATES["DIRS"] entries. Any multi-line `{# #}` is an `Error` — `manage.py
check` exits non-zero, `runserver` refuses to start, and the NFSN gunicorn
boot aborts. **Trust the check.** If your CI fails on `opinions.E001`, fix
the comment — don't disable the check.

```django
{# good: one-liner annotation #}
{% comment %}
good: multi-line block,
spanning several lines,
WILL NOT render to the user.
{% endcomment %}
```

### Don't use nested f-strings with bracket-indexed lookups

`f"{row['n']}"` is fine in Python 3.12+ but **NFSN runs Python 3.11**, where
nested-string brackets inside an f-string are a `SyntaxError`.
Use `%`-formatting or `.format()` for dict-key lookups in scripts that
will run on production.

### NumPy on NFSN's FreeBSD is broken

Both numpy 2.x and 1.x ship FreeBSD wheels missing `cblas_sdot` from the
system BLAS — `import numpy` fails at runtime. **Do not add numpy as a
dependency.** The cosine math in `suggest_tags` uses MariaDB's native
`VEC_DISTANCE_COSINE` instead; the same primitive powers semantic search.

If a new task wants matrix math, lean on MariaDB VECTOR + raw SQL, or pull
small enough datasets that pure-Python loops are fine.

### Court.short_label is a Python @property

Not a database column. You CANNOT `.values("court__short_label")` or
`.annotate(...)` against it. Group by `court_id` and resolve to Court
instances in Python.

### MariaDB drops idle connections during long sleeps

Any management command that does a 30-60s `time.sleep()` between DB writes
(CL rate-limit cooldowns, Voyage API backoffs) needs retry-with-reconnect
on the write side. Pattern lives in `embed_opinions` and `ingest_court`.
Catch `OperationalError (2013, "Lost connection to MySQL server during
query")`. Use bare `BaseException` (not `Exception`) — NFSN's SSL socket
raises `KeyboardInterrupt` on EINTR during long sleeps.

Settings.py has `CONN_HEALTH_CHECKS = True` so the gunicorn-side connection
pool pings each pooled connection before reusing it. That fixed a class of
500s on judge pages under bulk-load contention.

### Defer raw_text + html_content on list-style queries

`Opinion.raw_text` and `Opinion.html_content` are TEXT columns holding
50-100KB each. Any list-style query (statute_detail, judge_detail's
recent_opinions, tag_detail, opinion_list) must `.defer("raw_text",
"html_content")` or pulling 50 rows blows past gunicorn's timeout.

Only `opinion_detail` actually renders `raw_text`.

### StatuteCitation default ordering bleeds into `.distinct()`

`StatuteCitation.Meta.ordering = ["opinion", "text_offset"]` silently joins
back to Opinion when used in a `.values_list().distinct()` chain. Always
chain explicit empty `.order_by()`:

```python
StatuteCitation.objects.filter(...).order_by().values_list("opinion_id", flat=True).distinct()
```

### Pre-resolve court IDs to skip the join

Bad (slow on 120K-row corpus):
```python
qs = Opinion.objects.filter(court__state=state)
```
Good:
```python
court_ids = list(state.courts.values_list("id", flat=True))
qs = Opinion.objects.filter(court_id__in=court_ids)
```
Court table is small (a handful of rows per state); resolving in Python
first turns a JOIN+COUNT(*) into an FK-index lookup. `opinion_list` does
this; `statute_detail` does an equivalent trick with `id__in` over a
pre-materialized list.

### Similar-opinions semantic search needs a date_cutoff

`VEC_DISTANCE_COSINE` over the state's full corpus is O(N) because the
embedding column allows NULL (MariaDB VECTOR INDEX requires NOT NULL).
At 60K rows the scan was fast; at 120K+ it blew past 20s and saturated
gunicorn's single worker. `semantic.similar_to_opinion` now caps the
candidate set to a 3-year window around the source opinion's
release_date. Don't remove this cap without first migrating embeddings
to NOT NULL + creating the actual VECTOR index.

### NFSN proxy cache can serve stale 503 for minutes

After fixing a slow/broken endpoint and restarting gunicorn, the public URL
may still return cached 503 for a few minutes. Bypass with the internal
gunicorn address from `/home/logs/daemon_gunicorn.log` (`Listening at: ...`)
to confirm the fix is real:

```bash
ssh docketdrift 'curl -sS -H "Host: mn.docketdrift.com" http://10.0.175.75:8000/some/path/'
```

### NH/AZ-COA court sites are Akamai-blocked

- `courts.nh.gov` returns 403 to any non-residential IP for every path,
  including sitemap.xml. WebFetch and curl from server-side both fail.
- `coa1.azcourts.gov` is a DotNetNuke site, more complex than the AZ
  Supreme single-page roster.
- `appeals2.az.gov` is yet another DNN host, different paths.

Workaround for these: Playwright on Onion's local Windows box (residential
IP isn't blocked), drop output PDFs/JSON to a watched folder, NFSN-side
process picks them up. See task #41.

### Local SSH-jail egress to public docketdrift.com is flaky

Curls from inside `ssh docketdrift '...'` to `https://docketdrift.com/`
sometimes time out due to NFSN's intra-rack routing for outbound HTTPS to
the same hostname. Use the internal gunicorn address (`http://10.0.175.75:8000`)
with `-H "Host: mn.docketdrift.com"`, OR ask Onion to refresh her
browser, OR check the daemon access log to confirm real traffic is landing.

## Deployment cheat sheet

```bash
# Pull + restart (code + middleware + settings)
ssh docketdrift 'cd /home/private/docketdrift && git pull && nfsn -j signal-daemon gunicorn TERM'

# Pull + migrate + restart (new migration)
ssh docketdrift 'cd /home/private/docketdrift && git pull && source .venv/bin/activate && python manage.py migrate && nfsn -j signal-daemon gunicorn TERM'

# Pull + collectstatic + restart (static asset changes)
ssh docketdrift 'cd /home/private/docketdrift && git pull && source .venv/bin/activate && python manage.py collectstatic --noinput && nfsn -j signal-daemon gunicorn TERM'

# Tail daemon log
ssh docketdrift 'tail -f /home/logs/daemon_gunicorn.log'

# Show MariaDB processlist (debug slow queries)
ssh docketdrift 'cd /home/private/docketdrift && source .venv/bin/activate && python -c "
import django, os
os.environ.setdefault(\"DJANGO_SETTINGS_MODULE\", \"docketdrift_site.settings\")
django.setup()
from django.db import connection
with connection.cursor() as c:
    c.execute(\"SHOW PROCESSLIST\")
    for r in c.fetchall():
        print(r[4], r[5], (r[7] or \"\")[:80])
"'
```

## Management commands reference

| Command | Purpose | State-aware? | Idempotent? |
|---|---|---|---|
| `migrate` | Standard Django migrations | global | yes |
| `ingest_court <cl_id> [--since YYYY-MM-DD]` | Pull recent opinions via CL REST API | per court | yes |
| `cron-ingest.sh` (`/home/private/docketdrift/cron-ingest.sh`) | Weekly wrapper; auto-discovers live courts via `Court.objects.filter(state__is_live=True)` | auto | yes |
| `load_cl_bulk --subset-dir <dir> --state <CODE>` | Load filtered CL bulk-dump CSVs | yes | yes |
| `scripts/cl_bulk_filter.py --state <CODE>` | Filter local CL bulk dump (~50GB sweep) to one state | yes | yes |
| `embed_opinions` | Voyage embeddings on raw_text → VECTOR column | global | yes (`WHERE embedding IS NULL`) |
| `embed_tags [--force]` | Voyage embeddings of Tag.label+description | global | yes (`embedded_at` skip) |
| `suggest_tags [--rescore-all] [--limit N]` | Score opinions vs tags via VEC_DISTANCE_COSINE | global | yes |
| `extract_statutes [--force]` | Pull `Minn. Stat. § ...` cites from opinion text | **MN-only currently** | yes |
| `resolve_judges --state <CODE> [--create-missing]` | Match byline+panel to existing Judge rows; `--create-missing` mints new ones | per state | yes |
| `scrape_judges <state> [--dry-run]` | Scrape current-roster bios. Supports `mn` (mncourts.gov sitemap) + `az` (azcourts.gov MeettheJustices single page) | per state | yes |
| `reconcile_az_judges [--dry-run]` | One-shot merge of duplicate AZ Judge rows from the first scrape_judges run | AZ-specific | yes (no-op after merge) |
| `backfill_dispositions` | Parse dispositions from raw_text into `disposition` field | global | yes |
| `manage.py check` | Django system checks (incl. opinions.E001 multi-line `{# #}` guard) | global | n/a |

## Architecture: where ML appears (and where it doesn't)

The site is **explicitly not** an AI legal assistant. The About page +
`/how-we-differ/` carry this in copy; the architecture mirrors it.

**ML appears in exactly two places:**

1. **Voyage embeddings for semantic search.** `voyage-law-2`, 1024-dim,
   stored in `Opinion.embedding` (native MariaDB VECTOR column). We
   compare a query vector to opinion vectors with cosine similarity
   and return a ranked list of opinion IDs. No text is generated.
2. **Tag-suggestion candidates.** Embeddings rank tags against opinions.
   Above `TAG_SUGGESTION_AUTO_APPLY_THRESHOLD` (0.40) the tag is
   auto-applied AND marked `AUTO_APPLIED` for transparent audit; below
   it the suggestion surfaces in the HTMX review queue at
   `/admin/opinions/tag-review/` for human accept/reject.

**Everything else is deterministic regex extraction.** Case number,
release date, disposition, panel composition, statute citations, court
breakdown, judge dossiers — all from `opinions/parsing/` or
`opinions/management/commands/`. No LLM is ever asked to synthesize,
summarize, or describe anything.

If a new task wants generative content (summaries, drafted text), STOP
and discuss with Onion first. The "no generation" posture is product
strategy, not engineering preference.

## Open work, ranked

These reflect the state of play at session-end on 2026-06-08. Tasks are
also tracked in the task list (`TaskList` tool); IDs in parens.

### Priority 1 — close NH/AZ gaps

1. **Embed NH+AZ opinion corpus** (#42). 58K opinions loaded but
   unembedded; semantic search returns nothing for them. Just kick
   `python manage.py embed_opinions` and let it run overnight on
   Voyage's 60 RPM free tier (~10-13 hr wall clock, ~$3 total).
2. **Fix AZ Supreme Court byline format** (#44) in `resolve_judges`. Only
   40 panel votes across 38K AZ opinions vs 17K for NH. AZ Court of
   Appeals format works (`Presiding Judge X delivered ... in which Judge Y
   joined`); AZ Supreme is different. Sample real AZ Supreme texts and add
   a third pattern.
3. **Re-run `resolve_judges --state AZ --create-missing`** after the
   scraper added 5 new Judge rows (Bolick, Beene, Lopez, King, Montgomery)
   that have 0 panel votes. The byline pattern should now match them
   against real opinions.
4. **Fix Cruz's full_name in the AZ Judge admin** — still says just "Cruz"
   from byline-learning. Should be "Maria Elena Cruz" (the scraper
   correctly captured it but the merge preserved the canonical row's
   name field).

### Priority 2 — close coverage gaps

5. **Playwright-on-Windows scrapers** (#41) for the three Akamai-blocked
   judge sites:
   - NH Supreme (`courts.nh.gov`)
   - AZ COA Div 1 (`coa1.azcourts.gov`)
   - AZ COA Div 2 (`appeals2.az.gov`)
   Output drops PDFs/JSON to a watched folder; NFSN-side `scrape_judges`
   ingests them. This also unblocks the broader "NH opinions newer than
   2025-11-07" problem if NH publishes their PDFs at a stable URL.
6. **Generalize `extract_statutes`** (#43) to NH (RSA) + AZ (A.R.S.).
   Split `opinions/parsing/statutes.py` into per-state modules + a
   dispatcher; update the command to accept `--state`. Phase 5 of
   STATE_ROLLOUT.md.

### Priority 3 — the brief's Phase 1D + 2

7. **Triage MN tag suggestions** (#39). 20,134 pending in
   `/admin/opinions/tag-review/`. HTMX UX designed for 100+/session.
   Every review recalibrates the precision/recall knee for the
   suggest_tags thresholds. Each accepted tag also makes the corpus
   richer for downstream analysis.
8. **Phase 1D LLM holding extraction** from session-brief.md. Optional
   v2. Claude Haiku decomposes each opinion into `OpinionHolding` rows
   (statute_cited + holding_direction + holding_text). One-time ~$90
   batch. Unlocks per-holding semantic search + per-issue judge voting.
   **Spend confirmation required from Onion before running.**

### Priority 4 — hardening / polish

9. **VECTOR INDEX migration** so `similar_to_opinion` doesn't need the
   3-year date_cutoff workaround. Requires migrating
   `opinions_opinion.embedding` to NOT NULL (which means filling in the
   nulls first via #1).
10. **FAQ schema on About** (P1-11 from session-brief).
11. **State-router middleware lookup cache** (P0-2). Currently
    `_resolve_state` hits the DB on every request. Cache by Host header
    for the duration of a worker's lifetime.
12. **Cloudflare in front of NFSN** (P0-6). Deferred because NFSN doesn't
    allow nameserver changes — needs alternate registrar setup.

## When to ASK

- Adding NEW dependencies — flag the FreeBSD risk before installing.
  Specifically: numpy, scipy, pandas, anything with C extensions.
- Touching `settings.py` middleware order or DB config — restart is
  required; confirm the timing matches what's in flight.
- Spending money: any LLM-extraction batch job (Claude, GPT) — show the
  cost estimate before kicking off. Voyage embedding is cheap (~$0.12/M
  tokens) but still flag costs > a few dollars.
- Bringing a new state online — go through `docs/STATE_ROLLOUT.md`
  phase-by-phase, not improvised.
- Anything that produces generative legal text — full stop, this would
  break the product's anti-hallucination posture.

## When NOT to ask

- Running idempotent management commands again — safe by design.
- Fixing template comment bleed — just fix it; system check will block
  the deploy anyway.
- Restarting gunicorn after a deploy — that's expected.
- Re-running `resolve_judges` or `extract_statutes` — idempotent.
- Re-running the bulk filter or load_cl_bulk — idempotent.

## Key files added or substantially changed this session

For orientation when looking at the repo with fresh eyes:

- `docs/STATE_ROLLOUT.md` (new) — 12-phase per-state runbook
- `opinions/checks.py` (new) — opinions.E001 system check
- `opinions/middleware.py` (extended) — CrawlerBlockMiddleware
- `opinions/scrapers/az_supreme.py` (new) — azcourts.gov scraper
- `opinions/admin_views.py` (new) — HTMX tag-review surface
- `opinions/parsing/statutes.py` (new) — Minn. Stat. extractor
- `opinions/templates/opinions/how_we_differ.html` (new) — anti-AI page
- `opinions/templates/opinions/statute_detail.html` (new)
- `opinions/templates/opinions/admin/tag_review*.html` (new)
- `opinions/templates/admin/change_form_object_tools.html` (override) —
  "View on site" opens in new tab
- `opinions/management/commands/extract_statutes.py` (new)
- `opinions/management/commands/embed_tags.py` (new)
- `opinions/management/commands/suggest_tags.py` (new)
- `opinions/management/commands/reconcile_az_judges.py` (new) — one-shot
- `opinions/management/commands/resolve_judges.py` (extended) — generic
  byline fallback + `--create-missing`
- `opinions/management/commands/scrape_judges.py` (extended) — AZ branch
- `scripts/cl_bulk_filter.py` (parametrized) — `--state` + `--court-ids`
- `cron-ingest.sh` (rewrote) — auto-discovers live courts
- `opinions/models.py` — added `StatuteCitation`, `TagSuggestion`,
  `Tag.embedding`, `Tag.embedded_at`, `get_absolute_url` on Judge +
  Opinion (powers admin "View on site"), AZ entries in
  `Court.short_label`
- `docketdrift_site/settings.py` — CONN_MAX_AGE, CONN_HEALTH_CHECKS,
  GZipMiddleware, security headers, fail-loud SECRET_KEY guard,
  CrawlerBlockMiddleware in MIDDLEWARE list, TAG_SUGGESTION_* thresholds
- `requirements.txt` — explicit "no numpy" comment after the FreeBSD
  BLAS discovery
- `opinions/templates/opinions/about.html` — hallucination disclosure +
  NH/AZ status refresh
- `opinions/templates/opinions/state_landing.html` — coverage-note banner

Migrations added this session: 0020_statutecitation,
0021_tag_embedded_at_tag_embedding_tagsuggestion, 0022_seed_az.

## Memory: how Onion likes to work

- Address her as **Onion** (preferred) or **Kellye** (longform/legal).
  **Never Kelly.** Windows OS account `kelly` is just the login.
- She wants me to drive the NFSN deploy loop end-to-end when possible.
  SSH is set up; her preference is "show me the result, don't make me
  run commands."
- She's security-conscious. Default to secure-by-default + explain
  tradeoffs plainly. Don't put secrets in the web root.
- Kids-software philosophy: NEVER propose engagement / monetization /
  attention-extraction patterns. Doesn't apply to DocketDrift directly
  but informs tone — be candid, no growth-hacking dark patterns.
- For multi-hour work, give her clear ASK prompts (AskUserQuestion) with
  the recommended option clearly marked. Then go execute.
- When she says "keep going," she means "close the next gap without
  asking me again until you need a real decision."
