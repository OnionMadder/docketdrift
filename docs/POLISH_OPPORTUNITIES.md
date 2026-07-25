# DocketDrift — polish opportunities

Audit date: 2026-07-11. Scope: small-to-medium, return-heavy polish on the
**current** site — not new features, not roadmap phases (P1–P4 in CLAUDE.md),
not architecture. Every item here honors the binding constraints: "data is
sacred" (query privacy), "no generative content," the neon-on-dark aesthetic,
GoatCounter-only analytics, no third-party CDN/service.

Method: read the live templates/CSS/views in this repo, then fetched the live
apex + all three state subdomains + representative opinion / judge / about /
how-we-differ / current-judges pages and diffed what renders against the code.

**Caveat on live-vs-local.** The working tree has uncommitted edits to
`apex.html`, `about.html`, `how_we_differ.html`, `_treatment_panel.html`,
`views.py`, `models.py`, and the parked holdings work (migrations 0026/0027,
`extract_holdings`). Where a finding depends on template text that is
uncommitted, it's flagged "verify vs live." Findings tied to committed files
(e.g. `state_landing.html`, `base.html`) are solid against the live site. See
§5 for the full delta.

---

## 1. Executive summary — the three highest-return moves

| # | Move | Effort | Why it pays |
|---|------|--------|-------------|
| 1 | **Kill the "0 legal topics to browse" zero-stat and the dead-end Tags card on the NH & AZ landings** | **S** | On 2 of 3 states, the primary landing page shows a naked `0` stat tile and a "Tags" browse card that promises "controlled-vocabulary tags across doctrine, subject, procedural, and posture categories" but leads to an empty page. It reads as broken/unfinished on first contact. |
| 2 | **Rework the Beta / Flagship labels** | **S** | The 60,377-opinion MN corpus and the 38K AZ corpus are both stamped **"Beta"** while the smaller 20K NH corpus is **"Flagship."** A new visitor landing on `mn.docketdrift.com` sees the most complete state marked "Beta" — reads as "not ready / don't rely on this." The label is internally justified (NH = proving ground) but it actively undersells the flagship data. |
| 3 | **Hoist a plain-English "what this is + no-AI / no-ads / free / private" line into the apex hero** | **S** | The apex hero is `DocketDrift` + "Real-time tracking of state judicial trends." The actual value prop — *free, searchable archive of real court opinions, no fabricated citations, no ads, private* — only appears in the OG description and a "What this is" block below the fold. A first-time visitor can't tell in 3 seconds what the site is or why it's different. |

All three are copy/template-level, no backend, no data migration, no
aesthetic change.

---

## 2. Per-category audit

### 2.1 UX / first-impression

| Finding | Evidence | Effort |
|---|---|---|
| Apex hero doesn't say what the site *is*. "Real-time tracking of state judicial trends" is a tagline, not an explanation. The differentiators live below the fold in "What this is" and only in OG meta. | `apex.html:79` hero lede; the concrete pitch ("Real opinions from real courts… No fabricated citations, no generated legal analysis") is only in `apex.html:6` og_description. | S |
| **Apex search box is a dead end.** The header search module renders on the apex (no `request.state` gate on `.search-module`), so a visitor sees "Search opinions…". Submitting POSTs to `home()`, which with `state is None` re-renders the apex — no results, no message. | `base.html:113–128` (search-module always rendered); `views.py:228–242` (apex branch ignores `search_q`). | S–M |
| "No verdicts asserted" (the site-wide justice-banner tagline) is cryptic to a newcomer — *verdicts about what?* | `base.html:153–155`. | S |
| Opinion body is locked to `max-height:75vh` with its own inner scrollbar. On mobile this is a dual-scroll box — the full opinion reads through a small window inside the page. Intentional (keeps metadata reachable) but worth reconsidering for the read-the-opinion core task. | `docketdrift.css:323–334`. | M |

### 2.2 Discoverability / SEO

