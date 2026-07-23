"""Resolve judges + auto-create PanelVote rows from opinion text.

The CL bulk load brought in ~3,610 PanelVote rows -- only ~6% of the
60K corpus has structured panel data. The remaining ~57K opinions DO
contain panel info in their raw_text (typical MN format: "Filed June 1,
2026 / Affirmed / Larson, Judge" + "Considered and decided by Larson,
Judge; Bjorkman, Judge; and Wheelock, Judge"), they just never got
matched to Judge model rows.

This command does that match: parses each opinion, extracts the byline
author + the panel list, looks up each name against ``state``'s Judge
table by last-name, and creates ``PanelVote`` rows with the appropriate
vote_type. Idempotent via ``get_or_create`` on the existing
``(opinion, judge)`` unique constraint -- re-runs only ever ADD votes,
never modify existing ones (except a Pass-1 upgrade from MAJORITY_JOIN
to MAJORITY_AUTHOR when the same judge turns out to be the byline
author).

Match strategy: last-name only, case-insensitive. Ambiguous last names
(multiple judges with the same surname) are skipped + counted in the
summary so the editor can disambiguate manually. Acceptable miss rate
for v1 -- the alternative is a per-judge alias table.

Usage::

    python manage.py resolve_judges            # full MN pass
    python manage.py resolve_judges --state MN --limit 500 --dry-run
    python manage.py resolve_judges --since 2020-01-01  # only recent

Cost: regex-only, no API calls. ~10-20 minutes for the full corpus
since each opinion's raw_text gets re-parsed.
"""
from __future__ import annotations

import re
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from django.core.management.base import BaseCommand
from django.db import connection
from django.utils.text import slugify

from opinions.models import Judge, Opinion, PanelVote


# Strip the role suffix (", Judge" / ", Justice" / etc.) off a byline.
_ROLE_SUFFIX_RE = re.compile(
    r",\s*(?:Chief\s+)?(?:Judge|Justice|J\.|C\.J\.)\.?\s*$",
    re.IGNORECASE,
)


# ----------------------------------------------------------------------
# Generic fallback byline extractor.
#
# For states without a registered state-specific parser, we still want
# to learn who authored + sat on the panel for each opinion. NH and AZ
# (and any future state that doesn't have a parser yet) hit this path
# until their state-specific parser is built.
#
# Pattern that catches the bulk of NH appellate opinions:
#   MACDONALD, C.J., and COUNTWAY and GOULD, JJ., concurred.
#   DONOVAN, COUNTWAY, and GOULD, JJ., concurred.
#   COUNTWAY and GOULD, JJ., concurred; TEMPLE, J., specially assigned ...
#
# Heuristic: scan the LAST ~2KB of raw_text (opinions sign off at the
# bottom) for the surnames immediately preceding "C.J.", "J.", or "JJ.,"
# tags followed by "concurred". The first surname tagged "C.J." (Chief
# Justice) is treated as the author if present -- that's the convention
# in single-author opinions where the chief signs first. Otherwise the
# panel is treated as per-curiam (all-join, no distinct author).
# ----------------------------------------------------------------------

# Surname token: starts with an uppercase letter (or accented uppercase
# letter to handle names like "VÁSQUEZ" / "Vásquez"), 3+ chars total,
# allows internal mixed case so both "MACDONALD" (NH all-caps style) and
# "Pelander" (AZ mixed-case style) match. Hyphens and apostrophes
# permitted for names like "O'Brien" / "Smith-Jones".
#
# `À-ÿ` covers Latin-1 supplement (À-ÿ) which catches the
# common accented characters in justice surnames (Vásquez, Núñez, etc.)
# without dragging in arbitrary unicode.
_SURNAME = r"[A-ZÀ-ß][A-Za-zÀ-ÿ\-']{2,}"

