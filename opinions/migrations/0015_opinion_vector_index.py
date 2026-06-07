"""HNSW vector index attempt -- no-op, kept as a migration slot.

MariaDB 11.7 requires every column in a VECTOR index to be ``NOT NULL``
(error 1252: "All parts of a VECTOR index must be NOT NULL"). Our
``Opinion.embedding`` is legitimately nullable -- ~5% of opinions have
no ``raw_text`` and therefore no embedding -- so we can't add the
index without either backfilling those NULLs with a zero-vector
sentinel (which would pollute similarity results) or denormalizing
embeddings into a separate non-null table.

For now we run ``VEC_DISTANCE_COSINE`` without an index. At 60K rows
* 1024-dim vectors, a full scan completes in ~30-80ms -- acceptable
for the search latency budget. Revisit (denormalize or backfill) when
the corpus crosses ~500K opinions or when search becomes the
bottleneck. The migration slot is preserved so future schema work
remains in order.
"""
from django.db import migrations


def noop(apps, schema_editor):
    return


class Migration(migrations.Migration):

    dependencies = [
        ("opinions", "0014_add_state_request"),
    ]

    operations = [
        migrations.RunPython(noop, noop),
    ]
