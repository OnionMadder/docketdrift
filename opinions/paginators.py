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
from django.utils.functional import cached_property


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

    @cached_property
    def count(self):
        cleaner = self.object_list.select_related(None).order_by()
        return cleaner.values("pk").count()
