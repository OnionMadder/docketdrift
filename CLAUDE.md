# DocketDrift — notes for Claude sessions

Survival kit for any Claude session working on this repo. Read once,
re-read whenever a recurring gotcha bites. The goal of this document is
to make the next session productive within the first 5 minutes.

## Where things stand right now

(Numbers as of session-end 2026-06-15.)

Three states live, all on subdomains of `docketdrift.com`:

| State | Subdomain | Opinions | Embedded | Judges | Panel votes | Statute cites | Date range |
|---|---|---|---|---|---|---|---|
| MN (flagship) | `mn.docketdrift.com` | 60,375 | 100% | 124 | 9,914 | 124,858 | 1851 to current |
| NH (beta) | `nh.docketdrift.com` | 20,715 | **100%** | 69 | 17,161 | 79,384 | Through 2026-06-03 |
| AZ (beta) | `az.docketdrift.com` | 38,066 | ~8% & climbing | 139 | 142 | 0 (extractor ready, not yet swept) | Through 2026-06-05 |

The apex `docketdrift.com` shows three live state tiles. About page is
trimmed; the full anti-hallucination disclosure + ML-architecture
breakdown live on `/how-we-differ/`. Judge pages carry a
votes-per-year SVG chart with `?vs=<other-slug>` overlay and a
"compare" link on every co-panelist; `/compare/judges/?a=&b=` is a
side-by-side dossier with a concordance + split-decision section.

**Embed (2026-06-15 redesign):** the self-respawning daemon wrapper is
GONE. Embedding is now driven by an NFSN **scheduled task** running
`scripts/embed_tick.sh` every ~10 min. Each tick runs ONE bounded pass
(`embed_opinions --max-runtime 480`), self-exits under NFSN's wallclock
cull, and the next tick resumes via the indexed `embedding_pending`
flag. Target state lives in `.embed_state` (one USPS code). Embedding
only runs in an **overnight window** (00:00–06:00 `America/Phoenix`, gated
in `embed_tick.sh`) so it doesn't contend with daytime crawler traffic —
outside the window each tick is a no-op (a manual `embed_opinions` run
bypasses the gate). A
single-flight `flock` prevents overlap; `embed_opinions` raises (non-zero
exit → NFSN emails) on any failure and rewrites a `.embed_progress`
beacon each batch. `heartbeat.sh` is now a pure alerter — stale beacon
with pending > 0 → email. No wrapper, no `.embed_expected`/`.embed_last_exit`
sentinels, no resurrect logic. See *Deployment cheat sheet* below. NH
finished cleanly on 2026-06-14; AZ in progress.

**Future feature work** is scoped in `docs/ROADMAP.md` — Phases 13-21
covering attorney extraction, citation treatment graph, holdings,
smart alerts, brief cite-checker, firm networks, opinion diff, and a
public read API. Numbering picks up where `STATE_ROLLOUT.md` (Phase
12 = weekly cron) leaves off.

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

### Function-local imports in a conditional branch → UnboundLocalError

A `from datetime import timedelta` (or any import/assignment) placed
**inside an `if` branch** makes that name **function-local for the entire
function** — Python decides local-vs-global at compile time. Any *other*
code path that uses the name without having executed that branch raises
`UnboundLocalError` at runtime: not at import, not at `manage.py check`,
only when that path runs. This shipped a live 500 on bare `/opinions/` —
the no-search path used `timedelta`, but it was imported only inside the
search branch, so it was unbound on the default landing. It hid because
the search path worked and the system check passed. **Import at module
scope**, or import locally in *every* branch that uses the name. Especially
watch the filtered/default and search/no-search branch splits in
`opinion_list` and `home`.

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

### `.values("pk")` alone doesn't strip select_related from COUNT

Django's `QuerySet.count()` clones the underlying Query. On some
queryset shapes that clone preserves both the `.select_related` JOINs
AND the `ORDER BY` clause even though neither affects the count. Using
`.values("pk")` strips the SELECT field list but Django can keep the
JOIN if the queryset's Query object was built with select_related
state.

