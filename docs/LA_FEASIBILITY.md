# Louisiana — feasibility scan (state #4)

**Status: INVENTORY + ESTIMATE ONLY. Nothing was built, seeded, migrated, or
committed.** This document exists so we walk into a conversation with a LA
practitioner (Marcus Plaisance) holding real numbers instead of vibes.

Scan run 2026-06-27. All counts pulled live from the CourtListener REST API
v4; all court-site probes are GET/HEAD header checks from this dev box (a
non-residential IP — the same vantage that exposed NH/AZ's Akamai walls).

**TL;DR**

- **CourtListener coverage is excellent** — better than NH or AZ. ~322K LA
  opinions already in CL with PDFs mirrored, current to June 2026. We do
  **not** have to scrape the courts for the corpus.
- **No court site bot-blocks us.** Unlike NH (`courts.nh.gov`) and AZ COA,
  every LA appellate site answers a plain GET. Cloudflare sits in front of 3
  of them but only rejects HEAD; GETs and direct PDF fetches return 200.
- **eyecite parses LA *case* cites cleanly** (`So. 2d` / `So. 3d`, and even the
  public-domain `04-1721 (La. App. 1 Cir. 3/24/05)` form). LA **statute/code**
  cites need a custom extractor — but that's true of every state.
- **The real cost driver is corpus size.** LA is ~5× MN. Embedding the full
  historical corpus is a multi-week overnight tail. A modern-first cut
  (post-2000 ≈ 103K, or post-2010 ≈ 47K) is the sane v1 and lands in ~1.5–2
  weeks.
- **Civil-law wrinkle that actually bites:** the statute parser needs 5–6
  separate citators (R.S., Civ. Code, C.C.P., C.E., C.Cr.P., Const.) instead of
  one, and LA Supreme's CL count is inflated by thin writ-disposition orders
  that need to be distinguished from merits opinions.

---

## 1. CourtListener coverage

CL maps Louisiana to **two in-use court IDs** (the 5 geographic Circuits of the
Court of Appeal are aggregated under one slug, exactly like AZ's `arizctapp`):

| CL court id | Court | CL opinion count | Date range (dateFiled) | PDFs? | API status |
|---|---|---:|---|---|---|
| `la` | Supreme Court of Louisiana | **199,175** | 1809-07-01 → 2026-06-01 | Yes — `download_url` to `lasc.org` + CL-mirrored `local_path` | 200 OK (authed) |
| `lactapp` | Louisiana Court of Appeal (all 5 Circuits aggregated) | **122,874** | 1881-07-01 → 2026-06-24 | Yes — `download_url` to circuit sites + CL-mirrored `local_path` | 200 OK (authed) |
| `laag` | Louisiana Attorney General Reports | (not counted) | — | — | exists but **out of scope** (AG opinions, not court opinions) |

**Total in-corpus: ~322,049 documents.** PDFs are broadly available, not just
recent — both sampled records carry a CL-hosted `local_path` PDF *and* a live
`download_url` to the originating court. I downloaded two live PDFs end-to-end
(a 2026 `lasc.org` Supreme PDF, 1.2 MB, and a 2026 `la2nd.org` 2nd-Circuit PDF,
335 KB) — both `200 application/pdf`. CL also exposes `plain_text` per
sub-opinion, so the bulk-dump path gives us text directly (no PDF extraction
required for the backfill).

### Freshness

Both courts are **current in CL** (Supreme to 2026-06-01, COA to 2026-06-24).
This matters: it means the Phase 7 bulk dump catches us up to ~now, and the
Phase 12 weekly CL-API cron keeps us current afterward — the same posture as
MN, *not* the "CL lags so we must scrape" posture that forced the NH/AZ
residential-Playwright builds.

### Corpus-size reality check (the headline number)

The full `la` count (199K) is **inflated by writ-disposition orders** — the
Louisiana Supreme Court issues thousands of one-line writ grant/denial
dispositions that CL indexes as "opinions." That's why `la` (a court of
discretionary review) shows *more* documents than `lactapp` in the modern era,
which is backward for a real merits-opinion comparison. Treat 199K as a
document count, not a merits-opinion count. Modern-era cuts:

| Window | `la` | `lactapp` | Combined |
|---|---:|---:|---:|
| All history | 199,175 | 122,874 | **322,049** |
| post-1990 | 102,992 | 55,347 | **158,339** |
| post-2000 | 66,746 | 36,596 | **103,342** |
| post-2010 | 29,275 | 17,786 | **47,061** |

For comparison: MN's *entire* live corpus is 60,375. So **post-2010 LA ≈ AZ-to-MN
sized; post-2000 LA ≈ 1.7× MN; full-history LA ≈ 5× MN.** This single choice —
how far back to go — dominates the whole estimate (Phase 7 + Phase 8).

---

## 2. LA court-site stack inventory

Probed from this (non-residential) dev box. **Key result: nothing bot-walls
us.** Three sites front with Cloudflare but only reject the HEAD method
(302 → `/Error/405`); a plain GET returns 200 and direct PDF fetches succeed.

| Court | URL | Server | CDN | GET status | Residential-Playwright needed? |
|---|---|---|---|---|---|
| Supreme | `www.lasc.org` | ASP.NET | **Cloudflare** | 200 (HEAD→405) | No for PDFs/CL; **maybe** for HTML (see note) |
| 1st Cir | `www.la-fcca.org` | ASP.NET | **Cloudflare** | 200 | No |
| 2nd Cir | `www.la2nd.org` | Apache (WordPress) | none | 200 | No |
| 3rd Cir | `www.la3circuit.org` | ASP.NET | **Cloudflare** | 200 | No |
| 4th Cir | `www.la4th.org` | Microsoft-IIS/10 | none | 200 | No |
| 5th Cir | `www.fifthcircuit.org` | Microsoft-IIS/10 | none | 200 | No |

All six URLs from the brief verified and correct.

**Sitemaps:** `lasc.org/sitemap.xml` redirects (HEAD→405; a GET likely returns
the Cloudflare shell, see below). The circuit sites are a mix of WordPress
(`la2nd.org` — has a real sitemap) and DNN/ASP.NET. We don't need sitemaps for
ingest (CL has the corpus); they'd only matter for a future direct-scrape
currency path.

**The one caveat — lasc.org HTML may be a Cloudflare JS-challenge shell.**
`GET /` and `GET /opinions/` both returned **200 but identical 12,516-byte
bodies** for two different paths — the signature of a "checking your browser"
interstitial rather than real page content. So *HTML scraping* of lasc.org
might still need a real browser. **But this does not block us:** the opinion
PDFs are directly fetchable (confirmed), and CL already mirrors the full
corpus. Contrast with NH/AZ, where the court site was the *only* source and the
block was total (403 on every path including the PDF). LA's worst case is
"can't scrape the Supreme Court's HTML index page," which we route around via
CL entirely.

**Net vs NH/AZ:** LA is the *easiest* state yet on the access axis. NH needed
residential Playwright for both judges and opinions; AZ COA needed it for
judges. LA needs it for **nothing on the corpus path** and at most for
nice-to-have judge-bio HTML.

---

## 3. Citator format sketch (DO NOT BUILD — estimate input only)

Louisiana is a civil-law jurisdiction, so the citation surface is wider than
any common-law state we've done: **one Revised-Statutes citator plus four
code-article citators plus the constitution**, versus the single `Minn. Stat.`
/ `RSA` / `A.R.S.` pattern elsewhere. Below is the parser sketch that would
feed a `statutes_la.py` module (multi-pass, mirroring how `statutes_mn.py`
already runs two passes). These regexes are **illustrative**, not tested
against a real LA corpus sample.

### 3a. La. R.S. (Louisiana Revised Statutes) — title:section

Forms seen in LA prose: `La. R.S. 13:5106`, `La. R.S. § 13:5106`,
`La. R.S. 14:30`, `La. Rev. Stat. Ann. § 23:1021`, `LSA-R.S. 9:2800` (the
West "LSA-" prefix is extremely common in older LA opinions).

```python
# title:section, optional § , optional (subsection), optional West "LSA-" prefix
LA_RS = re.compile(
    r'\b(?:La\.\s*)?(?:LSA-)?R\.?S\.?\s*(?:Ann\.\s*)?'
    r'§?\s*'
    r'(?P<title>\d{1,2})'
    r':(?P<section>\d{1,4}(?:\.\d{1,4})?)'
    r'(?:\((?P<sub>[A-Z0-9]+)\))?',
    re.I)
# slug: la.rs.<title>.<section>[.<sub>]   display: "La. R.S. <title>:<section>"
```

Note `La. Rev. Stat. Ann.` long-form and the `LSA-R.S.` West reprint prefix —
both must be accepted or a large fraction of pre-2000 cites are missed.

### 3b. La. Civ. Code (Civil Code) — article

Forms: `La. Civ. Code art. 2315`, `La. C.C. art. 2315`, `La. Civ.Code Ann.
art. 2315`, `LSA-C.C. art. 2315`, `art. 2315` (bare, when context already
established — **do not** match bare `art.` globally; too noisy).

```python
LA_CC = re.compile(
    r'\b(?:La\.\s*)?(?:LSA-)?C(?:iv)?\.?\s*C(?:ode)?\.?\s*(?:Ann\.\s*)?'
    r'art(?:icle)?s?\.?\s*'
    r'(?P<article>\d{1,4}(?:\.\d{1,3})?)'
    r'(?:\((?P<sub>[A-Z0-9]+)\))?',
    re.I)
# slug: la.cc.art.<article>   display: "La. Civ. Code art. <article>"
```

### 3c. La. C.C.P. (Code of Civil Procedure) — article

Forms: `La. C.C.P. art. 966`, `La. Code Civ. Proc. art. 966`,
`LSA-C.C.P. art. 966`, `La. Code Civ. Proc. Ann. art. 966`.

```python
LA_CCP = re.compile(
    r'\b(?:La\.\s*)?(?:LSA-)?C(?:ode)?\.?\s*C(?:iv)?\.?\s*P(?:roc)?\.?\s*'
    r'(?:Ann\.\s*)?art(?:icle)?s?\.?\s*'
    r'(?P<article>\d{1,4}(?:\.\d{1,3})?)',
    re.I)
# slug: la.ccp.art.<article>   display: "La. C.C.P. art. <article>"
```

### 3d. La. C.Cr.P. (Code of Criminal Procedure) — article

Forms: `La. C.Cr.P. art. 703`, `La. Code Crim. Proc. art. 404`,
`LSA-C.Cr.P. art. 703`.

```python
LA_CCRP = re.compile(
    r'\b(?:La\.\s*)?(?:LSA-)?C(?:ode)?\.?\s*Cr(?:im)?\.?\s*P(?:roc)?\.?\s*'
    r'(?:Ann\.\s*)?art(?:icle)?s?\.?\s*'
    r'(?P<article>\d{1,4}(?:\.\d{1,3})?)',
    re.I)
# slug: la.ccrp.art.<article>   display: "La. C.Cr.P. art. <article>"
```

### 3e. La. C.E. (Code of Evidence) — article

Forms: `La. C.E. art. 401`, `La. Code Evid. art. 401`, `LSA-C.E. art. 401`.

```python
LA_CE = re.compile(
    r'\b(?:La\.\s*)?(?:LSA-)?C(?:ode)?\.?\s*E(?:vid)?\.?\s*'
    r'(?:Ann\.\s*)?art(?:icle)?s?\.?\s*'
    r'(?P<article>\d{1,4})',
    re.I)
# slug: la.ce.art.<article>   display: "La. C.E. art. <article>"
```

**Disambiguation hazard:** `C.C.`, `C.C.P.`, `C.Cr.P.`, `C.E.` share a prefix.
Order matters — match the *longest/most-specific* code abbreviation first
(C.Cr.P. before C.C.P. before C.C.), the same "strongest signal wins" ordering
the treatment classifier already uses. A naive C.C. regex will eat C.C.P.
cites. This is the single fiddliest part of the LA statute parser.

### 3f. La. Const. (Constitution)

Forms: `La. Const. art. I, § 2`, `La. Const. Ann. art. V, § 16`. Low frequency;
out of scope for v1 the same way `N.H. Const.` and `Ariz. Const.` are skipped
in the existing extractors.

### 3g. Neutral / public-domain cites + docket numbers

- **LA adopted public-domain ("medium-neutral") citation.** Modern opinions
  carry a docket-anchored cite: `<docket> (La. <M/D/YY>), <So.3d page>` —
  e.g. `Succession of Faget, 10-0188 (La. 11/30/10), 53 So. 3d 414`, and for
  the COA `Doe v. Roe, 04-1721 (La. App. 1 Cir. 3/24/05), 899 So. 2d 707`.
  The parenthetical encodes court + exact decision date.
- **There is no `<year> La. <n>` neutral reporter like NH's `2026 N.H. 7`.**
  LA still cites to **So. / So. 2d / So. 3d** (Southern Reporter) as the
  canonical reporter cite, with the public-domain docket parenthetical layered
  in. So LA's `Opinion.reporter_cite` would be the **Southern Reporter cite**
  (e.g. `53 So. 3d 414`), which — like MN/AZ and unlike NH — is assigned
  **post-publication by West and is not in the slip-opinion text**. This puts
  LA's citation graph on the **same CL-reporter-cite-backfill gate as MN/AZ**
  (see §4 and §6).
- **Docket-number shapes** (for the Phase-4 parser, distinct from citations):
  - LA Supreme: `YYYY-XX-NNNN` where `XX` is a writ/appeal-type code —
    `2023-CC-1234` (civil writ), `2023-CD`/`CK`/`KK`/`KA`/`KO`/`KP`/`KH`
    (criminal), `2023-OB`/`OC` (bar/disciplinary), `CA` (civil appeal). Older
    forms drop the century: `04-1721`. The two-letter code is **load-bearing**
    — it's how you'd separate merits opinions from writ dispositions.
  - LA COA: `YY-NNNN` plus a circuit qualifier in the cite (`(La. App. 1 Cir.
    ...)`); the slip PDFs name files like `56784ca.pdf` (`ca` = civil appeal).

---

## 4. eyecite handling check (smoke-tested locally)

eyecite **2.7.7** is installed in the dev venv (it is deliberately NOT on NFSN
— FreeBSD C-extension risk — and the wrapper isn't wired into production yet).
I ran ~20 hand-crafted, representative LA citation strings through
`eyecite.get_citations()`. Verdict by category:

### Case citations — eyecite handles them CLEANLY ✅

| Input | eyecite result |
|---|---|
| `Pitre v. Opelousas Gen. Hosp., 530 So. 2d 1151 (La. 1988)` | `FullCaseCitation` `530 So. 2d 1151` ✅ |
| `Loescher v. Parr, 324 So. 2d 441 (La. 1975)` | `FullCaseCitation` `324 So. 2d 441` ✅ |
| `... 296 So. 3d 1234 (La. 2020)` | `FullCaseCitation` `296 So. 3d 1234` ✅ |
| `Succession of Faget, 10-0188 (La. 11/30/10), 53 So. 3d 414` | `FullCaseCitation` `53 So. 3d 414` ✅ (drops the public-domain half) |
| `State v. Draughn, 2005-1825 (La. 1/17/07), 950 So. 2d 583` | `FullCaseCitation` `950 So. 2d 583` ✅ (drops public-domain half) |
| `Doe v. Roe, 04-1721 (La. App. 1 Cir. 3/24/05), 899 So. 2d 707` | **two** cites: `04-1721 (La.App. 1 Cir. 3/24/05)` **and** `899 So. 2d 707` ✅ |
| in-prose paragraph w/ 2 case cites + 2 statute cites | found both `So.` case cites, ignored statutes ✅ |

So the Southern Reporter (`So.`, `So. 2d`, `So. 3d`) is fully in eyecite's
reporters DB, and it even recognizes the LA public-domain
`NN-NNNN (La. App. N Cir. M/D/YY)` form as a citation. **The existing
`citations_eyecite.py` wrapper would work for LA case cites with zero changes**
— same as the MN/AZ plan.

One inconsistency to note: eyecite caught the public-domain parenthetical for
the *COA* example but **not** for the bare-`(La. M/D/YY)` *Supreme* examples
(it only returned the `So.3d` half there). Since our `reporter_cite` would be
the Southern cite anyway, this doesn't hurt resolution — but it means we can't
rely on eyecite alone to harvest the public-domain docket cite if we ever want
it.

### Statute / code citations — eyecite finds NOTHING ❌ (expected)

Every `La. R.S.`, `La. Civ. Code art.`, `La. C.C.P.`, `La. C.Cr.P.`, `La. C.E.`,
and `La. Const.` string returned **zero** citations. This is exactly the same
as MN/AZ/NH: statute extraction is **always** a custom per-state module
(`statutes_<code>.py`), never eyecite. So this is not a LA-specific problem —
it's the existing architecture. The only LA-specific cost is that the statute
module is ~5–6 citators instead of one (§3).

### Which way it lands

**eyecite handles LA case cites cleanly; LA statute cites need the custom
`statutes_la.py` we'd write anyway.** The citation-*graph* half (OpinionCitation
edges between cases) is *solvable via the existing eyecite wrapper* — the LA
work there is wiring, not building, and it's gated on the same CL reporter-cite
backfill as MN/AZ, not on any LA civil-law quirk.

---

## 5. State-rollout effort estimate

Benchmarked phase-by-phase against `docs/STATE_ROLLOUT.md`. "Standard" =
the runbook's own per-state-after-the-first numbers. Deltas are LA-specific.

| Phase | Standard | LA-specific delta | LA estimate |
|---|---|---|---|
| 0. Decide scope | 30 min | **Big decision: full-history (322K) vs modern-first (post-2000 ≈103K / post-2010 ≈47K).** Drives everything downstream. | **1–1.5 h** |
| 1. Seed State + Court rows | 30 min | 2 courts (`la`, `lactapp`). 5 Circuits collapse into one APPEALS row — model's `unique_together=(state, level)` allows only one (matches CL's `lactapp` aggregation; see §6 risk). | **30–45 min** |
| 2. NFSN subdomain alias | 10 min + cert wait | none | **10 min** |
| 3. `Court.short_label` | 10 min | `La.` / `La. Ct. App.` (Bluebook). Trivial. | **10 min** |
| 4. Parser (`parsing/la.py`) | 4–8 h (the long pole) | **Civil-law + writ-heavy.** Supreme docket codes (CC/CD/KK/KA/OB…) must separate merits from writ dispositions; 5 Circuits may format bylines/panels differently; per-curiam-heavy; public-domain cite header. Bigger surface than any prior state. | **10–14 h** |
| 5. Statute extractor (`statutes_la.py`) | 2–3 h (one citator) | **5–6 citators** (R.S., Civ. Code, C.C.P., C.Cr.P., C.E., Const.) + the C.C./C.C.P./C.Cr.P. prefix-disambiguation ordering + the `LSA-` West-prefix variants. ~3–4× the single-citator surface. | **6–10 h** |
| 6. Judge roster | 1–2 h (or skip) | LA Supreme = 7 justices (lasc.org HTML may be a Cloudflare shell → hand-enter or residential browser). 5 Circuits ≈ 50–60 judges across 6 mixed sites. Or accept byline-learned partial coverage (Option A) = 0 scraping. | **6–8 h scraped, or 0 (defer)** |
| 7. Bulk corpus ingest | 1–2 h local + 1–2 h NFSN | **Corpus is ~5× MN at full history.** Filter sweep + tar/scp + `load_cl_bulk` all scale with size. Add `la`,`lactapp` to `STATE_COURT_CL_IDS`. | **full: 8–12 h · post-2000: 3–4 h · post-2010: 2–3 h** |
| 8. Downstream (embed/tags/judges) | ~9 h unattended (50K) | **THE TAIL.** Embed is O(corpus). Throttled to the 6 h overnight window. See projection below. | **full: ~10 overnight windows · post-2000: ~3 windows · post-2010: ~1–2 windows** |
| 9. Statute extraction over corpus | 5–10 min | Needs Phase-5 multi-citator module; runtime scales with 322K (pk-windowed batching already hardened). | **20–45 min** |
| 10. Validation | 30 min | + spot-check writ-disposition vs merits rendering, civil-law disposition pills | **45 min** |
| 11. Flip `is_live` + About | 10 min | none | **10 min** |
| 12. Weekly cron | automatic | `la`+`lactapp` auto-join the CL-API refresh once live; both fresh in CL | **automatic** |

