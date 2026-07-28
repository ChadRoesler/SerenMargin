"""The content-blind contract for GET /notes/stats.

WHY THIS FILE EXISTS: `kind` is free-form on write, and the stats endpoint used
to echo raw kind strings straight into its response dict. That made the
CONTENT-BLIND promise false for anyone who ever used the field as a second
content line - which, for an unconstrained text field on a private-notes
service, is a matter of when rather than whether.

The endpoint exists specifically so an operator can confirm the service works
WITHOUT reading the notes. If it can leak note text, it fails at the one job
that justified building it.

These tests are the contract. They should be hard to delete quietly.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from seren_margin.app import create_app
from seren_margin.config import MarginConfig
from seren_margin.models import KNOWN_KINDS, bucket_kind


@pytest.fixture
def client(tmp_path):
    cfg = MarginConfig(db_path=str(tmp_path / "test.db"))
    app = create_app(cfg)
    with TestClient(app) as c:
        yield c


SECRET = "chad has not slept properly in nine days and it shows"


def test_freeform_kind_never_reaches_stats(client):
    """The load-bearing one. A kind used as prose must not appear in stats."""
    r = client.post("/notes", json={"content": "x", "kind": SECRET})
    assert r.status_code == 200

    body = client.get("/notes/stats").text
    assert SECRET not in body
    for word in ("slept", "nine", "shows", "chad"):
        assert word not in body.lower(), f"leaked {word!r} into stats"


def test_unknown_kinds_bucket_to_other(client):
    for k in ("wondering about something", "3am thought", "??"):
        client.post("/notes", json={"content": "x", "kind": k})
    kinds = client.get("/notes/stats").json()["kinds"]
    assert kinds == {"other": 3}


def test_known_kinds_survive_for_engine_check(client):
    """Bucketing must not flatten everything to 'other' - the operator still
    needs a usable shape read, or the endpoint is decoration."""
    client.post("/notes", json={"content": "a", "kind": "reminder"})
    client.post("/notes", json={"content": "b", "kind": "reminder"})
    client.post("/notes", json={"content": "c", "kind": "question"})
    kinds = client.get("/notes/stats").json()["kinds"]
    assert kinds == {"reminder": 2, "question": 1}


def test_missing_kind_is_its_own_bucket(client):
    client.post("/notes", json={"content": "a"})
    client.post("/notes", json={"content": "b", "kind": "   "})
    stats = client.get("/notes/stats").json()
    assert stats["total"] == 2
    assert stats["kinds"] == {"_unkinded": 2}


def test_stats_never_contains_note_content_or_topic(client):
    client.post("/notes", json={
        "content": "the actual private thought",
        "topic": "a revealing topic name",
        "kind": "observation"})
    body = client.get("/notes/stats").text
    assert "private thought" not in body
    assert "revealing" not in body


def test_stats_response_keys_are_closed_set(client):
    """A future field added to NoteStats should have to walk past this test.
    Content leaks arrive as innocuous-looking additions."""
    client.post("/notes", json={"content": "x"})
    assert set(client.get("/notes/stats").json().keys()) == {"total", "kinds"}


# ── the bucketer, directly ──────────────────────────────────────────────────

def test_bucket_kind_normalises_case_and_whitespace():
    """'Reminder ' and 'reminder' are one intent; splitting them would leak the
    writer's typing habits into a surface meant to reveal nothing."""
    assert bucket_kind("Reminder ") == "reminder"
    assert bucket_kind("  OBSERVATION") == "observation"


def test_bucket_kind_none_and_blank():
    assert bucket_kind(None) == "_unkinded"
    assert bucket_kind("") == "_unkinded"
    assert bucket_kind("   ") == "_unkinded"


def test_bucket_kind_unknown_is_other():
    assert bucket_kind("a whole sentence about something") == "other"


def test_every_known_kind_maps_to_itself():
    for k in KNOWN_KINDS:
        assert bucket_kind(k) == k
