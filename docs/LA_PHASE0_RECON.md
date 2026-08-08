# Louisiana — Phase 0 recon (2026-08-08)

Answers the four Phase-0 questions in `docs/LA_BUILD_LIST.md`. Each finding is
scoped to what it changes downstream in the build list. Measurement artifacts
live in the session scratchpad; commands are reproducible.

**TL;DR — the two make-or-break unknowns both fell in our favor.**
1. **lasc.org PDFs are directly fetchable from a server.** Cloudflare-cached,
   HTTP 200, `Content-Type: application/pdf`, no auth. NFSN can download
   without a browser.
2. **BUT the opinion listing is a Blazor SPA** (JS-rendered, reCAPTCHA
   component present) — every URL returns the same 13.9KB shell to curl.
   Listing needs a headed browser like MN's mn.gov work.
3. **Neither the CL cluster nor the docket record carries the COA circuit.**
   `docket_number` (e.g. `2026 CW 0867`) has no circuit, `appeal_from_str`
   is empty, `court_id` is always `lactapp`. Circuit must come from the PDF
   header ("COURT OF APPEAL, FIRST CIRCUIT") or a parish→circuit map.

Net effect on the build list: **Phase 7b becomes "attended listing +
unattended download," not "attended everything."** Roughly halves the
attendance ask if the reCAPTCHA doesn't fire aggressively.

---

## 1. CL court IDs — CONFIRMED

Confirmed against CL's live `/api/rest/v4/courts/` endpoint (no auth needed
for court metadata):

| CL id | full_name | jurisdiction | citation | in_use | has_opinion_scraper | notes |
|---|---|---|---|---|---|---|
| `la` | Supreme Court of Louisiana | S | `La.` | true | true | url = `http://www.lasc.org/` |
| `lactapp` | Louisiana Court of Appeal | SA | `La. Ct. App.` | true | true | all 5 circuits under one id |
| `lasuperct` | Superior Court of Louisiana | ST | (blank) | **false** | — | historical, do not ingest |
| `laag` | Louisiana Attorney General Reports | SAG | `La. Att'y Gen.` | true | — | AG opinions, out of scope |
| `lacterrapp` | Louisiana Court of Errors and Appeals | SA | (blank) | false | — | 1843-1846 historical curiosity |

**Cluster totals (live API, 2026-08-08):**

| court | CL total clusters |
|---|---|
| `la` (Supreme) | 200,210 |
| `lactapp` (all COA) | 144,255 |

The 200K Supreme figure includes the whole 1813→present history in CL; the
audit's ~12K "missing since 2020" figure is a slice of that. LACTAPP's most
recent clusters are dated **2026-08-07** (today) — the audit's "feed intact"
verdict re-confirmed from live data.

