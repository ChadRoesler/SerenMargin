"""Pydantic models for SerenMargin.

A MarginNote is the corkboard primitive: a private note-to-self the model
writes, reads back when it chooses, and deletes when it's done with it.

Deliberately NO lifecycle machinery - no pin, no expiry, no done-state.
These are private thoughts you jot down; they live until explicitly deleted.
The model writes them; the model decides when to surface them; the human sees
them only on offer-and-accept.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Optional

from pydantic import BaseModel, Field


def _now() -> float:
    return time.time()


def _new_id() -> str:
    return uuid.uuid4().hex


class MarginNote(BaseModel):
    """A single private note-to-self.

    Fields:
      - content: the note text (often imperative)
      - topic / kind: light organization. kind powers the content-blind stats
        (it's operator-facing taxonomy, not note content)
      - ts: write time, stamped by the server
      - id: stable identifier for fetch/delete
      - extra: free-form escape hatch for writer-supplied metadata

    Lives until deleted. No pin/expiry/done - see module docstring.
    """

    content: str = Field(..., description="The note text. Often imperative.")
    topic: Optional[str] = Field(None)
    kind: Optional[str] = Field(
        None,
        description="Free-form category, e.g. 'reminder' / 'observation'. "
                    "Intentionally unconstrained - over-classifying private "
                    "notes adds friction. Let the model write whatever shape "
                    "it wants.",
    )

    ts: float = Field(default_factory=_now)

    id: str = Field(default_factory=_new_id)
    extra: dict[str, Any] = Field(default_factory=dict)


class NoteCreate(BaseModel):
    """Input shape for POST /notes - writer-supplied fields only. Server
    stamps ts and id.
    """
    content: str
    topic: Optional[str] = None
    kind: Optional[str] = None
    extra: dict[str, Any] = Field(default_factory=dict)


class NoteStats(BaseModel):
    """The engine-check view. CONTENT-BLIND by design - exposes shape
    without exposing what's in the notes.

    This is the surface for 'is the engine running' validation that respects
    the operator's stated relational stance of not reading individual notes.
    Kind counts are included (kinds are operator-facing taxonomy rather than
    note content); content text never appears in this response.
    """
    total: int
    kinds: dict[str, int] = Field(default_factory=dict)