| Finding | Evidence | Effort |
|---|---|---|
| **Twitter/X card title & description are generic on every page.** `base.html` defaults them to `{{ self.og_title }}` / `{{ self.og_description }}`. `self` is a **Jinja2** concept; these are **Django** templates, where `self` is undefined → resolves to `''` → the `default:` fallback fires. No page overrides `twitter_title`/`twitter_description`, so X/LinkedIn-via-Twitter-tag previews show the literal "DocketDrift" / "Public records, treated as public." instead of the page title. (og:* tags are fine — pages override those.) Fix: default the twitter blocks to the og_* block output, or drop the twitter title/description tags so scrapers fall back to og:*. | `base.html:46–47, 62–63`. | S |
| Hardcoded "1930" archive floor understates every corpus. The expand-archive control says "Full archive · 1930–present" / "Expand to full archive (1930–)" and the dig-deeper copy says "going back to the 1930s" — but MN starts 1851, NH 1843, AZ 1866. Understates the historical depth that's a genuine selling point. | `state_home.html:76, 78, 168`. Actual ranges: `mn` 1851, `nh` 1843, `az` 1866 (live landing stats). | S |
| JSON-LD is thorough and consistent (WebSite / Organization / Dataset / LegalCase / Person / BreadcrumbList / FAQPage all present and well-formed). No change needed — this is a strength; noted so a later pass doesn't "fix" it. | `apex.html:8–73`, `state_landing.html:8–81`, `opinion_detail.html:12–47`, `how_we_differ.html:8–56`. | — |

### 2.3 Mobile / responsive

| Finding | Evidence | Effort |
|---|---|---|
| `doc-table` reflows to stacked cards at ≤760px (thead hidden). Values are self-descriptive (docket / court pill / date / disposition pill / title), but the stacked cells carry **no field labels** — a first-time mobile user sees an unlabeled date and a bare docket string. Consider `td::before` labels like the differ-table already uses. | `documents.css:219–268` (reflow); label pattern already exists at `docketdrift.css:1759–1768`. | S |
| Citation-cart FAB and the "Cite this case" tool are NH-only and already shrink sensibly on phones. No issue found. | `docketdrift.css:3092–3105`. | — |
| No horizontal-scroll traps found. Tables reflow; grids use `auto-fit`/`auto-fill minmax`. This category is in good shape. | — | — |

### 2.4 Search UX

| Finding | Evidence | Effort |
|---|---|---|
| No-results / degraded / capped states are all handled with calm, non-alarm copy — genuinely well done. Noted as a strength. | `state_home.html:51–61, 197–207`. | — |
| The over-broad "200+ — narrow your search" path date-sorts a fulltext-index sample without a date window (documented tradeoff), and the notice explains it. Fine as-is. | `state_home.html:56–61`. | — |
| Query-privacy plumbing (POST search, `.js-keepq`/`.js-search` re-submit so no query ever enters an href) is correct and elegant. Do not regress it. Flagged so a future "add a shareable search URL" request is recognized as a privacy violation, not a feature. | `base.html:113–222`. | — |

### 2.5 Opinion-detail polish

| Finding | Evidence | Effort |
|---|---|---|
| **Cross-state polish gap.** NH opinion pages get the polished "Similar opinions" card with % scores, the "Cite this case" tool, click-to-copy pilcrows, and deep-link flash. MN/AZ get the *legacy* bottom-of-page plain "Semantically similar" table, no %, no cite tool, no copy pilcrows. The biggest, likely most-trafficked corpus (MN) shows the least-polished opinion page. Intentional NH-first rollout, but it's the most visible cross-state inconsistency. | `opinion_detail.html:257–347` (NH card vs MN/AZ table); cite tool `:106–131` NH-gated. | M (roll the similar-opinions card to MN/AZ) |
| "Authorities cited" subhead reads "Identified automatically; this list may not be exhaustive." — good, honest. No change. | `_treatment_panel.html:63`. | — |
| Paragraph anchors: on MN/AZ the pilcrows are plain navigate-on-click (copy/flash is NH-gated JS). Fine and documented; if the flash/copy affordance is cheap to un-gate it would improve MN/AZ deep-linking, but it's genuinely NH-first by design. | `opinion_detail.html:450–546`. | M |