Correction to the build list: **the domain is `lasc.org` not `lasc.gov`**
(CL's court record has it right; the build list has a typo).

## 2. lasc.org accessibility — SPLIT: listing walled, PDFs open

The single biggest recon question. Answered in three measurements:

**A. Root page and any URL return the SAME 13.9KB HTML shell.** Measured on
`/`, `/Opinions`, and `/opinions?p=2026-014` (a real April 2026 release). All
three: HTTP 200, exactly ~13.9KB, identical Blazor+SignalR importmap. The
shell references `GoogleCaptchaComponent`, `Radzen.Blazor`, and
`telerik-blazor.js` — enterprise Blazor with **reCAPTCHA loaded**. No
opinion data is in the raw response. This is **not a Radware/Akamai wall**
— it's a JS-only listing, but the effect on scripted fetch is the same.

**B. Rendered in a real browser (Chromium via the in-app browser tool),
the SPA hydrates fully within a couple seconds and exposes clean anchors.**
Verified visiting `/`, `/courtactions/2021`, and `/opinions?p=2026-037`:
DOM shows release dates as `<a href="/opinions?p=YYYY-NNN">`, release
pages show individual opinion anchors as
`<a href="/opinions/YYYY/NR<NNN>_<YY>-<NNNN>.<TYPE>.OPN.pdf">…</a>`.
Full case caption + one-line disposition are rendered inline.

**C. PDFs are DIRECTLY FETCHABLE with curl.** Verified on the sample
Fuller v. State (2026-CD-00927, opinion release 037):

```
$ curl -I "https://www.lasc.org/opinions/2026/NR037_26-0927.CD.OPN.pdf"
HTTP/1.1 200 OK
Content-Type: application/pdf
Content-Length: 1242154
Server: cloudflare
cf-cache-status: HIT
Accept-Ranges: bytes
```

No 403, no bot wall, no auth. Cloudflare cache HIT. **This is the critical
finding**: the PDF endpoint is a plain static asset from Cloudflare's edge
and DOES NOT participate in the Blazor/reCAPTCHA layer. NFSN can pull the
PDFs at bulk parallelism the same way it pulls MN PDFs (which is why MN's
manifest-mode split works despite mn.gov's wall).

**Implication for Phase 7b:** shape is `residential browser LISTS → NFSN
DOWNLOADS`, mirroring MN's backfill. Only the listing needs attention;
downloads are unattended. Given reCAPTCHA is loaded, expect the same
"space out loads, be prepared to solve one by hand" mn.gov posture — not
worse.

### URL scheme (locked down)

```
Release calendar (per year):   https://www.lasc.org/courtactions/<YYYY>
News release (single date):    https://www.lasc.org/opinions?p=<YYYY>-<NNN>
Opinion PDF:                   https://www.lasc.org/opinions/<YYYY>/NR<NNN>_<YY>-<NNNN>.<TYPE>.OPN.pdf
                                 - NNN   = release number, zero-padded to 3 (e.g. 037)
                                 - YY    = 2-digit year (e.g. 26)
                                 - NNNN  = docket sequence, zero-padded to 4 (e.g. 0927)
                                 - TYPE  = docket class code: CD, CA, CC, KA, KP, KK, KH, KO, B, C
                                             (Civil/Criminal appeal + writ classes; matches CL's
                                              docket_number 2nd token, e.g. "2026 CW 0867")
```

### Release volume — from lasc.org's own 2021 calendar

The 2021 calendar (a "dead" year per CL) shows **51 news releases**, split
into three types (the "Type" column on `/courtactions/2021`):

| release type | count in 2021 | contents |
|---|---|---|
| **Opinions** | 9 | full merits opinions — the "real" opinions |
| **Actions** | 36 | writ grants/denials/reassignments (per curiam) |
| **Rehearings** | 6 | rehearing dispositions |

Sample release 2026-037 (a "PER CURIAM" opinion release) contained 1 item.
Extrapolating naively from the audit's 2019 figure (1,859 LA Supreme
items on CL): **51 releases × ~36 items/release ≈ ~1,850/yr**, matching.
Across 2020→mid-2026 (~5.5 yr) that's **~275 releases and ~10K items**
to backfill — aligned with the audit's "~12K missing" estimate.

### Sitting Court (verified from the site nav)

The site's About/nav references 7 justices: **John L. Weimer** (Chief),
plus **McCallum, Griffin, Guidry, Cole, Burris, and Crain** (Crain
identified from the byline convention — 6 concurring/dissenting names
plus per-curiam authorship in the sample = 7-seat court, matching the
elected-Supreme-of-7 build-list note).

## 3. Circuit assignment key for `lactapp` — NOT in CL, comes from the PDF or a parish map

The build list flagged this as the second make-or-break unknown. Answer is
"it's not where you'd hope, but it's still tractable."

### What CL does NOT carry

Verified against three CL surfaces (auth token from `.env`):

- **`docket_number` = `YYYY CW NNNN`** (or `CA`, `CC`, `KA`, `KP`, `KH`,
  `JC`). Sample of 12 recent `lactapp` dockets: all use this format,
  **none encode the circuit**. This is different from AZ, where `1 CA-…`
  and `2 CA-…` prefixes cleanly split Div One from Div Two.
- **`appeal_from_str`** — **EMPTY on every recent `lactapp` docket** in the
  sample (12/12 blank). Ditto `appeal_from_id`.
- **`court_id`** — always `lactapp` (as expected — CL merges the 5 circuits).
- **Cluster metadata is threadbare.** Sample cluster 10942275 (a real
  `lactapp` opinion filed 2026-08-07) has: `case_name` = "Succession of
  Tom Frank Self", `citations` = `[]`, `citation_count` = 0, `judges` = "",
  `panel_names` = null, `headnotes` = "", `summary` = "",
  `precedential_status` = "Unknown", `source` = "C" (scraped from court
  site). **The circuit is nowhere in the JSON.**

### What DOES carry the circuit

Two orthogonal signals, both reliable:

1. **The PDF header itself.** Louisiana COA opinions by convention start
   with e.g. "STATE OF LOUISIANA / COURT OF APPEAL / FIRST CIRCUIT" (or
   "SECOND CIRCUIT" etc.) on page 1. This is universal across the five
   circuits' publication formats and is the primary signal. (Not
   independently verified on a lactapp PDF this session — lasc.org
   carries only Supreme PDFs, and CL's opinion-body fetch stalled twice
   under tonight's load. **Verify on a sample of ~10 lactapp opinions
   before implementing** — see "Recommended follow-up" below.)