The hard fix is the chain in `opinions/paginators.py:NoJoinCountPaginator`:
```python
@cached_property
def count(self):
    cleaner = self.object_list.select_related(None).order_by()
    return cleaner.values("pk").count()
```
`select_related(None)` is the explicit reset; `order_by()` (no args)
strips the ORDER BY so the count doesn't pointlessly sort before
aggregating. Filter clauses including raw `.extra()` SQL (FULLTEXT
MATCH()) are preserved.

Used in: OpinionAdmin changelist, `opinion_list` public search,
`tag_detail` paginator. Any future paginator over a select_related'd
queryset on a big table should reach for this paginator.

### Decorator-orphan SyntaxError on helper insertion

When adding a helper function or module-level constants BETWEEN an
existing `@cache_control(...)` decorator and the view it decorates,
the decorator silently attaches to the helper, which crashes module
load with `SyntaxError: invalid syntax` (decorator on a variable
assignment) and 500s the entire site at every URL.

I hit this twice in the 2026-06-09 session — once on `judge_detail`,
once on `opinion_list` — because the editor pattern of "find the
function header, insert helper just above it" lands inside the
decorator/function pair. Move the new helper ABOVE the decorator, or
move the decorator down to immediately precede the view's `def`.

### Multi-line `{# #}` template comments — opinions.E001

Already documented above, but worth re-stating: **I broke this myself**
in the 2026-06-09 session by adding a 3-line `{# ... #}` block in
`opinion_detail.html`. The E001 check is deploy-blocking, so any
`manage.py` invocation (including `embed_opinions`) failed during
Django setup. The self-respawning embed wrapper happily logged
"Restarting in 30s" for ~48 hours while the embed never advanced.

Lessons restated:
- Use `{% comment %}{% endcomment %}` for any comment that doesn't
  fit on a single line.
- When a long-running wrapped command stops advancing for unexpected
  reasons, the first thing to check is whether the most recent commit
  introduced a system-check failure. Run `python manage.py check` on
  NFSN if in doubt.

### Pooled MariaDB connection retains "interrupted" state after KILL QUERY

After running `KILL QUERY <id>` against a stuck query, the next request
that picks up the same pooled connection from gunicorn's worker can
hit `pymysql.err.OperationalError: (1317, 'Query execution was
interrupted')` even though nothing's actively interrupted. The bad
connection stays in the pool until `CONN_MAX_AGE` (60s) expires.
During that window every request lands a 500.

If you find yourself debugging stuck queries on prod, prefer
`nfsn -j signal-daemon gunicorn TERM` over `KILL QUERY` — the worker
restart flushes the pool clean rather than leaving poisoned
connections behind.

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

### `embedding IS NULL` is unindexable; use the `embedding_pending` shadow column

`Opinion.embedding` is a raw-SQL VECTOR column (added by migration,
not declared as a Django field). MariaDB cannot index NULL-ness on
that column, so the embed_opinions hot-loop SELECT
`WHERE embedding IS NULL ...` does a full-table scan on every batch.
At low embedded-coverage on a new state (e.g. AZ at 1-5%) that scan
takes 25-30 seconds per batch -- long enough that NFSN's wallclock
supervisor culls the wrapper before it makes meaningful progress.

The 2026-06-14 fix in migration 0023 adds an indexed
`embedding_pending` BooleanField that shadows the same state. The
composite index on `(embedding_pending, court_id)` makes the batch
fetch sub-100ms regardless of corpus size. `embed_opinions` flips the
flag in the same UPDATE statement that writes the vector, so the two
fields stay consistent without a trigger.

If you ever need to migrate the `embedding` VECTOR column itself
(e.g. NOT NULL constraint for VECTOR INDEX) **set max_statement_time
to 0 inside the migration first** (see next gotcha) -- otherwise the
ALTER will run past the 25s cap.

### Long migrations trip max_statement_time = 25

