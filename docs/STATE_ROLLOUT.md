# Bringing a new state online

End-to-end runbook for going from "state doesn't exist" to "state is live
on `<code>.docketdrift.com` with a full corpus, judge dossiers, statute
graph, and tag suggestions." Written for someone with no DocketDrift
context — every step lists files touched, gotchas observed during the
MN/NH/AZ rollouts, and a concrete pass/fail gate.

Follow the phases in order. Each phase has a **gate** — a check to run
before moving on. Stop and debug if a gate fails; later phases assume
earlier ones succeeded.

Vocabulary: **CL** = CourtListener (Free Law Project's public archive).
**NFSN** = NearlyFreeSpeech.NET (the production host). **CoA** = Court
of Appeals.

---

## At-a-glance checklist

```
□ Phase 0  — Decide scope                                          (30 min)
□ Phase 1  — Seed State + Court rows (data migration)              (30 min)
□ Phase 2  — NFSN subdomain alias  (member panel, manual)          (10 min + ~5 min cert wait)
□ Phase 3  — Court.short_label mapping (Bluebook abbreviation)     (10 min)
□ Phase 4  — Parser: case_number / release_date / disposition /    (4-8 hr)
             panel regex
□ Phase 5  — Statute extractor: state-specific citation patterns   (2-3 hr; optional v1)
□ Phase 6  — Judge roster: scraper + ingest                        (1-2 hr; optional)
□ Phase 7  — Bulk corpus ingest (CL bulk dump path)                (1-2 hr local + 1-2 hr NFSN)
□ Phase 8  — Embeddings, tag suggestions, judge panel-vote         (UNATTENDED, ~9 hr for 50K opinions)
             resolution
□ Phase 9  — Statute extraction over the new corpus                (5-10 min)
□ Phase 10 — Validation: spot-check pages render                   (30 min)
□ Phase 11 — Flip is_live=True, restart, update About              (10 min)
□ Phase 12 — Weekly cron for incremental updates                   (automatic)
```

Net wall clock: ~3 days first time (2 days attended + 2 overnight runs).
About a day for state #4+ once the parser/extractor patterns are
familiar.

---

## Timeline view

```
DAY 1 (attended, ~6-10 hr)
├─ Morning: Phases 0-3 (~80 min)  scope, seed, alias, short_label
├─ Midday:  Phase 4 (4-8 hr)      parser regex — the long pole
└─ Evening: Phase 7-local         start the 56GB CL-bulk filter sweep on
                                  the Windows box; runs unattended overnight

DAY 2 (attended midday, ~3-5 hr; long unattended tail)
├─ Morning: Phase 7-NFSN (1-2 hr) tar+scp the subset, load_cl_bulk
├─ Midday:  Phase 5 + 6 (3-5 hr)  statute extractor + judge scraper
└─ Evening: Phase 8 kick off      embed_opinions + suggest_tags +
                                  resolve_judges as a single nohup chain;
                                  runs ~9 hr UNATTENDED overnight

DAY 3 (attended, ~1-2 hr)
├─ Morning: Phase 9-11            statute extraction, validation, flip live
└─ Phase 12 confirms itself on the next scheduled cron run.
```

If you skip Phase 5 (statute extractor) and Phase 6 (scraper), Day 2's
attended work collapses to just the NFSN load + Phase 8 kickoff (~2 hr).

---

## What's universal vs per-state

| Universal (write once, reused) | Per-state (you author every state) |
|---|---|
| Model schema (`Opinion`, `Judge`, `Court`, `Tag`, ...) | CL court IDs (`minn`/`minnctapp`/`nh`/`ariz`/`arizctapp`) |
| Embedding pipeline (Voyage `voyage-law-2`) | Parser regex (case-number format, disposition phrasing, panel layout) |
| Tag suggestion engine (cosine-similarity scoring) | Statute citation pattern (`Minn. Stat. § N.NN`, `RSA N:N`, `A.R.S. § N-NN`) |
| Public templates (`state_landing`, `judge_detail`, `opinion_detail`) | Judge-roster source URL (each state has its own judicial-branch directory) |
| Admin views (HTMX tag-review queue, etc.) | Bluebook short label (`Minn. Ct. App.`, `N.H.`, `Ariz.`, `Ariz. Ct. App.`) |
| Sitemap chunks | NFSN subdomain alias (manual UI step) |
| Privacy + About scaffolding | Historical depth (MN reaches 1851; NH and AZ are modern-only) |
| State router middleware (`StateRouterMiddleware`) | About-page Status block entry |

---

## Pre-flight: one-time setup

Before any state-specific work, confirm these are in place. If you're
the same operator who brought up MN, NH, and AZ, you've already done all
of this — re-confirm anyway, the cost of a misconfigured env is hours.

1. **`VOYAGE_API_KEY`** is set in NFSN's environment (used by
   `embed_opinions` and `embed_tags`). Test:
   ```bash
   ssh docketdrift 'python -c "import os; print(bool(os.environ.get(\"VOYAGE_API_KEY\")))"'
   ```