### Embed-tail projection (Phase 8 detail)

Anchored to MN: 60,375 opinions → ~9 h embed. LA opinions skew **shorter** on
average (writ dispositions, older terse opinions), so per-doc cost is likely a
bit lower than MN's — treat these as upper bounds.

| Scope | Docs | Embed wall-clock @ MN rate | Overnight 6 h windows | ~Voyage cost |
|---|---:|---|---|---:|
| post-2010 | 47K | ~7 h | 1–2 nights | ~$6 |
| post-2000 | 103K | ~15 h | ~3 nights | ~$12–15 |
| full history | 322K | ~48 h | ~8–10 nights (or 2–3 babysat `--max-runtime 0` days) | ~$35–45 |

(`suggest_tags` ≈ minutes; `resolve_judges` ≈ tens of minutes — both negligible
beside embed.)

### Wall-clock milestones

- **"First opinions live on `la.docketdrift.com`"** — minimum-viable path
  (Phases 1, 2, 3, 7, partial-8-embed, 11) with the parser as the real gate.
  Browse + corpus can ship before the embed tail finishes (semantic search just
  degrades until it does). **≈ 2–3 attended days** once the parser is written;
  faster if you ship the modern cut and let embed run behind it.
- **"Fully embedded + statute-extracted"**
  - **Modern-first (post-2000):** ~4–6 attended days of build + ~3 overnight
    embed nights → **≈ 1.5–2 calendar weeks.**
  - **Full history (322K):** ~5–7 attended days + ~8–12 overnight embed nights
    → **≈ 3–4 calendar weeks** (the embed tail, not the code, is the long pole).

