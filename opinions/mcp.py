"""DocketDrift MCP server -- read-only, stateless, hand-rolled.

A minimal Model Context Protocol endpoint served from the existing Django
app at ``/mcp``. Lets MCP clients (Claude Desktop / Claude Code / any MCP
host) attach DocketDrift as a tool and ground legal answers in verbatim
official opinion text.

Design decisions, each deliberate:

- **Hand-rolled JSON-RPC, no SDK.** The official ``mcp`` PyPI package
  drags anyio/httpx/pydantic onto NFSN's FreeBSD, where C-extension
  deps are a documented minefield (see the numpy gotcha in CLAUDE.md).
  The stateless server profile of the Streamable HTTP transport is
  ~200 lines of plain Django; the protocol surface we need is just
  ``initialize`` / ``tools/list`` / ``tools/call``.
- **Stateless.** No ``Mcp-Session-Id``, no SSE streams, no server-push.
  Every POST is a self-contained JSON-RPC message answered with plain
  ``application/json`` (the spec explicitly permits a JSON response in
  place of an SSE stream). GET returns 405 -- we never initiate.
- **Read-only + privacy-preserving.** Tools only read. Tool arguments
  (the user's research queries) ride in the POST body, which the
  query-stripped gunicorn access log never records -- consistent with
  the site-wide "we cannot produce what we never stored" posture. Do
  NOT add logging of tool arguments here, ever.
- **Bounded.** Every DB path inherits the web tier's 25s
  max_statement_time (never lifted here), LIMITs everything, defers
  the giant TEXT columns on list queries, and reuses the hardened view
  helpers (``_fulltext_candidate_ids``, ``_match_opinions``,
  ``_pick_opinion``, ``_cohort_with_heat``) rather than reinventing
  their query shapes. Semantic search is deliberately NOT exposed in
  v1 -- the cosine scan is the one expensive path on the single-worker
  deployment; keyword + cite + graph lookups cover the grounding use
  case.

DARK-DEPLOY STATUS: live but unlisted. No site links, no docs page.
Before public launch (connector-directory submission): add the MCP
section to /privacy/ and load-test tools/call concurrency.
"""
from __future__ import annotations

import json
import threading

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt

from opinions.models import (
    Court,
    Judge,
    Opinion,
    OpinionCitation,
    ParallelCite,
    State,
    StatuteCitation,
)

PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "docketdrift", "version": "1.0.0"}

# Hard caps on tool output sizes. MCP hosts stuff tool results into the
# model's context window; a 700KB opinion would blow it. full_text is
# opt-in and capped; list tools cap their row counts.
MAX_FULL_TEXT_CHARS = 150_000
MAX_LIST_LIMIT = 50
MAX_QUOTE_CHARS = 500

# /mcp shares ONE gunicorn worker (8 threads) with the public website, so
# a busy connector can starve the site. Load-tested 2026-08-26 against
# internal gunicorn: the five indexed tools answer in 3-17ms, but
# search_opinions runs the corpus-wide FULLTEXT candidate scan --
# ~0.9s on a narrow query, ~10s on a common term. Mixed tool calls stayed
# clean at 4/8/16 concurrent (site landing 0.02s), but at 32 the LA
# landing page degraded to 6.3s. No 5xx and no poisoned connections at
# any level, so the failure mode is starvation, not corruption.
#
# Cap the ONE expensive path rather than all of tools/call: throttling
# the millisecond lookups would cost availability and buy nothing. An
# in-process semaphore is genuinely effective here because the
# deployment is workers=1 (same reason the query-embedding cache works).
# Refused calls come back as isError content, which the model reads and
# can act on, rather than a transport error it cannot.
MCP_SEARCH_CONCURRENCY = getattr(settings, "MCP_SEARCH_CONCURRENCY", 3)
_SEARCH_SLOTS = threading.BoundedSemaphore(MCP_SEARCH_CONCURRENCY)
_THROTTLED_TOOLS = {"search_opinions"}


def _live_state_codes() -> list[str]:
    return list(
        State.objects.filter(is_live=True).values_list("code", flat=True)
    )


def _resolve_state(code: str) -> State | None:
    if not code:
        return None
    try:
        return State.objects.get(code=code.upper())
    except State.DoesNotExist:
        return None


def _court_ids(state: State) -> list[int]:
    return list(state.courts.values_list("id", flat=True))