2. **SSH alias** for the production server: a `Host docketdrift` block in
   `~/.ssh/config` pointing at NFSN's SSH endpoint with key auth. Test:
   ```bash
   ssh docketdrift 'whoami'
   ```
3. **Local Windows machine** has the CL bulk dump (~56GB) under
   `C:\Users\kelly\courtlistener-bulk\`. The Phase 7 filter runs against
   this. If absent, download the latest dump from CL first.
4. **Do NOT add these dependencies**: `numpy`, `scipy`, `pandas`, or
   anything with a C extension that requires BLAS/LAPACK. NFSN's FreeBSD
   wheels for those ship without `cblas_sdot`; `import numpy` fails at
   runtime. The cosine math in `suggest_tags` deliberately uses MariaDB's
   native `VEC_DISTANCE_COSINE` so we don't need numpy. If a new task
   wants matrix math, use raw SQL against the VECTOR columns, or pull a
   small enough subset that pure-Python loops are fine.

---

## From the three rollouts (reference numbers)

Use these as sanity checks on your estimates when picking a new state.

| State | Opinions | Date range | Judges | Panel votes | Statute cites | Embed time | Embed cost |
|---|---|---|---|---|---|---|---|
| MN | 60,375 | 1851 → current | 124 | 9,914 | 124,858 | ~9 hr | ~$7 |
| NH | 20,693 | through 2025-11-07 | 69 | 17,032 | 0 (extractor not generalized) | ~3 hr | ~$2.5 |
| AZ | 38,066 | through 2026-06-05 | 138 | 104 (Supreme byline pattern incomplete) | 0 (extractor not generalized) | ~6 hr | ~$5 |

Embed cost is Voyage's `voyage-law-2` at $0.12/M tokens. Plan ~$0.10-0.14
per 1K opinions; multiply by your expected corpus size.

---

## Phase 0 — Decide scope

Before any code, answer four questions in writing:

1. **CL court IDs.** What's the state Supreme Court's CL `id`? Does the
   state have an intermediate Court of Appeals (CoA)? Are CoA divisions
   one CL id or several?
   ```bash
   curl -s "https://www.courtlistener.com/api/rest/v4/courts/?jurisdiction=S&in_use=true&format=json" \
     | jq '.results[] | select(.id | contains("<state-abbrev>")) | {id, full_name, position}'
   ```
   Observed in MN/NH/AZ:
   - MN: `minn` (Supreme), `minnctapp` (CoA)
   - NH: `nh` (Supreme only)
   - AZ: `ariz` (Supreme), `arizctapp` (CoA — CL aggregates Division 1 and Division 2 under one slug)

2. **Bluebook short label.** Pull from the actual Bluebook or any recent
   reporter cite — what's the canonical short form for each court?
   Examples: `Minn.` / `Minn. Ct. App.` / `N.H.` / `Ariz.` / `Ariz. Ct.
   App.`. These become `Court.short_label` in Phase 3.

3. **Judge-roster source.** Does the state's judicial branch publish a
   public directory of current judges? URL of the page where the current
   highest-court roster lives. Examples:
   - MN: `mncourts.gov/About-The-Courts/Court-Information/Justices.aspx` ✅ scrapable
   - NH: `courts.nh.gov/our-courts/supreme-court/justices` ❌ Akamai-blocked
   - AZ Supreme: `azcourts.gov/MeettheJustices` ✅ scrapable
   - AZ CoA Div 1: `coa1.azcourts.gov` ❌ Akamai-blocked
   - AZ CoA Div 2: `appeals2.az.gov` ❌ Akamai-blocked

   "Akamai-blocked" means the site returns 403 to any non-residential IP
   (including NFSN's egress, WebFetch, and any curl from server-side).
   The workaround is a Playwright scraper running on a residential
   connection (see Phase 6).

4. **Historical depth.** Is bulk ingest going back to the founding era
   feasible (MN: 1851 — territorial supreme court forward), or only the
   modern era (NH/AZ: post-1900 in practice)? This is determined by what
   CL has in its bulk dump.

**Gate:** Four answers written down. Without these, downstream phases go
in circles.

---

## Phase 1 — Seed State + Court rows

Data migration. Follow `opinions/migrations/0022_seed_az.py` as the
cleanest template. It uses `RunPython` with a forward `seed()` and
reverse `unseed()`.

```bash
# Create a new empty migration
.venv/Scripts/python manage.py makemigrations opinions --empty -n seed_<code>
# Edit it to mirror 0022_seed_az.py (state + each court)
# Apply locally
.venv/Scripts/python manage.py migrate opinions
```

Fields per state:
- `State.code` (USPS 2-letter, uppercase)
- `State.name` (full)
- `State.slug` (USPS lowercase — matches the subdomain)
- `State.is_live=False` (don't surface on apex picker yet)

Fields per court:
- `Court.state` (FK)
- `Court.level` (`SUPREME` or `APPEALS`)
- `Court.name` (display, e.g. "Arizona Supreme Court")
- `Court.slug` (`supreme` / `appeals`)
- `Court.courtlistener_id` (e.g. `ariz`, `arizctapp` — answer #1 from Phase 0)

Use `update_or_create` so the migration is idempotent — running it twice
must be a no-op, never a duplicate.

**Gate:** `manage.py shell` shows the new `State` + `Court` rows.
Push + migrate on NFSN:
```bash
ssh docketdrift 'cd /home/private/docketdrift && git pull && \
    source .venv/bin/activate && python manage.py migrate && \
    nfsn -j signal-daemon gunicorn TERM'