# Run of "<S1>, <S2>, and <S3>, JJ.," or "<S>, J.," patterns near the
# disposition footer. Captures the comma-separated surname list before
# the role suffix. The optional ``chief`` prefix catches "<X>, C.J., and"
# at the start of a mixed signoff like:
#   MACDONALD, C.J., and COUNTWAY and GOULD, JJ., concurred.
# where the Chief Justice has their own inline C.J. marker before the
# remaining JJ.-tagged panel members.
#
# Also accepts AZ-style "concurring" (Court of Appeals convention) and
# "joined" (rare older formats) as alternatives to "concurred".
_PANEL_GROUP_RE = re.compile(
    # Inline chief / presiding signer at the start of the byline:
    # NH style: "MACDONALD, C.J., and ..."
    # AZ-CtApp style: "Vasquez, P.J., and ..."   (Presiding Judge)
    rf"(?:\b(?P<chief>{_SURNAME}),\s*(?:C\.J\.|P\.J\.),\s*and\s+)?"
    rf"\b(?P<panel>(?:{_SURNAME})(?:\s*,?\s*(?:and\s+)?(?:{_SURNAME}))*)"
    rf",?\s+(?P<role>C\.J\.|P\.J\.|JJ?\.)\s*,?\s*(?:concurred|concurring|join(?:ed)?)\b"
)

# NH dissent-footer continuation. In opinions with a dissent, the
# footer-style signoff continues past the majority's "concurred." with
# an explicit dissenter line:
#   MACDONALD, C.J., and DONOVAN and COUNTWAY, JJ., concurred;
#   BASSETT, J., dissented.
# Each dissenter gets a DISSENT_AUTHOR vote -- they wrote their own
# dissenting opinion that gets a separate section header
# ("BASSETT, J., dissenting.") earlier in the body. We use this footer
# pattern rather than the body header because the footer is more
# uniform and unambiguous (the body header can show up inside cited
# quotations from OTHER cases).
_DISSENT_FOOTER_RE = re.compile(
    rf"\b(?P<name>{_SURNAME}),\s*J\.,?\s+dissented\b",
)

