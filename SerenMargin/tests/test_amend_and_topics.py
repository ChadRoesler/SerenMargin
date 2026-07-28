"""amend_note, list_my_topics, the topic filter, relative age, and the
schema migration that makes amend possible on an existing database.

The migration test is the one that matters most. Everything else here is a
feature; that one is "does Chad's year-old notes.db survive the upgrade", and
the answer has to be provably yes on a service whose owner deliberately isn't
reading the data closely enough to notice damage.
"""
from __future__ import annotations

import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from seren_margin.app import create_app
from seren_margin.config import MarginConfig
from seren_margin.models import AMEND_SEPARATOR, MarginNote, humanize_age
from seren_margin.store import MarginStore


@pytest.fixture
def store(tmp_path):
    return MarginStore(tmp_path / "notes.db")


@pytest.fixture
def client(tmp_path):
    cfg = MarginConfig(db_path=str(tmp_path / "notes.db"))
    with TestClient(create_app(cfg)) as c:
        yield c


# ══ the migration ═══════════════════════════════════════════════════════════

# Exactly the v0.1.0 table: no amended_at, no topic index.
_V010_SCHEMA = """
CREATE TABLE notes (
    id TEXT PRIMARY KEY, content TEXT NOT NULL, topic TEXT,
    kind TEXT, ts REAL NOT NULL, extra TEXT
);
CREATE INDEX idx_notes_ts ON notes(ts DESC);
"""


def _make_v010_db(path, rows=3):
    with sqlite3.connect(path) as conn:
        conn.executescript(_V010_SCHEMA)
        for i in range(rows):
            conn.execute(
                "INSERT INTO notes (id, content, topic, kind, ts, extra) "
                "VALUES (?,?,?,?,?,?)",
                (f"old{i}", f"a thought from before the upgrade {i}",
                 "legacy", "observation", 1700000000.0 + i, "{}"))
        conn.commit()


def test_migration_adds_column_without_losing_rows(tmp_path):
    db = tmp_path / "notes.db"
    _make_v010_db(db, rows=3)

    store = MarginStore(db)  # opening it runs the migration

    notes = store.list_all()
    assert len(notes) == 3, "upgrade lost notes"
    assert all(n.amended_at is None for n in notes)
    assert {n.content for n in notes} == {
        f"a thought from before the upgrade {i}" for i in range(3)}


def test_migration_is_idempotent(tmp_path):
    """Runs on every startup, so running it twice must be a no-op."""
    db = tmp_path / "notes.db"
    _make_v010_db(db, rows=2)
    MarginStore(db)
    s2 = MarginStore(db)
    s3 = MarginStore(db)
    assert len(s3.list_all()) == 2
    cols = {r[1] for r in sqlite3.connect(db).execute("PRAGMA table_info(notes)")}
    assert "amended_at" in cols


def test_migration_backfills_the_search_index(tmp_path):
    """A v0.1.0 db has no notes_fts at all. After upgrade, old notes must be
    findable - otherwise search silently only covers notes written since."""
    db = tmp_path / "notes.db"
    _make_v010_db(db, rows=3)
    store = MarginStore(db)
    hits, finder = store.search("upgrade")
    assert finder == "fts"
    assert len(hits) == 3


def test_legacy_note_can_be_amended_after_migration(tmp_path):
    db = tmp_path / "notes.db"
    _make_v010_db(db, rows=1)
    store = MarginStore(db)
    updated = store.amend("old0", "and here's what I think now")
    assert updated is not None
    assert updated.amended_at is not None
    assert "what I think now" in updated.content


def test_fresh_db_has_the_column_from_schema(tmp_path):
    """Fresh databases get it from SCHEMA, not the migration - both paths must
    land in the same place."""
    MarginStore(tmp_path / "fresh.db")
    cols = {r[1] for r in
            sqlite3.connect(tmp_path / "fresh.db").execute("PRAGMA table_info(notes)")}
    assert "amended_at" in cols


# ══ amend ═══════════════════════════════════════════════════════════════════

def test_amend_appends_and_keeps_the_original(store):
    n = store.add(MarginNote(content="I think the redraft budget is too tight"))
    updated = store.amend(n.id, "turns out 3 was right, I was impatient")
    assert "redraft budget is too tight" in updated.content
    assert "I was impatient" in updated.content
    assert AMEND_SEPARATOR in updated.content


