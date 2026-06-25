# DocketDrift Roadmap — post-3-state foundation

What to build after MN / NH / AZ are fully embedded, panel-vote-graphed,
statute-extracted, and editorial-review-active. Phases here pick up
where `STATE_ROLLOUT.md` stops (Phase 12 = weekly cron firing).

The numbering is from-here-forward (Phase 13+) so the runbook stays
the source of truth for *bringing up* a new state, and this document
is the source of truth for *building new features on top* of an
established state. Phases are independent unless a `Depends:` line
says otherwise.

This document is intentionally aspirational. None of the phases below
are committed scope. They're sketches with enough fidelity that we
can drop into any one of them and know what we're building.

---

## Where we are when this is written (2026-06-12)

- MN: 100% embedded, 124K statute cites, 9.9K panel votes, 20K+
  pending tag suggestions
- NH: 100% embedded, 79K statute cites, 17K panel votes
- AZ: embed in progress (~21 hr ETA), statute extractor ready but not
  yet swept, 142 panel votes (low because AZ Supreme byline pattern
  was a late fix and pre-existing CoA dominance)
- Judge comparison (`/compare/judges/`) live: votes-per-year chart
  with overlay, side-by-side dossier, concordance + split-decisions
- Statute pages live for MN/NH; AZ pending the post-embed
  `extract_statutes --state AZ` sweep
- Self-resurrecting embed wrapper + heartbeat supervisor live

---

## How to read this document

Each phase lists:
- **What** the feature does
- **Why** it's worth building (user value + alignment with the
  anti-hallucination posture)
- **Depends** on prior phases or data prerequisites
- **Rough scope** — sessions of focused work, not calendar weeks
- **Files / models** that would land

---

## Phase 13 — Attorney extraction + cross-reference

**What.** Parse the COUNSEL block in every opinion's raw_text and
populate two new tables:

- `Attorney` — name, slug, state, optional bar_number / CL person id
- `AttorneyAppearance` — (attorney, opinion, side, argued: bool,
  firm_at_time, role_within_firm). `side` is an enum:
  appellant / appellee / petitioner / respondent / amicus / state /
  cross-appellant / intervenor.

Then build dossier + cross-reference pages mirroring the judge
analytics already shipped:

- `/attorney/<slug>/` — total appearances, cases by side, win/loss
  by disposition, top judges-appeared-before, top adversaries, oral
  vs brief-only ratio, area of practice (via opinion tags)
- `/attorney/<slug>/?vs=<judge-slug>` — the killer query. "Attorney X
  before Judge Y: N appearances, M dispositions, K with Y dissenting"
- `/compare/attorneys/?a=&b=` — side-by-side, like `/compare/judges/`
- Search-box routing: paste an attorney name in the search bar →
  redirect to dossier when there's a high-confidence single match
  (same routing pattern that handles docket and statute cites)

**Why.** The closest equivalent on paid databases (Lex Machina
attorney analytics, Bloomberg Law Litigation Analytics) costs
~$200/mo per seat and is the single most-cited reason appellate
boutiques pay for those services. The data is *already in
raw_text*; we just haven't extracted it. Aligns with anti-
hallucination posture because every appearance traces to a real
opinion the user can click through to verify.

**Depends.** Three-state foundation locked (embed + statute extract
+ panel votes). No model migrations needed before this; Phase 13
adds the new models.

**Rough scope.** ~3 focused sessions:
1. Counsel-block parser per state (MN, NH, AZ) + dispatcher.
   Mirrors the existing `opinions/parsing/statutes_{mn,nh,az}.py`
   pattern.
2. Models + `extract_counsel --state X` management command +
   reconcile pass for the same-name-different-person problem (use
   `(state, slug)` unique + manual merge admin like
   `reconcile_az_judges` already does for judges).
3. Public templates (dossier + cross-reference + side-by-side) +
   search routing.

**Files.**
- `opinions/parsing/counsel.py` (dispatcher)
- `opinions/parsing/counsel_mn.py`, `counsel_nh.py`, `counsel_az.py`
- `opinions/models.py` — add `Attorney`, `AttorneyAppearance`
- `opinions/migrations/00XX_attorney_appearance.py`
- `opinions/management/commands/extract_counsel.py`
- `opinions/views.py` — add `attorney_detail`, `attorney_compare`
- `opinions/templates/opinions/attorney_detail.html`,
  `attorney_compare.html`
- `STATE_ROLLOUT.md` — sketch a new "Phase 5.5 — Counsel extraction"
  step so future states get this on day one instead of as a retrofit