2. **Parish → circuit is a deterministic statutory map.** Each of
   Louisiana's 64 parishes belongs to exactly one COA circuit by
   La. R.S. 13:312. The parish appears in every LA opinion caption
   (verified on the LA Supreme sample: `(Parish of East Baton Rouge)`
   under the caption). A ~64-entry hardcoded lookup covers all cases.
   Redundant to the header parse — good as a cross-check or a fallback
   when the header extractor misses.

### Consequence for the build

`assign_la_circuits` (Phase 7c) needs a two-step reader, not a one-line
docket-prefix classifier:
- Primary: regex on the first ~2KB of `raw_text` for
  `(?i)court of appeal[,\s]+(first|second|third|fourth|fifth)\s+circuit`.
- Cross-check / fallback: extract the parish from the caption, look it up
  in a static `PARISH_TO_CIRCUIT` map.
- Disagreement → log and skip (same posture as AZ's malformed division
  reporter — 37 rows left on Div 1 with a note).

The build list's `assign_az_divisions` template still applies for the
"update slim embedding table in lockstep" mechanics; only the
classification input changes.

## 4. Sample opinion — docket formats, disposition vocab, byline conventions

One opinion pulled through the full pipeline (`la-supreme-cd.pdf`, 43 pages,
1.24MB). This is a single point, not a corpus — enough to shape the parser
scope but not to freeze the disposition/byline lists.

### Docket format (LA Supreme)

```
YYYY-<TYPE>-NNNNN
```

Where TYPE is a 1–2 letter class code. On the sample: `2026-CD-00927`.
Known Supreme classes (from lasc.org's calendar + CL's usage on the sister
`lactapp` court): `CA`/`CC`/`CD` (civil), `KA`/`KP`/`KK`/`KH`/`KO` (criminal),
`B` (bar), `C` (misc). Full enumeration is a Phase-4 task; the parser
should treat the middle group as `[A-Z]{1,2}` and record it, not gate on it.

Note the **hyphen** in Supreme dockets (`2026-CD-00927`) vs the **space** in
CL's `lactapp` docket normalization (`2026 CW 0867`). The MN precedent
(normalize both to a single canonical form before comparing) applies —
`normalize_case_numbers` will need an LA branch.

### Disposition vocabulary (Supreme)

From the sample's page 1:

> STAY LIFTED. INJUNCTION LIFTED. REVERSED AND RENDERED.
> SEE PER CURIAM.

Louisiana Supreme dispositions are compact, all-caps, period-separated,
one line — **very parser-friendly**. Vocabulary observed (single opinion,
so a floor not a ceiling): `STAY LIFTED`, `INJUNCTION LIFTED`, `REVERSED`,
`RENDERED`, `SEE PER CURIAM`. Standard writ actions (not in sample but
documented) include `WRIT GRANTED`, `WRIT DENIED`, `NOT CONSIDERED`,
`AFFIRMED`, `REVERSED IN PART`, `REMANDED`, and rehearing-specific
`REHEARING GRANTED/DENIED`.

