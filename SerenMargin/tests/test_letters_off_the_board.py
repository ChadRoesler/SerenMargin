"""
Letters are mail, not the board (18 Oct 2026).

Pinned here:
- list_my_notes leaves letters out unless asked (include_letters=True);
  read_letters is their door, and opening the board never marks one read
- list_my_topics counts letters on their own line, not in the `null` thread,
  and topics + letters still reconcile with the store's total
- search still finds a letter: it is a note underneath
- the same over HTTP: GET /notes (letters=true to include them) and
  GET /notes/topics
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from seren_margin.app import create_app
from seren_margin.config import MarginConfig
from seren_margin.models import MarginNote
from seren_margin.store import MarginStore


@pytest.fixture
def impl(tmp_path):
    pytest.importorskip("mcp")
    from seren_margin.mcp.tools import MarginToolImpl
    cfg = MarginConfig(db_path=str(tmp_path / "notes.db"))
    return MarginToolImpl(MarginStore(cfg.resolved_db_path()), cfg)


def _fill(impl):
    impl.note_to_self("a thought of my own", topic="the room")
    impl.note_to_self("an untopiced one")
    impl.write_letter("to the next me: the draft review went fine")
    impl.write_letter("to the next me: watch the port")


def test_the_board_is_the_notes_and_the_mail_is_apart(impl):
    _fill(impl)
    board = impl.list_my_notes()
    assert board["count"] == 2
    assert all(n["kind"] != "letter" for n in board["notes"])
    everything = impl.list_my_notes(include_letters=True)
    assert everything["count"] == 4
    # looking at the board opened nothing: both letters still wait
    assert impl.read_letters()["count"] == 2


def test_a_topic_filter_still_leaves_the_mail_out_unless_asked(impl):
    impl.note_to_self("kept", topic="t")
    impl.store.add(MarginNote(content="mail", kind="letter", topic="t"))
    assert [n["content"] for n in impl.list_my_notes(topic="t")["notes"]] == ["kept"]
    assert len(impl.list_my_notes(topic="t", include_letters=True)["notes"]) == 2


def test_topics_count_letters_on_their_own_line_and_still_reconcile(impl):
    _fill(impl)
    impl.read_letters(limit=1)                       # one opened, one waiting
    r = impl.list_my_topics()
    assert {t["topic"]: t["count"] for t in r["topics"]} == {"the room": 1, None: 1}
    assert r["letters"] == {"count": 2, "unread": 1}
    assert sum(t["count"] for t in r["topics"]) + r["letters"]["count"] == impl.store.stats().total


def test_search_still_finds_a_letter(impl):
    _fill(impl)
    hits = impl.search_my_notes("port")
    assert hits["count"] == 1 and hits["notes"][0]["kind"] == "letter"


def test_over_http_the_same(tmp_path):
    with TestClient(create_app(MarginConfig(db_path=str(tmp_path / "notes.db"), http_reads=True))) as c:
        c.post("/notes", json={"content": "mine"})
        c.post("/letters", json={"content": "mail"})
        assert [n["content"] for n in c.get("/notes").json()["entries"]] == ["mine"]
        assert len(c.get("/notes?letters=true").json()["entries"]) == 2
        topics = c.get("/notes/topics").json()
        assert topics["letters"] == {"count": 1, "unread": 1}
        assert sum(t["count"] for t in topics["topics"]) == 1
