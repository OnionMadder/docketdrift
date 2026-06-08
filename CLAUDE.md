# DocketDrift — notes for Claude sessions

Concise survival kit for any Claude session working on this repo. Read once,
re-read whenever a recurring gotcha bites.

## The repo and its shape

- Django 5.2 + PyMySQL + MariaDB 11.7 on NFSN. Local dev defaults to SQLite.
- Frontend = Django templates + minimal JS + HTMX where it earns its keep.
  No SPA, no build pipeline.
- The state subdomain (`mn.docketdrift.com`, `nh.docketdrift.com`, ...) is
  resolved to a `State` row by `opinions/middleware.py` and attached as
  `request.state`. Apex (`docketdrift.com`) has `request.state = None`.
- Per-state landing template is `opinions/templates/opinions/state_landing.html`,
  rendered by `home()` when `request.state` is set.
- Production deploys via SSH push + `nfsn signal-daemon gunicorn TERM`. Onion
  prefers I drive that loop myself when possible (SSH config is set up).

## Recurring gotchas — DO NOT MAKE THESE AGAIN

### Django template comments are single-line only

`{# this is fine #}` works **only on a single line**. A multi-line `{# ... #}`
block renders as raw page text — Onion has caught this leaking into the
hero of the apex more than once. **Always** use `{% comment %} ... {% endcomment %}`
for any explanatory block longer than a single line. Single-line `{# #}` for
inline annotations is fine.

```django
{# good: one-liner annotation #}
{% comment %}
good: multi-line block,
spanning several lines,
WILL NOT render to the user.
{% endcomment %}

{# bad: multi-line {# #} block --
   THIS RENDERS AS TEXT
   and Onion sees it in the page. #}
```

### Don't use nested f-strings with bracket-indexed lookups

`f"{row['n']}"` is fine in Python 3.12+ but **NFSN runs Python 3.11**, where
`{row["n"]}` inside an f-string is a `SyntaxError: f-string: unmatched '['`.
Use `%`-formatting or `.format()` for dict-key lookups when writing scripts
that will run on production:

```python
# bad (3.11 SyntaxError):
print(f"{row['count']:>5}  {row['slug']}")
# good:
print("%5d  %s" % (row["count"], row["slug"]))
```

### NumPy on NFSN's FreeBSD is broken

Both numpy 2.x and 1.x ship FreeBSD wheels missing `cblas_sdot` from the
system BLAS — `import numpy` fails at runtime. Do **not** add numpy as a
dependency. The cosine math in `suggest_tags` uses MariaDB's native
`VEC_DISTANCE_COSINE` instead; the same primitive powers semantic search.

If a new task wants matrix math, lean on MariaDB VECTOR + raw SQL, or pull
small enough datasets that pure-Python loops are fine.

### Court.short_label is a Python @property

It is NOT a database column. You CANNOT `.values("court__short_label")` or
`.annotate(...)` against it. To group by court display label, group by
`court_id` first, then resolve to Court instances in Python:

```python
rows = list(qs.values("court_id").annotate(n=Count("id")))
courts_map = {c.id: c for c in Court.objects.filter(id__in=[r["court_id"] for r in rows])}
breakdown = [{"court": courts_map[r["court_id"]], "n": r["n"]} for r in rows]
```

### MariaDB drops idle connections during long sleeps

Any management command that does a 30-60s `time.sleep()` between DB writes
(e.g. waiting for CL rate limit, waiting for a Voyage API call) needs
retry-with-reconnect on the write side. `embed_opinions` and `ingest_court`
have this pattern; copy it when adding a new long-running command. The error
to catch is `OperationalError (2013, "Lost connection to MySQL server during
query")`. Bare `BaseException` is intentional — NFSN's SSL socket raises
`KeyboardInterrupt` on EINTR during long sleeps, which would otherwise abort
the run.

### Statute / opinion detail queries: defer `raw_text` + `html_content`

`Opinion.raw_text` and `Opinion.html_content` are TEXT columns holding 50-100KB
each. Any list-style query (statute_detail, judge_detail, tag_detail, ...)
must `.defer("raw_text", "html_content")` or pulling 50 rows for a page
quickly blows past gunicorn's timeout. Only the opinion_detail page wants
`raw_text`.

### StatuteCitation default ordering bleeds into `.distinct()`

`StatuteCitation.Meta.ordering = ["opinion", "text_offset"]` silently joins
back to Opinion when used in a `.values_list().distinct()` chain. Always
chain an explicit empty `.order_by()` before `.values_list()` on
StatuteCitation when you want an index-only scan:

```python
StatuteCitation.objects.filter(...).order_by().values_list("opinion_id", flat=True).distinct()
```

### NFSN proxy cache can serve stale 503 for minutes

After fixing a slow/broken endpoint and restarting gunicorn, the public URL
may still return cached 503 for a few minutes. Bypass with the internal
gunicorn address from `/home/logs/daemon_gunicorn.log` (`Listening at: ...`)
to confirm the fix is real:

```bash
ssh docketdrift 'curl -sS -H "Host: mn.docketdrift.com" http://10.0.175.75:8000/some/path/'
```

If the direct-gunicorn hit returns 200 but the public URL still 503s, the fix
is live — the edge cache will clear within ~5 min.

## Deployment cheat sheet

```bash
# Pull + restart (picks up new code + settings + middleware)
ssh docketdrift 'cd /home/private/docketdrift && git pull && nfsn -j signal-daemon gunicorn TERM'

# Pull + migrate + restart (when there's a new migration)
ssh docketdrift 'cd /home/private/docketdrift && git pull && source .venv/bin/activate && python manage.py migrate && nfsn -j signal-daemon gunicorn TERM'

# Pull + collectstatic + restart (when static files changed)
ssh docketdrift 'cd /home/private/docketdrift && git pull && source .venv/bin/activate && python manage.py collectstatic --noinput && nfsn -j signal-daemon gunicorn TERM'

# Tail the daemon log
ssh docketdrift 'tail -f /home/logs/daemon_gunicorn.log'
```

## When to ASK

- Adding NEW dependencies (numpy, scipy, pandas, etc.) — flag the FreeBSD risk
  before installing.
- Touching settings.py middleware order or DB config — restart is required;
  confirm the timing matches what's in flight.
- Spending money: any LLM-extraction (Claude, GPT) batch job — show the cost
  estimate before kicking off.

## When NOT to ask

- Running idempotent management commands again — safe by design.
- Fixing template comment bleed (this gotcha above) — just fix it.
- Restarting gunicorn after a deploy — that's expected.
