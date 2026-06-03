"""
Multi-tenant judicial-opinion schema for DocketDrift.

State is the tenant. Every Opinion belongs to a Court, every Court belongs
to a State; a Judge is scoped to a State. The incoming subdomain
(e.g. `mn.docketdrift.com`) is resolved to a State by
``opinions.middleware.StateRouterMiddleware`` and attached as
``request.state``, which views use to filter every query.

Embedding storage: ``OpinionHolding.embedding`` is a JSONField for now so the
schema is portable between SQLite (dev) and MariaDB. When we deploy to MariaDB
11.7+, we migrate that column to a native VECTOR type for indexed similarity.
Until then similarity is computed in Python on demand.
"""
from django.db import models


class State(models.Model):
    """A US state we cover. USPS 2-letter code is the primary key."""

    code = models.CharField(
        max_length=2,
        primary_key=True,
        help_text="USPS 2-letter code, uppercase. e.g. 'MN'.",
    )
    name = models.CharField(max_length=64, unique=True)
    slug = models.SlugField(
        max_length=64,
        unique=True,
        help_text="Lowercase subdomain slug; matches code.",
    )
    is_live = models.BooleanField(
        default=False,
        help_text="True once this state's subdomain is advertised on the apex picker.",
    )

    class Meta:
        ordering = ["code"]

    def __str__(self):
        return f"{self.code} - {self.name}"


class Court(models.Model):
    """An appellate court within a state."""

    class Level(models.TextChoices):
        SUPREME = "SUPREME", "Supreme Court"
        APPEALS = "APPEALS", "Court of Appeals"

    state = models.ForeignKey(State, on_delete=models.PROTECT, related_name="courts")
    level = models.CharField(max_length=16, choices=Level.choices)
    name = models.CharField(
        max_length=128,
        help_text="Display name, e.g. 'Minnesota Supreme Court'.",
    )
    slug = models.SlugField(max_length=64)
    courtlistener_id = models.CharField(
        max_length=32,
        unique=True,
        help_text="CourtListener court identifier (e.g. 'minn', 'minnctapp').",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("state", "level")]
        ordering = ["state__code", "level"]

    def __str__(self):
        return self.name


class Judge(models.Model):
    """A judge. Scoped to a state; appears on opinions from that state's courts."""

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        RETIRED = "RETIRED", "Retired"
        SENIOR = "SENIOR", "Senior"
        UNKNOWN = "UNKNOWN", "Unknown"

    state = models.ForeignKey(State, on_delete=models.PROTECT, related_name="judges")
    full_name = models.CharField(max_length=128)
    slug = models.SlugField(max_length=128)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.UNKNOWN,
    )
    courtlistener_id = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="CourtListener person identifier, when known.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("state", "slug")]
        ordering = ["full_name"]

    def __str__(self):
        return f"{self.full_name} ({self.state_id})"


def _opinion_pdf_upload_path(instance, filename):
    """Where uploaded opinion PDFs live under MEDIA_ROOT.

    Lays them out as ``opinions/<state>/<year>/<basename>`` so a glance at the
    media tree mirrors the editorial taxonomy. ``instance.court`` may not yet
    be saved when ``upload_to`` runs on a fresh row, so we guard fk access.
    """
    state_code = "unk"
    year = "unsorted"
    try:
        if instance.court_id and instance.court.state_id:
            state_code = (instance.court.state_id or "unk").lower()
    except Exception:
        pass
    try:
        if instance.release_date:
            year = str(instance.release_date.year)
    except Exception:
        pass
    return f"opinions/{state_code}/{year}/{filename}"