def test_amend_does_not_move_the_original_timestamp(store):
    """Ordering reflects when a thought STARTED. If amend moved ts, every
    amendment would drag an old note to the top and flatten the timeline."""
    n = store.add(MarginNote(content="first", ts=1000.0))
    updated = store.amend(n.id, "more")
    assert updated.ts == 1000.0
    assert updated.amended_at > 1000.0


def test_amend_keeps_board_order_stable(store):
    old = store.add(MarginNote(content="old thought", ts=1000.0))
    store.add(MarginNote(content="new thought", ts=2000.0))
    store.amend(old.id, "an addition, just now")
    assert [n.content.split()[0] for n in store.list_all()] == ["new", "old"]


def test_amend_updates_the_search_index(store):
    """A stale index on a private notes service is invisible until it silently
    fails to find something."""
    n = store.add(MarginNote(content="a note about nothing much"))
    store.amend(n.id, "actually it was about pelicans")
    hits, _ = store.search("pelicans")
    assert [h.id for h in hits] == [n.id]
    # And the original text is still findable.
    assert [h.id for h in store.search("nothing much")[0]] == [n.id]


def test_amend_twice_accretes(store):
    n = store.add(MarginNote(content="one"))
    store.amend(n.id, "two")
    final = store.amend(n.id, "three")
    assert final.content.count(AMEND_SEPARATOR) == 2
    for part in ("one", "two", "three"):
        assert part in final.content


def test_amend_unknown_id_returns_none(store):
    assert store.amend("nope", "text") is None


def test_amend_empty_addition_is_rejected(store):
    n = store.add(MarginNote(content="unchanged"))
    assert store.amend(n.id, "   ") is None
    assert store.get(n.id).content == "unchanged"


def test_amend_route(client):
    nid = client.post("/notes", json={"content": "original"}).json()["id"]
    r = client.post(f"/notes/{nid}/amend", json={"addition": "appended"})
    assert r.status_code == 200
    body = r.json()["note"]
    assert "original" in body["content"] and "appended" in body["content"]
    assert body["amended_age"] is not None


def test_amend_route_404s_on_unknown(client):
    assert client.post("/notes/nope/amend",
                       json={"addition": "x"}).status_code == 404


def test_amend_route_400s_on_blank(client):
    nid = client.post("/notes", json={"content": "x"}).json()["id"]
    assert client.post(f"/notes/{nid}/amend",
                       json={"addition": "  "}).status_code == 400


# ══ topics ══════════════════════════════════════════════════════════════════

def test_list_topics_counts_and_orders_by_recency(store):
    store.add(MarginNote(content="a", topic="chad", ts=1000.0))
    store.add(MarginNote(content="b", topic="chad", ts=1100.0))
    store.add(MarginNote(content="c", topic="serenmargin", ts=3000.0))
    topics = store.list_topics()
    assert [t.topic for t in topics] == ["serenmargin", "chad"]
    assert {t.topic: t.count for t in topics} == {"serenmargin": 1, "chad": 2}


def test_list_topics_collects_untopiced_under_null(store):
    store.add(MarginNote(content="a"))
    store.add(MarginNote(content="b", topic="   "))
    store.add(MarginNote(content="c", topic="real"))
    topics = {t.topic: t.count for t in store.list_topics()}
    assert topics == {None: 2, "real": 1}


def test_topic_counts_reconcile_with_stats(store):
    for i in range(5):
        store.add(MarginNote(content=str(i), topic="t" if i % 2 else None))
    assert sum(t.count for t in store.list_topics()) == store.stats().total


def test_amendment_makes_a_thread_look_live(store):
    """Last-touched counts amendments - a thread you added to yesterday is live
    regardless of when it started."""
    old = store.add(MarginNote(content="a", topic="dormant", ts=1000.0))
    store.add(MarginNote(content="b", topic="recent", ts=2000.0))
    store.amend(old.id, "picked this back up")
    assert [t.topic for t in store.list_topics()] == ["dormant", "recent"]


