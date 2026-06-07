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

    @property
    def short_label(self) -> str:
        """Compact label for the doc-table court pill (e.g. 'Minn. Ct. App.').

        New states are added here when they go live -- the per-state map
        beats trying to derive abbreviations algorithmically (Tex. vs Tx.,
        Cal. vs Calif. -- legal Bluebook convention varies). Fall through
        to ``self.name`` for any unmapped court so nothing breaks if a row
        ships before its label is curated.
        """
        if self.state_id == "MN":
            if self.level == self.Level.SUPREME:
                return "Minn."
            if self.level == self.Level.APPEALS:
                return "Minn. Ct. App."
        if self.state_id == "NH":
            if self.level == self.Level.SUPREME:
                return "N.H."
        return self.name

    @property
    def level_slug(self) -> str:
        """Lowercase level for use in CSS modifier classes (court-pill--supreme)."""
        return (self.level or "").lower()


class Judge(models.Model):
    """A judge -- scoped to a state, optionally bound to a specific court.

    One row per person. Sources combine into the same table:

    - The MN judiciary site scraper sets the current roster (photo, bio,
      appointment date, ``is_currently_seated=True``).
    - The opinion parser learns historical judges from authored bylines
      (``status=UNKNOWN`` until matched against CL).
    - CourtListener ``/people/`` resolution backfills canonical ``full_name``
      and ``courtlistener_id`` on matched rows.

    The ``source_id`` field lets the scraper find its own rows again on
    re-run (e.g. mncourts.gov slug) without duplicating.
    """

    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        SENIOR = "SENIOR", "Senior"
        RETIRED = "RETIRED", "Retired"
        UNKNOWN = "UNKNOWN", "Unknown"

    class Role(models.TextChoices):
        CHIEF_JUSTICE = "CHIEF_JUSTICE", "Chief Justice"
        ASSOCIATE_JUSTICE = "ASSOCIATE_JUSTICE", "Associate Justice"
        CHIEF_JUDGE = "CHIEF_JUDGE", "Chief Judge"
        JUDGE = "JUDGE", "Judge"
        UNKNOWN = "UNKNOWN", "Unknown"

    state = models.ForeignKey(State, on_delete=models.PROTECT, related_name="judges")
    court = models.ForeignKey(
        "Court",
        on_delete=models.PROTECT,
        related_name="judges",
        null=True,
        blank=True,
        help_text="Primary court this judge sits on. Null for historical or unmapped rows.",
    )
    full_name = models.CharField(max_length=128)
    slug = models.SlugField(max_length=128)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.UNKNOWN,
    )
    role = models.CharField(
        max_length=24,
        choices=Role.choices,
        default=Role.UNKNOWN,
        blank=True,
    )
    is_currently_seated = models.BooleanField(
        default=False,
        help_text="True if this judge appears on the current judicial roster.",
    )
    appointment_date = models.DateField(
        null=True,
        blank=True,
        help_text="When this judge took the bench in their current role.",
    )
    bio_url = models.URLField(
        max_length=512,
        blank=True,
        default="",
        help_text="Link to the judge's official bio page (e.g. mncourts.gov/judges/...).",
    )
    bio_summary = models.TextField(
        blank=True,
        default="",
        help_text="Short paragraph from the official bio. Plain text, no HTML.",
    )
    photo_url = models.URLField(
        max_length=512,
        blank=True,
        default="",
        help_text="External URL of the official portrait. Self-hosted later.",
    )
    courtlistener_id = models.CharField(
        max_length=64,
        blank=True,
        default="",
        db_index=True,
        help_text="CourtListener person identifier, when known.",
    )
    source_id = models.CharField(
        max_length=128,
        blank=True,
        default="",
        db_index=True,
        help_text="Stable ID from the source roster (e.g. mncourts.gov slug). For scraper idempotency.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("state", "slug")]
        ordering = ["full_name"]
        indexes = [
            models.Index(fields=["state", "is_currently_seated"]),
            models.Index(fields=["court", "is_currently_seated"]),
        ]

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
    disposition = models.CharField(
        max_length=128,
        blank=True,
        default="",
        help_text="Overall result for the case (e.g. 'Affirmed', 'Reversed and remanded'). "
                  "Separate from per-holding direction on OpinionHolding.",
    )
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
    disposition_bucket = models.CharField(
        max_length=24,
        blank=True,
        default="",
        db_index=True,
        help_text="Auto-populated outcome bucket slug (affirmed / reversed / vacated / ...). "
                  "Indexed for fast filtering from the sidebar Outcomes legend.",
    )

    class ReviewStatus(models.TextChoices):
        AI_ONLY = "ai_only", "AI-processed only"
        FLAGGED = "flagged", "Flagged for review"
        REVIEWED = "reviewed", "Human-reviewed"

    review_status = models.CharField(
        max_length=16,
        choices=ReviewStatus.choices,
        default=ReviewStatus.AI_ONLY,
        db_index=True,
        help_text=(
            "Editorial review state. Shown publicly so readers know whether a "
            "human has read and curated this opinion vs. whether it has only "
            "been machine-processed. Default for ingested opinions is "
            "AI-processed; flip via admin action when reviewed."
        ),
    )
    reviewed_by = models.CharField(
        max_length=120,
        blank=True,
        default="",
        help_text="Username of the editor who marked this opinion human-reviewed.",
    )
    reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Auto-stamped when review_status flips to human-reviewed.",
    )
    review_notes = models.TextField(
        blank=True,
        default="",
        help_text="Internal editor notes. Not shown publicly.",
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

    @property
    def disposition_class(self) -> str:
        """CSS bucket slug for color-coding the disposition pill.

        Reads the stored ``disposition_bucket`` column (populated at save
        time from ``compute_disposition_bucket``). Falls back to recomputing
        when the column is empty -- helpful for the brief window before the
        backfill data migration runs.
        """
        if self.disposition_bucket:
            return self.disposition_bucket
        from opinions.utils import compute_disposition_bucket
        return compute_disposition_bucket(self.disposition)

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
        # Keep the indexed outcome bucket in sync with the free-form
        # disposition string on every save.
        from opinions.utils import compute_disposition_bucket
        self.disposition_bucket = compute_disposition_bucket(self.disposition)

        # Auto-stamp reviewed_at on transition into the human-reviewed state
        # via direct form edits. Admin bulk actions stamp explicitly, so this
        # is the catch-all for one-off edits where the editor flips the
        # dropdown but forgets to fill in the timestamp.
        if (
            self.review_status == self.ReviewStatus.REVIEWED
            and self.reviewed_at is None
        ):
            from django.utils import timezone
            self.reviewed_at = timezone.now()

        super().save(*args, **kwargs)

        # After the row has a pk, run the state's opinion parser. Fills empty
        # Opinion fields and writes an audit row to ParseLog. The parser is
        # idempotent: it skips if a ParseLog already exists with a matching
        # raw_text_sha256, so re-saves and cron re-runs are no-ops.
        if self.raw_text and self.court_id:
            self._maybe_run_parser()

    def _maybe_run_parser(self) -> None:
        """Run the state parser, populate empty fields, write ParseLog.

        Idempotent on ``raw_text_sha256``: skips when a ParseLog already
        exists for this Opinion + state + text-hash. Wrapped in a broad
        try/except so a parser regression never blocks an Opinion save --
        the cron-ingested rows are the most important to protect, and
        failures are routed to the ``opinions`` logger so they surface in
        the daemon log instead of vanishing.
        """
        import hashlib
        import logging
        import time

        try:
            from opinions.parsing import REGISTRY, parse as parse_state

            state_code = self.court.state_id  # State PK is the 2-letter code.
            if not state_code:
                return

            text_hash = hashlib.sha256(self.raw_text.encode("utf-8")).hexdigest()
            if ParseLog.objects.filter(
                opinion=self,
                parser_state=state_code,
                raw_text_sha256=text_hash,
            ).exists():
                return

            started = time.time()
            result = parse_state(state_code, self.raw_text)
            elapsed_ms = int((time.time() - started) * 1000)
            if result is None:
                return  # No parser registered for this state; don't log noise.

            # Populate empty Opinion fields. Never overwrite human input.
            changed = []
            if result.case_number and not self.case_number:
                self.case_number = result.case_number
                changed.append("case_number")
            if result.case_name and not self.title:
                self.title = result.case_name
                changed.append("title")
            if result.release_date and not self.release_date:
                self.release_date = result.release_date
                changed.append("release_date")
            if result.disposition and not self.disposition:
                self.disposition = result.disposition
                changed.append("disposition")
                from opinions.utils import compute_disposition_bucket
                self.disposition_bucket = compute_disposition_bucket(self.disposition)
                changed.append("disposition_bucket")
            # is_precedential default is True; only the parser's explicit
            # "False" finding (saw the nonprecedential footer) overrides.
            if result.is_precedential is False and self.is_precedential is True:
                self.is_precedential = False
                changed.append("is_precedential")

            if changed:
                type(self).objects.filter(pk=self.pk).update(
                    **{f: getattr(self, f) for f in changed}
                )

            parser = REGISTRY.get(state_code)
            ParseLog.objects.create(
                opinion=self,
                parser_state=state_code,
                parser_version=getattr(parser, "version", "v1"),
                extracted=result.as_dict(),
                missing_fields=result.missing_fields(),
                raw_text_sha256=text_hash,
                duration_ms=elapsed_ms,
            )
        except Exception:
            logging.getLogger("opinions").exception(
                "Parser run failed for Opinion id=%s", self.pk
            )


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


class QueryEmbedding(models.Model):
    """Cache layer for the Scan endpoint's query-side embeddings.

    Voyage's embed-a-query call costs real money. Without a cache, every
    user search hits the API; a single bot doing 100 scans/min would blow
    up the Voyage bill faster than NFSN's CPU bill. With this cache, the
    Nth user searching for the same phrase pays nothing -- we serve the
    cached vector from a tiny on-disk lookup.

    Stored as a JSON-string list of 1024 floats rather than a native
    VECTOR column because Django's ORM doesn't speak VECTOR and we never
    need to do distance comparisons on the cache itself (we compare
    against ``Opinion.embedding``, which IS a VECTOR column). The cache
    just stores; the distance math runs at search time.

    Normalized lowercase query is the PK so identical-but-different-case
    queries share an entry. Length cap 512 to bound the column; very long
    queries (> 512 chars) bypass the cache entirely.
    """

    query = models.CharField(
        max_length=512,
        primary_key=True,
        help_text="Lowercase-normalized search query.",
    )
    embedding_json = models.TextField(
        help_text="JSON array of 1024 floats -- the Voyage embedding for ``query``.",
    )
    hit_count = models.PositiveIntegerField(
        default=1,
        help_text="How many times this cached embedding has been served.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(
        auto_now=True,
        db_index=True,
        help_text="Used for LRU eviction if the cache ever needs trimming.",
    )

    class Meta:
        ordering = ["-last_used_at"]

    def __str__(self):
        return f"{self.query!r} (hits={self.hit_count})"


class StateRequest(models.Model):
    """A reader's request to add their state's appellate corpus.

    Surfaced on the apex page so visitors who don't see their state get a
    way to register interest without emailing the maintainer. The admin
    list groups these so we can see which states have the most demand and
    prioritize expansion accordingly.

    No auth required to submit (public CTA), so we keep the surface area
    small: state_name (free-text -- could be "California", "CA", "PR"),
    optional email for follow-up, optional notes for context. ip_address
    is admin-only and exists for anti-spam triage; never displayed.
    """

    state_name = models.CharField(
        max_length=64,
        help_text="What the user typed -- could be 'California', 'CA', 'Puerto Rico'.",
    )
    email = models.EmailField(
        blank=True,
        default="",
        help_text="Optional. If provided, we'll email when their state goes live.",
    )
    notes = models.TextField(
        blank=True,
        default="",
        help_text="Optional. Anything the requester wants to tell us.",
    )
    ip_address = models.GenericIPAddressField(
        null=True,
        blank=True,
        help_text="For anti-spam triage. Admin-only; never shown publicly.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["state_name"]),
            models.Index(fields=["-created_at"]),
        ]

    def __str__(self):
        return f"{self.state_name} requested {self.created_at:%Y-%m-%d}"


class ParseLog(models.Model):
    """Audit trail for opinion parser runs.

    Records which parser version ran against which Opinion text, what it
    extracted, and what fields it couldn't fill in. Lets us:
    - Sort opinions by parse completeness (find rows needing human review).
    - Catch quality regressions when we tweak parser rules between versions.
    - Skip re-parsing unchanged text via the raw_text_sha256 cache key.
    """

    opinion = models.ForeignKey(
        "Opinion",
        on_delete=models.CASCADE,
        related_name="parse_logs",
    )
    parser_state = models.CharField(
        max_length=2,
        help_text="State code (e.g. 'MN') of the parser that ran.",
    )
    parser_version = models.CharField(
        max_length=16,
        default="v1",
        help_text="Bumped when parser rules change in a way that affects output.",
    )
    extracted = models.JSONField(
        default=dict,
        help_text="The full ParsedOpinion serialized as JSON (release_date is ISO).",
    )
    missing_fields = models.JSONField(
        default=list,
        help_text="Tracked top-level fields the parser couldn't fill in.",
    )
    raw_text_sha256 = models.CharField(
        max_length=64,
        help_text="SHA-256 of raw_text at parse time. Used to skip re-parses of unchanged text.",
    )
    duration_ms = models.IntegerField(
        default=0,
        help_text="Wall-clock parse time in milliseconds.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["parser_state", "-created_at"]),
            models.Index(fields=["opinion", "-created_at"]),
        ]

    def __str__(self):
        return (
            f"ParseLog({self.parser_state}/{self.parser_version}) "
            f"for opinion {self.opinion_id}"
        )
