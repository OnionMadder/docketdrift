# Message sent to Free Law Project

**Channel:** https://free.law/contact — topic dropdown → **CourtListener**.
(No public email address is published; the form routes by topic.)

**Status: SENT 2026-08-05** by Onion. **Do not send again.** If a follow-up is
ever needed, reply in the issue thread rather than re-opening the form — a
second unsolicited contact on the same matter reads as pestering, and the issue
is where technical back-and-forth belongs.

Below is the message **as actually sent**, verbatim.

---

Hi.

I run DocketDrift, a free state appellate archive built partly on CourtListener
data. I've posted this on juriscraper issue
[#1115](https://github.com/freelawproject/juriscraper/issues/1115), but it
seemed worth flagging directly because it looks like an active problem rather
than a historical one.

Your Minnesota Court of Appeals ingestion appears to be dropping most of what
it should catch, and the trend is downward. Live API, `count=on`:

* 2016: 1,231 clusters (control — healthy)
* 2024: 463
* 2025: 149

The 2020–2022 hole is already described in that issue. The part I haven't seen
noted is 2024–2025 running at roughly 38% and 12% of a normal year. Your
2026-03-31 bulk export suggests it's the nonprecedential stream specifically:
2024 carries 381 Published against 175 Unpublished, where Minnesota's Court of
Appeals files on the order of 1,100 unpublished opinions a year.

Separately: we've rebuilt 2017–2023 from the Minnesota State Law Library
archive - about 7,500 opinions, and you're welcome to all of it, in whatever
form is easiest (docket number, filing date, court and source PDF URL per
opinion, or the PDFs). It's public-record material and we have no interest in
holding onto it exclusively. Happy to just put it somewhere you can pull from.

The issue comment has the archive specifics that might help fix the scraper
itself: the filename scheme, the fact that the two courts file on different
weekdays (we swept Mondays only at first and silently got zero Supreme Court
opinions), and that the search form's date parameters are accepted and then
ignored.

- Kellye Strickland, docketdrift.com

---

## What was promised, and where it is

The message offers the rebuilt data "in whatever form is easiest." That is
prepared and ready to hand over — see `docs/flp_handover.md`. If they reply
asking for it, nothing needs building.