def _opinion_brief(op: Opinion) -> dict:
    """Compact metadata dict for one opinion (no body text)."""
    return {
        "docket": op.case_number,
        "title": op.title,
        "court": op.court.short_label,
        "date": str(op.release_date) if op.release_date else None,
        "disposition": op.disposition or None,
        "reporter_cite": op.reporter_cite or None,
        "precedential": op.is_precedential,
        "url": op.get_absolute_url(),
    }


# --------------------------------------------------------------------------
# Tool implementations. Each returns a JSON-serializable dict; raising
# ValueError produces a clean isError result with the message.
# --------------------------------------------------------------------------

def tool_search_opinions(args: dict) -> dict:
    """Keyword search over one state's corpus (FULLTEXT, bounded)."""
    from opinions.views import _fulltext_candidate_ids

    query = (args.get("query") or "").strip()
    if not query:
        raise ValueError("query is required")
    state = _resolve_state(args.get("state") or "")
    if state is None:
        raise ValueError(
            f"unknown or missing state; use one of {_live_state_codes()}"
        )
    limit = min(int(args.get("limit") or 10), MAX_LIST_LIMIT)
    year_from = args.get("year_from")
    year_to = args.get("year_to")

    ids, capped = _fulltext_candidate_ids(query, _court_ids(state))
    if ids is None:
        return {"results": [], "note": "search timed out; try a narrower query"}
    if not ids:
        return {"results": [], "note": "no matches"}

    qs = (
        Opinion.objects.filter(id__in=ids)
        .defer("raw_text", "html_content")
        .select_related("court", "court__state")
    )
    if year_from:
        qs = qs.filter(release_date__year__gte=int(year_from))
    if year_to:
        qs = qs.filter(release_date__year__lte=int(year_to))
    rows = list(qs.order_by("-release_date")[:limit])

    out = {"results": [_opinion_brief(o) for o in rows]}
    if capped:
        out["note"] = (
            "query matched 200+ opinions; results are a sample, not a "
            "ranking -- narrow the query for completeness"
        )
    return out


def tool_get_opinion(args: dict) -> dict:
    """Fetch one opinion by docket number within a state."""
    from opinions.views import _match_opinions, _pick_opinion

    docket = (args.get("docket") or "").strip()
    if not docket:
        raise ValueError("docket is required")
    state = _resolve_state(args.get("state") or "")
    if state is None:
        raise ValueError(
            f"unknown or missing state; use one of {_live_state_codes()}"
        )
    want_full = bool(args.get("full_text"))

    qs = (
        Opinion.objects.filter(court__state=state)
        .select_related("court", "court__state")
    )
    if not want_full:
        qs = qs.defer("raw_text", "html_content")
    matches = _match_opinions(qs, docket)
    if not matches:
        raise ValueError(f"no opinion found for docket {docket!r} in {state.code}")
    op = _pick_opinion(matches)

    out = _opinion_brief(op)
    out["holding"] = op.holding_summary or None
    if len(matches) > 1:
        out["siblings"] = [
            {
                "docket": m.case_number,
                "court": m.court.short_label,
                "date": str(m.release_date) if m.release_date else None,
            }
            for m in matches if m.pk != op.pk
        ]
        out["note"] = (
            "this docket has multiple decisions (e.g. COA + Supreme, or "
            "original + amended); the most authoritative/recent is shown"
        )
    if want_full:
        text = op.raw_text or ""
        out["full_text"] = text[:MAX_FULL_TEXT_CHARS]
        if len(text) > MAX_FULL_TEXT_CHARS:
            out["full_text_truncated"] = True
    return out


def tool_lookup_citation(args: dict) -> dict:
    """Resolve a pasted reporter cite or docket number to an opinion."""
    cite = " ".join((args.get("cite") or "").split())
    if not cite:
        raise ValueError("cite is required")

    # 1) Canonical reporter cite.
    op = (
        Opinion.objects.filter(reporter_cite__iexact=cite)
        .defer("raw_text", "html_content")
        .select_related("court", "court__state")
        .first()
    )
    if op:
        return {"matched_by": "reporter_cite", **_opinion_brief(op)}

    # 2) Parallel cites (official + regional forms both resolve).
    pc = (
        ParallelCite.objects.filter(cite__iexact=cite)
        .select_related("opinion", "opinion__court", "opinion__court__state")
        .first()
    )
    if pc:
        return {"matched_by": "parallel_cite", **_opinion_brief(pc.opinion)}

    # 3) Docket number, searched per live state (sequential indexed
    # lookups via _match_opinions -- see the IN-list gotcha).
    from opinions.views import _match_opinions, _pick_opinion
    hits = []
    for code in _live_state_codes():
        state = _resolve_state(code)
        qs = (
            Opinion.objects.filter(court__state=state)
            .defer("raw_text", "html_content")
            .select_related("court", "court__state")
        )
        matches = _match_opinions(qs, cite)
        if matches:
            hits.append(_opinion_brief(_pick_opinion(matches)))
    if len(hits) == 1:
        return {"matched_by": "docket", **hits[0]}
    if hits:
        return {"matched_by": "docket", "multiple_states": hits}
    raise ValueError(
        f"nothing found for {cite!r} -- tried reporter cites, parallel "
        f"cites, and docket numbers across {_live_state_codes()}"
    )


