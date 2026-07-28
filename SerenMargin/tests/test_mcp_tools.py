"""MCP tool tests - MarginToolImpl called directly.

Gated on the `mcp` package. The class-not-closures structure exists precisely so
these can run without FastMCP, an MCP client, or an HTTP roundtrip.

Note the import gate is on `mcp` only because tools.py imports FastMCP for the
type hint on register_tools; the impl methods themselves touch nothing but the
store.
"""
from __future__ import annotations

import pytest

pytest.importorskip("mcp")

from seren_margin.config import MarginConfig
from seren_margin.mcp.tools import MarginToolImpl, TOOL_NAMES
from seren_margin.store import MarginStore


@pytest.fixture
def impl(tmp_path):
    cfg = MarginConfig(db_path=str(tmp_path / "notes.db"))
    return MarginToolImpl(MarginStore(cfg.resolved_db_path()), cfg)


# ── note_to_self ────────────────────────────────────────────────────────────

def test_note_to_self_writes_and_returns_id(impl):
    r = impl.note_to_self("the redraft budget feels tight")
    assert r["ok"] is True
    assert r["id"]
    assert impl.store.get(r["id"]).content == "the redraft budget feels tight"


def test_note_to_self_carries_topic_and_kind(impl):
    r = impl.note_to_self("x", topic="chad", kind="observation")
    note = impl.store.get(r["id"])
    assert note.topic == "chad"
    assert note.kind == "observation"
    assert r["topic"] == "chad"


def test_note_to_self_strips_surrounding_whitespace(impl):
    r = impl.note_to_self("   padded thought   ")
    assert impl.store.get(r["id"]).content == "padded thought"


def test_note_to_self_rejects_empty(impl):
    """Returns a structured error rather than raising - an exception across the
    MCP boundary is a worse experience than {ok: false} with a reason."""
    for bad in ("", "   ", "\n\t"):
        r = impl.note_to_self(bad)
        assert r["ok"] is False
        assert "empty" in r["error"]


def test_note_to_self_accepts_long_content(impl):
    """No length cap is a deliberate design choice; pin it so a well-meaning
    'sensible limit' has to argue with a test first."""
    long = "a thought that kept going. " * 500
    assert impl.note_to_self(long)["ok"] is True


# ── list_my_notes ───────────────────────────────────────────────────────────

def test_list_my_notes_empty_board(impl):
    r = impl.list_my_notes()
    assert r["count"] == 0
    assert r["notes"] == []


def test_list_my_notes_is_newest_first(impl):
    for c in ("first", "second", "third"):
        impl.note_to_self(c)
    notes = impl.list_my_notes()["notes"]
    assert [n["content"] for n in notes] == ["third", "second", "first"]


def test_list_my_notes_respects_limit(impl):
    for i in range(10):
        impl.note_to_self(f"note {i}")
    assert impl.list_my_notes(limit=3)["count"] == 3


# ── search_my_notes ─────────────────────────────────────────────────────────

def test_search_my_notes_finds_by_word(impl):
    impl.note_to_self("the Behemoth sits over the collarbone", topic="chad")
    impl.note_to_self("unrelated thought about dice colors")
    r = impl.search_my_notes("collarbone")
    assert r["count"] == 1
    assert "Behemoth" in r["notes"][0]["content"]


def test_search_my_notes_reports_finder(impl):
    impl.note_to_self("x")
    assert impl.search_my_notes("x")["finder"] in {"fts", "like"}


def test_search_my_notes_echoes_query(impl):
    assert impl.search_my_notes("anything")["query"] == "anything"


def test_search_my_notes_empty_query_is_empty_result(impl):
    impl.note_to_self("something")
    assert impl.search_my_notes("  ")["count"] == 0


def test_search_my_notes_miss_returns_zero_not_error(impl):
    impl.note_to_self("something")
    assert impl.search_my_notes("zzzquux")["count"] == 0


# ── retract_note ────────────────────────────────────────────────────────────

def test_retract_note_removes_it(impl):
    nid = impl.note_to_self("temporary")["id"]
    r = impl.retract_note(nid)
    assert r["ok"] is True
    assert impl.store.get(nid) is None


def test_retract_note_unknown_id_is_soft_false(impl):
    r = impl.retract_note("nope-not-an-id")
    assert r["ok"] is False
    assert "no note" in r["note"]


def test_retract_note_is_not_recoverable(impl):
    """Hard delete with no history is the documented contract, not an
    oversight. If a tombstone table ever appears, this should fail."""
    nid = impl.note_to_self("gone for good")["id"]
    impl.retract_note(nid)
    assert impl.list_my_notes()["count"] == 0
    assert impl.search_my_notes("gone")["count"] == 0


# ── roster ──────────────────────────────────────────────────────────────────

def test_tool_names_match_impl_methods():
    """TOOL_NAMES is what the parity test compares the manifest against, so it
    must actually describe the class rather than drift into decoration."""
    for name in TOOL_NAMES:
        assert callable(getattr(MarginToolImpl, name, None)), f"{name} missing"


def test_stats_is_not_on_the_ai_surface():
    """The operator's engine-check endpoint stays the operator's. If it ever
    shows up here, someone has confused whose surface it is."""
    assert not any("stat" in n for n in TOOL_NAMES)


def test_list_my_topics_tool(impl):
    impl.note_to_self("a", topic="chad")
    impl.note_to_self("b", topic="chad")
    impl.note_to_self("c", topic="rhys")
    r = impl.list_my_topics()
    assert r["count"] == 2
    assert {t["topic"]: t["count"] for t in r["topics"]} == {"chad": 2, "rhys": 1}
    assert all(t["latest_age"] for t in r["topics"])


def test_list_my_notes_topic_filter(impl):
    impl.note_to_self("a", topic="chad")
    impl.note_to_self("b", topic="rhys")
    r = impl.list_my_notes(topic="chad")
    assert r["count"] == 1 and r["topic"] == "chad"


def test_amend_note_tool_appends(impl):
    nid = impl.note_to_self("I think X")["id"]
    r = impl.amend_note(nid, "and I was wrong about X")
    assert r["ok"] is True
    content = r["note"]["content"]
    assert "I think X" in content and "wrong about X" in content
    assert r["note"]["amended_age"] is not None


def test_amend_note_tool_unknown_id(impl):
    r = impl.amend_note("nope", "text")
    assert r["ok"] is False and "no note" in r["error"]


def test_amend_note_tool_rejects_blank(impl):
    nid = impl.note_to_self("unchanged")["id"]
    r = impl.amend_note(nid, "   ")
    assert r["ok"] is False
    assert impl.store.get(nid).content == "unchanged"


def test_reads_carry_relative_age(impl):
    impl.note_to_self("x")
    assert impl.list_my_notes()["notes"][0]["age"] == "just now"
    assert impl.search_my_notes("x")["notes"][0]["age"] == "just now"


def test_no_done_or_pin_tool_returns():
    """The fossil manifest advertised mark_note_done against a route that never
    existed. Guard the roster against lifecycle grammar creeping back in."""
    for banned in ("mark_note_done", "pin_note", "unpin_note", "complete_note"):
        assert banned not in TOOL_NAMES


def test_register_tools_wires_all_of_them(tmp_path):
    from mcp.server.fastmcp import FastMCP
    from seren_margin.mcp.tools import register_tools

    cfg = MarginConfig(db_path=str(tmp_path / "notes.db"))
    store = MarginStore(cfg.resolved_db_path())
    mcp = FastMCP("seren-margin-test")
    impl = register_tools(mcp, store, cfg)
    assert isinstance(impl, MarginToolImpl)
