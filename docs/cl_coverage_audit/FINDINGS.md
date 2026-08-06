# CourtListener state-court coverage audit — findings (2026-08-06)

**Method** (proven on Minnesota, juriscraper#1115): read CL's own bulk export
(`2026-03-31`), count opinion clusters per court per year, and watch two
tells — a year count cratering against the court's own 2012–2019 median, and
the **unpublished stream dying first** (the MN signature: 2018 carried 213
rows, all Published, where 2016 was 1,138 Unpublished of 1,451).

Run: `python cl_coverage_audit.py cl_coverage_summary.csv` on the box holding
`C:/Users/kelly/courtlistener-bulk/` (~10 min; streams 7GB of bz2, no API
calls, no bot walls). `cl_coverage_summary.csv` here is the full output:
every state supreme/appellate court × year 1990–2025 × published/unpublished.

**Headline: Minnesota is not an outlier — 17 courts show the same shape,
totaling roughly 60–80K missing opinions across 2023–2025 alone.**

## Tier 1 — sustained, multi-year, near-total

| court | baseline/yr | 2023–2025 |
|---|---|---|
| `la` Louisiana Supreme | 2,409 | **0–2%** (dead since 2020 — see below) |
| `mich` Michigan Supreme | 2,279 | 1–2% |
| `nev` Nevada Supreme | 1,615 | 4–6% — **only court with the unpub-died tell** |
| `nc` North Carolina Supreme | 1,075 | 5–7% |
| `indctapp` Indiana COA | 2,521 | 11–15% |
| `ohio` Ohio Supreme | 1,232 | 15–17% |
| `tenncrimapp` Tennessee Crim. App. | 945 | 8% (2023) |

## Tier 2 — fresh collapses (2024–2025)

`txctapp5` 3,128/yr → **25 in 2025** (1%); `txctapp14` 2,272/yr → **0 in
2025**; `pa` Pennsylvania Supreme 3,760/yr → 288 in 2025 (8%);
`texcrimapp` → 18% in 2025.

## Tier 3 — half-degraded large courts (~50%)

`nyappdiv` (12,473/yr baseline!), `fladistctapp` (8,366/yr), `washctapp`,
`moctapp`, `pasuperct`, `txctapp4`.

## Louisiana deep-dive (next DocketDrift state — Onion's call, 2026-08-06)

The LA picture SPLITS, favorably for a rollout:

- **`la` (Supreme): dead since 2020.** 1,859 in 2019 → 8 (2020) → **zero**
  (2021, 2022) → 11/45/49 (2023–25). ~**12,000 opinions/writ actions
  missing** — the largest single-court hole in the country. Same 2020
  vintage as MN's break; likely another COVID-era site-redesign scraper
  death. A writ-heavy court that issued ~2K/yr did not genuinely drop to 11.
- **`lactapp` (COA): intact.** 1,700–2,500/yr continuously through 2025.
  (A pub/unpub ratio flip around 2019–2020 is unexplained but totals hold.)
  Note CL lumps all FIVE circuits under one court id.

Rollout consequence: COA foundation comes from CL bulk (standard
`load_cl_bulk` path); the Supreme Court needs a direct lasc.gov source from
day one — one court, and the verification work for FLP report #2 doubles as
rollout recon.

## Caveats — read before reporting any of this upstream

1. **Candidates, not verdicts.** From CL data alone, a real filing decline
   and a dead scraper are indistinguishable. Every flagged court must be
   verified against the court's actual output before it goes in a report.
   MN's credibility came from measuring both sides.
2. **Order-heavy baselines.** LA/MI supreme baselines include writ/leave
   dispositions. CL *dropping a stream it used to carry* is still a
   regression, but name the composition honestly.
3. **The list is a FLOOR.** Courts that degraded before 2019 have poisoned
   baselines and are invisible to this window — MN itself (broken mid-2017)
   does not appear. A 2010–2016-baseline pass would surface that class.
4. **Platform migrations are the likely mechanism** (many states moved
   portals 2020–2023), which is good news: it means fixable scrapers.

## Pacing (Onion's rule)

Land MN with FLP end-to-end before opening a second front. One finished
report at a time; the volunteer reputation is built on sequence, not volume.
