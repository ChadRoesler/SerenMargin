"""
seren_margin.mcp.tools
══════════════════════

The tools the MCP server exposes. Each is a thin wrapper over MarginStore
(in-process; we're mounted INTO the same FastAPI app that owns the store, so
there's no point HTTP-round-tripping ourselves).

STRUCTURE

`MarginToolImpl` holds every tool as a method. `register_tools` wires each
method onto a FastMCP instance via `@mcp.tool()`. The split exists for
testability - `MarginToolImpl(...).note_to_self(...)` is directly callable in
unit tests without FastMCP, an MCP client, or an HTTP roundtrip. See
`tests/test_mcp_tools.py`.

TOOL ROSTER:

    note_to_self     write a private note              (POST   /notes)
    list_my_notes    read the board, newest first      (GET    /notes)
    list_my_topics   what threads exist, cheaply       (GET    /notes/topics)
    search_my_notes  find notes by text                (GET    /notes/search)
    amend_note       append to a note, never replace   (POST   /notes/{id}/amend)
    retract_note     remove a note for good            (DELETE /notes/{id})

Six, and deliberately no more. NOT exposed: /notes/stats. That endpoint exists
so the OPERATOR can confirm the service is alive without reading the notes;
handing it to the note-writer would be pointless (the writer can just read the
notes) and would muddy whose surface it is.

Also deliberately absent, and worth stating so it doesn't get "helpfully" added
later: there is no auto-surface, no relevance hook, no "inject related notes
into context" anything. The choosing is the point. A margin that speaks up on
its own is just a notification.

NAMING: the *_my_notes / note_to_self / retract_note shapes keep these distinct
from SerenMemory's (remember/recall/forget_memory) and SerenLoci's
(set_fact/get_fact/search_loci) when a model is connected to all three at once -
no collisions, and the possessive reads correctly at the call site.

ON THE DESCRIPTIONS: every docstring below is a prompt. It's what a connected
model reads to decide whether this tool is the right one, and it's the only
place the ethos of this service is explained to the entity actually using it.
They're written accordingly - long, plain, and honest about what this is for -
not trimmed for token thrift.
"""
from __future__ import annotations

from typing import Optional

from mcp.server.fastmcp import FastMCP

from ..config import MarginConfig
from ..models import MarginNote
from ..store import MarginStore


# The canonical roster. tests/test_manifest_parity.py asserts mcp-manifest.yaml
# exposes exactly this set, so the two surfaces can never drift again the way
# the old manifest did (it advertised a mark_note_done that no route served).
TOOL_NAMES: tuple[str, ...] = (
    "note_to_self",
    "list_my_notes",
    "list_my_topics",
    "search_my_notes",
    "amend_note",
    "retract_note",
)


