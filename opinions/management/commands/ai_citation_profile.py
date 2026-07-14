"""Profile the opinions that LIVE AI agents fetched -- "what kind of law is AI
grounding on through us?"

Joins the query-stripped gunicorn access log (which opinions retrieval agents
pulled: chatgpt-user / claude-user / claude-web / perplexity-user) to each
opinion's DB metadata -- court, state, era, disposition, precedential status.
The log holds paths only (search is POST, so there are no queries to read), so
this reports the *character* of what AI reaches for, never anyone's questions.
Read-only; makes no writes.

Usage:
    python manage.py ai_citation_profile [--days N] [--log PATH] [--top N]
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import date, timedelta
from urllib.parse import unquote

from django.core.management.base import BaseCommand, CommandError

from opinions.models import Opinion

# Live retrieval agents: a user's AI session fetched the page to answer/cite it
# right then (as opposed to bulk training crawlers like gptbot / meta-externalagent).
LIVE_AGENT_TOKENS = ("chatgpt-user", "claude-user", "claude-web", "perplexity-user")
DEFAULT_LOG = "/home/logs/daemon_gunicorn.log"

# CLF-ish access line:
#   host [dd/Mon/yyyy:hh:mm:ss tz] "METHOD /path HTTP/x.y" status bytes "UA"
# The path can contain spaces (case numbers like "No. 84-102"), so capture it
# lazily up to the trailing ' HTTP/'.
_LINE = re.compile(
    r'\[(\d{1,2}/[A-Za-z]{3}/\d{4}):[^\]]*\]\s+'
    r'"[A-Z]+\s+(\S.*?)\s+HTTP/[0-9.]+"\s+'
    r'(\d{3})\s+\S+\s+"([^"]*)"'
)
_OPINION_PATH = re.compile(r"^/opinion/(.+?)/?$")


def _bar(write, title, counter, total, chronological=False):
    write(f"{title}:")
    if not counter:
        write("  (none)")
        write("")
        return
    items = sorted(
        counter.items(),
        key=(lambda kv: kv[0]) if chronological else (lambda kv: -kv[1]),
    )
    for k, v in items:
        pct = (100.0 * v / total) if total else 0.0
        write(f"  {v:>5}  {pct:4.0f}%  {k}")
    write("")


class Command(BaseCommand):
    help = "Profile the corpus opinions that live AI agents fetched (access-log x DB join)."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=7)
        parser.add_argument("--log", default=DEFAULT_LOG)
        parser.add_argument("--top", type=int, default=20)

    def handle(self, *args, days, log, top, **opts):
        want = {
            (date.today() - timedelta(days=i)).strftime("%d/%b/%Y")
            for i in range(days)
        }

        try:
            fh = open(log, "r", errors="replace")
        except OSError as exc:
            raise CommandError(f"Cannot read access log {log!r}: {exc}")

        hits = Counter()  # case_number -> live-agent fetches (status 200)
        total_events = 0
        with fh:
            for line in fh:
                m = _LINE.search(line)
                if not m:
                    continue
                d, path, status, ua = m.group(1), m.group(2), m.group(3), m.group(4).lower()
                if d not in want or status != "200":
                    continue
                if not any(tok in ua for tok in LIVE_AGENT_TOKENS):
                    continue
                pm = _OPINION_PATH.match(path)
                if not pm:
                    continue
                case = unquote(pm.group(1)).strip()
                if case:
                    hits[case] += 1
                    total_events += 1

        if not hits:
            self.stdout.write(
                f"No live-agent opinion fetches in the last {days} days. "
                "(Widen --days, or check back as AI grounding grows.)"
            )
            return

        cases = list(hits.keys())
        found = {
            o.case_number: o
            for o in Opinion.objects.filter(case_number__in=cases)
            .select_related("court", "court__state")
            .defer("raw_text", "html_content")
        }

        by_state = Counter()
        by_court = Counter()
        by_decade = Counter()
        by_disp = Counter()
        by_prec = Counter()
        matched_events = 0
        ranked = []
        for case, n in hits.items():
            o = found.get(case)
            if not o:
                continue
            matched_events += n
            by_state[o.court.state.name] += n
            by_court[o.court.short_label] += n
            if o.release_date:
                by_decade[(o.release_date.year // 10) * 10] += n
            by_disp[o.disposition_bucket or "other"] += n
            by_prec["precedential" if o.is_precedential else "nonprecedential"] += n
            ranked.append((n, o))

        unmatched_cases = sum(1 for c in cases if c not in found)
        unmatched_events = total_events - matched_events
        ranked.sort(key=lambda t: (-t[0], t[1].release_date or date.min))

        w = self.stdout.write
        w(f"AI-citation profile -- last {days} days")
        w(f"Live agents: {', '.join(LIVE_AGENT_TOKENS)}")
        w(
            f"Fetch events (200): {total_events}   matched to a corpus opinion: "
            f"{matched_events}   unmatched: {unmatched_events} "
            f"({unmatched_cases} distinct paths not resolved to an opinion)"
        )
        w("")
        _bar(w, "By state", by_state, matched_events)
        _bar(w, "By court", by_court, matched_events)
        _bar(
            w, "By era (decade filed)",
            Counter({f"{k}s": v for k, v in by_decade.items()}),
            matched_events, chronological=True,
        )
        _bar(w, "By disposition", by_disp, matched_events)
        _bar(w, "Precedential?", by_prec, matched_events)

        w(f"Top {top} opinions AI reached for:")
        for n, o in ranked[:top]:
            cite = f"  [{o.reporter_cite}]" if o.reporter_cite else ""
            title = (o.title or "")[:70]
            w(f"  {n:>3}x  {o.court.short_label:<7} {o.release_date}  {title}{cite}")