Two design implications for `parsing/la.py`:
- **The disposition sits on page 1 in the news-release block**, not at
  the end of the opinion (unlike NH's tail-of-opinion convention). Read
  it from the top matter, same shape as MN's "Filed:" header.
- **Writ actions dispose differently from full opinions** — the "PER
  CURIAM" full-opinion sample above disposes in *sentences*; typical
  writ Actions releases dispose in single-word verdicts (`WRIT DENIED`).
  Transcribe both faithfully; don't force-map writ vocab into
  common-law affirmed/reversed buckets (the NH historic-tier lesson).

### Byline / panel convention (Supreme)

From the sample:

```
PER CURIAM:
  <caption>
STAY LIFTED. INJUNCTION LIFTED. REVERSED AND RENDERED.
SEE PER CURIAM.
Weimer, C.J., dissents and assigns reasons.
McCallum, J., additionally concurs and assigns reasons.
Griffin, J., dissents and assigns reasons.
Guidry, J., dissents and assigns reasons.
Cole, J., additionally concurs and assigns reasons.
Burris, J., additionally concurs and assigns reasons.
```

Structure:
- **Authoring "panel" declaration** = either `PER CURIAM:` or
  `<SURNAME>, J.:` heading before the caption.
- **Individual justice actions listed below the disposition** — one line
  each, format `<SURNAME>, <J.|C.J.>, (dissents|concurs|additionally
  concurs) [and assigns reasons].`
- The **implicit panel** = all 7 sitting justices; the byline block names
  only those with a separate opinion. `resolve_judges` needs an LA branch
  that (a) infers the full 7-seat panel by court+date lookup, (b)
  extracts explicit dissent/concur lines to derive individual votes. This
  is a NEW pattern — MN/AZ/NH all use explicit panel names.
- The `dissents|concurs|additionally concurs` verbs map cleanly onto our
  existing `PanelVote.stance` values.

### PDF text-extraction quality

pypdf extracted the Fuller PDF cleanly: proper sentence-level text, no OCR
artifacts, no visible layout scrambling. Two minor cosmetic issues
(leading spaces broken from `FROM:` → `FR OM:`, `CAPACITY` → `C APACITY`)
are the standard pypdf single-letter break — the parser's whitespace
normalizer will absorb them the same way it does on MN/AZ. **No fuzzy
OCR-recovery layer needed for modern lasc.org PDFs.** Older/scanned PDFs
in the 2020-dead window may or may not be image-only; sample 5-10 from
2020-2021 before committing.

---

## Consequences for the build list

Referencing sections in `docs/LA_BUILD_LIST.md`:

- **Phase 0 recon:** COMPLETE. Update the doc to strike the `.gov`/`.org`
  typo, and to record the answers below.
- **Phase 4 (parser, the long pole):** shape is the same as MN's — top-of-
  document header block carries release#, docket, caption, parish,
  disposition, and byline. LA's addition vs MN is the byline/dissent
  block (a NEW pattern for `resolve_judges`, budget ~½ day extra).
- **Phase 5 (statutes):** unchanged — needs `La. R.S. <title>:<sec>` +
  `La. Civ. Code art. <n>` + siblings. Sample too small to enumerate
  vocabulary; do a 300-opinion frequency scan in Phase 5 itself
  (the MN pattern).
- **Phase 7 (bulk):** unchanged — `cl_bulk_filter.py --state LA
  --court-ids la,lactapp` gets everything CL has for LA in one pass.
  Given 200K + 144K = ~344K clusters and prior local runs, budget
  ~1-2hr filter + ~2hr NFSN load (similar to AZ).
- **Phase 7b (lasc.gov backfill):** SHAPE CONFIRMED as
  attended-listing / unattended-download. ~275 release pages need a
  Blazor-tolerant crawler (Playwright + `channel="chrome"`, same pattern
  as NH — the `scripts/nh_scraper/` template applies more than
  `scripts/mn_scraper/` because NH also drives a SPA with real Chrome).
  Downloads are direct via NFSN parallel curls. Effort estimate:
  ~1 day attended (browser listing, manifest build, occasional
  reCAPTCHA); ~2hr unattended (parallel download); ~2hr ingest.
- **Phase 7c (`assign_la_circuits`):** SHAPE CHANGED — cannot key on the
  docket prefix (the AZ pattern). Instead, PDF-header regex + parish
  fallback. Small script, ~2hr; the mechanics
  (`assign_az_divisions`-style batch UPDATE + slim-embedding-table
  lockstep on `(court_id, release_date, opinion_id)`) are unchanged.
  Add the ~64-entry `PARISH_TO_CIRCUIT` static map.
- **Phase 9-10 (judges):** LA Supreme's implicit-panel-with-explicit-
  dissenters convention needs its own byline branch in `resolve_judges`.
  Budget mirrors NH's dissent-footer extension work; ~½ day.
- **Risks 1-4 in the build list:**
  1. lasc.gov accessibility — **halved.** Listing walled (Blazor), PDFs open.
  2. Circuit key — **shifted from bulk-metadata to PDF-header parse.**
     Tractable; costs one new extractor + a static parish map.
  3. Writ/order composition — **confirmed real.** LA Supreme output is
     writ-heavy (36 Actions vs 9 Opinions in 2021). Decision needed:
     ingest all three types or only "Opinions"? Recommendation: **ingest
     all three,** matching CL's approach and the audit's ~12K figure.
     Writs are what lawyers cite when they say "the Supreme Court denied
     writs on this issue" — dropping them undersells the corpus.
  4. Civil-law citation coverage — untouched by this recon. Measure in
     Phase 6 on a 300-opinion sample, per plan.

---

## Recommended follow-up before Phase 4 kickoff

Two ~½hr items that would close the last soft edges:

1. **Fetch 5-10 real `lactapp` opinion PDFs** (one from each circuit,
   preferably one from ~2020 and one from ~2024) and confirm the
   "COURT OF APPEAL, <ORDINAL> CIRCUIT" header regex hits every one.
   The five circuit court sites are:
   - 1st: `www.la-fcca.org`
   - 2nd: `www.la2nd.org`
   - 3rd: `www.la3circuit.org`
   - 4th: `www.la4th.org`
   - 5th: `www.fifthcircuit.org`
2. **Sample 5 pre-2020 lasc.org PDFs** to confirm text-extraction
   quality holds on the older archive (are any image-only scans that
   need OCR?).

Both can be batched with the first attended lasc.gov session.

---

## FLP report #2 material

Everything in section 2 above (with the year-by-year counts from the
existing `docs/cl_coverage_audit/` on top) is the report. Framing: "we
built the tooling to fill this hole; here's what we learned about
lasc.org's structure that would let anyone else do the same." Emphasize
that **the actual PDF endpoints are open** — the effort to keep LA
Supreme current from now on is much smaller than the initial backfill.

## Reproducibility

All measurements taken 2026-08-08. Artifacts saved to the session
scratchpad (`la-supreme-cd.pdf`, `la-clusters.json`,
`lactapp-clusters.json`, `lactapp-dockets.json`, `lactapp-full.json`,
`lactapp-full-docket.json`, `la-release-014.html`). Reproducing the
critical PDF-fetchability finding:

```bash
curl -I "https://www.lasc.org/opinions/2026/NR037_26-0927.CD.OPN.pdf"
# expect: HTTP/1.1 200, Content-Type: application/pdf, Server: cloudflare
```

Reproducing the SPA-only-listing finding:

```bash
curl -sS "https://www.lasc.org/opinions?p=2026-014" | wc -c   # ~13,935
curl -sS "https://www.lasc.org/Opinions"           | wc -c   # ~13,935  (same shell)
```
