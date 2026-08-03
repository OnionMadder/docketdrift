# Draft comment for freelawproject/juriscraper#1115

**Status: NOT SENT.** Posting is Onion's call. Venue: a comment on the existing
open issue (not a new issue — grossir already documented the 2020–2023 hole on
2026-04-10; this adds measurement and a root-cause datapoint).

---

Independent confirmation of the 2020–late-2023 gap, with per-year numbers, from
someone maintaining a Minnesota appellate corpus downstream of CourtListener.

**Measured three ways, all agreeing** (2026-08-03):

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

2. The live v4 API today: `/clusters/?docket__court=minnctapp&date_filed__gte=2021-01-01&date_filed__lte=2021-12-31&count=on`
   → `0`; same for `minn` → `0`. Control, `minnctapp` 2016 → `1231`.

3. `dockets.csv` in the same export is empty for those years too — docket
   numbers `A20-*` are absent entirely, `A21-*` = 15, `A22-*` = 120 (vs 1,401
   for `A15-*`). So it isn't only clusters; the dockets never landed either.

Two details that may narrow the decay window: **2018 and 2019 are
Published-only** in the bulk export (zero `Unpublished`), where 2016 was 1,138
Unpublished out of 1,451. And 2017 already drops to ~25% of 2016. So the
degradation looks staged — unpublished coverage failing first (2017–2018), then
everything (2020), rather than one clean break.

**On "the back scraper doesn't work on the server; on a local install it runs"**
— that matches what we see probing `mn.gov` from a datacenter IP. The archive
host is behind Radware Bot Manager, and the behavior is IP- and
fingerprint-dependent, not just rate-dependent:

- `https://mn.gov/law-library-stat/archive/ctapun/2021/` → `302` interstitial
  (`<hr><center>rdwr</center>`) from a datacenter IP.
- The search UI (`mn.gov/law-library/search/`) returns the Radware CAPTCHA to
  `curl`/`requests` from a server, but renders normally in a headed real-Chrome
  session on a residential connection.
- Even from that residential browser, the wall trips on the **second rapid
  programmatic navigation** — the first load is clean, the next draws a CAPTCHA.
  So a retry loop makes it worse, and pacing matters more than concurrency.

Worth noting the **PDFs themselves are not walled** — only the listing/search
is. Individual opinion PDFs fetch fine from a datacenter IP with a plain GET:

```
$ curl -sI https://mn.gov/law-library-stat/archive/ctapun/2021/OPa210414-112221.pdf
HTTP/2 200
content-type: application/pdf
```

So if the blocker is discovery rather than download, a scraper that obtains the
URL list some other way could still fetch documents server-side normally. The
current filename scheme is `archive/<category>/<year>/OP<case>-<mmddyy>.pdf`
(categories `ctappub`, `ctapun`, `COAspectorders`, `supct`); the older
`archive/<category>/a<NNNNNN>.pdf` form now 404s for opinions but is still
served for `COAspectorders`. Filing dates in those filenames are consistently
Mondays.

Happy to share the exact per-year/per-court counts against the authoritative
source once we've completed our own backfill of that window, if a diff against
your ingest would be useful for verification.