### 2.6 Judge dossier polish

| Finding | Evidence | Effort |
|---|---|---|
| **NH (the "flagship") judges show fallback portraits, not real headshots.** All five seated NH justices resolve to `…/static/opinions/judges/nh/fallback-<name>.jpg`. courts.nh.gov is Akamai-blocked, so real photos may be unobtainable — but the visible result is generic placeholders on the flagship state's roster. At minimum, a nicer neutral silhouette beats a per-name "fallback-" file that looks like a broken asset. | Live `nh.docketdrift.com/current-judges/` img src. | S–M |
| Charts (server-rendered SVG) carry `role="img"` + descriptive `aria-label` and per-dot `<title>` tooltips — accessible and legible. Bar-fills track the semantic color key. No change. | `judge_detail.html:216–255`. | — |
| Judge pages with no panel data show a clear, honest empty-state explaining *why* (CourtListener free-text panels). Good. | `judge_detail.html:150–159`. | — |

### 2.7 Copy audit

| Finding | Evidence | Effort |
|---|---|---|
| **"Real-time" overclaims and clashes with the site's own honesty.** The brand subtitle and apex lede say "Real-time tracking of state judicial trends," but ingestion is weekly and the landing shows a "Coverage note … filed N days ago" when a corpus is >30d stale (NH's newest is 2026-06-11). For a site whose whole brand is austere honesty ("No verdicts asserted," the freshness monitor), "real-time" is the one off-key word. Suggest "Weekly-refreshed" / "Tracking state appellate decisions." | `base.html:98`, `apex.html:79`; `coverage-note` logic `views.py:290–300`. | S |
| **The strongest privacy claim is invisible to users.** The best differentiator — search is POST so the query never touches a URL, log, or CDN key; "we can't produce what we never stored" — lives only in code comments (`base.html`, CLAUDE.md). The public Privacy page talks about GoatCounter but never says *we don't even log what you searched, by construction.* Surfacing this is high-value, on-brand copy. | `privacy.html:16–31` vs `base.html:10–35, 114–128`. | S |
| Positioning copy (about / how-we-differ) is confident, non-apologetic, and consistent — a real strength. The anti-hallucination framing ("the hallucination is the same generative behavior that produces the fluent prose") is excellent. | `how_we_differ.html:82–99`, `about.html:98–102`. | — |
| Privacy page bullet list ("browser's language setting") is slightly out of sync with the actual GoatCounter config (state/region + device; path pinned to `/`, referrer dropped). Minor; worth aligning while surfacing the POST-search story above. | `privacy.html:20–26` vs `base.html:27–35`. | S |

### 2.8 Consistency across state subdomains

| Finding | Evidence | Effort |
|---|---|---|
| Beta/Flagship labeling (see §1.2). | `state_landing.html:86–90`, `about.html:120–125`. | S |
| "0 legal topics to browse" + dead Tags card on NH/AZ (see §1.1). `total_tags_used` = distinct editorial Tags actually applied; NH/AZ = 0 (tag-suggestion queue not yet worked), MN = 19. The landing renders the raw 0 and still shows the Tags browse card promising the full vocabulary. `tag_index` itself degrades gracefully to an empty-state — but the landing dangles the visitor there. | `state_landing.html:126–130, 184–188`; `views.py:200–204`; `tag_index.html:69–71`. | S |
| MN/AZ opinion pages less polished than NH (see §2.5). | — | M |
| Header nav, footer justice-banner, breadcrumbs, JSON-LD, meta blocks are otherwise uniform across states — good. | `base.html`. | — |

### 2.9 Accessibility

