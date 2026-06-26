"""Smoke test for the eyecite wrapper (``citations_eyecite.py``).

Proves the library installs and runs against ACTUAL DocketDrift opinion text --
without touching any production path or writing anything. Pulls a small sample
of MN opinions (MN is the largest non-NH corpus and the first migration target),
runs both the raw eyecite tokenizer (for a citation-type breakdown) and our
``extract`` wrapper (for the resolved-row count), and prints a summary.

Run it locally, where eyecite is installed (it is NOT installed on NFSN, by
design -- the wrapper isn't wired into production yet)::

    .venv/Scripts/python.exe opinions/parsing/citations_eyecite_smoke.py
    .venv/Scripts/python.exe opinions/parsing/citations_eyecite_smoke.py --limit 25 --state AZ

No output is persisted; nothing is committed. This is a one-shot sanity check.
"""
from __future__ import annotations

import argparse
import collections
import os
import sys


def _bootstrap_django() -> None:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "docketdrift_site.settings")
    import django

    django.setup()


def main() -> int:
    parser = argparse.ArgumentParser(description="eyecite wrapper smoke test")
    parser.add_argument("--state", default="MN", help="USPS code to sample (default MN).")
    parser.add_argument("--limit", type=int, default=15, help="Opinions to sample (default 15).")
    args = parser.parse_args()

    _bootstrap_django()

    from eyecite import get_citations
    from eyecite.models import FullCaseCitation

    from opinions.models import Court, Opinion
    from opinions.parsing.citations_eyecite import extract

    court_ids = list(
        Court.objects.filter(state__code=args.state.upper()).values_list("id", flat=True)
    )
    if not court_ids:
        print("No courts for state %s -- nothing to sample." % args.state.upper())
        return 1

    sample = list(
        Opinion.objects.filter(court_id__in=court_ids)
        .exclude(raw_text="")
        .only("id", "title", "raw_text")
        .order_by("-release_date")[: args.limit]
    )
    print("Sampling %d %s opinions...\n" % (len(sample), args.state.upper()))

    type_counts: collections.Counter = collections.Counter()
    total_raw = 0
    total_full = 0
    total_wrapper = 0
    errors = 0

    for op in sample:
        text = op.raw_text or ""
        try:
            raw = get_citations(text)
        except Exception as exc:  # noqa: BLE001 -- smoke test: report, don't crash
            errors += 1
            print("  ERROR opinion %s: %s: %s" % (op.id, type(exc).__name__, exc))
            continue
        for c in raw:
            type_counts[type(c).__name__] += 1
        full = sum(1 for c in raw if isinstance(c, FullCaseCitation))
        # self_cite stays empty here: MN/AZ reporter cites aren't in our opinion
        # text yet (they await the CourtListener backfill), so there's nothing to
        # exclude. The wrapper still dedupes within each opinion.
        wrapped = extract(text, self_cite="")
        total_raw += len(raw)
        total_full += full
        total_wrapper += len(wrapped)
        name = (op.title or "")[:48]
        print(
            "  op %-7s raw=%-4d full-case=%-4d wrapper-rows=%-4d  %s"
            % (op.id, len(raw), full, len(wrapped), name)
        )

    print("\n--- summary ---")
    print("opinions sampled : %d" % len(sample))
    print("errors           : %d" % errors)
    print("raw citations    : %d  (all eyecite tokens, incl. id./supra/short)" % total_raw)
    print("full case cites  : %d  (FullCaseCitation only)" % total_full)
    print("wrapper rows     : %d  (deduped resolvable ExtractedCitation rows)" % total_wrapper)
    print("\nby eyecite type:")
    for name, count in type_counts.most_common():
        print("  %-22s %d" % (name, count))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
