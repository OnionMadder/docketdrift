# DocketDrift

**A navigator for the public record — state appellate court opinions, organized.**

Live: [docketdrift.com](https://docketdrift.com) ·
[Minnesota](https://mn.docketdrift.com) ·
[New Hampshire](https://nh.docketdrift.com) ·
[Arizona](https://az.docketdrift.com)

State appellate research has historically been dominated by paid databases.
DocketDrift collates published opinions from official sources, normalizes them
into a structured archive, and makes patterns in judicial decisions easier to
find and read together. It is a solo project, and it is free to use.

---

## What it is not

DocketDrift **does not generate text**. There is no chat box, no "summarize
this for me," no synthesized holdings, no drafted prose anywhere on the site.

That is a deliberate architectural commitment, not a roadmap gap. Generative
legal tools have been documented to fabricate case citations at meaningful
rates; the failure mode is the same behavior that produces the fluent prose in
the first place. A system that cannot produce text cannot produce a fake
citation.

**Machine learning appears in exactly two places:**

1. **Semantic search** — `voyage-law-2` embeddings (1024-dim) stored in
   MariaDB's native `VECTOR` column. A query vector is compared to opinion
   vectors by cosine similarity; the output is an ordered list of real opinion
   IDs. No text is generated.
2. **Tag-suggestion candidates** — embeddings rank a fixed vocabulary of
   editorial tags against each opinion. Above a confidence threshold the tag is
   auto-applied *and* marked `AUTO_APPLIED` for audit; below it, the suggestion
   enters a human review queue.

Everything else — case number, release date, disposition, panel composition,
statute citations, holdings — is deterministic extraction over the published
text. Either the pattern matches or it doesn't.

The **"The holding"** panel is the one surface that looks generated and isn't:
it quotes the court's own holding sentence *verbatim* (courts announce holdings
in a small, stable phrase set — "we hold that…", "we conclude that…"). When no
such sentence exists, the panel does not render. A blank holding is honest; a
guessed one is not.

## Query privacy

Users are lawyers, and a lawyer's research trail is work product that can be
subpoenaed. The posture is architectural rather than promissory: **we cannot
produce what we never stored.**

- Search is **POST**, so the query never enters a URL — not the access log, not
  the proxy log, not a CDN cache key, not browser history, not a shared link.
- The gunicorn access log is **query-stripped** by a custom log format.
- Analytics is goatcounter only, with the page path pinned to a constant `"/"`
  and the referrer blanked. Which opinions you read is itself a research trail,
  so it isn't recorded.
- No query is ever persisted server-side — not logged, not sessioned, not
  stored in an analytics row.

## Data sources and attribution

DocketDrift stands substantially on the work of the
[Free Law Project](https://free.law/):

- **[CourtListener](https://www.courtlistener.com/)** supplies the historical
  backfill and a standing source for ongoing ingestion, via both the REST API
  and the bulk exports. The Minnesota and Arizona citation graphs (605K+ edges)
  are built from CourtListener's bulk citation-map export, scoped to edges where
  both endpoints are in this corpus. Reporter citations for those states come
  from the bulk citations export.
- **Direct ingestion from the courts** covers what CourtListener doesn't carry
  promptly — particularly recent nonprecedential and order opinions, which some
  courts publish only behind bot-walled archives. Those states are read from the
  court's own release archive on a weekly schedule.

Free Law Project's [eyecite](https://github.com/freelawproject/eyecite) is
vendored as preparation for a future text-extraction pass on the non-NH states.
**It is not wired into any production code path today** — the New Hampshire
citation graph uses a hand-written neutral-cite parser, and MN/AZ use the bulk
citation map described above.

## Corpus

As of August 2026 — 119,000+ opinions, all embedded for semantic search.

| | Minnesota | New Hampshire | Arizona |
|---|---|---|---|
| Opinions | 60,457 | 20,723 | 38,132 |
| Date range | 1851–present | 1843–present | 1866–present |
| Dispositions parsed | 97.6% | 78.5% | 63.9% |
| Reporter cites | 92.9% | 90.2% | 75.0% |
| Judges | 122 | 36 | 119 |

Plus 56,165 panel votes, 605,424 citation edges, and 39,446 extracted holdings.

### Known coverage gaps

Stated plainly, because silence reads as completeness:

- **Minnesota 2017–2023 is incomplete**, and 2020–2022 is currently empty. An
  ingestion defect (listing from an Elasticsearch-backed endpoint that returned
  only a fraction of existing records) meant most opinions in that window were
  never retrieved. The defect is fixed; the backfill is not yet done. The
  historical corpus (1851–2016) and 2024-forward are unaffected.
- **Disposition and holding extraction thin out sharply on pre-1980 text**,
  where courts used a different vocabulary ("Exceptions overruled." rather than
  "Affirmed."). Historic dispositions are *transcribed, never mapped* — deciding
  that "exceptions overruled" *means* "affirmed" is an editorial read of the
  record, and that call isn't ours to make.
- **~14,400 opinions carry a synthetic `CL-<id>` docket number** where the real
  docket number wasn't available at ingest time. They are reachable, but not by
  the identifier a lawyer would actually paste.
- **Editorial review is early.** Most records have not been read by a human.
  Every record publishes its own review status, so this is visible per opinion
  rather than averaged away.

## Stack

Django 5.2 · MariaDB 11.7 (native `VECTOR`) · PyMySQL · gunicorn · HTMX where it
earns its keep. No SPA, no build pipeline, no JavaScript framework. Server-rendered
templates and server-rendered SVG charts.

Deployed on [NearlyFreeSpeech.NET](https://www.nearlyfreespeech.net/) shared
hosting, which shapes several design decisions — no numpy (the FreeBSD wheels
are broken), no resident daemons (a ~10-minute wallclock cull), and a 25-second
statement timeout that batch commands must lift explicitly.

## Local development

Requires Python 3.11 (production pins to it; 3.12+ f-string syntax will not run
there). Local dev defaults to SQLite, so no database server is needed.

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
python manage.py migrate
python manage.py runserver
```

State subdomains are resolved by middleware from the `Host` header, so to see a
state site locally, browse via a hostname like `mn.localhost:8000` (the apex
`localhost:8000` renders the state picker).

`python manage.py check` runs the project's own system checks, including a
deploy-blocking guard against multi-line `{# … #}` template comments — Django
only supports those on a single line, and a multi-line one renders as raw page
text to the user.

## Contributing

Issues and pull requests are welcome. Two standing constraints on any change:

1. **Nothing may generate legal text.** Extraction and quotation, always.
2. **Nothing may create an artifact that could be subpoenaed to reveal what a
   user was researching.** "Store it securely" is not the bar; "never store it"
   is.

New dependencies with C extensions need a FreeBSD-compatibility check before
they can ship.

## License

[GNU Affero General Public License v3.0](LICENSE) — the same license
CourtListener uses. If you run a modified version of this software as a network
service, the AGPL requires you to offer its source to your users.

Opinion text itself is the public record and is not covered by that copyright.

## Contact

Errors in an opinion record, a judge bio, or a search result:
[hello@docketdrift.com](mailto:hello@docketdrift.com)