def tool_get_judge(args: dict) -> dict:
    """Judge dossier: role counts, courts, tenure, co-panelist heat."""
    from django.db.models import Count
    from opinions.views import _cohort_with_heat

    name = (args.get("judge") or "").strip()
    if not name:
        raise ValueError("judge is required (slug or name)")
    state = _resolve_state(args.get("state") or "")
    if state is None:
        raise ValueError(
            f"unknown or missing state; use one of {_live_state_codes()}"
        )

    judge = Judge.objects.filter(state=state, slug=name).first()
    if judge is None:
        cands = list(
            Judge.objects.filter(state=state, full_name__icontains=name)[:6]
        )
        if len(cands) == 1:
            judge = cands[0]
        elif cands:
            return {
                "ambiguous": [
                    {"slug": j.slug, "name": j.full_name} for j in cands
                ]
            }
        else:
            raise ValueError(f"no judge matching {name!r} in {state.code}")

    from opinions.models import PanelVote
    vote_counts = dict(
        PanelVote.objects.filter(judge=judge)
        .values_list("vote_type")
        .annotate(n=Count("id"))
        .values_list("vote_type", "n")
    )
    opinions_qs = Opinion.objects.filter(panel_votes__judge=judge).distinct()
    # Indexed ORDER BY ... LIMIT 1 for tenure bounds, per the
    # aggregate-Min/Max-under-court-filter gotcha.
    first = opinions_qs.order_by("release_date").values_list(
        "release_date", flat=True).first()
    last = opinions_qs.order_by("-release_date").values_list(
        "release_date", flat=True).first()

    return {
        "name": judge.full_name,
        "slug": judge.slug,
        "state": state.code,
        "court": judge.court.short_label if judge.court else None,
        "status": judge.get_status_display() if judge.status else None,
        "currently_seated": judge.is_currently_seated,
        "url": judge.get_absolute_url(),
        "active_span": {"first_opinion": str(first) if first else None,
                        "last_opinion": str(last) if last else None},
        "votes": {
            "authored_majority": vote_counts.get("MAJORITY_AUTHOR", 0),
            "joined_majority": vote_counts.get("MAJORITY_JOIN", 0),
            "authored_concurrence": vote_counts.get("CONCURRENCE_AUTHOR", 0),
            "authored_dissent": vote_counts.get("DISSENT_AUTHOR", 0),
        },
        "co_panelists": _cohort_with_heat(judge, top_n=8),
    }


def tool_citing_opinions(args: dict) -> dict:
    """Inbound citations to an opinion, with treatment + citing passages."""
    from django.db.models import Count
    from opinions.views import _match_opinions, _pick_opinion

    docket = (args.get("docket") or "").strip()
    if not docket:
        raise ValueError("docket is required")
    state = _resolve_state(args.get("state") or "")
    if state is None:
        raise ValueError(
            f"unknown or missing state; use one of {_live_state_codes()}"
        )
    limit = min(int(args.get("limit") or 15), MAX_LIST_LIMIT)

    qs = (
        Opinion.objects.filter(court__state=state)
        .defer("raw_text", "html_content").select_related("court")
    )
    matches = _match_opinions(qs, docket)
    if not matches:
        raise ValueError(f"no opinion found for docket {docket!r} in {state.code}")
    op = _pick_opinion(matches)

    edges = (
        OpinionCitation.objects.filter(cited_opinion=op)
        .select_related("citing_opinion", "citing_opinion__court",
                        "citing_opinion__court__state")
        .order_by("-citing_opinion__release_date")
    )
    treatment_mix = dict(
        OpinionCitation.objects.filter(cited_opinion=op)
        .values_list("treatment")
        .annotate(n=Count("id"))
        .values_list("treatment", "n")
    )
    rows = []
    for e in edges[:limit]:
        c = e.citing_opinion
        rows.append({
            "docket": c.case_number,
            "title": c.title,
            "court": c.court.short_label,
            "date": str(c.release_date) if c.release_date else None,
            "treatment": e.treatment,
            "citing_passage": (e.context_quote or "")[:MAX_QUOTE_CHARS] or None,
            "url": c.get_absolute_url(),
        })
    return {
        "cited_opinion": _opinion_brief(op),
        "total_citing": sum(treatment_mix.values()),
        "treatment_mix": treatment_mix,
        "citing": rows,
    }