---

## Phase 14 — Citation treatment graph ("has this been overruled?")

**What.** Parse every opinion's body for citations to other opinions
in our corpus and build an `OpinionCitation` table:
(citing_opinion, cited_opinion, treatment, context_text). Classify
the treatment from sentence-level cues — "we overrule",
"we distinguish", "see also", "but see", "but cf.", etc. Then surface
on each opinion detail page:

> *State v. Romero*, 2026 N.H. 7 has been:
> - Cited 23 times
> - Distinguished in 4 opinions (link list)
> - Followed in 17 opinions (link list)
> - Overruled in 2 opinions (link list)

**Why.** This is exactly Westlaw KeyCite / Lexis Shepard's — the
single most-load-bearing feature in paid legal research. Lawyers
verify every cite before they file. Building our own from the
already-parsed opinions makes us competitive on the part of the
workflow that's hardest to live without when leaving paid databases.
Deterministic regex extraction, no LLM needed for Phase 1 of this.

**Depends.** Phase 13 helpful but not required. The citation parsing
is independent of attorney parsing.

**Rough scope.** ~2 focused sessions:
1. Citation extractor (regex over body text, captures
   "<case_name>, <reporter_cite> (year)" patterns) +
   `OpinionCitation` model. Per-state reporter formats just like
   the statute extractors.
