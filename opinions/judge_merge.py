"""Shared machinery for merging duplicate Judge rows.

Used by the ``merge_hyphenated_judges`` and ``merge_surname_only_judges``
management commands. Kept out of the commands package (and un-prefixed) so both
can import it without either becoming the other's dependency.

The only model that references Judge is PanelVote (FK on_delete=PROTECT), so a
merge is: reassign the loser's votes to the survivor -- honoring the
(opinion, judge) unique constraint by keeping the stronger vote type on a
collision -- then delete the now-vote-less loser row.
"""

from django.db import transaction

from opinions.models import PanelVote

# Vote-type strength: on an (opinion, judge) collision after reassignment we
# keep the higher-ranked type. Authoring any opinion outranks merely joining;
# within a tier majority > concurrence > dissent; non-participation lowest.
VOTE_RANK = {
    PanelVote.Vote.MAJORITY_AUTHOR: 7,
    PanelVote.Vote.CONCURRENCE_AUTHOR: 6,
    PanelVote.Vote.DISSENT_AUTHOR: 5,
    PanelVote.Vote.MAJORITY_JOIN: 4,
    PanelVote.Vote.CONCURRENCE_JOIN: 3,
    PanelVote.Vote.DISSENT_JOIN: 2,
    PanelVote.Vote.RECUSED: 1,
    PanelVote.Vote.NOT_PARTICIPATING: 0,
}


# Generational suffixes that trail a surname; skipped when finding the surname
# token so "John T. Broderick Jr" and "Broderick" group together.
_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "2", "3", "4", "2nd", "3rd", "4th"}


def surname(full_name: str) -> str:
    """Surname token of a name, skipping a trailing generational suffix.

    'John T. Broderick Jr' -> 'Broderick', 'Charles G. Douglas 3' -> 'Douglas',
    '' for a blank name. Taking the raw last token would return the suffix and
    split a judge's full-name row from their bare-surname vote row.
    """
    parts = full_name.strip().split()
    while parts and parts[-1].strip(".,").lower() in _NAME_SUFFIXES:
        parts = parts[:-1]
    return parts[-1] if parts else ""


def metadata_score(judge) -> int:
    """Rough editorial richness of a Judge row.

    Used to pick the survivor of a merge so the row carrying the bio / roster
    status / portrait wins over a bare vote-only duplicate. A seated bio row
    outranks a vote-heavy stub even when the stub has more panel votes -- the
    votes move either way, but the bio/slug can only live on one row.
    """
    score = 0
    if (judge.bio_summary or "").strip():
        score += 4
    if judge.is_currently_seated:
        score += 4
    if (judge.photo_url or "").strip():
        score += 2
    if (judge.bio_url or "").strip():
        score += 1
    if judge.appointment_date is not None:
        score += 1
    if (judge.courtlistener_id or "").strip():
        score += 1
    if judge.court_id is not None:
        score += 1
    if judge.status and judge.status != "UNKNOWN":
        score += 1
    if judge.role and judge.role != "UNKNOWN":
        score += 1
    return score


def _carry_metadata_forward(loser, survivor) -> bool:
    """Fill any empty/default survivor field from the loser. Returns True if the
    survivor was changed (caller decides whether to persist)."""
    changed = False
    for f in ("bio_url", "bio_summary", "photo_url", "courtlistener_id", "source_id"):
        if not (getattr(survivor, f) or "").strip() and (getattr(loser, f) or "").strip():
            setattr(survivor, f, getattr(loser, f))
            changed = True
    if survivor.appointment_date is None and loser.appointment_date is not None:
        survivor.appointment_date = loser.appointment_date
        changed = True
    if not survivor.is_currently_seated and loser.is_currently_seated:
        survivor.is_currently_seated = True
        changed = True
    if survivor.court_id is None and loser.court_id is not None:
        survivor.court_id = loser.court_id
        changed = True
    if survivor.status == "UNKNOWN" and loser.status and loser.status != "UNKNOWN":
        survivor.status = loser.status
        changed = True
    if survivor.role == "UNKNOWN" and loser.role and loser.role != "UNKNOWN":
        survivor.role = loser.role
        changed = True
    return changed


def merge_judge(loser, survivor, apply: bool) -> tuple[int, int]:
    """Move ``loser``'s PanelVotes onto ``survivor``, then delete ``loser``.

    Any editorial metadata the loser has that the survivor lacks (bio, portrait,
    roster status, appointment date, CL id, court, status/role) is carried
    forward before deletion, so a merge never loses information regardless of
    which row was chosen as survivor.

    Returns ``(votes_reassigned, votes_deduped)``. With ``apply=False`` the same
    counts are computed against current data without writing anything.
    """
    loser_votes = list(
        PanelVote.objects.filter(judge=loser).values("id", "opinion_id", "vote_type")
    )
    survivor_by_opinion = {
        pv.opinion_id: pv
        for pv in PanelVote.objects.filter(judge=survivor).only(
            "id", "opinion_id", "vote_type"
        )
    }

    moved = deduped = 0
    if not apply:
        for lv in loser_votes:
            if lv["opinion_id"] in survivor_by_opinion:
                deduped += 1
            else:
                moved += 1
        return moved, deduped

    with transaction.atomic():
        for lv in loser_votes:
            existing = survivor_by_opinion.get(lv["opinion_id"])
            if existing is None:
                PanelVote.objects.filter(pk=lv["id"]).update(judge=survivor)
                moved += 1
            else:
                # Collision: keep the stronger vote type, drop the loser row.
                if VOTE_RANK.get(lv["vote_type"], -1) > VOTE_RANK.get(
                    existing.vote_type, -1
                ):
                    existing.vote_type = lv["vote_type"]
                    existing.save(update_fields=["vote_type"])
                PanelVote.objects.filter(pk=lv["id"]).delete()
                deduped += 1
        # Preserve the loser's editorial metadata before it's gone.
        if _carry_metadata_forward(loser, survivor):
            survivor.save()
        # PROTECT on PanelVote.judge is now satisfied (loser has no votes).
        loser.delete()

    return moved, deduped