```

---

## Phase 2 — NFSN subdomain alias

**Web UI only** — NFSN doesn't expose alias management via CLI.

1. members.nearlyfreespeech.net → Sites → docketdrift
2. **Add a New Alias** → `<code>.docketdrift.com`
3. NFSN auto-issues a Let's Encrypt cert. Wait for the orange padlock
   icon next to the new alias.
4. DNS is already handled — `*.docketdrift.com` is a wildcard.

**Gotcha — cert timing.** The "3-5 min" you'll see in NFSN docs is
optimistic. In practice MN took ~5 min, NH took ~8 min, AZ took ~12 min.
If it's been over 30 minutes and the padlock is still grey, the alias
didn't register — re-add it.

**Gate:**
```bash
ssh docketdrift 'curl -sS -o /dev/null -w "HTTP %{http_code}\n" --max-time 10 https://<code>.docketdrift.com/'
```
returns `HTTP 200`. (You'll see the empty state landing page because no
opinions yet.)

---

## Phase 3 — Court.short_label mapping

`opinions/models.py` → `Court.short_label` is a `@property` with a
per-state `if` ladder. Add the new state's clauses:

```python
if self.state_id == "<CODE>":
    if self.level == self.Level.SUPREME:
        return "<bluebook supreme abbrev>"   # e.g. "Ariz."
    if self.level == self.Level.APPEALS:
        return "<bluebook appeals abbrev>"   # e.g. "Ariz. Ct. App."
