"""Shared paginator classes.

Django's stock ``Paginator`` calls ``self.object_list.count()`` for its
``count`` property. When the underlying queryset has been decorated with
``.select_related(...)`` for the change/list rendering pass, the count
query inherits those JOINs even though they're useless for a COUNT.

On the DocketDrift corpus that turned a ~1ms indexed COUNT into a 30+s
multi-table join COUNT that saturated gunicorn's worker threads under
embed contention. Both the admin opinion changelist and the public
``/opinions/?q=...`` search hit the same wall.

The fix is one line: ``.values("pk")`` before counting strips the
select_related joins from the SQL Django emits, leaving a simple
single-table COUNT against the indexed primary key.

Used by:
- ``opinions.admin.OpinionAdmin`` (admin changelist pagination)
- ``opinions.views.opinion_list`` (public search results pagination)
"""
from django.core.paginator import Paginator
from django.db import connection
from django.utils.functional import cached_property

# Seconds the COUNT gets before we stop waiting. Deliberately well under
# settings.py's 25s session cap: a KILLed statement poisons the pooled
# connection and cascades 500s onto unrelated pages, so the count must fail
# on OUR terms, early, not MariaDB's.
COUNT_TIMEOUT_S = 8

# When the exact count times out, count this many rows instead. The page
# still paginates and still renders; it just reports "N+" rather than a
# precise total.
COUNT_CAP = 5000


class NoJoinCountPaginator(Paginator):
    """Paginator whose ``.count`` doesn't carry select_related joins or
    ORDER BY into the COUNT SQL.

    Django's ``QuerySet.count()`` clones the underlying Query and adds
    a COUNT aggregate -- but on some versions / queryset shapes the
    clone preserves both the ``.select_related`` JOINs and the
    ``ORDER BY`` clause even though neither affects the count. On the
    DocketDrift corpus that turned a fast indexed COUNT into a
    3-table-join COUNT-with-sort that ran 30+ seconds and saturated
    gunicorn's threads.

    The fix is to explicitly clear both before counting. ``.values("pk")``
    alone isn't enough -- it strips the SELECT field list but Django
    can keep the JOIN if the queryset's Query object was built with
    select_related state. ``select_related(None)`` is the explicit reset,
    and ``order_by()`` strips the ORDER BY so the count doesn't pointlessly
    sort before aggregating.

    Filter clauses (including raw ``.extra()`` SQL like FULLTEXT MATCH())
    are preserved, so filtered counts stay accurate.
    """

    #: True when ``count`` gave up and returned the capped figure instead of
    #: an exact total. Templates should render "N+" rather than "N" -- a
    #: number we know is wrong must not be shown as if it were right.
    count_is_capped = False

    @cached_property
    def count(self):
        """Exact count when it is affordable, a capped one when it is not.

        Stripping the joins is no longer enough. A filtered count can be
        slow for a reason `.values("pk")` cannot fix -- there is no
        composite index for the filter. Measured 2026-09-14:
        `court_id IN (LA) AND disposition_bucket='affirmed'` counts 70,708
        rows in **66s**, and the MN equivalent in 28s, because a
        single-column `disposition_bucket` index beside a `court_id` filter
        degenerates into a clustered walk of the 2.75GB table (the
        documented "one non-covered column beside a court_id filter"
        gotcha). Both blew the 25s cap and **500'd `/opinions/` on 14% of
        its requests** until this bound was added.

        So: bound it, and degrade rather than die. A capped count keeps the
        page rendering and keeps the failure OURS -- a MariaDB KILL at the
        session cap poisons the pooled connection and takes unrelated pages
        down with it.

        The real fix is a composite `(court_id, disposition_bucket)` index,
        which is a deliberate big-table migration; this is the guard that
        should stay regardless, because the next filter without an index
        will land here too.
        """
        cleaner = self.object_list.select_related(None).order_by().values("pk")

        if connection.vendor != "mysql":
            return cleaner.count()

        try:
            with connection.cursor() as cur:
                cur.execute("SET SESSION max_statement_time = %s",
                            [COUNT_TIMEOUT_S])
            try:
                n = cleaner.count()
            finally:
                with connection.cursor() as cur:
                    cur.execute("SET SESSION max_statement_time = 25")
            return n
        except BaseException:
            # Bare BaseException, not Exception: the KILL often lands during
            # fetch and surfaces as a raw pymysql error that is not a
            # DatabaseError subclass (same reason semantic.py catches wide).
            # Drop the connection -- a fresh one re-applies the 25s cap from
            # init_command.
            connection.close()

        # Capped fallback: COUNT over a LIMITed subquery, which stops early.
        try:
            n = cleaner[:COUNT_CAP].count()
        except BaseException:
            connection.close()
            n = 0
        self.count_is_capped = True
        return n