`settings.py` sets `init_command: "SET SESSION max_statement_time =
25"` on every MariaDB connection. That cap is right for web requests
but too tight for any migration that touches a 240K-row opinions_opinion
table -- ALTER TABLE + CREATE INDEX both ran ~30s on 2026-06-14 and
got killed mid-statement with errno 1317 ("Query execution was
interrupted"), leaving the schema half-applied.

Fix in the migration itself, not by editing settings:

```python
operations = [
    migrations.RunSQL(
        "SET SESSION max_statement_time = 0",
        reverse_sql=migrations.RunSQL.noop,
    ),
    # ... schema ops ...
]
```

The SET only affects the migration's connection, so web traffic
continues to have the 25s ceiling. Migration 0023 ships this pattern.

### gunicorn worker recycle every ~3 minutes -> cyclical user-visible slowness

`run.sh` originally had `--max-requests 200 --max-requests-jitter
50`. With `--workers 1` and any sustained traffic (crawlers +
heartbeat + precompute cron + real users), the single worker hit
recycle every 150-250 requests -- which on the live site landed at
every 2-5 minutes. Each recycle, the new worker pays cold-DB-
connection + cold-FileBasedCache + cold-template-compile cost on its
first batch of requests, and that batch stalls 5-20 seconds. Users
hit it as "sometimes the page loads instantly, sometimes it hangs for
15-20 seconds" cycles every few minutes.

Fixed 2026-06-14 by bumping to `--max-requests 5000 --max-requests-
jitter 500` (25x). Django doesn't actually leak meaningful memory in
normal operation; the original 200 was paying for a problem we don't
have. A typical recycle now happens every ~75-90 minutes during
sustained traffic, basically invisible to real users.

Diagnose by grepping for `Worker exiting` in
`/home/logs/daemon_gunicorn.log`. If the timestamps are < 10 min
apart under any sustained load, max-requests is too aggressive.

### NFSN's ~10-minute wallclock cull on shared-hosting daemons

NFSN's shared-hosting supervisor quietly SIGKILLs long-running
daemon-style processes after roughly 10 minutes. SIGKILL is silent
(no log, trap-EXIT doesn't fire). This is policy, not load-dependent.

**Don't fight the cull — don't run daemons.** The old approach (a
self-respawning wrapper + heartbeat-resurrect + `99`-sentinel
handshake) tried to survive the cull and bred a whole class of
silent-death bugs (exit-0-on-failure, duration-based brake blind spots,
missing-sentinel stand-down). It was removed on 2026-06-15.

The replacement: `embed_opinions --max-runtime 480` makes each run
SELF-EXIT cleanly well under the cull, and an NFSN scheduled task
(`scripts/embed_tick.sh`, every ~10 min) just keeps invoking it.
A killed pass is now harmless — the `.embed_progress` beacon is written
every batch, the `flock` releases on death, and the next tick resumes.
If you ever need another long-running job on NFSN, use this pattern
(bounded command + scheduled task), NOT a resident daemon.

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

# Run a system check after a deploy (catches opinions.E001 et al.
# BEFORE the wrapped long-running commands trip on it silently)
ssh docketdrift 'cd /home/private/docketdrift && source .venv/bin/activate && python manage.py check'

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

# EMBED PIPELINE (cron-tick model, 2026-06-15). No daemon, no wrapper.
# An NFSN scheduled task runs scripts/embed_tick.sh every ~10 min; it
# reads the target state from .embed_state and runs ONE bounded pass.
# Embedding only runs OVERNIGHT (00:00-06:00 America/Phoenix, gated in
# embed_tick.sh via EMBED_START_HOUR/EMBED_END_HOUR) so it doesn't contend
# with daytime crawler traffic; outside the window each tick is a no-op.
# To widen/move the window, edit those two constants (EMBED_END_HOUR=24 =
# all day). A manual run (below) bypasses the window entirely.

# Start / switch the embedding target state (takes effect next tick):
ssh docketdrift 'echo AZ > /home/private/docketdrift/.embed_state'

# Pause embedding after the current pass (scheduled task stays registered;
# it no-ops while .embed_state is absent). To resume, set .embed_state again.
ssh docketdrift 'rm -f /home/private/docketdrift/.embed_state'

# Run a pass by hand (e.g. to watch it, or to push a state to completion
# now). --max-runtime 0 = run until done; omit it to use the 0 default.
ssh docketdrift 'cd /home/private/docketdrift && source .venv/bin/activate &&
  python manage.py embed_opinions --state AZ --max-runtime 0'

# Check progress: the beacon is "<unix_ts> <pending_remaining>".
ssh docketdrift 'cd /home/private/docketdrift &&
  echo "beacon: $(cat .embed_progress 2>/dev/null)";
  tail -3 /home/logs/embed_opinions.log'

# Is a pass running right now? (None between ticks is NORMAL.)
ssh docketdrift 'ps -axww | grep -E "embed_tick|manage.py embed_opinions" | grep -v grep || echo "(idle between ticks)"'

# REGISTER THE SCHEDULED TASK (one-time, NFSN member UI -- not scriptable):
#   Manage Site -> Scheduled Tasks -> Add:
#     Tag:      embed-tick
#     Command:  /home/private/docketdrift/scripts/embed_tick.sh
#     Schedule: every 10 minutes
# The heartbeat (separate existing task) alerts if the beacon goes stale
# with pending > 0 -- that's the only embed supervision now.
```

## Management commands reference

| Command | Purpose | State-aware? | Idempotent? |
|---|---|---|---|
| `migrate` | Standard Django migrations | global | yes |
| `ingest_court <cl_id> [--since YYYY-MM-DD]` | Pull recent opinions via CL REST API | per court | yes |
| `cron-ingest.sh` (`/home/private/docketdrift/cron-ingest.sh`) | Weekly wrapper; auto-discovers live courts via `Court.objects.filter(state__is_live=True)` | auto | yes |
| `load_cl_bulk --subset-dir <dir> --state <CODE>` | Load filtered CL bulk-dump CSVs | yes | yes |
| `scripts/cl_bulk_filter.py --state <CODE>` | Filter local CL bulk dump (~50GB sweep) to one state | yes | yes |
| `ingest_pdfs --dir <path> --state <CODE> --court <slug>` | Bulk-ingest a directory of opinion PDFs. Uses the state's registered parser to populate fields; SHA-256 dedup; optional `--no-pdf`; `--dry-run` for preview. **Used for Akamai-blocked states where CL lags.** | per state | yes (skips existing on `(court, case_number)`) |
| `embed_opinions [--state <CODE>] [--limit N]` | Voyage embeddings on raw_text → VECTOR column. `--state` restricts to one state's courts. | per state (optional) | yes (`WHERE embedding IS NULL`) |
| `embed_tags [--force]` | Voyage embeddings of Tag.label+description | global | yes (`embedded_at` skip) |
| `suggest_tags [--rescore-all] [--limit N]` | Score opinions vs tags via VEC_DISTANCE_COSINE | global | yes |
| `extract_statutes [--state <CODE>] [--force]` | Pull statute citations. Now multi-state via the `opinions/parsing/statutes.py` dispatcher (MN: `Minn. Stat.`, NH: `RSA`, AZ: `A.R.S.`). | per state (optional) | yes |
| `resolve_judges --state <CODE> [--create-missing] [--since YYYY-MM-DD]` | Match byline+panel to existing Judge rows; `--create-missing` mints new ones. Hybrid: state parser fills what it knows, generic byline extractor fills the rest. | per state | yes |
| `scrape_judges <state> [--dry-run]` | Scrape current-roster bios. Supports `mn` (mncourts.gov sitemap) + `az` (azcourts.gov MeettheJustices single page). NH/AZ-COA blocked by Akamai — needs #41 Playwright path. | per state | yes |
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

State of play at session-end 2026-06-12. Items struck through were
closed in the 2026-06-09 → 2026-06-12 session.

### Priority 1 — close NH/AZ gaps

1. ~~**Finish NH embed.**~~ ✅ Done 2026-06-14 — NH at 100%, ~$2 in
   Voyage cost. Wrapper exited 0 cleanly.
2. **Finish AZ embed.** ~33.3K opinions left as of 2026-06-15. Now runs
   via the cron-tick model (`scripts/embed_tick.sh` + `.embed_state=AZ`,
   see *Deployment cheat sheet*). **Blocked on one manual step:** the
   `embed-tick` NFSN scheduled task must be registered in the member UI
   (not scriptable). Until then ticks don't fire and the heartbeat will
   correctly alert that the beacon is stale. ETA once scheduled: ~1-2
   days wall clock at the AZ rate (large opinions, API-latency-bound);
   ~$5-6 Voyage cost.
3. **Run `extract_statutes --state AZ`** to populate AZ's A.R.S.
   citation graph. Extractor module exists (`statutes_az.py`); it just
   hasn't been swept over the AZ corpus yet. ~10-15 min on NFSN.
4. ~~Fix AZ Supreme Court byline format (#44).~~ ✅ Done — `JUSTICES X,
   Y, and Z` plural-prefix handling shipped in commit `986e14f`. AZ
   panel votes 40 → 142.
5. ~~Re-run `resolve_judges --state AZ --create-missing`.~~ ✅ Done —
   ran with the new byline regex; AZ judge count 133 → 139.
6. ~~Fix Cruz's full_name.~~ ✅ Done — manually updated to "Maria Elena
   Cruz" in admin.
7. ~~Generalize `extract_statutes` to NH (RSA) + AZ (A.R.S.) (#43).~~
   ✅ Done — `opinions/parsing/statutes.py` is now a state-dispatched
   registry with one module per state. NH statute graph went from 0 to
   79,384 cites; AZ extractor exists but hasn't been swept yet (item 3
   above).
8. ~~NH dissent detection in `resolve_judges`.~~ ✅ Done — the generic
   byline extractor now parses `<NAME>, J., dissented.` footers (the
   convention NH uses to record solo dissenters). Concordance section
   on `/compare/judges/` can now show actual split decisions on NH
   pairs.

### Priority 2 — close coverage gaps

9. **Playwright-on-Windows scrapers** (#41) for the three Akamai-blocked
   judge / opinion sites:
   - NH Supreme (`courts.nh.gov`) — current corpus is only through
     2026-06-03 because of this block; we manually scp 2026 PDFs via
     `ingest_pdfs` instead.
   - AZ COA Div 1 (`coa1.azcourts.gov`)
   - AZ COA Div 2 (`appeals2.az.gov`)
   Output drops PDFs/JSON to a watched folder; NFSN-side `scrape_judges`
   ingests them. Without this, NH stays behind CL by a quarter-or-so
   and AZ COA judge bios are missing.
10. **Backfill `reporter_cite` field on Opinion.** The NH parser already
    extracts the citation line (`2026 N.H. 1`); making it a queryable
    field unlocks paste-the-cite search on the public site (current
    statute-cite redirect handles RSA/Minn. Stat./A.R.S. but not
    reporter cites). Migration + populate command.

### Priority 3 — editorial throughput

11. **Triage MN tag suggestions** (#39). 20,161 pending in
    `/admin/opinions/tag-review/`. HTMX UX designed for 100+/session.
    Each review recalibrates the precision/recall knee for the
    `suggest_tags` thresholds. (Onion's manual work, not a coding task.)
12. **Re-run `suggest_tags` after NH embed completes.** NH's
    tag-suggestion queue is currently sparse because most NH opinions
    weren't embedded yet; once embedding finishes, run
    `python manage.py suggest_tags` to fill the queue.
13. **Phase 1D LLM holding extraction.** Claude Haiku decomposes each
    opinion into `OpinionHolding` rows (statute_cited +
    holding_direction + holding_text). One-time ~$90 batch. Unlocks
    per-holding semantic search + per-issue judge voting. **Spend
    confirmation required from Onion before running.**

### Priority 4 — hardening / polish

14. **VECTOR INDEX migration** so `similar_to_opinion` doesn't need the
    3-year date_cutoff workaround. Requires migrating
    `opinions_opinion.embedding` to NOT NULL — which means item #1 + #2
    have to finish first (no NULL embeddings left in the corpus).
15. **Search-snippet INSTR query is slow on big raw_text.** The
    `_attach_match_snippets` helper in `views.py` runs
    `INSTR(LOWER(raw_text), LOWER(?))` on each result row -- across 50
    rows it can take 5-10s under embed contention. Worth caching the
    INSTR position via a session variable or switching to MariaDB's
    MATCH SNIPPET if performance becomes a complaint.
16. **State-router middleware lookup cache** (P0-2). `_resolve_state`
    hits the DB on every request. Cache by Host header for the
    duration of a worker's lifetime.
17. **Cloudflare in front of NFSN** (P0-6). Deferred because NFSN
    doesn't allow nameserver changes — needs alternate registrar setup.
18. ~~FAQ schema on About.~~ ✅ Done in the SEO + schema.org pass
    (commit `d487247`), which also added Organization +
    GovernmentOrganization markup on judge/opinion/statute pages,
    refreshed canonical URLs, expanded sitemaps, and pulled meta-
    descriptions out of the templates.

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

For orientation when looking at the repo with fresh eyes. Items
added or meaningfully changed in the **2026-06-09 → 2026-06-15**
sessions are listed here — see the prior CLAUDE.md if you need the
2026-06-08 cut.

### Stability / supervisor (2026-06-14 sub-session)

- `scripts/embed_state_loop.sh` — **DELETED 2026-06-15.** Was the
  self-respawning daemon wrapper; replaced by the cron-tick model
  (`scripts/embed_tick.sh`). Its rapid-fail brake / `99`-sentinel /
  `--skip-preflight` machinery bred the silent-death bug class and is
  gone. See *NFSN's ~10-minute wallclock cull* gotcha.
- `scripts/embed_tick.sh` (new 2026-06-15) — thin stateless cron entry.
  Reads target state from `.embed_state`, `exec`s one bounded
  `embed_opinions --max-runtime 480` pass. No loop, no sentinels.
  Registered as an NFSN scheduled task every ~10 min.
- `scripts/heartbeat.sh` — runs every 10 min via NFSN scheduled task.
  Probes `/healthz`. **No longer supervises/resurrects the embed**
  (rewritten 2026-06-15): it only ALERTS when the `.embed_progress`
  beacon is stale with pending > 0. The resurrect/exit-code/pgrep logic
  is gone.
- `scripts/preflight.sh` (new) — pre-push check that catches
  multi-line `{# #}` template comments and decorator-orphan
  SyntaxErrors before they hit production.
- `opinions/views.py:healthz` (new) — single-`SELECT 1` health probe
  endpoint. No template, no cache, no auth.
- `opinions/paginators.py:NoJoinCountPaginator` — refined to chain
  `.select_related(None).order_by()` before counting. `.values("pk")`
  alone wasn't always enough.
- `run.sh` — `--max-requests 200 → 5000` (25x). The original setting
  was causing user-visible cyclical slowness every few minutes.
- `docketdrift_site/settings.py` — added FileBasedCache backend with
  `_cache_dir = os.environ.get("DOCKETDRIFT_CACHE_DIR")` (persistent
  across worker recycles); added DATABASES OPTIONS
  `init_command: "SET SESSION max_statement_time = 25"`; lowered
  CONN_MAX_AGE 60s → 30s to shrink the post-KILL-QUERY poison window;
  bumped explore_tags cache TTL 15min → 2hr.
- `opinions/context_processors.py:explore_tags_sized` — pre-resolved
  court_ids in the per-tag MATCH-AGAINST COUNTs so the context
  processor doesn't fire 20+ JOIN-COUNTs per templated response.

### Indexable embed flag (2026-06-15 sub-session)

- `opinions/models.py:Opinion.embedding_pending` — new BooleanField,
  default True, db_index'd via composite `(embedding_pending,
  court_id)` named `op_pending_court_idx`. Shadow of the raw VECTOR
  column's IS-NULL state, but indexable.
- `opinions/migrations/0023_opinion_embedding_pending.py` (new) — adds
  the column, the composite index, and a RunPython that backfills
  `embedding_pending = FALSE` from existing `embedding IS NOT NULL`
  state. Starts with `migrations.RunSQL("SET SESSION
  max_statement_time = 0")` so the schema ops don't trip the
  per-statement timeout from settings.
- `opinions/management/commands/embed_opinions.py` — uses
  `embedding_pending = TRUE` in both the count and batch-fetch
  SELECTs; flips the flag in the per-row UPDATE alongside the
  vector. Also raises CommandError instead of `return` on API
  exhaust so the supervisor reads a real non-zero exit code.
  Defaults bumped: `DEFAULT_BATCH 128 → 256`,
  `DEFAULT_MAX_BATCH_TOKENS 60K → 90K`.

### Earlier in the session (2026-06-09 → 2026-06-12)

### New modules / commands

- `opinions/charts.py` (new) — server-rendered SVG line-chart builder.
  Powers the votes-per-year chart on `/judge/<slug>/` and
  `/compare/judges/`. No JS chart library.
- `opinions/paginators.py` (new) — `NoJoinCountPaginator`. Drops
  `select_related` joins AND `ORDER BY` from the COUNT(*) query that
  Django's stock Paginator would otherwise inherit. Used by
  OpinionAdmin, `opinion_list`, `tag_detail`. See the gotcha section
  for why `.values("pk")` alone isn't enough.
- `opinions/parsing/nh.py` (new) — NH Supreme Court parser. Handles
  modern slip-cite format incl. associate-J / chief-C.J. / per-curiam
  bylines, plural-`Case Nos.`, citation-derived case name, "Opinion
  Issued:" date, tail-anchored disposition (incl. `So ordered`).
- `opinions/parsing/statutes_mn.py`, `statutes_nh.py`,
  `statutes_az.py` (new) — per-state extractors. `statutes.py` is now
  a thin state-keyed dispatcher.
- `opinions/management/commands/ingest_pdfs.py` (new) — bulk-ingest a
  directory of opinion PDFs. pypdf text extraction, state-parser-driven
  field population, SHA-256 dedup, optional `pdf_file` storage. Used
  for the 22 NH 2026 opinions and reusable for any future direct
  upload.

### Substantially extended

- `opinions/management/commands/resolve_judges.py` — three big extensions:
  (a) AZ-Supreme plural-prefix handling (`JUSTICES X, Y, and Z`);
  (b) hybrid extraction (use parser, fall back to generic per-field);
  (c) NH dissent footer parsing (`<NAME>, J., dissented.`) with an
  8KB tail window so dissenter lines after the concurrence line still
  match.
- `opinions/management/commands/embed_opinions.py` — `--state <CODE>`
  filter so you can finish one state's corpus before tackling another.
- `opinions/views.py` — added `judge_compare`, `_judge_stats`,
  `_concordance`, `_yearly_panel_votes`, `_attach_match_snippets`;
  extended `opinion_list` with statute-cite redirect, docket-shape
  routing, and snippet generation; `opinion_detail` passes
  `request.GET.q` to the formatter for highlight-on-arrival.
- `opinions/templatetags/opinion_text.py` — paragraph anchors
  (`[¶N]` → `id="para-N"` with self-link), optional highlight
  argument that wraps every match in `<mark>`.
- `opinions/admin.py` — TagSuggestion inline on OpinionAdmin; defer
  raw_text+html_content on the changelist; `paginator = NoJoinCountPaginator`.

### Templates

- `opinions/templates/opinions/judge_compare.html` (new) — side-by-side
  dossier with overlay chart + concordance + split-decisions table.
- `opinions/templates/opinions/_judge_compare_col.html` (new) — partial
  that renders one judge's stat block. Used twice from the parent.
- `opinions/templates/opinions/judge_detail.html` — added voting-
  trajectory chart section + "compare" link on each cohort entry.
- `opinions/templates/opinions/opinion_detail.html` — paragraph
  anchors, `?q=` highlight banner, scroll-to-first-mark script.
- `opinions/templates/opinions/state_home.html` — match-context
  snippet sub-rows under each search result.
- `opinions/templates/opinions/about.html` — first paragraph trimmed,
  hallucination Q&A still present, longer ML-architecture content
  moved to `how_we_differ.html`.
- `opinions/templates/opinions/how_we_differ.html` — receives the
  longer hallucination content.
- `opinions/templates/opinions/state_landing.html`,
  `apex.html` — SEO + schema.org pass: added WebSite + Organization
  + (per-state) Dataset JSON-LD, refreshed canonical URLs, expanded
  meta descriptions.

### Other

- `opinions/static/opinions/css/docketdrift.css` — chart card,
  concordance bar + vote chips, search-snippet rows, paragraph-anchor
  + opinion-find banner, op-para-anchor + `.opinion-body mark`.
- `docs/STATE_ROLLOUT.md` — restructured for future contributors,
  697 lines, Day 1/2/3 timeline + standalone gotcha sections (commit
  `4511c2b`).
- `/home/private/docketdrift/_embed_<state>_loop.sh` (NFSN, not in repo)
  — **removed 2026-06-15** along with `.embed_expected` / `.embed_last_exit`.
  The self-respawning-wrapper pattern is retired; embedding is a bounded
  command driven by an NFSN scheduled task (`scripts/embed_tick.sh`).

### Migrations

- `0023_opinion_embedding_pending` (2026-06-14) — adds the indexed
  shadow flag described above. RunPython backfill ran ~5:47 wall
  clock on the 240K-row table.

`Opinion.reporter_cite` is the next anticipated migration when
roadmap item #10 lands.

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
