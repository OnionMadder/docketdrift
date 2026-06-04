"""Base dataclass + protocol for state-specific opinion parsers."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Optional


# Top-level field names a caller might want to copy onto the Opinion row.
# Used by ``ParsedOpinion.missing_fields`` to report what the parser
# couldn't fill in -- this is what writes to ``ParseLog.missing_fields``.
_TRACKED_FIELDS = (
    "case_number",
    "case_name",
    "release_date",
    "is_precedential",
    "disposition",
    "author",
)


@dataclass
class ParsedOpinion:
    """Output of a ``StateParser.parse(raw_text)`` run.

    Every field is optional. Callers populate the corresponding ``Opinion``
    field only when (a) the parser found something AND (b) the Opinion
    field is currently empty -- the parser never overwrites human input.

    ``confidence`` maps field name -> [0.0, 1.0]. Downstream code can
    ignore values below some threshold or surface them in a review queue.

    ``as_dict()`` returns a JSON-serializable dict (dates as ISO strings)
    suitable for ``ParseLog.extracted``.
    """

    case_number: Optional[str] = None
    case_name: Optional[str] = None
    release_date: Optional[date] = None
    is_precedential: Optional[bool] = None
    disposition: Optional[str] = None
    author: Optional[str] = None
    panel: list[str] = field(default_factory=list)
    statutes_cited: list[str] = field(default_factory=list)
    citations_to: list[str] = field(default_factory=list)
    confidence: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict:
        out = asdict(self)
        rd = out.get("release_date")
        if isinstance(rd, date):
            out["release_date"] = rd.isoformat()
        return out

    def missing_fields(self) -> list[str]:
        """Names of the tracked top-level fields the parser didn't fill in."""
        missing = []
        for name in _TRACKED_FIELDS:
            value = getattr(self, name)
            if value is None or value == "" or value == []:
                missing.append(name)
        return missing


class StateParser:
    """Interface every state parser implements.

    Subclasses set ``state_code`` (USPS 2-letter, uppercase) and ``version``
    (a short string we bump when we make breaking changes to the rules,
    so ``ParseLog`` rows are trackable across parser revisions).
    """

    state_code: str = ""
    version: str = "v1"

    def parse(self, raw_text: str) -> ParsedOpinion:
        raise NotImplementedError
