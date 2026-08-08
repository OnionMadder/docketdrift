# Louisiana — build list

What it takes to bring Louisiana online, derived from the MN/NH/AZ rollouts
(`docs/STATE_ROLLOUT.md` is the generic 12-phase runbook; this is the
LA-specific overlay) and the coverage audit
(`docs/cl_coverage_audit/FINDINGS.md`). Draft 2026-08-08 — a PLAN, not started.

LA is deliberately the **hardest rollout yet** and the most instructive: it's
the first state that needs **two ingest sources**, the first that uses
**`Court.division` at scale** (five COA circuits), and the first **civil-law**
jurisdiction (its statutes and the Civil Code cite unlike any common-law state).
It's paced behind landing MN with Free Law Project — the Supreme-court source
recon doubles as FLP report #2.

---

## The LA shape (why it's not just "AZ again")

| Court | CL id | CL status | Our source |
|---|---|---|---|
| Louisiana Supreme Court | `la` | **DEAD since 2020** — 1,859 (2019) → 0 (2021–22) → ~a few/yr. ~12,000 opinions/writ actions missing, the largest single-court hole in the U.S. | **pre-2020: CL bulk. 2020–present: direct from lasc.org** (the new-source build) |
| LA Courts of Appeal (1st–5th Circuits) | `lactapp` | **INTACT** — 1,700–2,500/yr through 2025 | CL bulk (`load_cl_bulk`), **then split into 5 circuit divisions** via `Court.division` |

Three structural firsts:
1. **Two-source Supreme ingest** — CL bulk for the healthy pre-2020 years,
   a **lasc.org** (not `.gov` — recon 2026-08-08 confirmed the domain)
   scraper for 2020→now. No existing state splits a single court's
   timeline across two sources.
2. **Five COA circuits under one CL id.** CL lumps all five under `lactapp`.
   We model them as five division courts (First–Fifth Circuit) using the
   `Court.division` field added 2026-08-07, assigned from the docket/circuit —
   exactly the AZ Div 1/Div 2 split, but ×5 and from day one.
3. **Civil law.** LA cites `La. R.S.` (Revised Statutes), `La. Civ. Code art.`,
   `La. Code Civ. Proc. art.`, `La. Ch. Code art.`, etc. — a different statute
   grammar than MN (`Minn. Stat.`), NH (`RSA`), AZ (`A.R.S.`). Reporter is the
   Southern Reporter (`So.2d` / `So.3d`), plus `La.` official cites.

---

## Reuse matrix — what carries over untouched

**Reusable as-is (the whole universal spine):**
- `load_cl_bulk`, `scripts/cl_bulk_filter.py` (COA + pre-2020 Supreme, offline, no API)
- `load_reporter_cites`, `load_parallel_cites`, `load_citation_edges` (from CL bulk)
- `ingest_pdfs` (the lasc.org Supreme PDFs), `ingest_court` (weekly incremental)
- `embed_opinions` + slim table, `suggest_tags`, `extract_holdings_text`
- `resolve_judges` (byline + panel; the 2026-08-07 parenthetical-citation guard
  already protects against cross-court leaks)
- `extract_citations` + `extract_statutes` **dispatchers** (add an LA module, below)
- `Court.division` (five circuits), state-router middleware, all templates,
  `check_freshness`, the semantic/search stack
- `assign_az_divisions` is the **template** for an `assign_la_circuits` command

**LA-specific NEW work (the build):**
1. Parser `opinions/parsing/la.py` — the long pole
2. Statute extractor `opinions/parsing/statutes_la.py` — civil-law patterns
3. Citation extractor `opinions/parsing/citations_la.py` — So.2d/So.3d + `La.`
4. **lasc.org Supreme source** — the genuinely new pipeline (recon first)
5. `Court.short_label` mappings + Phase-1 seed (6 court rows: Supreme + 5 circuits)
6. `assign_la_circuits` — split `lactapp` bulk into the five circuit divisions
7. Judge rosters — LA Supreme (7 elected justices) + 5 COA circuits

---

## Phased build list (LA overlay on the 12-phase runbook)

**Phase 0 — Scope + recon (½ day). LA-specific, do FIRST.** ✅ **DONE
2026-08-08** — full findings in `docs/LA_PHASE0_RECON.md`. Summary:
- [x] CL court ids confirmed: `la` (Supreme, 200,210 clusters), `lactapp`
      (all COA, 144,255 clusters, feed current through today).