# AZ-style byline lives at the TOP of the opinion, not the bottom. Two
# distinct conventions, both handled here:
#
# 1. AZ Court of Appeals -- mixed-case "Judge", singular per name:
#      Presiding Judge David B. Gass delivered the decision of the court,
#      in which Judge Michael J. Brown and Judge Andrew J. Becke joined.
#      Vice Chief Judge Eppich authored the opinion of the Court, in which
#      Presiding Judge Vasquez and Chief Judge Staring concurred.
#
# 2. AZ Supreme Court -- ALL-UPPERCASE "JUSTICE", plus the panel often
#    shares one plural "JUSTICES" prefix over a comma-separated list:
#      CHIEF JUSTICE TIMMER authored the Opinion of the Court, in which
#      VICE CHIEF JUSTICE LOPEZ, JUSTICES BOLICK, BEENE, KING, and CRUZ
#      joined.
#    Author is the first named role; panel = everything in the joined list.
#
# Two-step: find the "<author>... authored/delivered... in which <list>
# concurred/joined" block, then enumerate via TWO sub-regexes -- a singular
# "<role> <name>" matcher (catches CoA and AZ-Supreme's leading roles) and a
# plural "JUSTICES <name>, <name>, ..." matcher (catches AZ-Supreme's
# panel-list shorthand).
#
# CRITICAL: name capture must be CASE-SENSITIVE so lowercase verbs like
# "joined" / "authored" don't slip into a name slot. The enclosing role
# prefix and verbs are CASE-INSENSITIVE so both "Chief Judge" (CoA) and
# "CHIEF JUSTICE" (Supreme) match. We mix these flags via Python's
# `(?-i:...)` inline scoping: the outer regex uses re.IGNORECASE, but the
# name capture group disables IGNORECASE locally.
_AZ_ROLE_PREFIX_CI = r"(?:Presiding\s+|Vice\s+Chief\s+|Chief\s+|Vice\s+)?(?:Judge|Justice)"
# Strict name: each word must start with an uppercase (or accented uppercase)
# letter. Allow internal periods (initials like "B."), apostrophes, hyphens.
_AZ_NAME_STRICT = (
    r"[A-ZÀ-ß][A-Za-zÀ-ÿ.'\-]+"           # required first word
    r"(?:\s+[A-ZÀ-ß][A-Za-zÀ-ÿ.'\-]+){0,3}"  # up to 3 additional words
)
_AZ_BYLINE_BLOCK_RE = re.compile(
    # Greedy-but-bounded block: "<role> <name> ... in which ... concurred/joined".
    # DOTALL because the block typically spans 2-3 lines.
    rf"\b{_AZ_ROLE_PREFIX_CI}\s+(?-i:{_AZ_NAME_STRICT})"
    rf"\s+(?:authored|delivered)[\s\S]{{0,400}}?"
    rf"in\s+which\s+[\s\S]{{0,400}}?\b(?:concurred|joined)\b",
    re.IGNORECASE | re.DOTALL,
)
# Singular: "<role> <name>" -- catches CoA's mixed-case "Judge Brown" and
# AZ-Supreme's "CHIEF JUSTICE TIMMER" / "VICE CHIEF JUSTICE LOPEZ" /
# "JUSTICE KING". Role prefix matches case-insensitively; name stays
# case-sensitive.
_AZ_NAMED_SINGULAR_RE = re.compile(
    rf"\b{_AZ_ROLE_PREFIX_CI}\s+((?-i:{_AZ_NAME_STRICT}))",
    re.IGNORECASE,
)
# Plural: "JUSTICES <name>, <name>, ..., and <name>" -- AZ Supreme shares
# one "JUSTICES" prefix over a comma-separated panel list, optionally with
# an Oxford-comma "and" before the final name. Each name in the list stays
# case-sensitive so " joined" / lowercase prose doesn't get slurped.
_AZ_NAMED_PLURAL_RE = re.compile(
    rf"\bJUSTICES\s+((?-i:{_AZ_NAME_STRICT})(?:\s*,\s*(?:and\s+)?(?-i:{_AZ_NAME_STRICT}))*)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GenericByline:
    """Output of the generic byline extractor."""
    author_last: str | None
    panel_last: list[str]
    dissenter_last: list[str]
    raw_matches: list[str]  # for debug / log inspection


def _extract_generic_byline(raw_text: str) -> GenericByline:
    """Extract author + panel last-names from any-state opinion text.

    Returns lowercased last-names ready to match against last_name_map.
    Falls back gracefully (empty author + empty panel) on text that
    doesn't follow either of the two supported conventions:

    - NH-style footer concurrence (``X, JJ., concurred.``) -- scanned in
      the LAST ~2KB of raw_text.
    - AZ-style top-of-opinion byline (``Judge X authored the opinion of
      the Court, in which Judge Y and Judge Z joined``) -- scanned in
      the FIRST ~4KB of raw_text.
    """
    if not raw_text:
        return GenericByline(None, [], [], [])

    author_last: str | None = None
    all_panel: list[str] = []
    dissenter_lasts: list[str] = []
    raw_matches: list[str] = []

    # --- AZ-style top-of-opinion byline ---
    # Caption is typically within the first 3-4KB (preamble + counsel
    # block + "OPINION" header + first sentence of the byline). Scan
    # the first 5KB to be safe.
    head = raw_text[:5000]

    def _last(name: str) -> str:
        return name.strip().split()[-1].lower().rstrip(",.;'")

    for block in _AZ_BYLINE_BLOCK_RE.finditer(head):
        raw_matches.append(block.group(0)[:200])
        block_text = block.group(0)

        # Two-pass extraction:
        # 1) Singular "<role> <name>" matches (CoA + AZ-Supreme leading roles)
        # 2) Plural "JUSTICES <name>, <name>, ..." panel lists (AZ Supreme)
        # Preserve textual order within each pass so the FIRST captured
        # judge is the byline author. Dedupe across both passes after.
        in_block_judges: list[str] = []

        for m in _AZ_NAMED_SINGULAR_RE.finditer(block_text):
            last = _last(m.group(1))
            if last:
                in_block_judges.append(last)

        for m in _AZ_NAMED_PLURAL_RE.finditer(block_text):
            names_blob = m.group(1)
            # Split on commas (with optional "and") and on bare " and ".
            for nm in re.split(r"\s*,\s*(?:and\s+)?|\s+and\s+", names_blob):
                last = _last(nm.strip())
                if last:
                    in_block_judges.append(last)

        if not in_block_judges:
            continue

        # First named judge = author; the rest = panel members. Dedupe
        # preserving order (a judge captured by both passes counts once).
        seen_in_block: set[str] = set()
        ordered: list[str] = []
        for j in in_block_judges:
            if j not in seen_in_block:
                seen_in_block.add(j)
                ordered.append(j)

        if author_last is None:
            author_last = ordered[0]
        for nm in ordered[1:]:
            all_panel.append(nm)

    # --- NH-style footer concurrence ---
    # Concentrate the search on the last 8KB -- panel lists are at the
    # footer, never the body. For unanimous opinions the footer sits in
    # the last few hundred bytes, but opinions with a dissent push the
    # majority footer back behind the (typically 4-10KB) dissent body,
    # so the window has to be generous. ``_PANEL_GROUP_RE`` requires the
    # explicit ", JJ., concurred" / ", J., concurred" anchor, so this
    # wider window doesn't admit false positives from majority prose
    # like "ROBERTS sued LARSON".
    tail = raw_text[-8000:]

    for m in _PANEL_GROUP_RE.finditer(tail):
        raw_matches.append(m.group(0))
        chief = m.group("chief")
        names_blob = m.group("panel")
        role = m.group("role")
        # The inline Chief Justice (when present) is the signer/author of
        # the opinion -- record + remember separately from the panel.
        if chief:
            author_last = chief.lower()
        # Split on " and " and "," to enumerate panel surnames. The
        # _SURNAME regex requires uppercase + 3+ letters, so role
        # abbreviations like "C.J." can't sneak through this token split,
        # but defensive: drop any leftover tokens that don't look like a
        # surname after lowercasing (period-containing tokens like "c.j.").
        names = re.split(r",\s*(?:and\s+)?|\s+and\s+", names_blob)
        names = [n.strip() for n in names if n.strip() and "." not in n]
        # Fallback author detection when no explicit chief prefix was
        # found: a single surname tagged C.J. / P.J. or a single-name J.
        # signoff is the author by convention.
        if author_last is None and role in ("C.J.", "P.J.") and names:
            author_last = names[0].lower()
        elif author_last is None and role == "J." and len(names) == 1:
            author_last = names[0].lower()
        all_panel.extend(n.lower() for n in names)

    # --- Dissenters in the same NH footer ---
    # The "concurred; X, J., dissented." continuation lives in the same
    # tail window as the majority signoff. Each match is a DISSENT_AUTHOR
    # vote -- they wrote a separate dissenting opinion. Dedupe across
    # matches (defensive: the same name shouldn't appear twice in a
    # single footer, but be safe).
    seen_dissenters: set[str] = set()
    for m in _DISSENT_FOOTER_RE.finditer(tail):
        ln = (m.group("name") or "").lower()
        if ln and ln not in seen_dissenters:
            seen_dissenters.add(ln)
            dissenter_lasts.append(ln)
            raw_matches.append(m.group(0))

    # Dedupe + drop the author from panel (author already counted via PV)
    seen = set()
    panel: list[str] = []
    for n in all_panel:
        if n in seen:
            continue
        if author_last is not None and n == author_last:
            continue
        seen.add(n)
        panel.append(n)
    # Also drop any name appearing both in panel and dissenters --
    # they're dissenters, not majority-joiners.
    panel = [n for n in panel if n not in seen_dissenters]
    return GenericByline(
        author_last=author_last,
        panel_last=panel,
        dissenter_last=dissenter_lasts,
        raw_matches=raw_matches,
    )


# Title-case for display when we create new Judge rows from a byline-
# only last name -- "MACDONALD" -> "Macdonald" reads better in the
# admin + on dossier pages. Editors can rename later to the canonical
# capitalization (e.g. "MacDonald").
def _titlecase_surname(upper: str) -> str:
    return upper[:1] + upper[1:].lower() if upper else upper


def _last_name(name: str) -> str:
    """Return the last token of ``name`` after stripping role suffix.

    'Jennifer L. Frisch'      -> 'Frisch'
    'Frisch, Judge'           -> 'Frisch'
    'L. Frisch, J.'           -> 'Frisch'
    'Van Buren, Judge'        -> 'Buren'   (acceptable miss for v1)
    """
    if not name:
        return ""
    cleaned = _ROLE_SUFFIX_RE.sub("", name).strip()
    # Re-strip just in case ",..." remains
    if "," in cleaned:
        cleaned = cleaned.split(",", 1)[0].strip()
    words = cleaned.split()
    if not words:
        return ""
    return words[-1].strip(".,'-")


class Command(BaseCommand):
    help = "Resolve byline + panel names to Judges and auto-create PanelVote rows."

    def add_arguments(self, parser):
        parser.add_argument("--state", default="MN", help="State code (default MN).")
        parser.add_argument(
            "--limit", type=int, default=None,
            help="Process at most N opinions (smoke-test convenience).",
        )
        parser.add_argument(
            "--since", default=None,
            help="Only opinions filed >= YYYY-MM-DD. Useful for incremental re-runs.",
        )
        parser.add_argument(
            "--min-id", type=int, default=0,
            help=(
                "Only opinions with id > this. Stateless resume cursor: the "
                "byline extraction is CPU-heavy and NFSN culls long processes "
                "on CPU time, so a full state is run as bounded chunks -- each "
                "run prints 'next --min-id <N>' to feed the next one."
            ),
        )
        parser.add_argument(
            "--max-runtime", type=int, default=0,
            help=(
                "Self-exit cleanly after N seconds (0 = run to completion). "
                "Keeps a run under NFSN's CPU-time cull; pair with --min-id to "
                "resume. Mirrors embed_opinions."
            ),
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Compute matches + counts but don't create PanelVote rows.",
        )
        parser.add_argument(
            "--create-missing", action="store_true",
            help=(
                "Create Judge rows for byline + panel last-names that "
                "don't match an existing roster. Use for states whose "
                "judges weren't seeded by a roster scraper -- byline-"
                "learned Judges get status=UNKNOWN + "
                "is_currently_seated=False so an editor can review + "
                "promote them later. Idempotent across re-runs via the "
                "(state, slug) unique constraint."
            ),
        )

    def handle(self, *args, state, limit, since, dry_run, create_missing,
               min_id, max_runtime, **options):
        # Local import: parsing module loads the state-parser registry
        from opinions.parsing import parse as parse_opinion

        state_code = state.upper()

        # Batch command: lift settings' 25s web-tier max_statement_time so the
        # corpus-wide COUNT + scan don't get KILLed under contention (errno
        # 1969). Vendor-guarded so local SQLite dev is a clean no-op. See the
        # "Batch commands MUST lift max_statement_time" gotcha in CLAUDE.md.
        if connection.vendor == "mysql":
            with connection.cursor() as cur:
                cur.execute("SET SESSION max_statement_time = 0")

        # Build last_name -> [Judge,...] lookup for the state. Ambiguity
        # (multiple judges sharing a last name) gets logged + skipped.
        judges = list(Judge.objects.filter(state__code=state_code))
        last_name_map: dict[str, list[Judge]] = defaultdict(list)
        for j in judges:
            ln = _last_name(j.full_name)
            if ln:
                last_name_map[ln.lower()].append(j)

        ambiguous_names = sum(1 for v in last_name_map.values() if len(v) > 1)
        unique_names = sum(1 for v in last_name_map.values() if len(v) == 1)

        self.stdout.write(self.style.SUCCESS(
            f"Resolving panels for {state_code}: "
            f"{len(judges)} judges, "
            f"{unique_names} unique-last-name lookups, "
            f"{ambiguous_names} ambiguous (skipped)"
            + ("  [--create-missing ON]" if create_missing else "")
        ))

        # Cache State row -- needed when --create-missing forges new Judges.
        from opinions.models import State as _State
        state_obj = _State.objects.get(code=state_code)

        # Counter for byline-learned Judges (only meaningful when
        # create_missing is on). Tracks per-name first-create so we can
        # log a single summary at the end.
        forged_judges: int = 0

        def _get_or_create_byline_judge(last_lower: str) -> Judge | None:
            """Return Judge for ``last_lower`` (state-scoped), creating one
            when --create-missing is on and no roster row exists.

            Updates ``last_name_map`` in place so subsequent opinions in
            the same run hit the cache instead of re-querying. Skips
            the create path when last_lower is ambiguous against the
            existing roster -- we'd rather miss than mint a duplicate.
            """
            nonlocal forged_judges
            existing = last_name_map.get(last_lower, [])
            if len(existing) == 1:
                return existing[0]
            if len(existing) > 1:
                # Ambiguous against roster -- caller decides what to do
                # (currently: skip + increment the ambiguous counter).
                return None
            if not create_missing:
                return None
            # Forge a Judge from the byline last-name only. Editor can
            # rename + upgrade status later via admin.
            display_name = _titlecase_surname(last_lower.upper())
            base_slug = slugify(display_name) or last_lower
            # (state, slug) is unique_together; suffix with -<n> if needed.
            slug = base_slug
            n = 2
            while Judge.objects.filter(state=state_obj, slug=slug).exists():
                slug = f"{base_slug}-{n}"
                n += 1
            if dry_run:
                # Synthesize a fake row so downstream logic doesn't crash;
                # don't hit the DB.
                new_j = Judge(state=state_obj, full_name=display_name, slug=slug)
            else:
                new_j = Judge.objects.create(
                    state=state_obj,
                    full_name=display_name,
                    slug=slug,
                    status=Judge.Status.UNKNOWN,
                    is_currently_seated=False,
                    source_id=f"byline:{state_code}:{last_lower}",
                )
            last_name_map[last_lower].append(new_j)
            forged_judges += 1
            return new_j

        # Pre-resolve court IDs so the scan is an FK-index lookup, not a JOIN
        # over the 2.75GB opinions table (the documented perf gotcha).
        court_ids = list(
            _State.objects.get(code=state_code).courts.values_list("id", flat=True)
        )
        # order_by("id") + id__gt makes --min-id a stable resume cursor.
        opinion_qs = (
            Opinion.objects.filter(court_id__in=court_ids, id__gt=min_id)
            .exclude(raw_text="")
            .select_related("court")
            .order_by("id")
        )
        if since:
            try:
                cutoff = date.fromisoformat(since)
            except ValueError:
                self.stderr.write(f"Bad --since date: {since!r}; use YYYY-MM-DD.")
                return
            opinion_qs = opinion_qs.filter(release_date__gte=cutoff)

        total = opinion_qs.count()
        if limit:
            total = min(total, limit)

        self.stdout.write(
            f"  scanning {total:,} opinions"
            + (f" filed since {since}" if since else "")
            + ("." if not dry_run else " (DRY RUN; no DB writes).")
        )

        scanned = 0
        author_resolved = panel_resolved = 0
        author_ambiguous = panel_ambiguous = 0
        new_author_votes = new_join_votes = upgraded_votes = 0
        new_dissent_votes = dissent_ambiguous = 0
        last_id = min_id
        timed_out = False
        t0 = time.time()

        for opinion in opinion_qs.iterator(chunk_size=500):
            if limit and scanned >= limit:
                break
            if max_runtime and (time.time() - t0) >= max_runtime:
                timed_out = True
                break
            scanned += 1
            last_id = opinion.id

            if scanned % 2_000 == 0:
                elapsed = time.time() - t0
                rate = scanned / max(elapsed, 0.001)
                eta = (total - scanned) / max(rate, 0.001)
                self.stdout.write(
                    f"  scanned {scanned:>6,}/{total:,}  "
                    f"author={new_author_votes:>5,}  "
                    f"join={new_join_votes:>5,}  "
                    f"dissent={new_dissent_votes:>4,}  "
                    f"upgraded={upgraded_votes:>4,}  "
                    f"({rate:>4.0f}/s, eta {eta/60:.0f}min)",
                    ending="\n",
                )

            # Hybrid extraction. The state-specific parser is preferred
            # for author + panel when it returns them, but state parsers
            # commonly leave one or both empty (e.g. the NH parser
            # captures the author byline at the top of the document but
            # doesn't try to parse the "<NAME>, C.J., and <NAMES>, JJ.,
            # concurred." footer -- that's _extract_generic_byline's
            # specialty). For each opinion we run the parser, then run
            # the generic extractor and use it as a fallback for any
            # field the parser left empty.
            result = parse_opinion(state_code, opinion.raw_text)
            if result is not None:
                author_last = (
                    _last_name(result.author).lower() if result.author else None
                )
                panel_lasts = [_last_name(p).lower() for p in result.panel]
                panel_lasts = [p for p in panel_lasts if p]
            else:
                author_last = None
                panel_lasts = []
            # Always run the generic extractor in addition to the parser:
            # the parser handles author + panel for its state, but only
            # the generic extractor scans the NH-style "; X, J., dissented."
            # continuation that carries dissenter names. Without this the
            # concordance page on judge_compare always read 100% agreement
            # for NH pairs since no DISSENT_AUTHOR votes existed.
            generic = _extract_generic_byline(opinion.raw_text)
            if author_last is None and generic.author_last:
                author_last = generic.author_last
            if not panel_lasts and generic.panel_last:
                panel_lasts = list(generic.panel_last)
            # The generic extractor treats the byline-footer chief justice
            # ("X, C.J., and ...") as the AUTHOR when running standalone --
            # that's right when there's no parser, since the chief is the
            # only signer it has. But when the parser identified a
            # different author at the top of the document (the real
            # signer), the chief is actually a JOINING panel member.
            # Promote them into the panel list.
            if (
                generic.author_last
                and generic.author_last != author_last
                and generic.author_last not in panel_lasts
                and generic.author_last not in generic.dissenter_last
            ):
                panel_lasts.append(generic.author_last)
            # Remove dissenters from the panel-joiner list if they accidentally
            # appeared there (defensive -- the generic extractor already
            # excludes dissenters from its panel output, but a state parser
            # may not have).
            dissenter_lasts = list(generic.dissenter_last)
            if dissenter_lasts:
                dissenter_set = set(dissenter_lasts)
                panel_lasts = [p for p in panel_lasts if p not in dissenter_set]

            # ---- Pass 1: Author ----
            author_judge: Judge | None = None
            if author_last:
                pre_existing = last_name_map.get(author_last, [])
                if len(pre_existing) > 1:
                    author_ambiguous += 1
                else:
                    author_judge = _get_or_create_byline_judge(author_last)
                    if author_judge is not None:
                        author_resolved += 1

            if author_judge and not dry_run:
                pv, created = PanelVote.objects.get_or_create(
                    opinion=opinion,
                    judge=author_judge,
                    defaults={"vote_type": PanelVote.Vote.MAJORITY_AUTHOR},
                )
                if created:
                    new_author_votes += 1
                elif pv.vote_type == PanelVote.Vote.MAJORITY_JOIN:
                    # Existing CL-loaded row only knew "joined majority";
                    # parser confirms this judge actually authored.
                    pv.vote_type = PanelVote.Vote.MAJORITY_AUTHOR
                    pv.save(update_fields=["vote_type"])
                    upgraded_votes += 1

            # ---- Pass 2: Panel members ----
            for panel_last in panel_lasts:
                pre_existing = last_name_map.get(panel_last, [])
                if len(pre_existing) > 1:
                    panel_ambiguous += 1
                    continue

                panel_judge = _get_or_create_byline_judge(panel_last)
                if panel_judge is None:
                    continue
                if author_judge is not None and panel_judge.pk == author_judge.pk:
                    # Already counted as author; don't downgrade to "joined".
                    continue
                panel_resolved += 1

                if not dry_run:
                    _, created = PanelVote.objects.get_or_create(
                        opinion=opinion,
                        judge=panel_judge,
                        defaults={"vote_type": PanelVote.Vote.MAJORITY_JOIN},
                    )
                    if created:
                        new_join_votes += 1

            # ---- Pass 3: Dissenters ----
            # Each dissenter wrote a separate dissenting opinion, so
            # the vote type is DISSENT_AUTHOR. If a prior pass wrote a
            # MAJORITY_JOIN row for this same judge (e.g. a CL bulk
            # load that didn't distinguish dissents), upgrade it in
            # place rather than leaving conflicting data.
            for dissenter_last in dissenter_lasts:
                pre_existing = last_name_map.get(dissenter_last, [])
                if len(pre_existing) > 1:
                    dissent_ambiguous += 1
                    continue

                dissent_judge = _get_or_create_byline_judge(dissenter_last)
                if dissent_judge is None:
                    continue

                if not dry_run:
                    pv, created = PanelVote.objects.get_or_create(
                        opinion=opinion,
                        judge=dissent_judge,
                        defaults={"vote_type": PanelVote.Vote.DISSENT_AUTHOR},
                    )
                    if created:
                        new_dissent_votes += 1
                    elif pv.vote_type == PanelVote.Vote.MAJORITY_JOIN:
                        # CL bulk row mis-classified -- the footer says
                        # this judge dissented, not joined.
                        pv.vote_type = PanelVote.Vote.DISSENT_AUTHOR
                        pv.save(update_fields=["vote_type"])
                        upgraded_votes += 1

        elapsed = time.time() - t0
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"{'Timed out' if timed_out else 'Done'} in {elapsed/60:.1f} min."
        ))
        if timed_out:
            self.stdout.write(self.style.WARNING(
                f"  time budget hit; resume the rest with:  "
                f"--min-id {last_id}"
            ))
        else:
            self.stdout.write(f"  reached end of corpus (last id {last_id}).")
        self.stdout.write(
            f"  scanned:             {scanned:>7,}\n"
            f"  authors resolved:    {author_resolved:>7,}  "
            f"(ambiguous skipped: {author_ambiguous})\n"
            f"  panels resolved:     {panel_resolved:>7,}  "
            f"(ambiguous skipped: {panel_ambiguous})\n"
            f"  new author votes:    {new_author_votes:>7,}\n"
            f"  new joined votes:    {new_join_votes:>7,}\n"
            f"  new dissent votes:   {new_dissent_votes:>7,}  "
            f"(ambiguous skipped: {dissent_ambiguous})\n"
            f"  upgraded (J->A/D):   {upgraded_votes:>7,}"
            + (
                f"\n  byline-learned judges (status=UNKNOWN): {forged_judges:>7,}"
                if create_missing else ""
            )
            + ("\n  (DRY RUN -- nothing saved)" if dry_run else "")
        )
