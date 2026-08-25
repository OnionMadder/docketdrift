# Louisiana launch copy — paste-ready blocks for the is_live flip

Drafted 2026-08-25 with measured numbers so Phase 11 ("flip + copy, 10
min") is actually 10 minutes. **Re-verify the counts marked (†) on flip
day** — stale numbers on a public page are the exact class of problem
the 2026-08-02 audit cleaned up.

Facts behind every claim (all measured 2026-08-25):

- 341,104 opinions (†), Supreme + all five Courts of Appeal, earliest
  1809-07-01.
- Dispositions 64.7% overall / 66.6% modern-1980+ (sweep DONE).
- **reporter_cite = 0 for every LA row** — CourtListener's citations
  export has nothing for LA's cluster ids (verified against live API;
  permanent upstream gap until CL backfills). Paste-a-So.2d-cite
  resolves nowhere in LA; docket lookup + search work.
- Citation graph: 1,543,411 extracted edges; 192,913 LA opinions (57%)
  carry outbound edges (†).
- Statutes 252,375 rows; holdings 34,624 (†).
- LA Supreme 2020–2025 is ~97% absent upstream (2,000–2,250/yr through
  2018 → 8 / 0 / 0 / 11 / 42 / 49). lasc.org backfill planned
  (unattended build; see TODO 7b).
- COA circuit attribution: ~51% assigned by header/parish/JDC signals;
  the remainder defaults to First Circuit (mostly OCR-poor writ
  dispositions) — the court FACET is approximate for those rows.
- Embedding scoped to 1980+ (~230K rows); pre-1980 text is fulltext-
  searchable but excluded from semantic search + similar-opinions.
- Judges: 172 roster-seeded + 227 byline-learned (evidence-verified
  2026-08-25 after the 1,062-phantom purge); learned rows are
  surname-only pending editorial; benches not yet seated.

---

## 1. about.html — Status section

Pill (add beside the three existing):

```html
<span class="status-pill status-pill--live">Live &middot; Louisiana</span>
```

State bullet (add to the Status list):

```html
<li><strong>Louisiana</strong> &mdash; <strong>341,000+ opinions</strong> spanning <strong>1809 to current</strong>: the Louisiana Supreme Court and all five Courts of Appeal, refreshed weekly. Statute citation graph, verbatim holdings, semantic search (1980-forward), and a 1.5-million-edge text-extracted citation graph are live. Louisiana has real, disclosed rough edges &mdash; see Known coverage gaps above before relying on completeness.</li>
```

## 2. about.html — Known coverage gaps, LA subsection

Add after the Minnesota material (keep the MN text unchanged):

```html
<h4>Louisiana</h4>
<p>Louisiana is our largest corpus and carries the most caveats. In the
spirit of the Minnesota disclosure above, here is exactly where it is
thin:</p>
<ul>
  <li><strong>Louisiana Supreme Court, 2020&ndash;2025</strong> &mdash;
  our upstream source holds roughly <strong>3%</strong> of the court's
  output for these years (a few dozen opinions per year against a
  historical norm above two thousand). The opinions were published
  normally and remain available from the court; we are building a
  direct reader of the court's own releases to close this, the same way
  Minnesota's gap was closed. Until then, recent Supreme writ actions
  and opinions are best checked against
  <a href="https://www.lasc.org/" target="_blank" rel="noopener">lasc.org</a>
  as well.</li>
  <li><strong>No reporter citations.</strong> Commercial reporter cites
  (So. 2d / So. 3d) reach us through CourtListener's bulk data, which
  currently has none for Louisiana. A Louisiana case is readable,
  searchable, and reachable by docket number, but pasting its So. 2d
  cite into search will not resolve it. The citation graph is built
  from the opinion text itself and works despite this.</li>
  <li><strong>Court of Appeal circuit labels are approximate for older
  writ rulings.</strong> Our upstream groups all five circuits into one
  feed; we re-derive the circuit from the document. About half of
  opinions carry a clean signal; the remainder &mdash; mostly brief,
  scan-quality writ dispositions &mdash; default to the First Circuit.
  Treat the circuit facet as reliable for signed opinions and
  indicative for writ rulings.</li>
  <li><strong>Semantic search covers 1980 forward.</strong> Earlier
  text (about a third of the corpus, back to 1809) is fully keyword-
  searchable and linked into the citation graph, but does not appear in
  semantic results or the similar-opinions panel.</li>
  <li><strong>Outcome extraction runs at about two-thirds</strong> on
  modern opinions &mdash; Louisiana's civil-law disposition vocabulary
  (suspensive and devolutive appeals, writ grants and denials) is
  transcribed, never mapped, and the pre-1980 tail largely predates the
  vocabulary we match.</li>
</ul>
```

## 3. about.html — FAQ JSON-LD ("Which states are live?")

Replace the answer text with (keep it in sync with the visible prose):

```
Four. Minnesota: 69,000+ appellate opinions from 1851 to current [keep
existing MN sentence]. New Hampshire: 20,000+ NH Supreme Court opinions
with byline-extracted judicial panel graph. Arizona: 38,000+ Arizona
Supreme Court and Court of Appeals opinions. Louisiana: 341,000+
opinions from 1809 to current across the Supreme Court and all five
Courts of Appeal — our largest corpus, launched with disclosed gaps
(Supreme Court 2020-2025 is thin upstream, no reporter citations, and
circuit labels are approximate for older writ rulings). New states are
added one at a time.
```

Also bump the "more than 119,000 opinions" figure in Editorial review
to the current corpus total (†) — it predates LA.

## 4. Flip checklist (Phase 11, in order)

1. Re-verify (†) numbers live.
2. `State.objects.filter(code="LA").update(is_live=True)` — then check
   what the apex tile + nav derive automatically vs. need edits.
3. Apply the copy blocks above; `manage.py check`; deploy; restart.
4. `precompute_explore_tags` (cold-cache rule) — LA's tag cloud will be
   empty until 8f runs; the context processor is cache-read-only so
   this is cosmetic, not availability.
5. **Wait several minutes after the restart**, then submit the la
   sitemap in Search Console (domain property already covers the
   subdomain; the stale-503 lesson says never submit right after a
   deploy).
6. llms.txt: add LA to the corpus description + stats.
7. `cron-ingest.sh` already auto-discovers live states — confirm the
   next weekly run picks LA up (it already runs via the registered
   `ingestlacoa`/`ingestlasupreme` tasks; the flip should not double
   anything — verify tags don't collide).
8. Ko-fi "Launch Louisiana" goal: mark reached / update copy.
