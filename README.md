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
- The gunicorn access log is **query-stripped** by a custom log format, and
  client addresses are recorded only at network granularity (/24 for IPv4,
  /48 for IPv6) — never a full IP beside a path and a timestamp.
- **There is no analytics script.** No Google, no self-hosted counter, no
  third-party beacon; zero visitor JavaScript leaves the site. (An earlier
  privacy-tuned counter was removed in August 2026 when a scraper demonstrated
  the signal could be forged anyway — an analytics number a crawler can fake is
  decoration, and it shipped visitor data to a third party to produce it.)
- No query is ever persisted server-side — not logged, not sessioned, not
  stored in an analytics row.

## Data sources and attribution

DocketDrift stands substantially on the work of the
[Free Law Project](https://free.law/):

- **[CourtListener](https://www.courtlistener.com/)** supplies the historical
  backfill and a standing source for ongoing ingestion, via both the REST API
  and the bulk exports. Reporter citations come from the bulk citations export,
  and part of the citation graph (605K edges) from the bulk citation-map
  export, scoped to edges where both endpoints are in this corpus.
- **Our own text extraction** supplies the larger share of the citation graph:
  per-state citation parsers read each opinion's body and resolve references
  against reporter cites, parallel cites, and docket numbers — those edges
  carry the verbatim citing passage and a treatment classification
  (followed / distinguished / overruled / criticized / explained).
- **Direct ingestion from the courts** covers what CourtListener doesn't carry
  promptly — particularly recent nonprecedential and order opinions, which some
  courts publish only behind bot-walled archives. Those states are read from the
  court's own release archive on a weekly schedule.

Free Law Project's [eyecite](https://github.com/freelawproject/eyecite) is
vendored but **not wired into any production code path** — a measured bake-off
against our extractors found it would add about 1% of resolved citation
targets while losing docket-format citations entirely (the only key that
reaches unpublished opinions). The write-up lives in the project notes; the
decision is revisitable if treatment quality ever outranks graph size.

## Corpus

As of August 2026 — 128,000+ opinions across the three live states, all
embedded for semantic search.

| | Minnesota | New Hampshire | Arizona |
|---|---|---|---|
| Opinions | 69,607 | 20,682 | 37,834 |
| Date range | 1851–present | 1843–present | 1866–present |
| Dispositions parsed | 97.9% | 78.4% | 64.2% |
| Reporter cites | 80.4% | 90.2% | 74.9% |
| Judges | 119 | 37 | 169 |
| Panel votes | 32,291 | 17,736 | 29,195 |

Plus a 1.45-million-edge citation graph (bulk-derived breadth + text-extracted
edges carrying verbatim citing passages and treatment classification) and
~45,000 verbatim-extracted holdings.

### Known coverage gaps

Stated plainly, because silence reads as completeness:

- **Minnesota 2017–2025 was rebuilt** after our upstream source turned out to
  hold no Minnesota appellate opinions at all for 2020–2022 and sharply reduced
  coverage from 2017 onward (we reported this upstream, with data). Roughly
  9,800 opinions were re-read directly from the State Law Library archive, so
  every year 2015–2025 now holds about 970–1,435 opinions and 2026 is kept
  current by our own weekly scraper. Rebuilt years remain *substantially
  covered rather than provably complete*: they are short on Supreme Court
  *orders* (the archive doesn't publish them) and carry no reporter citations
  (those arrive via the upstream bulk data, which has nothing for these years)
  — which is also why the Minnesota reporter-cite figure above reads lower
  than it used to; the corpus grew by opinions that have no cite to carry.
- **Disposition and holding extraction thin out sharply on pre-1980 text**,
  where courts used a different vocabulary ("Exceptions overruled." rather than
  "Affirmed."). Historic dispositions are *transcribed, never mapped* — deciding
  that "exceptions overruled" *means* "affirmed" is an editorial read of the
  record, and that call isn't ours to make.
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
