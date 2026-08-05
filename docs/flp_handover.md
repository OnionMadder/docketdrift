# Free Law Project handover — MN 2017–2023

Prepared 2026-08-05, **ready to send if they ask.** The contact message and the
juriscraper #1115 comment both offer this; this file is so nobody has to
rebuild it under time pressure.

## The file

`docketdrift_mn_2017_2023.csv` — **7,908 rows**, one per opinion found in the
Minnesota State Law Library archive for 2017–2023.

| column | meaning |
|---|---|
| `docket_number` | canonical MN docket, `A19-1234` (zero-padded, no `NO. ` prefix) |
| `date_filed` | our parsed filing date, falling back to the date in the filename |
| `cl_court_id` | `minnctapp` or `minn`, using **CourtListener's** court ids |
| `precedential_status` | `Published` / `Unpublished`, in CL's vocabulary |
| `source_pdf_url` | direct mn.gov PDF URL — **fetches fine from a datacenter IP** |
| `in_docketdrift` | whether we hold a matching record (7,901 of 7,908 do) |
| `disposition` | what our parser read, e.g. `Affirmed` — informational only |

Built on prod from the scrape manifests joined to our parsed records:
`/tmp/docketdrift_mn_2017_2023.csv`. Regenerate with
`scripts/` + the manifests in the session scratchpad if it's ever lost — or
simply re-run the sweep, which is now a known quantity.

Deliberate choice: it lists **everything the archive holds for those years**,
not our guess at their gaps. A complete checklist lets them diff against their
own corpus and verify, rather than trust our idea of what they're missing.

## Column vocabulary is theirs, not ours

`cl_court_id` and `precedential_status` use CourtListener's values so the file
drops into their tooling without translation. Our own slugs (`appeals`,
`supreme`) and boolean `is_precedential` are deliberately not exposed.

## Caveats to send with it

State these; do not let the row count imply completeness.

1. **~83% of Court of Appeals opinions, ~53% of Supreme filings**, measured
   against 2016 Q1 — a quarter CL has complete, so it's a real control rather
   than a guess.
2. **The Supreme shortfall is a category difference, not a scraping failure.**
   The archive publishes Supreme *opinions* but not Supreme *orders* — attorney
   discipline, Lawyers Professional Responsibility matters, administrative
   dockets like `ADM10-8032`. CL's own 2016 data includes those; ours cannot.
3. **17 `minnctapp` opinions from 2016 Q1 are in the archive but absent from
   CourtListener** — in a quarter that otherwise looks complete on their side.
   Worth offering separately; it suggests the decay began earlier and more
   subtly than the year totals show.
4. **No reporter citations.** N.W.2d cites are assigned post-publication and
   reach us through CL's bulk data, which has nothing for the years CL is
   missing. The dockets and dates are ours; the cites are not.

## If they want the PDFs instead

~7,900 files, roughly 1 GB. They fetch fine server-side — only the mn.gov
*listing* is bot-walled, not the documents. `scripts/mn_scraper/fetch_manifest.py`
downloads a manifest's URLs directly; they can run the same thing themselves
from the `source_pdf_url` column without needing anything from us.
