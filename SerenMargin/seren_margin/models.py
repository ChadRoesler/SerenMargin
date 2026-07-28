"""Pydantic models for SerenMargin.

A MarginNote is the primitive: a private note-to-self the writer jots down,
reads back when they choose, amends as the thought develops, and retracts when
they're done with it.

This is a thought-space, not a task queue. Deliberately NO lifecycle machinery -
no pin, no expiry, no done-state. A thought isn't "completed"; it's either still
worth keeping or it isn't. So the only removal verb is retract.

The writer writes them; the writer decides when to surface them; the operator
sees them only on offer-and-accept.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Optional

from pydantic import BaseModel, Field, computed_field


def _now() -> float:
    return time.time()


def _new_id() -> str:
    return uuid.uuid4().hex


# ── relative age ────────────────────────────────────────────────────────────
#
# WHY: a raw epoch float is not a quantity anyone has intuitions about. Reading
# back `ts: 1785249853.30` tells you nothing about whether that thought is from
# this morning or from March, which is the difference between the board reading
# as a timeline and reading as an undifferentiated pile.
#
# Attached as a pydantic computed_field rather than assembled in the routes, so
# it appears in model_dump() automatically and BOTH surfaces (HTTP and MCP) get
# it for free with no chance of one drifting from the other.
#
# Deliberately absent from NoteStats - see that class. Age of individual notes
# is note data, and the operator's engine-check surface doesn't get note data.

_MINUTE = 60.0
_HOUR = 3600.0
_DAY = 86400.0


def humanize_age(ts: float, *, now: Optional[float] = None) -> str:
    """Render a timestamp as a rough relative age. Coarse on purpose - the
    useful signal is 'recent / a while ago / old', not precision.

    Future timestamps (clock skew, a restored backup) render as "just now"
    rather than a negative age, because a nonsense number in a read-back is
    more confusing than a slightly wrong one.
    """
    delta = (now if now is not None else _now()) - ts
    if delta < 45:
        return "just now"
    if delta < _HOUR:
        n = max(1, int(delta // _MINUTE))
        return f"{n} minute{'s' if n != 1 else ''} ago"
    if delta < _DAY:
        n = int(delta // _HOUR)
        return f"{n} hour{'s' if n != 1 else ''} ago"
    if delta < 7 * _DAY:
        n = int(delta // _DAY)
        return f"{n} day{'s' if n != 1 else ''} ago"
    # Weeks stop at ~a month. Letting this branch run to 60 days would render
    # "8 weeks ago", which is strictly harder to feel than "2 months ago" - the
    # whole point of this function is legibility, not resolution.
    if delta < 30 * _DAY:
        n = int(delta // (7 * _DAY))
        return f"{n} week{'s' if n != 1 else ''} ago"
    if delta < 365 * _DAY:
        n = int(delta // (30 * _DAY))
        return f"{n} month{'s' if n != 1 else ''} ago"
    n = int(delta // (365 * _DAY))
    return f"{n} year{'s' if n != 1 else ''} ago"


# ── kind vocabulary ─────────────────────────────────────────────────────────
#
# `kind` is FREE-FORM on write, on purpose: making someone pick from a dropdown
# while they're jotting a half-formed thought is exactly the friction this
# service exists to not have.
#
# But /notes/stats promises to be CONTENT-BLIND, and a free-text field WILL get
# used as a second content line - "reminder" today, "worried about the deploy
# tomorrow" the moment it's convenient. Echoing raw kinds into the stats dict
# would quietly break the content-blind promise, and it would break it in the
# one endpoint an operator built specifically so they could check the engine
# WITHOUT reading the notes.
#
# So: free-form in, bucketed out. Anything outside this vocabulary counts as
# "other" - the operator still learns "23 notes, some uncategorised", which is
# all engine-check ever needed, and learns nothing about what any of them say.
KNOWN_KINDS: frozenset[str] = frozenset({
    "observation",
    "reminder",
    "question",
    "idea",
    "feeling",
    "note",
})

_UNKINDED = "_unkinded"
_OTHER = "other"


def bucket_kind(kind: Optional[str]) -> str:
    """Map a free-form kind onto the content-blind reporting vocabulary.

    Case- and whitespace-insensitive, since "Reminder " and "reminder" are the
    same intent and splitting them would leak the writer's typing habits into a
    surface that's supposed to reveal nothing.
    """
    if kind is None:
        return _UNKINDED
    k = kind.strip().lower()
    if not k:
        return _UNKINDED
    return k if k in KNOWN_KINDS else _OTHER


# ── the amendment separator ─────────────────────────────────────────────────
#
# Amendments append into the note's own content rather than living in a side
# table. A thought that developed is ONE thought - reading it should be reading
# one blob of text, not reconstructing a join. The separator is plain ASCII on
# purpose: no em dashes, no unicode, nothing that can explode when a note gets
# printed to a legacy Windows console.
AMEND_SEPARATOR = "\n\n--- later ---\n\n"


class MarginNote(BaseModel):
    """A single private note-to-self.

    Fields:
      - content: the note text. Whatever the writer wants to keep. Grows by
        append when amended (see AMEND_SEPARATOR).
      - topic: a light thread-label, so related notes can be found together.
        Searchable alongside content, and enumerable via list_my_topics.
      - kind: free-form category (see KNOWN_KINDS for the vocabulary that
        survives into content-blind stats; anything else buckets to "other").
      - ts: first-write time, stamped by the server. Amending does NOT move it -
        ordering should reflect when a thought STARTED, not when it was last
        poked.
      - amended_at: last amendment time, or None if never amended.
      - id: stable identifier for fetch/amend/retract
      - extra: free-form escape hatch for writer-supplied metadata

    Lives until retracted. No pin/expiry/done - see module docstring.
    """

    content: str = Field(..., description="The note text.")
    topic: Optional[str] = Field(
        None,
        description="Light thread-label grouping related notes. Searchable.",
    )
    kind: Optional[str] = Field(
        None,
        description="Free-form category, e.g. 'reminder' / 'observation'. "
                    "Intentionally unconstrained on write - over-classifying "
                    "private notes adds friction. Bucketed to a fixed "
                    "vocabulary on the content-blind stats surface.",
    )

    ts: float = Field(default_factory=_now)
    amended_at: Optional[float] = Field(
        None, description="Last amendment time; None if never amended.")

    id: str = Field(default_factory=_new_id)
    extra: dict[str, Any] = Field(default_factory=dict)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def age(self) -> str:
        """How long ago this thought was first written, in words."""
        return humanize_age(self.ts)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def amended_age(self) -> Optional[str]:
        """How long ago it was last amended, or None if it never was."""
        return None if self.amended_at is None else humanize_age(self.amended_at)


class NoteCreate(BaseModel):
    """Input shape for POST /notes - writer-supplied fields only. Server
    stamps ts and id.
    """
    content: str
    topic: Optional[str] = None
    kind: Optional[str] = None
    extra: dict[str, Any] = Field(default_factory=dict)


class NoteAmend(BaseModel):
    """Input shape for POST /notes/{id}/amend. Appends; never replaces."""
    addition: str


class TopicSummary(BaseModel):
    """One thread on the board: its label, how many notes carry it, and when it
    was last touched. The unit of cheap orientation."""
    topic: Optional[str] = Field(
        None, description="The topic label, or null for untopiced notes.")
    count: int
    latest_ts: float
    latest_age: str


class NoteStats(BaseModel):
    """The engine-check view. CONTENT-BLIND by design - exposes shape without
    exposing what's in the notes.

    This is the surface for "is the engine running" validation that respects the
    operator's stated relational stance of not reading individual notes. Kind
    counts appear only after bucketing (see bucket_kind); note text never
    appears at all.

    NOTE FOR ANYONE EXTENDING THIS: topics and per-note ages are deliberately
    NOT here, even though both are cheap to compute and would look like
    harmless "shape". A topic label is a phrase the writer chose - it is note
    content wearing a metadata hat. If you want richer stats, the question to
    answer first is "could a person infer what a note SAYS from this field",
    and for topics the answer is plainly yes.

    Residual disclosure, stated plainly so nobody has to reverse-engineer it:
    the bucket histogram still reveals the SHAPE of the board - a spike in
    "feeling" is legible as a spike in "feeling". That's the irreducible cost of
    having any engine-check at all. An operator who wants even that gone can
    read `total` and ignore `kinds`.
    """
    total: int
    kinds: dict[str, int] = Field(default_factory=dict)