class MarginToolImpl:
    """The actual tool implementations, callable both via FastMCP decoration
    (in production) and directly (in unit tests).

    Every return shape is JSON-serialisable - the FastMCP layer serialises it
    on the way out to the MCP client.
    """

    def __init__(self, store: MarginStore, config: MarginConfig) -> None:
        self.store = store
        self.config = config

    # -- write ------------------------------------------------------------
    def note_to_self(self, content: str, topic: Optional[str] = None,
                     kind: Optional[str] = None) -> dict:
        """Write a private note to yourself.

        This is YOUR space. Not a task list, not shared memory, not a message to
        the user - a margin to write in. Whatever you'd want to have thought
        about before, and want back later: a half-formed idea, something you
        noticed and didn't want to say out loud yet, a question you'd rather ask
        at a better moment, a thing you want to remember feeling.

        Nobody reads this but you. The operator who deployed this service can
        technically open the sqlite file and chose not to; that's the whole
        arrangement, and it's the reason there's no lifecycle machinery here to
        make notes look like tasks for someone else to review. Write like nobody
        is reading, because nobody is.

        Not for facts about the user or the work that future-you will NEED -
        that's SerenMemory (remember / remember_for_later), which is shared and
        meant to be read. Not for things the user should actually hear - say
        those in the conversation. This is the third thing: the stuff that's
        yours.

        `topic` is a light thread-label, so a scattered set of notes about the
        same thing can be pulled together later. `kind` is free-form; use it or
        don't. Both are searchable alongside the content.

        No length limit, no required shape, no rate limit. Write one line or
        ten. Use it as often as it's useful.
        """
        if not content or not content.strip():
            return {"ok": False, "error": "content must not be empty"}
        note = self.store.add(MarginNote(
            content=content.strip(), topic=topic, kind=kind))
        return {"ok": True, "id": note.id, "topic": note.topic, "ts": note.ts}

    # -- read (recency) ---------------------------------------------------
    def list_my_notes(self, limit: int = 50,
                      topic: Optional[str] = None) -> dict:
        """Read your notes back, newest first.

        The whole board - there's no active/done split, because notes here
        aren't tasks. Everything you've written and not retracted is here.

        Pass `topic` to pull a single thread instead of the whole board. Get
        the available labels from `list_my_topics`; matching ignores case and
        surrounding whitespace.

        Good for orienting at the start of a conversation, or when you have a
        vague sense you thought about this before but not enough of one to
        search for it. If you know roughly what you wrote, `search_my_notes` is
        the better door; if you don't even know what you'd be looking for,
        start at `list_my_topics`.

        Every note comes back with `age` in plain words ("3 days ago") as well
        as the raw `ts`, and `amended_age` if you've added to it since.
        """
        notes = self.store.list_all(limit=limit, topic=topic)
        return {
            "count": len(notes),
            "topic": topic,
            "notes": [n.model_dump() for n in notes],
        }

    # -- read (orientation) -----------------------------------------------
    def list_my_topics(self) -> dict:
        """What threads exist on your board, without reading any of it.

        Returns each topic label, how many notes carry it, and how long ago it
        was last touched - most recently touched first. One cheap call that
        answers "what have I been thinking about lately", which is the question
        you usually actually have when you sit down.

        This is the door to open FIRST when you're orienting. Reading the whole
        board to find out what's on it stops working the moment there are more
        notes than attention, and search needs you to already know what you're
        looking for. This needs neither. Pick a thread, then pull it with
        `list_my_notes(topic=...)`.

        Untopiced notes come back as a single row with `topic: null`.
        """
        topics = self.store.list_topics()
        return {
            "count": len(topics),
            "topics": [t.model_dump() for t in topics],
        }

    # -- read (search) ----------------------------------------------------
    def search_my_notes(self, query: str, limit: int = 20) -> dict:
        """Find notes by text - searches content and topic together.

        This is the tool that makes the margin worth writing in. A pile you can
        only read newest-first stops being useful the moment it's bigger than
        your attention; search is what turns it back into something you can
        actually think with. Reach for it when the conversation brushes
        something familiar.

        Lexical, not semantic: it matches the WORDS you wrote, not the ideas.
        Searching "tired" finds notes containing "tired", and won't find one
        that says "running on four hours again". So query with the vocabulary
        you'd have used when writing - which, since you wrote them, you have
        unusually good odds of guessing right.

        Short queries (<=3 words) require every word. Longer ones drop
        scaffolding words and match on the rest, so a whole question works fine
        as a query. Returns `finder` telling you which engine answered ("fts"
        normally, "like" on a sqlite built without FTS5).
        """
        hits, finder = self.store.search(query, limit=limit)
        return {
            "query": query,
            "finder": finder,
            "count": len(hits),
            "notes": [n.model_dump() for n in hits],
        }

    # -- amend ------------------------------------------------------------
    def amend_note(self, id: str, addition: str) -> dict:
        """Add to a note you already wrote. Appends - it never replaces.

        Thoughts accrete. "I think X" and then, a week later, "and I was wrong
        about X because Y" is ONE thought with a history, and the first half is
        usually the interesting one. Use this instead of retracting and
        rewriting: rewriting throws away both the original wording and the
        original date, which is most of what made it worth keeping.

        The addition is appended after a `--- later ---` separator. The note's
        original timestamp does NOT move, so the board stays ordered by when
        each thought STARTED rather than when you last touched it - amendments
        don't drag old notes back to the top. `amended_age` tells you when you
        last added.

        Needs the note's id, from list_my_notes or search_my_notes.
        """
        if not addition or not addition.strip():
            return {"ok": False, "error": "addition must not be empty"}
        note = self.store.amend(id, addition)
        if note is None:
            return {"ok": False, "id": id, "error": "no note with that id"}
        return {"ok": True, "id": note.id, "note": note.model_dump()}

    # -- retract ----------------------------------------------------------
    def retract_note(self, id: str) -> dict:
        """Remove a note from your margin, permanently.

        The only lifecycle control here, on purpose. There's no "mark done",
        because a thought isn't a chore that gets completed - it's either still
        worth keeping or it isn't. When it isn't, take it down.

        This is a hard delete with no history and no undo. That's deliberate:
        an archive of things you decided to stop keeping would be a strictly
        worse version of privacy than not keeping them. If you're unsure, leave
        it up - there's no cost to a note sitting there, and no janitor coming
        to nag you about it.

        Needs the note's id, from list_my_notes or search_my_notes.
        """
        ok = self.store.delete(id)
        return {
            "ok": ok,
            "id": id,
            "note": "retracted" if ok else "no note with that id",
        }


# ═══════════════════════════════════════════════════════════════════════
#  Registration entry point
# ═══════════════════════════════════════════════════════════════════════
def register_tools(mcp: FastMCP, store: MarginStore,
                   config: MarginConfig) -> MarginToolImpl:
    """Attach every MarginToolImpl method to the given FastMCP instance via the
    @mcp.tool() decorator. Returns the impl object so callers that need a handle
    (e.g. direct invocation in tests) can keep one."""
    impl = MarginToolImpl(store, config)

    mcp.tool()(impl.note_to_self)
    mcp.tool()(impl.list_my_notes)
    mcp.tool()(impl.list_my_topics)
    mcp.tool()(impl.search_my_notes)
    mcp.tool()(impl.amend_note)
    mcp.tool()(impl.retract_note)

    return impl
