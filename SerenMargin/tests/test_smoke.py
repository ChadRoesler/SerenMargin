"""Smoke test for SerenMargin. Validates basic CRUD against tmpdir sqlite.

The deep content-blind assertions live in test_stats_content_blind.py; the one
here is the shallow "does the endpoint leak the obvious thing" check that should
fail first and loudest.

No lifecycle: notes have no pin/expiry/done and live until retracted.
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


def test_root_reports_finder(client):
    """Which search engine answered is operator-visible at the front door -
    a Nano that silently fell back to LIKE should be diagnosable in one curl."""
    body = client.get("/").json()
    assert body["name"] == "SerenMargin"
    assert body["finder"] in {"fts", "like"}


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


def test_topic_round_trips(client):
    """topic is a real field the model can set - it was missing from the old
    manifest entirely, so it went unexercised."""
    nid = client.post("/notes", json={
        "content": "x", "topic": "serenmargin"}).json()["id"]
    assert client.get(f"/notes/{nid}").json()["topic"] == "serenmargin"


def test_empty_content_rejected(client):
    assert client.post("/notes", json={"content": "   "}).status_code == 400


def test_delete_unknown_id_404s(client):
    assert client.delete("/notes/not-a-real-id").status_code == 404


def test_search_endpoint(client):
    client.post("/notes", json={"content": "a thought about pelicans"})
    client.post("/notes", json={"content": "an unrelated thought"})
    r = client.get("/notes/search", params={"q": "pelicans"})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["finder"] in {"fts", "like"}
    assert "pelicans" in body["entries"][0]["content"]


def test_stats_is_content_blind(client):
    """Shallow guard - the full contract is in test_stats_content_blind.py."""
    secret = "supercalifragilistic-private-thought"
    client.post("/notes", json={"content": secret, "kind": "observation"})
    r = client.get("/notes/stats")
    assert r.status_code == 200
    assert secret not in r.text
    assert r.json()["total"] == 1


def test_mcp_manifest_served_and_substituted(client):
    r = client.get("/mcp-manifest")
    assert r.status_code == 200
    assert "__BASE_URL__" not in r.text
    assert "__VERSION__" not in r.text
    assert "note_to_self" in r.text