def test_topics_route(client):
    client.post("/notes", json={"content": "a", "topic": "rhys"})
    r = client.get("/notes/topics")
    assert r.status_code == 200
    assert r.json()["topics"][0]["topic"] == "rhys"
    assert r.json()["topics"][0]["latest_age"]


def test_topics_route_not_shadowed_by_the_id_route(client):
    """Same route-order trap as /notes/stats and /notes/search."""
    r = client.get("/notes/topics")
    assert r.status_code == 200 and "topics" in r.json()


# ══ topic filter ════════════════════════════════════════════════════════════

def test_list_all_filters_by_topic(store):
    store.add(MarginNote(content="a", topic="chad"))
    store.add(MarginNote(content="b", topic="rhys"))
    assert [n.content for n in store.list_all(topic="chad")] == ["a"]


def test_topic_filter_is_case_and_whitespace_insensitive(store):
    """'Chad' and 'chad ' are the same thread to whoever typed them; a filter
    that disagrees is just a silent empty result."""
    store.add(MarginNote(content="a", topic="Chad"))
    for probe in ("chad", "CHAD", "  Chad  "):
        assert len(store.list_all(topic=probe)) == 1


def test_topic_filter_miss_is_empty(store):
    store.add(MarginNote(content="a", topic="chad"))
    assert store.list_all(topic="nonexistent") == []


def test_topic_filter_route(client):
    client.post("/notes", json={"content": "a", "topic": "chad"})
    client.post("/notes", json={"content": "b", "topic": "rhys"})
    assert client.get("/notes", params={"topic": "chad"}).json()["count"] == 1
    assert client.get("/notes").json()["count"] == 2


def test_empty_topic_param_means_whole_board(client):
    """?topic= (blank) must not filter to 'notes whose topic is empty string'."""
    client.post("/notes", json={"content": "a", "topic": "chad"})
    client.post("/notes", json={"content": "b"})
    assert client.get("/notes", params={"topic": ""}).json()["count"] == 2


# ══ relative age ════════════════════════════════════════════════════════════

def test_age_appears_on_every_read_path(client):
    nid = client.post("/notes", json={"content": "x", "topic": "t"}).json()["id"]
    assert client.get(f"/notes/{nid}").json()["age"] == "just now"
    assert client.get("/notes").json()["entries"][0]["age"] == "just now"
    assert client.get("/notes/search",
                      params={"q": "x"}).json()["entries"][0]["age"] == "just now"


def test_amended_age_is_none_until_amended(client):
    nid = client.post("/notes", json={"content": "x"}).json()["id"]
    assert client.get(f"/notes/{nid}").json()["amended_age"] is None


def test_age_never_reaches_stats(client):
    """Per-note data stays off the operator's engine-check surface."""
    client.post("/notes", json={"content": "x"})
    body = client.get("/notes/stats").json()
    assert set(body) == {"total", "kinds"}


def test_topics_never_reach_stats(client):
    """A topic label is a phrase the writer chose - note content in a metadata
    hat. It must not appear in the content-blind surface."""
    client.post("/notes", json={
        "content": "x", "topic": "a revealing thread name"})
    assert "revealing" not in client.get("/notes/stats").text


@pytest.mark.parametrize("delta,expected", [
    (0, "just now"),
    (10, "just now"),
    (120, "2 minutes ago"),
    (60 * 60, "1 hour ago"),
    (5 * 3600, "5 hours ago"),
    (86400, "1 day ago"),
    (3 * 86400, "3 days ago"),
    (8 * 86400, "1 week ago"),
    (40 * 86400, "1 month ago"),
    (400 * 86400, "1 year ago"),
])
def test_humanize_age_buckets(delta, expected):
    now = 1_800_000_000.0
    assert humanize_age(now - delta, now=now) == expected


def test_humanize_age_handles_future_timestamps():
    """Clock skew or a restored backup shouldn't render a negative age - a
    nonsense number in a read-back is worse than a slightly wrong one."""
    now = 1_800_000_000.0
    assert humanize_age(now + 5000, now=now) == "just now"


def test_age_is_computed_not_stored(store):
    """It must reflect real elapsed time, not a value frozen at write."""
    n = store.add(MarginNote(content="x", ts=time.time() - 3 * 86400))
    assert store.get(n.id).age == "3 days ago"