def tool_get_statute(args: dict) -> dict:
    """Opinions citing a statute reference within a state."""
    reference = (args.get("reference") or "").strip().lower()
    if not reference:
        raise ValueError(
            "reference is required (normalized slug, e.g. 'minn.stat.609.185')"
        )
    state = _resolve_state(args.get("state") or "")
    if state is None:
        raise ValueError(
            f"unknown or missing state; use one of {_live_state_codes()}"
        )
    limit = min(int(args.get("limit") or 15), MAX_LIST_LIMIT)

    meta = (
        StatuteCitation.objects.filter(reference_slug=reference)
        .values("reference_display").first()
    )
    if not meta:
        raise ValueError(f"no citations found for statute {reference!r}")

    # Materialize distinct opinion ids first (index-only), then one
    # literal id__in fetch -- the statute_detail strategy.
    opinion_ids = list(
        StatuteCitation.objects.filter(reference_slug=reference)
        .order_by().values_list("opinion_id", flat=True).distinct()[:500]
    )
    court_ids = set(_court_ids(state))
    rows = list(
        Opinion.objects.filter(id__in=opinion_ids, court_id__in=court_ids)
        .defer("raw_text", "html_content")
        .select_related("court", "court__state")
        .order_by("-release_date")[:limit]
    )
    return {
        "statute": meta["reference_display"],
        "citing_opinion_count": len(opinion_ids),
        "opinions": [_opinion_brief(o) for o in rows],
    }


# --------------------------------------------------------------------------
# Tool registry + JSON Schemas
# --------------------------------------------------------------------------

_STATE_SCHEMA = {"type": "string", "description": "USPS state code (e.g. MN, NH, AZ)"}

TOOLS = [
    {
        "name": "search_opinions",
        "description": (
            "Keyword search over one state's appellate opinions. Returns "
            "docket numbers, titles, courts, dates, and canonical URLs. "
            "Results come from full-text matching; for a known citation "
            "use lookup_citation instead."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "search terms"},
                "state": _STATE_SCHEMA,
                "year_from": {"type": "integer"},
                "year_to": {"type": "integer"},
                "limit": {"type": "integer", "description": "max results (default 10, cap 50)"},
            },
            "required": ["query", "state"],
        },
        "fn": tool_search_opinions,
    },
    {
        "name": "get_opinion",
        "description": (
            "Fetch one opinion by docket number: metadata, disposition, the "
            "court's own verbatim holding sentence when available, and "
            "optionally the full opinion text. Text is the court's own "
            "words -- DocketDrift never generates or summarizes."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "docket": {"type": "string", "description": "docket number, e.g. A23-0373"},
                "state": _STATE_SCHEMA,
                "full_text": {"type": "boolean", "description": "include full opinion text (capped at 150K chars)"},
            },
            "required": ["docket", "state"],
        },
        "fn": tool_get_opinion,
    },
    {
        "name": "lookup_citation",
        "description": (
            "Resolve a pasted citation to an opinion. Accepts reporter "
            "cites in any known form ('425 N.W.2d 580', '221 Ariz. 236', "
            "'2026 N.H. 7') or a docket number. Searches all live states."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "cite": {"type": "string", "description": "reporter cite or docket number"},
            },
            "required": ["cite"],
        },
        "fn": tool_lookup_citation,
    },
    {
        "name": "get_judge",
        "description": (
            "Judge dossier: vote-role counts (authored/joined/dissented/"
            "concurred), courts, active span, and co-panelist alignment "
            "(aligned / partial / split counts per frequent co-panelist)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "judge": {"type": "string", "description": "judge slug or (partial) name"},
                "state": _STATE_SCHEMA,
            },
            "required": ["judge", "state"],
        },
        "fn": tool_get_judge,
    },
    {
        "name": "citing_opinions",
        "description": (
            "How later opinions treat a given opinion (DocketDrift's "
            "KeyCite/Shepard's layer): inbound citations with treatment "
            "classification (followed / distinguished / criticized / "
            "overruled / cited) and the verbatim citing passage."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "docket": {"type": "string"},
                "state": _STATE_SCHEMA,
                "limit": {"type": "integer"},
            },
            "required": ["docket", "state"],
        },
        "fn": tool_citing_opinions,
    },
    {
        "name": "get_statute",
        "description": (
            "Opinions citing a statute. Reference is the normalized slug "
            "form: 'minn.stat.609.185', 'rsa.265.79' (NH), 'ars.13-1105' "
            "(AZ). Returns the citing-opinion count and the most recent "
            "citing opinions."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "reference": {"type": "string"},
                "state": _STATE_SCHEMA,
                "limit": {"type": "integer"},
            },
            "required": ["reference", "state"],
        },
        "fn": tool_get_statute,
    },
]