| Finding | Evidence | Effort |
|---|---|---|
| Palette contrast is **better than feared.** `--text-dim` (#948ca1) on `--bg-deep` (#050505) ≈ 6.3:1 — clears AA for body text. The `-ink` variants for magenta/violet/neutral were deliberately built for AA. This category is largely handled; do **not** propose a palette change. | `core.css:12, 43–44`; `docketdrift.css:32–86`. | — |
| Small, low-opacity tag-cloud chips are the one likely AA miss: `.tag-chip--xs` is 0.62rem at `opacity:0.62` on neon-pink. At ~10px that's "small text" needing 4.5:1 and the opacity likely drops it below. Bump the xs opacity floor. | `docketdrift.css:706`. | S |
| Alt text, aria-labels, focus-visible rings, `prefers-reduced-motion` guards, `role="img"` on charts, `title`+`aria-label` on color-only review dots — all present. Strong a11y hygiene. | throughout; e.g. `judge_detail.html:85`, `opinion_detail.html:443–446`, `state_home.html:110`. | — |
| Verify: `.nav-toggle` is the first focusable element and is `position:fixed`. Confirm it's `display:none` (not just visually hidden) on desktop so keyboard users don't tab into an invisible control. | `core.css:276`, media queries `:706, :830`. | S (verify) |

### 2.10 Performance

| Finding | Evidence | Effort |
|---|---|---|
| Three render-blocking stylesheets in `<head>` (core / documents / docketdrift, unminified; docketdrift.css is ~3,100 lines). On NFSN behind CDN caching this is low-stakes, but concatenating + minifying the three is a cheap LCP win. | `base.html:72–74`. | S–M |
| Self-hosted font ships as **.ttf**, not woff2. woff2 is ~40% smaller for the same glyphs; `font-display:swap` is already set. | `docketdrift.css:132–138`. | S |
| Images already `loading="lazy"`; no hero images; LCP is text. No urgent work. | `judge_detail.html:85`, `current_judges.html:85`. | — |

---

## 3. Nice-to-haves for later (bigger polish, not urgent)

- **Roll the NH "Similar opinions" card + cite tool to MN/AZ** once the
  proving-ground period is deemed done. It's the single biggest visible
  quality gap between states. (M–L; touches per-state gating in
  `opinion_detail.html` and depends on cost of the MN/AZ cosine scan — the
  `semantic.py` band-aids already bound it.)
- **Replace the per-name `fallback-*.jpg` NH portraits** with a single shared
  neutral silhouette asset (or real headshots if a residential-Chrome scrape
  of an obtainable source turns up). (S–M)
- **Apex search box**: either remove it on the apex or turn it into a
  "pick a state to search" affordance so it isn't a silent no-op. (M)
- **Minify + concatenate CSS; convert font to woff2.** (S–M)
- **Mobile stacked-table field labels** via `td::before`. (S)

---

## 4. Explicit non-suggestions (considered and rejected)

- **Palette / neon-contrast overhaul.** Considered proposing WCAG substitutes
  for the neon-on-dark combos. Rejected: the aesthetic is load-bearing and the
  measured contrast is fine (`--text-dim` ≈ 6.3:1; `-ink` variants exist by
  design). The only real AA risk is the xs tag chips (§2.9) — a one-line
  opacity fix, not a palette change.
- **Add page-path / event analytics to understand engagement.** Rejected —
  directly violates "data is sacred." GoatCounter is pinned to `path:"/"` on
  purpose. Off-limits.
- **Put the search query in a shareable GET URL / add a "copy search link"
  button.** Rejected — the POST-only design is the privacy moat. Any feature
  that lands a query in a URL is a regression, not a polish.
- **LLM-generated opinion summaries on MN/AZ, or a "what's the holding" box.**
  Rejected — generative-content prohibition. (The NH summarized-holding panel
  is a *separate, deliberate, bounded, labeled* surface being proven on NH; it
  is not "AI content" in the prohibited sense, and it isn't live yet anyway —
  see §5.)
- **Drop "Beta" entirely / call everything "live."** Considered as the §1.2
  fix. Softer alternative recommended instead: keep an honest status word but
  stop stamping the 60K/38K corpora as "Beta" (e.g. MN "Full corpus," NH
  "Flagship · most features first"), so the label informs without underselling.
- **Un-cap the opinion-body inner scroll** (§2.1). Flagged as a consideration,
  not a recommendation — removing `max-height:75vh` is a real UX judgment call
  (metadata reachability vs. natural reading) and should be Onion's call, not a
  drive-by.

---

## 5. Live-vs-local delta

What's in the working tree / committed code but **not visible on the live site**
(confirmed by fetching prod):

| Thing | State in repo | On live site? |
|---|---|---|
| **Summarized-holding panel** (the NH LLM surface) | `opinion_detail.html:135–232` committed & gated on `opinion.holding_summary`; the field/data come from parked migration `0026` + untracked `extract_holdings` | **No.** Confirmed absent on a live NH opinion (`2024-0304`). The `holding_summary` field isn't on prod, so the `{% if %}` is falsy and the panel silently doesn't render. Safe no-op. |
| **Holdings-aware `about.html` / `how_we_differ.html` rewrite** (3-place-ML framing, new do/don't table, "Summarized holdings" section) | Working tree (both files show as modified) | **No.** Live how-we-differ still shows the **old two-place-ML** copy and the pre-holdings do/don't table ("Answer what's the holding in…?", "Summarize cases into prose"). |
| `extract_holdings`, `cluster_citations`, `embed_citations` commands; `holding_review*` templates; migrations `0026`/`0027` | Untracked in working tree | **No.** Local only. |
| `docs/LA_FEASIBILITY.md`, `session-brief.md`, `docs/MN_COA_BACKFILL.md` | Untracked / local | N/A (docs) |

**Coordination risk to flag for the next deploy:** the modified `about.html` and
`how_we_differ.html` advertise the summarized-holding feature. Deploying those
two templates **before** migration 0026 + an `extract_holdings` run on prod
would claim a feature that produces zero visible panels (holdings live only
where `holding_summary` is populated, NH-first). Ship the disclosure copy and
the backend/data together, or the "How we summarize" links point at prose about
a feature the reader can't find.

Everything else audited (state_landing, base, judge_detail, current_judges,
tag_index, privacy, the semantic/citation/cite-tool surfaces on NH) matches
between repo and live.

---

## 6. Surprises worth Onion's attention

1. **The Twitter/X card meta is silently generic on every page** (§2.2) — a
   `self.` Jinja-ism that no-ops in Django templates. Easy to miss because OG
   (which most platforms use) is correct; only X/Twitter-tag consumers see the
   generic fallback. Cheapest discoverability fix in the audit.
2. **The best privacy engineering on the site is invisible** (§2.7). The
   POST-only, query-never-logged design is genuinely differentiating and it's
   only documented in code comments. The public Privacy page undersells it.
3. **Two of three state landings look empty/broken at a glance** because of the
   `0 legal topics` stat + dead Tags card (§1.1). This is the item most likely
   to make a first-time visitor bounce, and it's a pure template fix.
4. The site is, on the whole, **more polished and more internally honest than
   most solo projects** — the JSON-LD, the search degraded-state copy, the a11y
   hygiene, and the anti-hallucination framing are all above bar. The
   highest-return work is trimming a few naked edges (zero-stats, mislabels,
   overclaims), not building anything.

### Live pages that surprised me

- **NH opinion detail** — better than expected. Cite tool, citation graph
  ("How this document has been cited" / "Cited by" / "Authorities cited"),
  and the %-scored Similar-opinions card all render cleanly and read like a
  paid product.
- **NH & AZ landings** — worse than expected, purely because of the `0 legal
  topics` tile and the Tags card that leads nowhere. The rest of the page is
  strong; one bad stat drags the whole first impression.
- **NH current-judges** — the fallback portraits are more noticeable than
  expected on the state the site calls "Flagship."