- [x] **lasc.ORG recon** (the domain is `.org` not `.gov`; correcting below).
      **Split verdict:** the opinion listing is a Blazor SPA (JS-rendered,
      reCAPTCHA loaded, every URL returns the same 13.9KB shell to curl),
      but **the PDF endpoints are Cloudflare-fronted static assets and
      fetch cleanly from a plain server-side curl** (HTTP 200,
      `Content-Type: application/pdf`, no auth, no wall). So Phase 7b's
      shape is **residential browser LISTS → NFSN DOWNLOADS**, mirroring MN
      — much less attended than a full walled backfill. URL scheme:
      release calendar `/courtactions/<YYYY>`, release page
      `/opinions?p=<YYYY>-<NNN>`, PDF
      `/opinions/<YYYY>/NR<NNN>_<YY>-<NNNN>.<TYPE>.OPN.pdf`. ~51
      releases/year × ~5.5 dead years ≈ ~275 release pages to enumerate,
      ~10K PDFs to download — matches the audit.
- [x] **Circuit assignment key** — NOT in the CL bulk (docket_number has
      no circuit, `appeal_from_str` is empty on every recent lactapp,
      cluster/docket metadata is threadbare — `source=C`, no citations,
      no judges). Two workable signals **outside** CL: (a) the PDF header
      ("COURT OF APPEAL, <FIRST|SECOND|…> CIRCUIT" on page 1, industry
      standard); (b) the parish in the caption, cross-referenced against
      a ~64-entry static `PARISH_TO_CIRCUIT` map (La. R.S. 13:312).
      **Shipping shape:** `assign_la_circuits` reads the PDF header,
      falls back to parish. Header extractor must be independently
      verified on ~10 real lactapp PDFs before Phase 7c ships — see the
      recon doc.