_TOOL_BY_NAME = {t["name"]: t for t in TOOLS}


# --------------------------------------------------------------------------
# JSON-RPC plumbing
# --------------------------------------------------------------------------

def _rpc_result(msg_id, result: dict) -> JsonResponse:
    return JsonResponse({"jsonrpc": "2.0", "id": msg_id, "result": result})


def _rpc_error(msg_id, code: int, message: str, status: int = 200) -> JsonResponse:
    return JsonResponse(
        {"jsonrpc": "2.0", "id": msg_id,
         "error": {"code": code, "message": message}},
        status=status,
    )


@csrf_exempt
def mcp_endpoint(request):
    """Stateless Streamable-HTTP MCP endpoint (POST-only, JSON responses)."""
    if request.method != "POST":
        resp = HttpResponse(status=405)
        resp["Allow"] = "POST"
        return resp

    try:
        msg = json.loads(request.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return _rpc_error(None, -32700, "parse error", status=400)

    if isinstance(msg, list):
        # Batching was removed in protocol 2025-06-18; keep the server simple.
        return _rpc_error(None, -32600, "batch requests not supported", status=400)
    if not isinstance(msg, dict):
        return _rpc_error(None, -32600, "invalid request", status=400)

    method = msg.get("method")
    msg_id = msg.get("id")

    # Notifications (no id) are acknowledged and dropped.
    if msg_id is None:
        return HttpResponse(status=202)

    if method == "initialize":
        return _rpc_result(msg_id, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
            "instructions": (
                "DocketDrift serves verbatim U.S. state appellate opinion "
                "text and metadata. Nothing is generated or summarized; "
                "'holding' fields quote the court's own sentence. Cite "
                "opinions by docket number + court + date, and link the "
                "canonical URL returned by each tool."
            ),
        })

    if method == "ping":
        return _rpc_result(msg_id, {})

    if method == "tools/list":
        return _rpc_result(msg_id, {
            "tools": [
                {k: t[k] for k in ("name", "description", "inputSchema")}
                for t in TOOLS
            ]
        })

    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        tool = _TOOL_BY_NAME.get(name)
        if tool is None:
            return _rpc_error(msg_id, -32602, f"unknown tool: {name}")
        args = params.get("arguments") or {}
        throttled = name in _THROTTLED_TOOLS
        if throttled and not _SEARCH_SLOTS.acquire(blocking=False):
            # Shed rather than queue: a queued search still pins a worker
            # thread, which is the exact resource we are protecting.
            return _rpc_result(msg_id, {
                "content": [{"type": "text", "text": (
                    "search is busy right now -- retry in a few seconds, or "
                    "send a narrower query (a broad common-law term scans "
                    "the whole corpus). Other tools are unaffected."
                )}],
                "isError": True,
            })
        try:
            result = tool["fn"](args)
        except ValueError as exc:
            return _rpc_result(msg_id, {
                "content": [{"type": "text", "text": str(exc)}],
                "isError": True,
            })
        except Exception:
            # Never leak internals; the site-wide posture is degrade, not 500.
            return _rpc_result(msg_id, {
                "content": [{"type": "text",
                             "text": "internal error; try a narrower request"}],
                "isError": True,
            })
        finally:
            if throttled:
                _SEARCH_SLOTS.release()
        return _rpc_result(msg_id, {
            "content": [{
                "type": "text",
                "text": json.dumps(result, ensure_ascii=False, indent=1),
            }],
        })

    return _rpc_error(msg_id, -32601, f"method not found: {method}")