```

**Gotcha — short_label is a Python @property, NOT a database column.**
You CANNOT use it in ORM aggregates: `.values("court__short_label")` and
`.annotate(label=F("court__short_label"))` both throw a `FieldError`. To
group opinions by Bluebook label, group by `court_id` and resolve to
Court instances in Python after the query.

**Gate:** `Court.objects.filter(state__code="<CODE>").first().short_label`
returns the expected string in `manage.py shell`.

---

## Phase 4 — Parser

A regex parser at `opinions/parsing/<code>.py`. Follow
`opinions/parsing/mn.py` as the template — it implements:

- `parse(text: str) -> ParsedOpinion | None`
- `ParsedOpinion` fields: `case_number`, `case_name`, `release_date`,
  `disposition`, `is_precedential`, `author`, `panel`

Register the parser in `opinions/parsing/__init__.py`:
```python
from opinions.parsing import mn as mn_parser, az as az_parser, <code> as <code>_parser
REGISTRY = {"MN": mn_parser, "AZ": az_parser, "<CODE>": <code>_parser}
```

**How to write the regex:** pull 5-10 representative opinions from CL's
web UI for each court the state has. Look for:

- The **case-number format**. Examples:
  - MN: `A23-0123` (single-letter prefix + year + 4-digit serial)
  - AZ Supreme: `CR-23-0001-PR` (letter prefix + year + serial + dispo suffix)
  - AZ CoA: `1 CA-CR 23-0001` (division + letter prefix + year + serial)
  - NH: `2023-0123` (year + serial — no letter prefix)
- Where the **release date** appears: header? footer? Following the word
  "Filed"? "Decided"?
- **Disposition language**: `"Affirmed"`, `"Reversed and remanded"`,
  `"Petition denied"`, etc. List the verbatim phrases the state uses;
  the parser maps them to `disposition_bucket` (the indexed slug for the
  Outcomes legend) via a save-hook elsewhere in the model.
- **Panel format**:
  - MN: `"Considered and decided by JUDGE, Presiding Judge; JUDGE, Judge; JUDGE, Judge"`
  - NH: `"MACDONALD, C.J., and BASSETT, HANTZ MARCONI, DONOVAN and COUNTWAY, JJ., concurred."`
  - AZ CoA: `"Presiding Judge X delivered the opinion of the Court, in which Judge Y and Judge Z joined."`
  - AZ Supreme: **different from CoA** — uses a layered "VICE CHIEF JUSTICE X, opinion of the Court" pattern that requires a separate regex (open task #44).
- **Per-curiam vs signed** opinions: per-curiam usually says exactly that
  in the header; signed opinions name the author.

**Gotcha — same state, different byline patterns across tiers.** Don't
assume your CoA byline regex will work for the Supreme Court. Test
separately. AZ's CoA pattern matched immediately; the AZ Supreme variant
is still incomplete (the symptom: AZ shows only ~100 panel votes across
38K opinions vs NH's 17K votes on 21K opinions — the votes that ARE
landing all came from AZ CoA).

**Gotcha — Django template comments are single-line only.** Unrelated to
the parser per se, but if you're also touching templates this rollout:
`{# this is fine #}` works only on a single line. Multi-line
`{# ... #}` renders as raw page text — that text bleeds into the live
page. There's a deploy-blocking system check (`opinions.E001`) that
walks every `.html` and refuses to start if it finds any. Always use
`{% comment %} ... {% endcomment %}` for blocks longer than one line.

**Gate:**
```bash
.venv/Scripts/python manage.py shell <<EOF
from opinions.parsing import parse
text = open("sample_<code>.txt").read()
print(parse("<CODE>", text))
EOF
```
Confirms `case_number` + `release_date` populate at minimum. Partial
fills are acceptable on v1 — the parser only fills empty fields, never
overwrites human input.

---

## Phase 5 — Statute extractor

Optional for v1; opinion pages render fine without statute links. Add
when you want the cross-reference graph.

`opinions/parsing/statutes.py` currently hardcodes MN's `Minn. Stat. §
N.NN` patterns. To go multi-state, refactor to a dispatcher:

1. Move MN's regexes from `statutes.py` into
   `opinions/parsing/statutes_mn.py`.
2. Add `opinions/parsing/statutes_<code>.py` for the new state. Patterns
   observed in real state appellate prose:
   - **MN**: `\bMinn\.?\s*Stat\.?\s*§?\s*(?P<chapter>\d+)(?:\.(?P<section>\d+))?(?:,\s*subd\.\s*(?P<subd>\d+))?`
   - **NH**: `\bRSA\s+(?P<chapter>\d+):(?P<section>\d+)(?:,\s*(?P<sub>[IVX]+))?`
   - **AZ**: `\bA\.R\.S\.\s*§\s*(?P<chapter>\d+)-(?P<section>\d+)(?:\((?P<sub>[A-Z0-9]+)\))?`
3. `statutes.py` becomes a thin dispatcher that picks the extractor by
   state code:
   ```python
   from . import statutes_mn, statutes_nh, statutes_az
   _REGISTRY = {"MN": statutes_mn, "NH": statutes_nh, "AZ": statutes_az}
   def extract(state_code, text):
       mod = _REGISTRY.get(state_code)
       if not mod:
           return []
       return mod.extract(text)
   ```
4. Update the `extract_statutes` management command to accept `--state
   <CODE>` and call the dispatcher.

This refactor is also open task #43 — generalizing extract_statutes to
NH (RSA) and AZ (A.R.S.) is one of the next two coding tasks.

**Gate:** Run the extractor against 3-5 known-citing opinions and verify
the `reference_slug` matches the expected form. Then run the full
extraction; visit `/<code>.docketdrift.com/statute/<slug>/` and see a
real opinion list.

---

## Phase 6 — Judge roster

`opinions/scrapers/<code>_courts.py` — scrapes the state's judicial
directory page for the current roster. Follow
`opinions/scrapers/mncourts.py` (MN) or `opinions/scrapers/az_supreme.py`
(AZ Supreme) as templates. Both use BeautifulSoup.

Per judge, extract:
- `full_name`
- `court` (Supreme vs Appeals — FK to the Court row from Phase 1)
- `role` (Chief Justice / Associate Justice / Chief Judge / Judge)
- `appointment_date` if available
- `bio_url`, `bio_summary`, `photo_url`
- `status=ACTIVE`, `is_currently_seated=True`
- `source_id` — the source's stable slug for the judge. Critical for
  scraper idempotency: lets a re-run find and update its own rows
  instead of duplicating them. Format: `<source>:<source-slug>`, e.g.
  `mncourts:walz` or `azcourts:justice-maria-elena-cruz`.

Run via:
```bash
.venv/Scripts/python manage.py scrape_judges --state <CODE>
```

**Gate:** `Judge.objects.filter(state__code="<CODE>",
is_currently_seated=True).count()` matches the actual roster count on
the official site. Spot-check 2-3 judges' bios.

**Note on CL person IDs:** the scraper doesn't fill `Judge.courtlistener_id`.
Backfill in the admin via the "search CL →" links in JudgeAdmin's
changelist if you need cross-references to CL person dossiers.

### When the official roster is Akamai-blocked

`courts.nh.gov`, `coa1.azcourts.gov`, and `appeals2.az.gov` return 403
to any non-residential IP for every path including sitemap.xml.
WebFetch and any server-side curl from NFSN fails. This is open task
#41 — a Playwright scraper running on a residential connection (the
local Windows box) drops output JSON/PDF to a watched folder, NFSN-side
`scrape_judges` ingests them. Until that lands, you have two options:

**Option A — accept partial coverage** via byline learning. After Phase 7
and Phase 8 land, run:
```bash
python manage.py resolve_judges --state <CODE> --create-missing
```
The command parses every opinion's byline + panel footer with a generic
last-name extractor (handles common patterns like `MACDONALD, C.J., and
COUNTWAY and GOULD, JJ., concurred.`), and when a surname doesn't match
any existing roster row, creates a `Judge` with `status=UNKNOWN`,
`is_currently_seated=False`, and `source_id="byline:<state>:<lastname>"`.
You get a real panel-vote graph without a scraper — but no photo, no
bio, no appointment date, and the `full_name` is the last name only
until an editor renames it.

**Option B — wait for #41** and roll the state out without judge stats
in v1. The site degrades gracefully; judge pages just show stat cards
with `0` counts.

### Gotcha — Cruz-style merge preserves byline last-name

If you scrape the roster (Option A doesn't apply) AND `--create-missing`
already ran AND you then run a one-shot reconcile command to merge
duplicates, watch the resulting `full_name`. In AZ, Justice Maria Elena
Cruz had a byline-learned row with `full_name='Cruz'` and a scraper row
with `full_name='Maria Elena Cruz'`. `reconcile_az_judges` merged them
but preserved the canonical (byline) row's name field — leaving just
`'Cruz'` on the merged record. The scraper had captured the right name
in `source_id` (`azcourts:justice-maria-elena-cruz`) so the data wasn't
lost, but the display was wrong until a manual `update_fields=["full_name"]`
fix. If you write a reconcile command for the new state, prefer the
SCRAPED row's `full_name` over the byline row's. The reconcile script
should explicitly:
```python
canonical.full_name = scraped.full_name  # prefer scraped (full name)
canonical.save(update_fields=["full_name"])
```

---

## Phase 7 — Bulk corpus ingest

**Why bulk, not the CL REST API.** CL's authenticated API tier has a
125 req/day limit, and exponential 429 backoff escalates aggressively
when multiple courts are pulling simultaneously. NH and AZ both tried
the API path first and got stuck in **21-hour cooldowns** before
completing even a single court. The bulk-dump path completed both
states in 1-2 hours wall clock. Use bulk for first-time corpus loads;
reserve the API for the weekly incremental ingest (Phase 12).

### 7a — Local Windows: filter the 56GB dump

```bash
cd C:\Users\kelly\docketdrift
.venv\Scripts\python scripts\cl_bulk_filter.py --state <CODE>
```

Or filter multiple states at once (saves a second 50GB sweep, useful
if you're bringing up two adjacent states):
```bash
.venv\Scripts\python scripts\cl_bulk_filter.py ^
    --state combined ^
    --court-ids <id1>,<id2> ^
    --out-dir C:\Users\kelly\courtlistener-bulk\<name>-subset
```

This is a ~1-2 hour disk sweep on commodity SSDs. Run it overnight if
your daytime hours are precious.

### 7b — Transfer the subset to NFSN

```bash
cd C:\Users\kelly\courtlistener-bulk
tar czf <code>-subset.tar.gz <code>-subset/
scp <code>-subset.tar.gz docketdrift:~/courtlistener-bulk/
```

### 7c — Load on NFSN

First, update `STATE_COURT_CL_IDS` in
`opinions/management/commands/load_cl_bulk.py` to add the new state's
CL court IDs:
```python
STATE_COURT_CL_IDS = {
    "MN": {"minn", "minnctapp"},
    "NH": {"nh"},
    "AZ": {"ariz", "arizctapp"},
    "<CODE>": {"<id1>", "<id2>"},  # new
}
```
Commit + push that change before the NFSN step.

Then:
```bash
ssh docketdrift 'cd ~/courtlistener-bulk && tar xzf <code>-subset.tar.gz && \
    cd /home/private/docketdrift && source .venv/bin/activate && \
    python manage.py load_cl_bulk \
        --subset-dir ~/courtlistener-bulk/<code>-subset \
        --state <CODE>'
```

This runs ~1-2 hours depending on opinion count.

**Gotcha — MariaDB drops idle connections during long pauses.** Any
management command that does a 30-60s pause between DB writes
(rate-limit cooldowns, retries, file I/O) needs retry-with-reconnect
on the write side. `load_cl_bulk` and `embed_opinions` both have this
internally — catch `OperationalError (2013, "Lost connection to MySQL
server during query")`, sleep, reconnect, retry. Don't add custom
long-sleep code to your state-specific scripts without the same handler.
Use bare `BaseException` (not `Exception`) in the retry handler —
NFSN's SSL socket raises `KeyboardInterrupt` on EINTR during long
sleeps and you need to swallow that too.

**Gate:** `Opinion.objects.filter(court__state__code="<CODE>").count()`
matches expectations (depending on bulk dump age and historical
coverage; typically thousands to tens of thousands for an active state's
appellate corpus).

---

## Phase 8 — Downstream pipeline

Three commands. All idempotent — re-running picks up only the rows that
need work. All can run unattended; this is your overnight phase.

```bash
ssh docketdrift 'cd /home/private/docketdrift && source .venv/bin/activate && \
    nohup bash -c "
        python -u manage.py embed_opinions && \
        python -u manage.py suggest_tags && \
        python -u manage.py resolve_judges --state <CODE>
    " > /home/logs/onboard_<code>.log 2>&1 < /dev/null & disown'
```

The `nohup ... & disown` lets the chain survive the SSH disconnect.
Tail `/home/logs/onboard_<code>.log` to watch progress.

### What each command does

- **`embed_opinions`** — voyage-law-2 embedding into `Opinion.embedding`
  VECTOR column. Skips rows where `embedding IS NOT NULL` (resumable).
  Throughput on Voyage's 60 RPM free tier: ~1.8 opinions/sec, ~9 hours
  for 58K opinions. Cost: ~$0.10-0.14 per 1K opinions ($0.12/M tokens).
- **`suggest_tags`** — cosine-similarity scoring against the starter
  tags. Above `TAG_SUGGESTION_AUTO_APPLY_THRESHOLD` (0.40) the tag is
  auto-applied and marked `AUTO_APPLIED` for transparent audit; below it
  the suggestion surfaces in the HTMX review queue at
  `/admin/opinions/tag-review/`. ~5 min for 60K opinions. Depends on
  `embed_opinions` completing.
- **`resolve_judges --state <CODE>`** — parses opinion `raw_text` for
  byline + panel, matches by last-name against the state's Judge roster,
  writes `PanelVote` rows. Skips ambiguous last-name collisions.
  Independent of embedding — can run in parallel if you want, but the
  chain above keeps things linear and the log readable.

### Gotchas in Phase 8

**`Opinion.embedding` is a raw SQL VECTOR column.** It is NOT declared
as a Django model field. You can't query it via the ORM
(`Opinion.objects.filter(embedding__isnull=True)` throws `FieldError`).
Use raw SQL via `connection.cursor()` for any coverage check. This
isn't a bug — VECTOR columns aren't natively a Django field type yet.

**Defer `raw_text` and `html_content` on list-style queries.** Both
columns hold 50-100KB each. Any list-style query (statute_detail,
judge_detail's recent_opinions, tag_detail, opinion_list) MUST chain
`.defer("raw_text", "html_content")` or pulling 50 rows blows past
gunicorn's request timeout. Only `opinion_detail` actually renders
`raw_text` — every other view should defer it.

**`StatuteCitation.Meta.ordering` bleeds into `.distinct()`.** The
default ordering on `["opinion", "text_offset"]` silently joins back to
Opinion when used in a `.values_list().distinct()` chain. Always chain
explicit empty `.order_by()`:
```python
StatuteCitation.objects.filter(...).order_by().values_list("opinion_id", flat=True).distinct()
```

**Pre-resolve court IDs to skip joins.** Bad (slow on 120K-row corpus):
```python
qs = Opinion.objects.filter(court__state=state)
```
Good:
```python
court_ids = list(state.courts.values_list("id", flat=True))
qs = Opinion.objects.filter(court_id__in=court_ids)
```
Court table is small (a handful of rows per state); resolving in Python
first turns a JOIN+COUNT(*) into an FK-index lookup.

**Similar-opinions semantic search needs a `date_cutoff`.**
`VEC_DISTANCE_COSINE` over the state's full corpus is O(N) because the
embedding column allows NULL (MariaDB VECTOR INDEX requires NOT NULL).
At 60K rows the scan was fast; at 120K+ it blew past 20s and saturated
the gunicorn worker. `semantic.similar_to_opinion` caps the candidate
set to a 3-year window around the source opinion's `release_date`.
Don't remove this cap without first migrating embeddings to NOT NULL
and creating an actual VECTOR INDEX.

**Gate:** No errors in the log. Spot-check a judge dossier — should now
show stat cards (Majority Authored, Joined Majority, etc.) populated.

---

## Phase 9 — Statute extraction

```bash
ssh docketdrift 'cd /home/private/docketdrift && source .venv/bin/activate && \
    python manage.py extract_statutes --state <CODE>'
```

Requires the Phase 5 dispatcher refactor first if you're not on MN.
Without it, the command does nothing for non-MN states. ~5-10 min for a
50K-opinion corpus.

**Gate:** Top-cited statute page for the state renders with a real
opinion list:
```bash
curl -sS https://<code>.docketdrift.com/statute/ | head -50
```

---

## Phase 10 — Validation

Before flipping `is_live=True`, manually check the four URL types.

```bash
# Per-state landing
curl -sS https://<code>.docketdrift.com/ | grep -oE "<title>[^<]+|<h1>[^<]+"

# A real opinion detail
curl -sS https://<code>.docketdrift.com/opinion/<case_number>/ | head -50

# A real judge dossier
curl -sS https://<code>.docketdrift.com/judge/<slug>/ | grep -oE "JUDICIAL RECORD|<h1>[^<]+"

# A real statute page (if Phase 5/9 done)
curl -sS https://<code>.docketdrift.com/statute/<slug>/ | head -30
```

Visually open the same URLs in a browser. Check:
- Court pill renders with the right Bluebook abbreviation (Phase 3)
- Disposition pills color-code correctly (parser fills
  `disposition_bucket` via the save hook)
- Judge photos load (CDN URL in `photo_url`)
- Statute links are clickable in opinion body (Phase 5)
- The auto coverage-note banner doesn't fire — if it does, the most
  recent opinion is more than 30 days old, meaning Phase 7's bulk dump
  was stale. Re-pull a fresher CL bulk dump or run `ingest_court` for
  each CL id to top up via the API.

**Gotcha — NFSN proxy cache can serve stale 503 for minutes.** After
fixing a slow/broken endpoint and restarting gunicorn, the public URL
may still return cached 503 for a few minutes. Don't conclude the fix
failed — bypass with the internal gunicorn address from
`/home/logs/daemon_gunicorn.log` (search for `Listening at: ...`) to
confirm the fix is real:
```bash
ssh docketdrift 'curl -sS -H "Host: <code>.docketdrift.com" http://10.0.175.75:8000/some/path/'
```

**Gotcha — local SSH-jail egress to public docketdrift.com is flaky.**
Curls from inside `ssh docketdrift '...'` to `https://docketdrift.com/`
sometimes time out due to NFSN's intra-rack routing for outbound HTTPS
to the same hostname. Use the internal gunicorn address with the Host
header (as above), or just verify in your browser.

**Gate:** All four URLs return 200 with sensible content.

---

## Phase 11 — Flip is_live=True

```bash
ssh docketdrift 'cd /home/private/docketdrift && source .venv/bin/activate && \
    python -c "
from opinions.models import State
s = State.objects.get(code=\"<CODE>\")
s.is_live = True
s.save(update_fields=[\"is_live\"])
print(\"Live:\", s)
" && nfsn -j signal-daemon gunicorn TERM'
```

The gunicorn restart picks up the new `is_live=True` so the apex picker
re-renders showing the new state tile.

Update `opinions/templates/opinions/about.html` Status block:
- Add the new state's beta pill alongside the existing ones.
- Add a bullet to the per-state list describing scope (date range, court
  coverage, ongoing editorial status).

Commit, push, pull on NFSN (templates aren't cached; no second restart
needed). Sitemaps regenerate automatically on the next request via the
`is_live` filter — no manual sitemap step.

**Gate:**
```bash
curl -sS https://docketdrift.com/ | grep -oE "<state-full-name>"
```
returns the state's name. Apex picker shows N+1 tiles where N was the
previous count.

---

## Phase 12 — Weekly cron

NFSN scheduled task triggers `/home/private/docketdrift/cron-ingest.sh`,
which calls `manage.py ingest_court <cl_id>` weekly for incremental
updates via CL's API. The bulk-dump backfill from Phase 7 catches you
up to the dump's snapshot date; the weekly cron keeps the corpus
current after that.

**No action required.** `cron-ingest.sh` auto-discovers every CL court
id belonging to a live state via:
```python
Court.objects.filter(state__is_live=True).values_list("courtlistener_id", flat=True)
```
Flipping `State.is_live=True` in Phase 11 puts the state's courts on
the weekly refresh schedule automatically.

**Gate:** Watch the next scheduled run land in NFSN's "Manage Scheduled
Tasks" log — the run header lists every CL court id it's about to
ingest, including the new state.

---

## What this gets you

After all 12 phases, the new state has:

- Public state subdomain serving a state-landing page with stat strip
- All opinions in the bulk dump, loaded + embedded + tag-suggestion-scored
- Current-roster judge dossiers with stats, court breakdown, recent
  opinions (or partial-coverage byline-learned dossiers if the official
  roster is Akamai-blocked)
- Statute citation graph linking opinions ↔ statutes (if Phase 5 done)
- Apex picker tile + About page entry
- Weekly auto-refresh via cron

Time budget per state, after the first:
- Phases 0-3, 11: ~2 hours total of scaffolding
- Phase 4 parser: 4-8 hours depending on opinion-text quirks
- Phase 5 statute extractor: 2-3 hours (or skip)
- Phase 6 judge scraper: 1-2 hours (or skip if Akamai-blocked)
- Phase 7 bulk ingest: ~1-2 hours local sweep (unattended) + ~1-2 hours
  on NFSN
- Phase 8 pipeline: ~3-9 hours unattended on NFSN (embed dominates)
- Phases 9-10, 12: ~1 hour of validation + automatic cron pickup

Net: ~1-2 days of attended work + 1-2 nights of unattended processing
per new state.

---

## When to deviate

**Skip Phase 4 (parser)** if the state's opinion text is unstructured
enough that regex won't help — just leave fields blank, let editorial
review fill them. The site still works; opinion pages show fewer
auto-filled fields. The cost is no `disposition_bucket` for the
Outcomes legend on the state's landing.

**Skip Phase 5 (statute extractor)** entirely for v1 of a new state —
the in-body statute link rewiring naturally falls through to "no link
shown" when no extractor exists. Add it later when the corpus is
established and you've identified the state's citation patterns from
real opinion prose.

**Skip Phase 6 (judge scraper)** if no public roster exists OR the
official roster site is Akamai-blocked. The state will only have
judges learned from opinion bylines via Phase 8's
`resolve_judges --create-missing`. Coverage is partial (no photo, no
bio, no appointment date, last-name-only display) but real.

### Minimum-viable-state

The four MUST-HAVE phases for a state to even ROUTE traffic to the new
subdomain: **1, 2, 3, 11**.

The smallest useful state — actual opinions browsable, semantic search
working, no statute or judge enrichment — is: **1, 2, 3, 7, 8 (embed
only), 11**. Roughly a day of work plus an overnight embed run.

Everything else makes the corpus richer.
