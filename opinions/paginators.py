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
    """Paginator whose ``.count`` doesn't carry select_related joins.

    ``.values("pk").count()`` discards the JOINs that ``.select_related``
    added for rendering but keeps every ``WHERE`` clause -- including
    raw ``.extra()`` SQL like FULLTEXT MATCH() -- so filtered counts
    stay accurate.
    """

    @cached_property
    def count(self):
        return self.object_list.values("pk").count()
