"""Smoke test for SerenMargin. Validates basic CRUD against tmpdir sqlite.

Critically asserts that /notes/stats is CONTENT-BLIND - the response body
must not contain any note text.

No lifecycle here: notes have no pin/expiry/done and live until deleted.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from seren_margin.app import create_app
from seren_margin.config import MarginConfig


@pytest.fixture
def client(tmp_path):
    cfg = MarginConfig(db_path=str(tmp_path / "test.db"))
    app = create_app(cfg)
    with TestClient(app) as c:
        yield c


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_write_list_get_delete_cycle(client):
    # Write a note
    r = client.post("/notes", json={"content": "ask Chad about the supersede gap"})
    assert r.status_code == 200
    note_id = r.json()["id"]
    assert note_id

    # Shows up in the list
    r = client.get("/notes")
    entries = r.json()["entries"]
    assert any(e["id"] == note_id for e in entries)

    # Fetch it directly
    r = client.get(f"/notes/{note_id}")
    assert r.status_code == 200
    assert r.json()["content"] == "ask Chad about the supersede gap"

    # Delete it
    r = client.delete(f"/notes/{note_id}")
    assert r.status_code == 200

    # Gone
    r = client.get(f"/notes/{note_id}")
    assert r.status_code == 404

    # And gone from the list
    r = client.get("/notes")
    assert not any(e["id"] == note_id for e in r.json()["entries"])


def test_topic_and_kind_round_trip(client):
    r = client.post("/notes", json={
        "content": "watch for the hedge-as-refusal trap",
        "topic": "rapport",
        "kind": "observation",
    })
    note_id = r.json()["id"]
    r = client.get(f"/notes/{note_id}")
    body = r.json()
    assert body["topic"] == "rapport"
    assert body["kind"] == "observation"
    assert body["ts"] is not None


def test_stats_is_content_blind(client):
    """Critical: stats endpoint must not leak note contents."""
    client.post("/notes", json={
        "content": "private thought about hedging behavior",
        "kind": "observation",
    })
    client.post("/notes", json={
        "content": "remember to ask about supersede gap",
        "kind": "reminder",
    })

    r = client.get("/notes/stats")
    assert r.status_code == 200
    stats = r.json()
    assert stats["total"] == 2
    assert stats["kinds"]["observation"] == 1
    assert stats["kinds"]["reminder"] == 1

    # The whole response body must not contain note text
    body_text = r.text
    assert "hedging" not in body_text
    assert "supersede" not in body_text
    assert "private thought" not in body_text


def test_empty_content_rejected(client):
    r = client.post("/notes", json={"content": "  "})
    assert r.status_code == 400


def test_missing_note_returns_404(client):
    r = client.get("/notes/does-not-exist")
    assert r.status_code == 404
    r = client.delete("/notes/does-not-exist")
    assert r.status_code == 404


def test_root_advertises_ethos(client):
    """The service's identity is in /. Validates ethos string didn't drift."""
    r = client.get("/")
    info = r.json()
    assert info["name"] == "SerenMargin"
    assert "opt-in by deploy" in info["ethos"]
    assert info["stats_endpoint"] == "/notes/stats"