**Recommendation framing for the Marcus conversation** (not a build decision):
ship **post-2010 or post-2000 first** — it's MN-to-1.7×-MN sized, lands in ~2
weeks, and the full historical backfill can run as a slow overnight tail
afterward without blocking launch.

### Where civil-law breaks the runbook's common-law assumptions

The runbook is implicitly common-law. LA stresses it in four places:

1. **Phase 4 (parser)** assumes one opinion shape per tier. LA Supreme mixes
   merits opinions with high-volume **writ dispositions**; the docket-code
   letter is the only reliable separator. The runbook has no concept of
   "filter out non-merits documents."
2. **Phase 5 (statutes)** assumes **one** statutory citator per state
   (`Minn. Stat.` / `RSA` / `A.R.S.`). LA has a Revised-Statutes citator **plus
   four code-article citators plus** the constitution. The `statutes.py`
   dispatcher architecture supports this (the per-state module just returns a
   list), but the module is 5–6× the work and carries the prefix-collision
   ordering hazard (§3).
3. **Phase 0/1 + the Court model** assume ≤2 courts. CL's `lactapp` aggregation
   saves us here, but it **erases per-Circuit identity** (1st–5th). If Marcus
   wants per-Circuit filtering, that's a model change (`unique_together` +
   per-Circuit Court rows), not a config tweak.
