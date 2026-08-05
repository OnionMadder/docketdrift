# Comment on freelawproject/juriscraper#1115

**Posted 2026-08-04, rewritten 2026-08-05.** The original paste carried rich
text from a chat UI and posted as ~52KB of raw HTML — readable, but the
year-by-year evidence table was stripped. This is the clean version, plus the
finding that current ingestion is still failing.

Comment id `IC_kwDOAVtA1c8AAAABNHu01Q`.

---

We maintain a Minnesota appellate corpus downstream of CourtListener and hit
this gap head-on. We've rebuilt **2017–2023** by reading the Minnesota State
Law Library archive directly — about **7,500 opinions** — and we're glad to
hand the whole set over in whatever form is most useful: docket number, filing
date, court, and source PDF URL per opinion, or the PDFs themselves. It's
public-record material and we have no interest in sitting on it.

**The part we think is most useful to you: this isn't only a historical gap.
Current ingestion still appears to be failing, and getting worse.**

Live v4 API, `/clusters/?docket__court=minnctapp&date_filed__gte=…&count=on`:

| year | clusters | vs 2016 |
|---|---|---|
| **2016** (control) | **1,231** | — |
| 2020 | 0 | 0% |
| 2021 | 0 | 0% |
| 2022 | 0 | 0% |
| 2024 | 463 | 38% |
| **2025** | **149** | **12%** |

The 2020–2022 hole is what the thread already describes. The part that may be
new is 2024 at ~38% and 2025 at ~12% of a normal year — so this reads less like
a backfill task and more like the forward scraper still dropping most of what
it should catch.

The `2026-03-31` bulk export points at which stream is being lost. For 2024 it
carries **381 Published / 175 Unpublished**. The Minnesota Court of Appeals
files on the order of 1,100 nonprecedential opinions a year, so the published
side looks roughly right while the unpublished side is largely absent. Same
signature earlier in the range: 2018 is Published-only in the export (213 rows,
zero Unpublished), where 2016 was 1,138 Unpublished of 1,451.

`dockets.csv` is empty for the dead years too — `A20-*` absent entirely,
`A21-*` = 15, `A22-*` = 120, against 1,401 for `A15-*` — so it isn't only
clusters that failed to land.

---

**On "the back scraper doesn't work on the server; on a local install it runs."**
That matches what we see, and the discriminator isn't the IP alone:

- Individual opinion PDFs fetch fine from a datacenter IP with a plain GET
  (`curl -sI …/archive/ctapun/2021/OPa210414-112221.pdf` → `HTTP/2 200`,
  `application/pdf`). Only the **search/listing** app is protected.
- The listing returns a Radware interstitial to `curl` from a datacenter host
  **and** from a residential connection. A residential IP by itself does not
  pass.
- What passes is a real browser: headed Chrome executing JS with a profile
  carrying prior clearance.
- Even then it trips on rapid navigation — first load clean, next a CAPTCHA. We
  settled on 8s between navigations with a human clearing the occasional
  challenge. Worth knowing: the block is **transient**. We concluded once that
  a profile was burned after a run failed completely; a short probe the next day
  walked straight through.

So if the blocker is discovery rather than download, a scraper that obtains the
URL list another way could still fetch documents server-side normally.

**Archive specifics, current as of 2026-08-05:**

- Filenames: `archive/<category>/<year>/OP<case>-<mmddyy>.pdf`, categories
  `ctappub`, `ctapun`, `COAspectorders`, `supct`. Supreme filenames sometimes
  carry a 4-digit year (`OPA231400-07152026.pdf`) and some have suffixes
  (`OPa190959-040620%20Revised.pdf`).
- The older `archive/<category>/a<NNNNNN>.pdf` form 404s for opinions but is
  still served for `COAspectorders`.
- **The two courts file on different days.** COA files Mondays (~89%, remainder
  Tuesdays); the Supreme Court files **Wednesdays** (~89%). We swept Mondays
  only at first and got zero Supreme opinions while every batch reported
  success — worth knowing before anyone builds a day-keyed walk.
- The search index is queryable by the date stamp inside the URL (`url:112221`),
  which was the only reliable way to bound a result set: the form's
  `start-date` / `end-date` parameters are accepted and silently ignored.

---

**Two caveats on the data we're offering,** so nobody inherits a false
impression of completeness:

- Measured against 2016 Q1 (a period you have complete), our sweep recovers
  ~83% of Court of Appeals opinions and ~53% of Supreme filings. The Supreme
  shortfall is a category difference, not a scraping failure: the archive
  publishes Supreme *opinions* but not Supreme *orders* — attorney discipline,
  Lawyers Professional Responsibility matters, administrative dockets like
  `ADM10-8032` — which your 2016 data does include.
- That same 2016 Q1 comparison turned up **17 `minnctapp` opinions present in
  the archive but absent from CourtListener**, in a quarter that otherwise
  looks complete on your side. Happy to list those separately — it may indicate
  the decay started earlier and more subtly than the year totals suggest.