- [x] Sampled LA Supreme opinion (43-page Fuller v. State, 2026-CD-00927):
      docket format `YYYY-<TYPE>-NNNNN` (Supreme uses hyphens vs
      lactapp's spaces — `normalize_case_numbers` needs an LA branch);
      disposition sits on **page 1 in the news-release header block**
      (not the tail — the NH pattern doesn't apply); byline convention
      is **implicit 7-seat panel + explicit dissent/concur lines**
      (`<SURNAME>, <J.|C.J.>, dissents and assigns reasons.`), a NEW
      pattern for `resolve_judges` — costs ~½ day of extension work.
      Writ dispositions are one-word verdicts (`WRIT DENIED` etc.);
      transcribe faithfully, do NOT force-map into common-law buckets
      (the NH historic-tier lesson).

**Phase 1 — Seed State + 6 Court rows (30 min).** LA + Supreme (`la`, division
"") + five COA circuits (`lactapp` split; give each a distinct
`courtlistener_id` since ours is unique — e.g. `lactapp-1`…`lactapp-5`, with
`lactapp` as the bulk-ingest landing court like AZ's `arizctapp`).

**Phase 2 — `la.docketdrift.com` subdomain alias (10 min, member panel).**

**Phase 3 — `short_label`s (10 min):** `La.` (Supreme), `La. Ct. App. 1st Cir.`
… `5th Cir.` (the division-aware `short_label` from 2026-08-07 handles the
`Div.`-style suffix; extend it for `Cir.`).

**Phase 4 — Parser `parsing/la.py` (1–2 days — the long pole, ×2 courts).**
case_number / release_date / disposition / byline for BOTH the Supreme (writ +
appeal dispositions) and COA formats. Budget extra: LA dispositions include
writ grant/deny/"not considered" that don't map to the common-law
affirmed/reversed vocabulary — transcribe, don't force-map (the NH historic-tier
lesson). Two courts with different layouts = test both paths independently
(the AZ-Supreme-never-parsed lesson).

**Phase 5 — Statute extractor `statutes_la.py` (½ day).** `La. R.S. <title>:<sec>`,
`La. Civ. Code art. <n>`, `La. Code Civ. Proc. art.`, `La. Ch. Code`, `La. Code
Crim. Proc.`. Register in the dispatcher. Civil-law = the Civil Code articles
are heavily cited; get those right.

**Phase 6 — Citation extractor `citations_la.py` (½ day).** So.2d/So.3d + `La.`
official; resolve against `reporter_cite` ∪ `ParallelCite` ∪ docket. Reuse the
AZ/MN module shape; measure resolvability on a 300-opinion sample before
trusting scope (the parallel-cite lesson: official `La.` cites may resolve 0%
until `load_parallel_cites` runs).

**Phase 7 — Bulk corpus ingest (1–2 hr local filter + 1–2 hr NFSN).**
`cl_bulk_filter.py --state LA --court-ids la,lactapp` → `load_cl_bulk`. Gets
the intact COA + pre-2020 Supreme in one offline pass, zero API.

**Phase 7b — lasc.org Supreme backfill 2020→present (NEW, attended if walled).**
The genuinely new pipeline: scrape the ~12K missing Supreme opinions/writs from
lasc.org → `ingest_pdfs --state LA --court supreme`. Mirror the MN archive
recipe: residential browser LISTS (manifest), NFSN downloads, chunked ingest,
**reconcile after** (a skipped window is not an empty one). Size + attendance
depend on the Phase-0 recon.

**Phase 7c — `assign_la_circuits` (½ day, models on `assign_az_divisions`).**
Split the `lactapp` bulk rows into the five circuit divisions by the Phase-0
key; update the slim embedding table's `court_id` in lockstep.

**Phase 8 — Derived layers, UNATTENDED (~overnight for the corpus).**
`embed_opinions` (overnight tick) → `suggest_tags` (WAIT for embeddings —
placeholder vectors) → `resolve_judges` → `extract_statutes` → `extract_citations`
→ `extract_holdings_text`. Same order + cull-safe chunking as AZ/MN.

**Phase 9–10 — Judge rosters + validation (½ day).** LA Supreme 7 elected
justices + 5 COA circuits, from the courts' sites (browser through any Akamai,
per the AZ bench work). Spot-check pages render on all 6 courts; run the
`render_check` Client pattern if the intra-rack route is flaky.

**Phase 11 — Flip `is_live=True`, restart, update `/about/` + apex tile (10 min).**
Disclose honestly: Supreme 2020→present provenance (lasc.org, not CL), and
whatever completeness ceiling the scrape hits — same posture as MN's rebuilt
years.

**Phase 12 — Weekly cron.** Add LA to the live-court auto-discovery
(`cron-ingest.sh`); add a lasc.org forward-fill wrapper if the Supreme source
needs it (mirror `run_mn_weekly.ps1`), plus a success beacon into
`freshness_check`.

---

## Rough effort

| Track | Effort | Attended? |
|---|---|---|
| Recon (Phase 0, incl. lasc.org) | ½ day | partly |
| Parser + statute + citation modules (4–6) | ~2.5 days | no |
| CL bulk ingest + circuit split (7, 7c) | ~1 day | no |
| lasc.org Supreme backfill (7b) | 1–3 days | **yes (CAPTCHAs), if walled** |
| Derived layers (8) | overnight | no |
| Judges + validation + flip (9–11) | ~1 day | partly |

**~5–7 working days**, the lasc.org backfill being the variable — if lasc.org
is open (no bot wall) it collapses to an unattended fetch; if walled like AZ,
it's the MN-style attended sweep.

## Biggest risks / unknowns (resolve in Phase 0)
1. **lasc.org accessibility** — open vs Akamai-walled decides 7b's shape and
   whether the whole rollout is mostly-unattended or needs keyboard time.
2. **Circuit assignment key** — if the `lactapp` bulk rows don't cleanly encode
   which circuit, the 5-way split needs another signal (court_name, docket).
3. **Writ/order composition** — LA Supreme output is writ-heavy; decide what
   counts as an "opinion" vs an order, and disclose the mix (the audit's caveat).
4. **Civil-law citation coverage** — the Civil Code article grammar is new;
   measure resolvability before claiming a citation graph.

## First move (when LA is greenlit)
Do **Phase 0 recon only** first — especially the lasc.org probe and the circuit
key — because those two answers size the entire rest of the build. Everything
downstream is well-trodden (it's the AZ rollout + `Court.division` + a second
source). The recon is also the deliverable FLP wants for report #2, so it earns
its keep even if the full rollout stays paced.
