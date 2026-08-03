# Draft comment for freelawproject/juriscraper#1115

**Status: NOT SENT.** Posting is Onion's call. Venue: a comment on the existing
open issue (not a new issue — grossir already documented the 2020–2023 hole on
2026-04-10; this adds measurement, a root-cause datapoint, and an offer of the
rebuilt data).

Rewritten 2026-08-03 after the backfill succeeded. The earlier version was a
bug report; this one leads with the offer, which is the better opening.

---

We maintain a Minnesota appellate corpus downstream of CourtListener and hit
this gap head-on. We've since rebuilt 2020–2022 by reading the Minnesota State
Law Library archive directly — **about 3,100 opinions** — and we're glad to
hand the whole set over in whatever form is most useful (docket number, filing
date, court, and the source PDF URL per opinion; or the PDFs themselves). It's
public-record material and we have no interest in sitting on it. Say the word
and we'll put it somewhere you can pull from.

Details in case they're useful for fixing the scraper itself.

**Confirming the gap, measured three ways** (2026-08-03):

1. The `2026-03-31` bulk export (`opinion-clusters.csv`, filtered to `minn` +
   `minnctapp`), clusters by `date_filed`:

   | year | minnctapp | minn |
   |---|---|---|
   | 2016 | 1,231 | 220 |
   | 2017 | 314 | 144 |
   | 2018 | 80 | 133 |
   | 2019 | 74 | 111 |
   | **2020** | **0** | **0** |
   | **2021** | **0** | **0** |
   | **2022** | **0** | **0** |
   | 2023 | 97 | 18 |
   | 2024 | 463 | 93 |

2. The live v4 API: `/clusters/?docket__court=minnctapp&date_filed__gte=2021-01-01&date_filed__lte=2021-12-31&count=on`
   → `0`; same for `minn` → `0`. Control, `minnctapp` 2016 → `1231`.

3. `dockets.csv` in the same export is empty for those years too — `A20-*`
   docket numbers absent entirely, `A21-*` = 15, `A22-*` = 120 (vs 1,401 for
   `A15-*`). So it isn't only clusters; the dockets never landed either.

Two details that may help narrow when it started: **2018 and 2019 are
Published-only** in the bulk export (zero `Unpublished`), where 2016 was 1,138
Unpublished of 1,451. And 2017 is already at ~25% of 2016. The degradation
looks staged — unpublished coverage failing first, then everything.

**On "the back scraper doesn't work on the server; on a local install it runs."**
That matches exactly what we see, and the discriminator isn't the IP alone:

- Individual opinion PDFs fetch fine from a datacenter IP with a plain GET
  (`curl -sI …/archive/ctapun/2021/OPa210414-112221.pdf` → `HTTP/2 200`,
  `content-type: application/pdf`). Only the **search/listing** app is
  protected.
- The listing returns a Radware interstitial (`<hr><center>rdwr</center>`) to
  `curl` from a datacenter host **and** to `curl` from a residential
  connection. A residential IP by itself does not pass.
- What passes is a real browser: headed Chrome executing JS with a profile that
  carries prior clearance.
- Even then, the wall trips on the **second rapid programmatic navigation** —
  first load clean, next one a CAPTCHA. Pacing matters more than concurrency; we
  settled on 8s between navigations and a human clearing the occasional
  challenge.

So if the blocker is discovery rather than download, a scraper that obtains the
URL list some other way could still fetch documents server-side normally.

**Archive specifics**, current as of 2026-08-03:

- Filenames: `archive/<category>/<year>/OP<case>-<mmddyy>.pdf`, categories
  `ctappub`, `ctapun`, `COAspectorders`, `supct`. Supreme filenames sometimes
  carry a 4-digit year (`OPA231400-07152026.pdf`), and some have suffixes
  (`OPa190959-040620%20Revised.pdf`).
- The older `archive/<category>/a<NNNNNN>.pdf` form now 404s for opinions but is
  still served for `COAspectorders`.
- **The Court of Appeals files Mondays (~89%, remainder Tuesdays); the Supreme
  Court files Wednesdays (~89%).** We initially swept Mondays only and got zero
  Supreme opinions while everything appeared to succeed — worth knowing before
  anyone else builds a day-keyed walk.
- The search index is queryable by the date stamp in the URL (`url:112221`),
  which turned out to be the only reliable way to bound a result set: the
  form's `start-date` / `end-date` parameters are accepted and silently
  ignored.

**Two caveats on the data we're offering**, so nobody inherits a false
impression of completeness:

- Measured against 2016 Q1 (a period you have complete), our sweep recovers
  ~83% of Court of Appeals opinions and ~53% of Supreme filings. The Supreme
  shortfall is a category difference, not a scraping failure: the archive
  publishes Supreme *opinions* but not Supreme *orders* (attorney discipline,
  Lawyers Professional Responsibility, administrative dockets like
  `ADM10-8032`), which your 2016 data does include.
- That same 2016 Q1 comparison turned up **17 `minnctapp` opinions present in
  the archive but absent from CourtListener**, in a quarter that otherwise
  looks complete on your side. Happy to list those separately if a spot-check
  would be useful — it may indicate the decay started earlier and more subtly
  than the year totals suggest.
