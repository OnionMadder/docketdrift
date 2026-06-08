# Bringing a new state online

End-to-end runbook for going from "state doesn't exist" to "state is live
on `<code>.docketdrift.com` with a full corpus, judge dossiers, statute
graph, and tag suggestions." Built from what we actually did across MN
(flagship), NH (small one-court state), and AZ (two-court state).

Follow the phases in order. Each phase has a **gate** — a concrete
check to run before moving on. Stop and debug if a gate fails; later
phases assume earlier ones succeeded.

---

## At-a-glance checklist

```
□ Phase 0 — Decide scope
□ Phase 1 — Seed State + Court rows (data migration)
□ Phase 2 — NFSN subdomain alias  (member panel, by hand)
□ Phase 3 — Court.short_label mapping (Bluebook abbreviation)
□ Phase 4 — Parser: regex for case_number / release_date / disposition / panel
□ Phase 5 — Statute extractor: state-specific citation patterns
□ Phase 6 — Judge roster: scraper + ingest
□ Phase 7 — Bulk corpus ingest (CL bulk dump path)
□ Phase 8 — Embeddings, tag suggestions, judge panel-vote resolution
□ Phase 9 — Statute extraction over the new corpus
□ Phase 10 — Validation: spot-check pages render
□ Phase 11 — Flip is_live=True, restart, update About
□ Phase 12 — Weekly cron for incremental updates
```

Most phases are 5-30 minutes. Phase 7's bulk filter is the long pole
(~1-2 hours on local disk for the 50GB opinions sweep).

---

## What's universal vs per-state

| Universal (same everywhere) | Per-state (you author once per state) |
|---|---|
| Model schema (`Opinion`, `Judge`, `Court`, `Tag`, ...) | CL court IDs |
| Embedding pipeline (Voyage) | Parser regex (case-number format, disposition language, panel layout) |
| Tag suggestion engine | Statute citation patterns (Minn. Stat. vs NH RSA vs Ariz. Rev. Stat.) |
| Public templates (`state_landing`, `judge_detail`, ...) | Judge-roster source (each state has its own directory site) |
| Admin views | Bluebook short label (`Minn. Ct. App.` vs `Ariz.` vs `N.H.`) |
| Sitemap chunks | NFSN subdomain alias |
| Privacy + About scaffolding | Historical depth (MN reaches 1851; NH and AZ are shallower) |

---

## Phase 0 — Decide scope

Before any code, answer:

1. **CL court IDs.** What's the state Supreme Court ID? Is there an
   intermediate Court of Appeals? Multiple districts?
   ```bash
   curl -s "https://www.courtlistener.com/api/rest/v4/courts/?jurisdiction=S&in_use=true&format=json" | jq '.results[] | select(.id | contains("<state-abbrev>")) | {id, full_name, position}'
   ```
2. **Bluebook short label.** Pull from the actual law-school bluebook
   or any recent reporter cite — what's the canonical "Minn. Ct. App."
   equivalent for this state's courts?
3. **Roster source.** Does the state's judicial branch publish a public
   directory of current judges? URL of the page where the current
   roster of the highest court lives.
4. **Historical depth.** Is bulk-ingest going back to the founding era
   feasible (MN: 1851), or only the modern era (NH/AZ are
   modern-only)?

**Gate:** Three answers written down. Without these, downstream phases
go in circles.

---

## Phase 1 — Seed State + Court rows

A data migration. Follow `opinions/migrations/0022_seed_az.py` as the
template (the cleanest one). It uses `RunPython` with a forward
`seed()` and reverse `unseed()`.

```bash
# Create a new migration
.venv/Scripts/python manage.py makemigrations opinions --empty -n seed_<code>
# Edit the empty migration to mirror 0022_seed_az.py
# Apply locally
.venv/Scripts/python manage.py migrate opinions
```

Fields per state:

- `State.code` (USPS 2-letter, uppercase)
- `State.name` (full)
- `State.slug` (USPS lowercase — matches the subdomain)
- `State.is_live=False` (don't surface on apex picker yet)

For each court the state has:
- `Court.state` (FK)
- `Court.level` (`SUPREME` or `APPEALS`)
- `Court.name` (display, e.g. "Arizona Supreme Court")
- `Court.slug` (`supreme` / `appeals`)
- `Court.courtlistener_id` (e.g. `ariz`, `arizctapp`)

**Gate:** Local `manage.py shell` shows the new `State` + `Court` rows.
Push + migrate on NFSN.

---

## Phase 2 — NFSN subdomain alias

**Web UI only** (NFSN doesn't expose alias mgmt via CLI).

1. members.nearlyfreespeech.net → Sites → docketdrift
2. **Add a New Alias** → `<code>.docketdrift.com`
3. NFSN auto-issues a Let's Encrypt cert (~3-5 min). Wait for the orange
   padlock icon next to the new alias.
4. DNS-wise this is already handled — `.docketdrift.com` is a wildcard.

**Gate:**
```bash
ssh docketdrift 'curl -sS -o /dev/null -w "HTTP %{http_code}\n" --max-time 10 https://<code>.docketdrift.com/'
```
returns `HTTP 200`. (You'll see the empty state landing page because
no opinions yet.)

---

## Phase 3 — Court.short_label mapping

`opinions/models.py` → `Court.short_label` is a `@property` with a
per-state `if` ladder. Add the new state's two clauses:

```python
if self.state_id == "<CODE>":
    if self.level == self.Level.SUPREME:
        return "<bluebook supreme abbrev>"  # e.g. "Ariz."
    if self.level == self.Level.APPEALS:
        return "<bluebook appeals abbrev>"  # e.g. "Ariz. Ct. App."
```

**Gate:** `Court.objects.filter(state__code="<CODE>").first().short_label`
returns the expected string in `manage.py shell`.

---

## Phase 4 — Parser

A regex parser at `opinions/parsing/<code>.py`. Follow `mn.py` as the
template — it implements:

- `parse(text: str) -> ParsedOpinion | None`
- ParsedOpinion fields: `case_number`, `case_name`, `release_date`,
  `disposition`, `is_precedential`, `author`, `panel`

Register the parser in `opinions/parsing/__init__.py`:
```python
from opinions.parsing import mn as mn_parser, az as az_parser
REGISTRY = {"MN": mn_parser, "AZ": az_parser, "<CODE>": <code>_parser}
```

**How to write the regex:** Pull 5-10 representative opinions from CL's
web UI for the target court(s). Look for:

- The **case-number format** (MN: `A23-0123`, AZ: `CR-23-0001-PR`)
- Where the **release date** appears (header? footer? "Filed" prefix?)
- Disposition language: `"Affirmed"`, `"Reversed and remanded"`,
  `"Petition denied"`, etc.
- Panel format: `"Considered and decided by JUDGE; JUDGE; JUDGE"`
  is MN's; other states will differ.
- Per-curiam vs signed opinions.

**Gate:**
```bash
# Smoke test against a real opinion
.venv/Scripts/python manage.py shell <<EOF
from opinions.parsing import parse
text = open("sample_<code>.txt").read()
print(parse("<CODE>", text))
EOF
```
Confirms case_number + release_date populate. Partial fills are OK on
v1 — the parser only fills empty fields, never overwrites human input.

---

## Phase 5 — Statute extractor

`opinions/parsing/statutes.py` currently hardcodes MN's `Minn. Stat. §
N.NN` patterns. For new states, add a per-state extractor.

Refactor plan when adding a second state's patterns:
1. Move the MN-specific regexes from `statutes.py` into a new
   `opinions/parsing/statutes_mn.py`.
2. Add `opinions/parsing/statutes_<code>.py` with the new state's
   patterns. For NH: `\bRSA\s*\d+:\d+`. For AZ: `\bA\.R\.S\.\s*§\s*\d+-\d+`.
3. `statutes.py` becomes a dispatcher that picks the right extractor by
   state code.
4. Update `extract_statutes` management command to accept `--state` and
   pick the right extractor.

**Gate:** Run the extractor against 3-5 known-citing opinions and
verify the extracted `reference_slug` matches the expected form. Run
the full extraction; visit `/<code>.docketdrift.com/statute/<slug>/`
and see a real opinion list.

**Out of scope for v1:** if you don't want statute pages yet, leave
the new state's pages without `/statute/` links. The system degrades
gracefully — opinion pages just don't get clickable statute links.

---

## Phase 6 — Judge roster

`opinions/scrapers/<code>_courts.py` — scrapes the state's judicial
directory page for the current roster. Follow
`opinions/scrapers/mncourts.py` as the template. It uses
BeautifulSoup.

Per judge, extract:
- `full_name`
- `court` (Supreme vs Appeals)
- `role` (Chief, Associate, etc.)
- `appointment_date` if available
- `bio_url`, `bio_summary`, `photo_url`
- `status=ACTIVE`, `is_currently_seated=True`
- `source_id` — the source's stable slug for the judge (lets the
  scraper find its own rows again on re-run without duplicating)

Run via:
```bash
.venv/Scripts/python manage.py scrape_judges --state <CODE>
```

**Gate:** `manage.py shell` count of `Judge.objects.filter(state__code="<CODE>",
is_currently_seated=True)` matches the actual roster count on the
official site. Spot-check 2-3 judges' bios.

**Note on CL person IDs:** the scraper doesn't fill `courtlistener_id`.
Backfill in the admin via the "search CL →" links in JudgeAdmin's
changelist (see admin polish from 2026-06-08).

---

## Phase 7 — Bulk corpus ingest

**Local Windows machine** (where the 56GB CL bulk dump lives).

```bash
cd C:\Users\kelly\docketdrift
.venv\Scripts\python scripts\cl_bulk_filter.py --state <CODE>
```

Or filter multiple states at once (saves a second 50GB sweep):
```bash
.venv\Scripts\python scripts\cl_bulk_filter.py ^
    --state combined ^
    --court-ids <id1>,<id2> ^
    --out-dir C:\Users\kelly\courtlistener-bulk\<name>-subset
```

Then:
```bash
cd C:\Users\kelly\courtlistener-bulk
tar czf <code>-subset.tar.gz <code>-subset/
scp <code>-subset.tar.gz docketdrift:~/courtlistener-bulk/
```

**On NFSN:**
```bash
ssh docketdrift 'cd ~/courtlistener-bulk && tar xzf <code>-subset.tar.gz && \
    cd /home/private/docketdrift && source .venv/bin/activate && \
    python manage.py load_cl_bulk \
        --subset-dir ~/courtlistener-bulk/<code>-subset \
        --state <CODE>'
```

(Update `STATE_COURT_CL_IDS` in `load_cl_bulk.py` to add the new state's
CL court IDs before this runs.)

**Gate:** `Opinion.objects.filter(court__state__code="<CODE>").count()`
matches expectations (depending on bulk dump age and historical
coverage; typically thousands to tens of thousands for an active
state's appellate corpus).

**Why the bulk path:** CL's API has a 125 req/day limit on the
authenticated tier, and exponential 429 backoff escalates fast when
multiple courts are pulling simultaneously. The bulk dump bypasses
the API entirely. NH and AZ both attempted via API on 2026-06-08
and got stuck in 21-hour cooldowns; the bulk path completed in 1-2
hours wall clock.

---

## Phase 8 — Downstream pipeline

All three commands are idempotent and pick up the new state's rows
automatically — no per-state config needed.

```bash
ssh docketdrift 'cd /home/private/docketdrift && source .venv/bin/activate && \
    python manage.py embed_opinions && \
    python manage.py suggest_tags && \
    python manage.py resolve_judges --state <CODE>'
```

- `embed_opinions` — voyage-law-2 embedding into `Opinion.embedding`
  VECTOR. ~$0.10 per 1K opinions. Takes ~5-10 min per 1K opinions on
  Voyage's free tier (60 req/min).
- `suggest_tags` — cosine similarity scoring against the 31 starter
  tags. ~5 min for 60K opinions; trivial for smaller corpora.
- `resolve_judges` — parses opinion `raw_text` for byline + panel,
  matches by last-name against the state's Judge roster, writes
  `PanelVote` rows. Skips ambiguous last-name collisions.

**Gate:** No errors. Spot-check a judge dossier — should now show
stat cards (Majority Authored, Joined Majority, etc.) populated.

---

## Phase 9 — Statute extraction

```bash
ssh docketdrift 'cd /home/private/docketdrift && source .venv/bin/activate && \
    python manage.py extract_statutes --state <CODE>'
```

Currently MN-only — generalize via Phase 5 first if this is a new state.

**Gate:** Top-cited statute page for the state renders with a real
opinion list.

---

## Phase 10 — Validation pass

Before flipping `is_live=True`, manually check:

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

The gunicorn restart picks up the new `is_live=True` so the apex
picker re-renders showing the new state tile.

Update `opinions/templates/opinions/about.html` Status block:
- Add the new state's beta pill alongside the existing ones.
- Add a bullet to the per-state list describing scope (date range, court
  coverage, ongoing editorial status).

Push + pull on NFSN (templates aren't cached; no second restart needed).

**Gate:**
```bash
curl -sS https://docketdrift.com/ | grep -oE "<state name>"
```
returns the state's name. Apex picker shows N+1 tiles where N was the
previous count.

---

## Phase 12 — Weekly cron

NFSN scheduled task triggers `cron-ingest.sh`, which calls
`manage.py ingest_court <cl_id>` weekly for incremental updates via
CL's API. The bulk-dump backfill from Phase 7 catches you up to the
dump's snapshot date; the weekly cron keeps the corpus current after
that.

Update `cron-ingest.sh` to add the new state's CL court IDs to the
list it iterates over.

**Gate:** `tail -f /home/logs/scheduled_*.log` after the next
scheduled run — see successful ingest entries for the new court IDs.

---

## What this gets you

After all 12 phases, the new state has:
- Public state subdomain serving a state-landing page with stat strip
- All opinions in the bulk dump, loaded + embedded + tag-suggestion-scored
- Current-roster judge dossiers with stats, court breakdown, recent
  opinions
- Statute citation graph linking opinions ↔ statutes (if Phase 5 done)
- Apex picker tile + About page entry
- Weekly auto-refresh via cron

Time budget per state, after the first:
- Phases 0-3, 6, 11: ~2 hours total of setup
- Phase 4 parser: 4-8 hours depending on opinion-text quirks
- Phase 5 statute extractor: 2-3 hours
- Phase 7 bulk ingest: ~2-3 hours wall clock (mostly the 50GB sweep
  unattended on your local disk)
- Phase 8 + 9: ~30-60 min unattended on NFSN
- Phase 10 + 12: 30 min validation + cron edit

Net: ~1-2 days of attended work + a few hours of unattended
processing per new state.

---

## When to deviate

Skip Phase 4 (parser) if the state's opinion text is unstructured
enough that regex won't help — just leave fields blank, let editorial
review fill them. The site still works; opinion pages just show fewer
auto-filled fields.

Skip Phase 5 (statute extractor) entirely for v1 of a new state — the
in-body statute link rewiring naturally falls through to "no link
shown" when no extractor exists. Add it later when the corpus is
established.

Skip Phase 6 (judge scraper) if no public roster exists — the state
will only have judges learned from opinion bylines via Phase 8's
`resolve_judges`. Coverage is partial but real.

The four MUST-HAVE phases for a state to even ROUTE traffic:
**1, 2, 3, 11**. Everything else makes the corpus richer.