4. **Citation graph / treatment** assumes precedent-based stare decisis. LA runs
   on **jurisprudence constante** and cites the **Code first, jurisprudence
   second** — so the "Authorities cited" panel will skew statute-heavy, and
   case-citation density per opinion is lower. The treatment classifier still
   *functions* (it keys on English court-voice cues like "we overrule"), but the
   doctrinal weight of an "overruled" signal is different in a civil-law system.

---

## 6. Risk register

**Biggest unknown:** *How much of the 199K `la` count is substantive merits
opinions vs. thin writ-disposition orders?* It determines the real corpus size,
embed cost, parser complexity, and whether search/landing pages get polluted
with one-line orders. This is the first thing to measure before committing
(pull 500 `la` docs, classify by docket code + length).

**Most likely to take 3× the estimate:** the **Phase-4 parser** — civil-law
docket codes + writ-vs-merits separation + 5-Circuit byline variance is a
materially bigger surface than MN/NH/AZ, and parser work is already the runbook's
long pole. The **multi-citator statute parser** (Phase 5) is the runner-up.

**What would block shipping at all:** *nothing hard-blocks.* CL coverage is
excellent, no site bot-walls the corpus path, eyecite handles the case cites.
The only true gap is a **feature**, not a blocker (next item).

Ranked risks:

