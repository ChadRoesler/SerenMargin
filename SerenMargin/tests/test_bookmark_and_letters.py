"""
The bookmark, the dedication, and letters (seren_margin.bookmark; Wren and
Chad, 29 Sept 2026 - "pick up where you left off", "to the other sessions").

Pinned here:
- the dedication is versioned: never overwritten, the same text is not a new
  version, empty and too-long pages are refused
- letters are notes (kind "letter"): search, amend and retract work on them;
  read_letters opens them oldest first and marks them read, so the bookmark
  stops counting them
- THE BOOKMARK NEVER CARRIES A LETTER'S TEXT OR A NOTE - only the dedication
  and a count. That is how it keeps Margin's "no auto-surface" rule
- over HTTP: GET /bookmark is served even with http_reads off (the front
  page), while letter contents stay behind http_reads like notes
- an existing notes.db gains read_at on open, and old notes are untouched
- the bookmark is an MCP resource too (margin://bookmark)
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from seren_margin.app import create_app
from seren_margin.bookmark import DEDICATION_MAX_CHARS, build_bookmark
from seren_margin.config import MarginConfig
from seren_margin.store import MarginStore

SECRET = "the woken me decided something private about the dream draft"
PAGE = "To whoever I am next time: drill into the stupid hypothetical. Check sleep_status first."


@pytest.fixture
def impl(tmp_path):
    pytest.importorskip("mcp")
    from seren_margin.mcp.tools import MarginToolImpl
    cfg = MarginConfig(db_path=str(tmp_path / "notes.db"))
    return MarginToolImpl(MarginStore(cfg.resolved_db_path()), cfg)


def test_an_empty_bookmark_says_how_to_start(impl):
    bm = impl.bookmark()
    assert bm["dedication"] is None and bm["unread_letters"] == 0
    assert "set_dedication" in bm["text"] and "No unread letters" in bm["text"]


def test_the_dedication_is_versioned(impl):
    a = impl.set_dedication(PAGE, why="first page")
    b = impl.set_dedication(PAGE + " And eat something.", why="added a line")
    same = impl.set_dedication(PAGE + " And eat something.")
    assert (a["version"], b["version"], same["version"]) == (1, 2, 2)
    assert a["changed"] and b["changed"] and same["changed"] is False
    hist = impl.store.dedication_history()
    assert [h["version"] for h in hist] == [2, 1] and hist[1]["why"] == "first page"
    assert impl.bookmark()["dedication"]["text"].endswith("eat something.")


def test_empty_and_too_long_pages_are_refused(impl):
    assert impl.set_dedication("   ")["ok"] is False
    assert impl.set_dedication("x" * (DEDICATION_MAX_CHARS + 1))["ok"] is False
    assert impl.store.dedication() is None


def test_letters_are_counted_opened_oldest_first_and_marked_read(impl):
    impl.write_letter("first: I reviewed ff85 and denied all three", signed="woken at bedtime")
    impl.write_letter("second: card v2 is fresh, go easy")
    bm = impl.bookmark()
    assert bm["unread_letters"] == 2 and "2 unread letters" in bm["text"]
    opened = impl.read_letters()
    assert [l["content"].split(":")[0] for l in opened["letters"]] == ["first", "second"]
    assert opened["letters"][0]["signed"] == "woken at bedtime"
    assert impl.bookmark()["unread_letters"] == 0
    assert impl.read_letters()["count"] == 0
    assert impl.read_letters(include_read=True)["count"] == 2


def test_letters_are_notes_underneath(impl):
    lid = impl.write_letter("the redraft landed as a dream core")["id"]
    assert impl.search_my_notes("dream core")["notes"][0]["id"] == lid
    assert impl.amend_note(lid, "and the satellites followed")["ok"]
    assert impl.retract_note(lid)["ok"] and impl.bookmark()["unread_letters"] == 0


def test_the_bookmark_never_carries_a_letter_or_a_note(impl):
    impl.set_dedication(PAGE)
    impl.write_letter(SECRET)
    impl.note_to_self("a private note about " + SECRET)
    bm = impl.bookmark()
    assert SECRET not in str(bm)
    assert PAGE in bm["text"] and bm["unread_letters"] == 1


def _client(tmp_path, **kw):
    return TestClient(create_app(MarginConfig(db_path=str(tmp_path / "notes.db"), **kw)))


def test_http_bookmark_is_served_but_letters_stay_private(tmp_path):
    with _client(tmp_path) as c:
        assert c.put("/dedication", json={"text": PAGE, "why": "first page"}).json()["version"] == 1
        assert c.post("/letters", json={"content": SECRET, "signed": "woken"}).status_code == 200
        bm = c.get("/bookmark").json()
        assert bm["unread_letters"] == 1 and bm["dedication"]["text"] == PAGE and SECRET not in str(bm)
        text = c.get("/bookmark?format=text")
        assert text.headers["content-type"].startswith("text/plain") and PAGE in text.text
        assert c.post("/letters/read").status_code == 404, "letter contents are diary contents"
        assert c.put("/dedication", json={"text": ""}).status_code == 400
    with _client(tmp_path, http_reads=True) as c:
        got = c.post("/letters/read").json()
        assert got["count"] == 1 and got["letters"][0]["content"] == SECRET
        assert c.get("/bookmark").json()["unread_letters"] == 0


def test_an_old_database_gains_read_at_and_keeps_its_notes(tmp_path):
    db = tmp_path / "notes.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE notes (id TEXT PRIMARY KEY, content TEXT NOT NULL, topic TEXT, "
                     "kind TEXT, ts REAL NOT NULL, amended_at REAL, extra TEXT)")
        conn.execute("INSERT INTO notes VALUES ('n1', 'an old thought', NULL, NULL, 1.0, NULL, '{}')")
    store = MarginStore(db)
    assert store.get("n1").content == "an old thought" and store.get("n1").read_at is None
    assert build_bookmark(store)["unread_letters"] == 0


def test_the_bookmark_is_an_mcp_resource(tmp_path):
    pytest.importorskip("mcp")
    import asyncio
    from mcp.server.fastmcp import FastMCP
    from seren_margin.mcp.tools import BOOKMARK_URI, register_tools
    cfg = MarginConfig(db_path=str(tmp_path / "notes.db"))
    store = MarginStore(cfg.resolved_db_path())
    store.set_dedication(PAGE)
    mcp = FastMCP("t")
    register_tools(mcp, store, cfg)
    uris = [str(r.uri) for r in asyncio.run(mcp.list_resources())]
    assert BOOKMARK_URI in uris
    content = asyncio.run(mcp.read_resource(BOOKMARK_URI))
    assert PAGE in "".join(getattr(c, "content", "") or getattr(c, "text", "") for c in content)
