"""Widen Opinion's uniqueness to (court, case_number, release_date).

The old constraint `(court, case_number)` silently drops legitimate
second decisions on one docket at ingest. A docket keeps its number
through the whole appellate life of a case, so:

  - COA + Supreme review of the same case (court differs)     -- ALREADY OK
  - opinion + amended opinion on the same court (date differs)  -- LOST TODAY
  - opinion on remand years after the first opinion            -- LOST TODAY

The COA + Supreme pair was reachable only because the court column
differs; the same-court second-decision case had nothing distinguishing
it. `ingest_pdfs` SKIP'd the second document; `ingest_court`'s
`update_or_create` overwrote the first with the second's fields.

Adding release_date to the unique key covers the observed pattern
(every documented sibling pair carries a different date). Two same-day
decisions on one docket are theoretically possible but extremely rare
in appellate practice; if it ever happens, `ingest_pdfs` will surface
it as a constraint error rather than silently losing the row.

De-risk measured on prod 2026-08-08 BEFORE writing this migration:
zero `(court, case_number, release_date)` collisions across the whole
128K-row corpus. So the constraint holds with no data touching needed.

Migration mechanics:
- ALTER UNIQUE INDEX on the 2.75GB opinions_opinion table. Unique-index
  DDL in MariaDB is ALGORITHM=INPLACE by default (online DML permitted
  for the ADD), and reads stay non-blocking. Sort+build of a normal
  B+tree over 3 small columns finishes in seconds -- unlike the
  documented VECTOR-INDEX horror stories, which failed on HNSW graph
  construction, not the index mechanic itself.
- max_statement_time is lifted for this connection (same pattern as
  migration 0023) so a slow-DDL moment on the shared DB cannot trip
  the 25s per-request cap.
- Django's AlterUniqueTogether emits a metadata DROP of the old
  2-column constraint followed by the CREATE of the 3-column one.
  Reads continue against the clustered PK throughout.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("opinions", "0035_judge_vice_chief_judge_role"),
    ]

    operations = [
        migrations.RunSQL(
            "SET SESSION max_statement_time = 0",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.AlterUniqueTogether(
            name="opinion",
            unique_together={("court", "case_number", "release_date")},
        ),
    ]