1. **Writ-disposition noise (HIGH / likely).** ~199K `la` docs include thousands
   of non-merits writ grants/denials. Without a filter they bloat the corpus,
   inflate embed cost, and dilute search. *Mitigation:* parse the docket-code
   letter to tag/segregate writ dispositions; consider excluding them from v1 or
   flagging them in the UI. Adds parser effort.
2. **Embed tail on full history (HIGH / certain if full-history chosen).** 322K
   docs = multi-week overnight embed under the 6 h window. *Mitigation:* ship
   modern-first; backfill history as a slow tail. Cost ~$35–45 also wants a
   spend OK from the Author (Voyage > a few dollars → flag rule).
3. **Citation graph gated on CL reporter-cite backfill (MEDIUM / structural).**
   LA's canonical cite is the Southern Reporter cite, assigned post-publication
   and absent from slip text — same gate as MN/AZ. So the KeyCite/Shepard's
   "Citing references" feature is **not** a day-1 LA deliverable; it waits on the
   same backfill. eyecite handles the parsing; resolution is the blocker.
4. **Multi-citator statute parser complexity (MEDIUM).** 5–6 citators + C.C./
   C.C.P./C.Cr.P. prefix disambiguation + `LSA-` variants. Mis-ordered regexes
   silently mis-slug cites. *Mitigation:* longest-match-first ordering, test
   against a real LA opinion sample before the full sweep.
