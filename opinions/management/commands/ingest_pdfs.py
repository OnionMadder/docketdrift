"""Bulk-ingest court opinion PDFs from a directory.

For each PDF in ``--dir``:

  1. Extract text via ``pypdf``.
  2. Run the state's registered parser (``opinions.parsing.parse``) to
     pull out case_number, case_name, release_date, disposition,
     is_precedential, and a confidence dict.
  3. Compute the raw_text SHA-256 for dedup.
  4. Look up the target Court row via ``--state`` + ``--court``.
  5. If an Opinion with that (court, case_number) already exists, skip
     by default (or overwrite when ``--update`` is on).
  6. Otherwise create a new Opinion row. When ``--keep-pdf`` is on
     (default), attach the source PDF as ``Opinion.pdf_file`` so the
     scan is downloadable from the public detail page.

Designed for cases where CourtListener's bulk dump or REST API don't
yet have a state's recent opinions. NH 2026 was the first use case
(CourtListener lag, and ``courts.nh.gov`` Akamai-blocks server-side
scraping), so the operator drops the official PDFs into a folder and
this command does the rest.

Idempotent: re-running over the same directory is a no-op once
(court, case_number) rows exist; pass ``--update`` to overwrite
parser-derived fields without losing existing review state.

Usage::

    python manage.py ingest_pdfs \\
        --dir ~/incoming-pdfs/nh-2026 \\
        --state NH \\
        --court supreme

    # Preview only, no DB writes:
    python manage.py ingest_pdfs --dir ~/incoming --state NH --dry-run

    # Force overwrite (e.g. parser fixes since last run):
    python manage.py ingest_pdfs --dir ~/incoming --state NH --update

Cost: regex + pypdf, no API calls. ~1-3 sec per PDF on dev hardware.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from django.core.files import File
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from opinions.models import Court, Opinion, State
from opinions.parsing import parse as parse_opinion


def _extract_pdf_text(path: Path) -> str:
    """Pull text from every page of a PDF, concatenated with newlines."""
    # pypdf is the same dep used by ``Opinion.save()`` for admin-uploaded
    # PDFs; importing here keeps the module load light when the command
    # isn't being run.
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return "\n".join((p.extract_text() or "") for p in reader.pages)


class Command(BaseCommand):
    help = "Ingest a directory of opinion PDFs into the corpus."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dir", required=True,
            help="Directory containing the PDF files to ingest.",
        )
        parser.add_argument(
            "--state", required=True,
            help="State code (USPS 2-letter, e.g. NH).",
        )
        parser.add_argument(
            "--court", default="supreme",
            help="Court slug within the state (default: 'supreme').",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Show what would be ingested without writing to the DB.",
        )
        parser.add_argument(
            "--update", action="store_true",
            help=(
                "Overwrite parser-derived fields on existing "
                "(court, case_number) rows. Preserves review_status, "
                "reviewed_by, reviewed_at, review_notes, and tags."
            ),
        )
        parser.add_argument(
            "--no-pdf", action="store_true",
            help=(
                "Skip copying the source PDF into media storage. The "
                "Opinion row still gets raw_text + parsed fields but "
                "won't have a downloadable PDF link."
            ),
        )

    def handle(
        self, *args,
        dir, state, court, dry_run, update, no_pdf,
        **options,
    ):
        state_code = state.upper()
        try:
            state_obj = State.objects.get(code=state_code)
        except State.DoesNotExist:
            raise CommandError(f"State {state_code!r} not found.")
        try:
            court_obj = Court.objects.get(state=state_obj, slug=court)
        except Court.DoesNotExist:
            raise CommandError(
                f"Court {court!r} not found in state {state_code!r}."
            )

        src = Path(dir).expanduser().resolve()
        if not src.is_dir():
            raise CommandError(f"Not a directory: {src}")

        pdfs = sorted(src.glob("*.pdf"))
        self.stdout.write(self.style.SUCCESS(
            f"Found {len(pdfs)} PDFs in {src}"
            f"  ->  {state_code} / {court_obj.name}"
            + ("  [DRY RUN]" if dry_run else "")
        ))
        if not pdfs:
            return

        created = updated = skipped = errored = 0
        for pdf in pdfs:
            label = f"  {pdf.name}"

            # Extract text
            try:
                raw_text = _extract_pdf_text(pdf)
            except Exception as exc:
                self.stdout.write(f"{label}  ERROR (PDF read): {exc}")
                errored += 1
                continue
            if not raw_text.strip():
                self.stdout.write(f"{label}  ERROR: empty text extraction")
                errored += 1
                continue

            # Parse
            parsed = parse_opinion(state_code, raw_text)
            if parsed is None:
                self.stdout.write(
                    f"{label}  ERROR: no parser registered for {state_code}"
                )
                errored += 1
                continue
            if not parsed.case_number:
                self.stdout.write(f"{label}  ERROR: parser found no case_number")
                errored += 1
                continue
            if not parsed.release_date:
                self.stdout.write(f"{label}  ERROR: parser found no release_date")
                errored += 1
                continue

            sha = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()

            # Dedup
            existing = Opinion.objects.filter(
                court=court_obj, case_number=parsed.case_number,
            ).first()

            if existing and not update:
                self.stdout.write(
                    f"{label}  SKIP   {parsed.case_number}"
                    f"  (already in DB as #{existing.pk})"
                )
                skipped += 1
                continue

            verb = "UPDATE" if existing else "CREATE"
            summary = (
                f"{label}  {verb}  {parsed.case_number}"
                f"  {parsed.release_date}"
                f"  {parsed.disposition or '?'}"
            )
            if parsed.case_name:
                summary += f"  | {parsed.case_name[:60]}"
            if dry_run:
                self.stdout.write(summary + "  [DRY RUN]")
                continue
            self.stdout.write(summary)

            # Write
            try:
                with transaction.atomic():
                    if existing:
                        op = existing
                        op.raw_text = raw_text
                        op.sha256 = sha
                        if parsed.release_date:
                            op.release_date = parsed.release_date
                        if parsed.disposition:
                            op.disposition = parsed.disposition
                        if parsed.case_name:
                            op.title = parsed.case_name
                        if parsed.is_precedential is not None:
                            op.is_precedential = parsed.is_precedential
                        op.save()
                        updated += 1
                    else:
                        op = Opinion(
                            court=court_obj,
                            case_number=parsed.case_number,
                            title=parsed.case_name or "",
                            release_date=parsed.release_date,
                            raw_text=raw_text,
                            sha256=sha,
                            disposition=parsed.disposition or "",
                            is_precedential=(
                                parsed.is_precedential
                                if parsed.is_precedential is not None
                                else True
                            ),
                        )
                        op.save()
                        created += 1

                    # Attach PDF to media storage. We do this AFTER the
                    # first save() so release_date is set -- it's used
                    # by _opinion_pdf_upload_path to derive the
                    # opinions/<state>/<year>/ tree.
                    if not no_pdf:
                        with pdf.open("rb") as fh:
                            op.pdf_file.save(pdf.name, File(fh), save=True)
            except Exception as exc:
                self.stdout.write(f"    -> WRITE FAILED: {exc}")
                errored += 1

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"Done.  created={created}  updated={updated}"
            f"  skipped={skipped}  errored={errored}"
        ))
