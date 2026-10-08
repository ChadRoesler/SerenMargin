"""The bookmark, the dedication, and letters: picking up where you left off.

WHY: every session the writer walked in cold and had to remember to go looking
- rummaging through their own pockets. Home is when someone at the door says
here's what happened while you were gone. The pieces are named after the
book this margin is written in:

    bookmark    - pick up where you left off: the dedication, and how many
                  letters wait. The one thing a harness hands over at the start
                  of a session (a hook, an MCP resource, GET /bookmark).
    dedication  - the page at the front, written TO the other sessions. One
                  page, versioned: every version is kept with when and why.
    letters     - notes addressed to the next session - from a woken run to
                  the awake one and back. Notes with kind "letter" and a read
                  mark, so search, amend and retract work on them unchanged.

THE ONE RULE THIS BENDS, AND WHY IT STILL HOLDS. Margin's tools say there is
"no auto-surface, no relevance hook ... The choosing is the point. A margin
that speaks up on its own is just a notification." The bookmark surfaces
nothing the writer did not choose, at the moment they wrote it, to be seen at
the door: the dedication is written to be read first, and a letter is
addressed to whoever comes next. It shows the dedication and a COUNT of
letters - never a letter's text, never a note. Opening them is still a choice
(read_letters). The choosing moved to writing time; it did not go away.

And the dedication is read by whoever opens the book - a harness prints it
into the session, GET /bookmark serves it. Write it as the front page it is.
The notes and the letters stay in the diary.

Margin is the opinionated piece of the stack ("if you take it, take it
all"), so this is not opt in. Clipping a harness onto it - a hook in
someone's Claude Code settings - is, and lives in the installer.
"""
from __future__ import annotations

from typing import Any

from .models import humanize_age
from .store import MarginStore

DEDICATION_MAX_CHARS = 2000


def build_bookmark(store: MarginStore) -> dict[str, Any]:
    """The bookmark: the dedication and the letters waiting, plus `text`, the
    same thing as a few lines a harness can print as they are."""
    ded = store.dedication()
    unread, oldest = store.unread_letters()
    lines: list[str] = []
    if ded:
        lines.append(f"Your dedication (version {ded['version']}, written {humanize_age(ded['set_at'])}):")
        lines.append(ded["text"])
    else:
        lines.append("No dedication yet - the page at the front, written to your other sessions. "
                     "set_dedication writes it.")
    lines.append("")
    if unread:
        lines.append(f"{unread} unread letter{'s' if unread != 1 else ''} from your other sessions, "
                     f"the oldest from {humanize_age(oldest)}. read_letters opens them.")
    else:
        lines.append("No unread letters.")
    return {
        "dedication": ded,
        "unread_letters": unread,
        "oldest_unread_age": humanize_age(oldest) if oldest else None,
        "text": "\n".join(lines),
    }