5. **lasc.org HTML is a Cloudflare JS-challenge shell (LOW).** Blocks *HTML*
   scraping of the Supreme index, not the corpus (CL has it) or the PDFs (direct
   fetch works). Only matters for judge-bio scraping and post-CL currency.
   *Mitigation:* CL for corpus + currency; hand-enter the 7 Supreme justices, or
   residential browser if we want bios.
6. **Per-Circuit identity loss (LOW / product decision).** CL aggregates the 5
   Circuits; our model holds one APPEALS row. Fine unless per-Circuit filtering
   is a wanted feature — then it's a schema change.
7. **Civil-law disposition vocabulary (LOW).** "Writ granted," "writ denied,"
   "remanded" may not map cleanly onto the existing `disposition_bucket` legend;
   the Outcomes legend may need LA-specific buckets.

---

## Appendix — method & provenance

- CL counts/date-ranges: live `GET /api/rest/v4/search/?type=o&court=<id>` (authed
  with the project token), 2026-06-27. Court IDs confirmed via
  `/api/rest/v4/courts/<id>/`. The 5 Circuits are **not** separate CL IDs
  (`lactapp1`…`5` all 404); they aggregate under `lactapp`.
- PDF availability: inspected `download_url` + `local_path` on the latest
  sub-opinion per court; downloaded two live PDFs end-to-end (200 / `application/
  pdf`).
- Site stack: `curl -I` (HEAD) + disambiguating `curl` (GET) per site; the
  lasc.org 405 is a HEAD-method rejection, not a bot block.
- eyecite: v2.7.7, `get_citations()` over ~20 hand-crafted LA strings; scratch
  script not committed.
- Nothing was written to the DB, no migrations, no `States`/`Courts` rows, no
  commits. The two `parsing/citations_eyecite*` and `statutes_*` modules were
  read, not modified.
</content>
</invoke>