2. Treatment classifier (also regex — sentence-level cues like
   "we overrule X" / "X was overruled by Y" / "X is
   distinguishable"). Conservative defaults; everything that doesn't
   match a treatment cue is classified `CITED`.

**Files.**
- `opinions/parsing/citations.py` (dispatcher) + per-state files
- `opinions/models.py` — `OpinionCitation`
- `opinions/management/commands/extract_citations.py`
- `opinions/templates/opinions/_treatment_panel.html` (renders on
  opinion_detail)

---

## Phase 15 — LLM holding extraction (deferred from Phase 1D)

**What.** Use Claude Haiku to decompose each opinion's body into
`OpinionHolding` rows: (opinion, statute_cited, holding_direction,
holding_text_verbatim). One-time batch over the established corpus,
incremental from then on. Unlocks per-statute holding search:
"every holding in MN that interpreted Minn. Stat. § 169.13" with the
actual verbatim text.

**Why.** This is the one item on the roadmap that uses generative
AI, and it's worth the careful framing: Haiku isn't being asked to
*synthesize* a holding, it's being asked to *extract* the verbatim
text and classify the direction (granted / denied / affirmed / etc.).
Output is always pinned to the source paragraph; the user clicks
through and reads the actual opinion text. This is the same posture
as the embedding-based semantic search — ML appears only to
*organize* real text, not to generate new claims.

**Depends.** Phase 13 helpful (attorney appearances + holdings give a
rich practice-area filter). Statute extractor done per state.

**Rough scope.** ~1 session of prompt engineering + cost estimation
+ one batch run. Spend confirmation required before running.

**Cost.** ~$90 for the full 3-state corpus at Haiku rates (estimate
from the session-brief math).

**Files.**
- `opinions/models.py` — `OpinionHolding` already partially exists;
  fill in
- `opinions/management/commands/extract_holdings.py`
- New view: `statute_detail` gets a "holdings under this statute"
  section

---

## Phase 16 — Reporter-cite backfill

**What.** Add `Opinion.reporter_cite` field, populated from each
state parser's existing citation extraction. Lets users paste a
reporter cite (`2026 N.H. 1`, `260 Ariz. 100`) into the search box
and land directly on the opinion — same one-click routing already
done for statute cites and docket numbers.

**Why.** Already on the open-work list in CLAUDE.md. Low risk, high
daily-use value. Lawyers copy-paste reporter cites constantly.

**Depends.** Nothing. Could ship before or after Phase 13.

**Rough scope.** ~1 hour:
- Migration adds the field
- Per-state parser already extracts it (NH definitely, MN and AZ
  need a small pattern); backfill via one-shot command
- `opinion_list` view detects reporter-cite-shaped query and
  redirects

---

## Phase 17 — Smart alerts (RSS + email)

**What.** Let users subscribe to "new opinions matching <criteria>"
and get an RSS feed or email digest. Criteria can be:
- A judge
- A tag
- A statute
- An attorney (post-Phase-13)
- A free-text search query

**Why.** Lawyers tracking specific issues / opposing counsel / panels
want to know the day a new opinion drops. Paid databases charge
extra for this on top of base seats. It's a daily-use feature and a
plausible monetization path if we ever needed one.

**Depends.** Just the existing ingest pipeline.

**Rough scope.** ~2 sessions. The RSS path is mostly free; the email
path needs an outbound mail provider on NFSN.

---

## Phase 18 — Brief cite-checker

**What.** Paste the citations from a brief, get back: which ones are
in our corpus, which have been distinguished / overruled (via
Phase 14 treatment graph), which have a panel composition that
matches your prospective panel (via the existing PanelVote data),
which have similar tags to your fact pattern.

**Why.** This is the workflow lawyers actually do during brief
prep, manually, today. The corpus is already structured enough that
a careful UI can flag stale cites with a real link to the
distinguishing opinion. Same anti-hallucination posture: we don't
predict, we surface citations the user verifies themselves.

**Depends.** Phase 14 (treatment graph) is the hard prerequisite.

**Rough scope.** ~2 sessions.

---

## Phase 19 — Co-counsel + firm networks

**What.** Build firm-level analytics on top of Phase 13's
attorney/appearance data: "Attorneys at Firm X have appeared 47
times before Justice Y, win rate 31%." Plus a co-counsel network
showing which attorneys frequently file briefs together.

**Why.** Firm partners track this informally; we'd surface it
structurally. Useful for both attorneys ("who would I want as
co-counsel here?") and clients ("which firm has actually argued in
front of this judge?").

**Depends.** Phase 13. Doesn't need new extraction, just new
templates over the existing AttorneyAppearance table.

**Rough scope.** ~1 session.

---

## Phase 20 — Side-by-side opinion diff (rehearings / amendments)

**What.** When the same court issues an amended opinion or grants
rehearing and re-publishes, dedup by (court, case_number) and offer
a diff view showing what changed between the original and amended.
The CL bulk dump already carries the cluster_id chains; we just need
to surface them.

**Why.** Amendments often have substantive consequence and aren't
flagged by anyone except specialty practitioners watching for them.
Useful for the same audience that wants the alert feed.

**Depends.** Nothing structural.

**Rough scope.** ~1 session.

---

## Phase 21 — Public read API

**What.** REST API at `/api/v1/` for opinions / judges / attorneys
/ statutes / panel votes. Read-only, rate-limited, no auth required
for public data, optional token for higher rate.

**Why.** Lets case-management tool vendors and academic researchers
integrate without scraping. Also fits the "public records as public"
posture explicitly — the API is the structured surface that match
that promise.

**Depends.** Nothing structural; DRF (Django REST Framework) drops
in cleanly on the existing models.

**Rough scope.** ~2 sessions including docs.

---

## Phase 22 — DocketDrift paragraph overlay (toggleable, non-destructive)

**What.** A DocketDrift-internal paragraph-numbering *overlay* for
opinions the court left unnumbered — rendered as a view layer only,
never written back into `raw_text` or any stored field. A toggle on
the opinion-detail page (default OFF — verbatim court text) lets a
user flip to the overlay, which numbers paragraphs sequentially and
exposes `#dd-para-N` deep-links + copy-link pilcrows the same way the
court-numbered opinions already get `#para-N`.

Hard design constraints (these are the whole point — see non-goals):
- **Never alters the record.** The canonical default is the court's
  verbatim text with the court's own paragraph numbers (where they
  exist). The overlay is computed at render time, not persisted into
  `raw_text`. If we ever cache the computed numbering, it lives in a
  SEPARATE column/table clearly marked as DocketDrift-derived, and the
  source text is reconstructable without it.
- **Visibly DocketDrift-assigned, not court-assigned.** Overlay markers
  must be unmistakably ours — distinct glyph/prefix (e.g. `DD¶N` or a
  tinted marker) + a banner ("Paragraph numbers added by DocketDrift —
  not part of the court's opinion") so no reader ever mistakes an
  overlay number for one the court wrote. Pinpoint cites generated from
  the overlay (Phase 14/18) must carry the same provenance flag.
- **Toggleable, opt-in.** "High-level users swap back and forth." Court
  text is the floor everyone sees; the overlay is something you turn on.
- **Court-numbered opinions are untouched.** If the court already
  numbered its paragraphs, there is no overlay — `#para-N` already
  works and is authoritative.

**Why.** Resolves the tension between "we want pinpoint-linkable,
manageable paragraphs everywhere" and "we never fabricate structure
into the record." The overlay gives uniform linkability without
altering a single opinion — the numbering is honestly labeled as a
DocketDrift reading aid, separable from the court's text at any time.
Same posture as embeddings/tags: a layer that *organizes* real text,
clearly marked as ours, with the verbatim source always one toggle
away. (Idea: Onion, 2026-06-25. See `feedback_no_pinpoint_cite_extraction`
in session memory — the line is fabricating numbers INTO the record;
a labeled, toggleable, non-persisted overlay does not cross it.)

**Depends.** The paragraph-anchor rendering already shipped
(`format_opinion_text` emits `id="para-N"` for court markers; the NH
flash + copy-link UX layer is live). This phase generalizes that to a
computed overlay for the unnumbered case. Useful alongside Phase 14/18
so pinpoint links can reach overlay paragraphs (carrying the
provenance flag).

**Rough scope.** ~2 focused sessions:
1. Render-time overlay numbering in `format_opinion_text` (a mode that
   numbers chunks DocketDrift-side when no court `[¶N]` markers are
   present), distinct `#dd-para-N` ids + `DD¶N` markers + provenance
   banner. NH-first per the proving-ground rule.
2. Toggle UX (default off, remembered per user via a cookie/localStorage
   — no account system needed) + ensure the verbatim view is always the
   canonical, crawlable, canonical-URL default so SEO + citations point
   at the court's text, not the overlay.

**Files.**
- `opinions/templatetags/opinion_text.py` — overlay numbering mode
- `opinions/templates/opinions/opinion_detail.html` — toggle + banner
- `opinions/static/opinions/css/docketdrift.css` — overlay marker style
- (optional, only if we cache) `opinions/models.py` — a clearly-named
  DocketDrift-derived numbering table, never mutating `raw_text`

---

## Infrastructure / hardening (ongoing, not numbered)

Items already tracked in `CLAUDE.md`'s "Open work, ranked":

- **VECTOR INDEX migration** on `Opinion.embedding` once all three
  states are fully embedded. Lets `similar_to_opinion` drop the
  3-year date-cutoff workaround and run sub-100ms across the whole
  corpus.
- **Playwright-on-Windows scrapers** for `courts.nh.gov`,
  `coa1.azcourts.gov`, `appeals2.az.gov` — currently the bottleneck
  on keeping NH 2026 current and AZ CoA judge bios complete.
- **State-router middleware lookup cache** — small per-request win.
- **Cloudflare in front of NFSN** — deferred because of registrar
  constraints; revisit when transferring the domain.
- **Tag triage** — 20K+ MN tag suggestions pending; editorial work,
  not code work.

---

## Explicit non-goals

These get said no to, on purpose, to preserve the anti-
hallucination posture documented on the About page and
`/how-we-differ/`.

- **No LLM-generated legal text.** No chat box, no
  "summarize this opinion", no synthesized holdings, no AI-drafted
  briefs. Phase 15's LLM holding extraction is the closest we go,
  and it's pinned to verbatim source text.
- **No outcome prediction.** No "your motion has a 73% chance of
  succeeding." We surface counts of past outcomes; we don't predict
  future ones.
- **No judge personality scoring.** No "Justice X is liberal."
  Counts, splits, dissent rates — fine. Labels — no.
- **No paywall on derived analytics from public records.** Public
  data, treated as public. If a monetization path becomes necessary,
  it's API tiers or hosted private corpora, not gating the public
  dataset.

---

## Phase ordering — rough recommendation

For the user value vs. effort curve:

1. **Phase 16** (reporter-cite backfill) first — 1 hour, immediate
   daily-use value, completely independent of everything else.
2. **Phase 13** (attorney extraction) — the headline feature, what
   you've been wanting.
3. **Phase 14** (citation treatment graph) — unlocks Phase 18 and
   makes opinion-detail pages substantially richer.
4. **Phase 15** (LLM holdings) — once Phase 14 lands, holding-level
   search is the natural next layer.
5. **Phase 17** (alerts) — daily-use feature; could land anytime
   after Phase 13.
6. **Phase 18** (brief cite-checker) — sits on top of Phase 14.
7. **Phase 19** (firm networks) — small, sits on Phase 13.
8. **Phase 20** (opinion diff) — small, independent.
9. **Phase 21** (API) — last, since the schema settles after the
   above.

Later / unscheduled:
- **Phase 22** (DocketDrift paragraph overlay) — a "could work" idea,
  not near-term. Slots in whenever uniform pinpoint-linkability on
  unnumbered opinions becomes worth the toggle UX; depends on nothing
  but the already-shipped anchor layer.

Infrastructure items interleave as the surface-area pressure builds.