class Opinion(models.Model):
    """A published appellate opinion."""

    court = models.ForeignKey(Court, on_delete=models.PROTECT, related_name="opinions")
    case_number = models.CharField(
        max_length=64,
        help_text="Docket number as published, e.g. 'A23-0123'.",
    )
    title = models.TextField(help_text="Case caption / title.")
    release_date = models.DateField(db_index=True)
    is_precedential = models.BooleanField(default=True)
    raw_text = models.TextField(blank=True, default="")
    html_content = models.TextField(blank=True, default="")
    source_url = models.URLField(max_length=512, blank=True, default="")
    courtlistener_id = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_index=True,
    )
    sha256 = models.CharField(
        max_length=64,
        blank=True,
        default="",
        help_text="Hash of the raw text, for dedupe.",
    )
    pdf_file = models.FileField(
        upload_to=_opinion_pdf_upload_path,
        blank=True,
        null=True,
        help_text=(
            "Optional uploaded PDF. If provided and raw_text is empty, the "
            "text is extracted on save (via pypdf) and the sha256 computed. "
            "Re-uploading without clearing raw_text won't re-extract."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [("court", "case_number")]
        ordering = ["-release_date"]
        indexes = [
            models.Index(fields=["-release_date"]),
        ]

    def __str__(self):
        return f"{self.case_number}: {self.title[:60]}"

    def extract_text_from_pdf(self) -> str:
        """Return concatenated plain text from the attached PDF, or ''.

        Reads the bytes into a ``BytesIO`` so we never close the underlying
        UploadedFile handle -- if we did (e.g. via ``with self.pdf_file.open():``),
        Django's subsequent ``FieldFile.save()`` inside ``pre_save`` would
        ``seek(0)`` on a closed file and raise ``ValueError: I/O operation on
        closed file``, blowing up the whole admin save. We rewind the source
        handle afterward so storage.save() reads from byte zero. Defensive
        about pypdf failures (encrypted PDFs, malformed pages, etc.) -- a
        ``return ""`` is the caller's signal that extraction didn't work.
        """
        if not self.pdf_file:
            return ""
        try:
            import pypdf  # local import keeps pypdf off the cold-start path
            from io import BytesIO

            f = self.pdf_file
            try:
                f.seek(0)
            except Exception:
                pass
            data = f.read()
            try:
                f.seek(0)
            except Exception:
                pass

            reader = pypdf.PdfReader(BytesIO(data))
            chunks = []
            for page in reader.pages:
                text = (page.extract_text() or "").strip()
                if text:
                    chunks.append(text)
            return "\n\n".join(chunks)
        except Exception:
            return ""

    def save(self, *args, **kwargs):
        # Extract text from a freshly-uploaded PDF the first time we see one
        # without an existing raw_text. Re-uploads don't re-extract unless the
        # caller clears raw_text first -- that's the "trust the user" escape
        # hatch if the first extraction came out garbled.
        if self.pdf_file and not self.raw_text:
            extracted = self.extract_text_from_pdf()
            if extracted:
                import hashlib
                self.raw_text = extracted
                if not self.sha256:
                    self.sha256 = hashlib.sha256(
                        extracted.encode("utf-8")
                    ).hexdigest()
        super().save(*args, **kwargs)


class PanelVote(models.Model):
    """How a single judge participated in a single opinion."""

    class Vote(models.TextChoices):
        MAJORITY_AUTHOR = "MAJORITY_AUTHOR", "Majority author"
        MAJORITY_JOIN = "MAJORITY_JOIN", "Joined majority"
        CONCURRENCE_AUTHOR = "CONCURRENCE_AUTHOR", "Concurrence author"
        CONCURRENCE_JOIN = "CONCURRENCE_JOIN", "Joined concurrence"
        DISSENT_AUTHOR = "DISSENT_AUTHOR", "Dissent author"
        DISSENT_JOIN = "DISSENT_JOIN", "Joined dissent"
        RECUSED = "RECUSED", "Recused"
        NOT_PARTICIPATING = "NOT_PARTICIPATING", "Did not participate"

    opinion = models.ForeignKey(Opinion, on_delete=models.CASCADE, related_name="panel_votes")
    judge = models.ForeignKey(Judge, on_delete=models.PROTECT, related_name="panel_votes")
    vote_type = models.CharField(max_length=24, choices=Vote.choices)

    class Meta:
        unique_together = [("opinion", "judge")]
        indexes = [
            models.Index(fields=["judge", "vote_type"]),
        ]

    def __str__(self):
        return f"{self.judge} -> {self.get_vote_type_display()} on opinion {self.opinion_id}"


class OpinionHolding(models.Model):
    """An extracted legal holding within an opinion."""

    class Direction(models.TextChoices):
        AFFIRMED = "AFFIRMED", "Affirmed"
        REVERSED = "REVERSED", "Reversed"
        REMANDED = "REMANDED", "Remanded"
        VACATED = "VACATED", "Vacated"
        SUPPRESSED = "SUPPRESSED", "Suppressed"
        ADMITTED = "ADMITTED", "Admitted"
        GRANTED = "GRANTED", "Granted"
        DENIED = "DENIED", "Denied"
        OTHER = "OTHER", "Other"

    opinion = models.ForeignKey(Opinion, on_delete=models.CASCADE, related_name="holdings")
    statute_cited = models.CharField(
        max_length=128,
        blank=True,
        default="",
        db_index=True,
        help_text="Normalized statute reference, e.g. 'Minn. Stat. 609.185'.",
    )
    legal_issue_tag = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text="Free-form tag for the legal issue addressed.",
    )
    holding_direction = models.CharField(
        max_length=16,
        choices=Direction.choices,
        default=Direction.OTHER,
    )
    holding_text = models.TextField()
    embedding = models.JSONField(
        null=True,
        blank=True,
        help_text=(
            "Sentence embedding for similarity search; null until generated. "
            "Migrate to MariaDB VECTOR column for indexed lookups in prod."
        ),
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["opinion", "id"]

    def __str__(self):
        return f"Holding on opinion {self.opinion_id}: {self.holding_text[:60]}"
