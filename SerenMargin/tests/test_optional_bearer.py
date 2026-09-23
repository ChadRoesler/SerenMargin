"""
A lock you can add, never one you must.

With no token, Margin behaves exactly as it always has: every route open, the
loopback bind is the guard. With a token pointer on the server block, the
family's middleware turns on for everything that touches a note, and the
three things a neighbour needs before it has credentials - liveness, the
front door, the tool manifest - stay public.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from seren_margin.app import create_app
from seren_margin.config import MarginConfig, load_config
from seren_margin.models import bucket_kind


def _client(tmp_path, **server):
    cfg = MarginConfig(db_path=str(tmp_path / "notes.db"), **server)
    return TestClient(create_app(cfg))


def test_no_token_means_open_exactly_as_before(tmp_path):
    with _client(tmp_path) as c:
        assert c.post("/notes", json={"content": "hi"}).status_code == 200
        assert c.get("/notes").status_code == 200
        assert c.get("/notes/stats").status_code == 200


def test_a_token_locks_every_note_route(tmp_path):
    with _client(tmp_path, bearer_token="sekret") as c:
        assert c.post("/notes", json={"content": "hi"}).status_code == 401
        assert c.get("/notes").status_code == 401
        assert c.get("/notes/search", params={"q": "x"}).status_code == 401
        assert c.get("/notes/stats").status_code == 401
        assert c.delete("/notes/nope").status_code == 401
        auth = {"Authorization": "Bearer sekret"}
        r = c.post("/notes", json={"content": "hi"}, headers=auth)
        assert r.status_code == 200
        note_id = r.json()["id"]
        assert c.get(f"/notes/{note_id}", headers=auth).status_code == 200
        assert c.get("/notes", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_liveness_and_the_manifest_stay_public_with_a_token(tmp_path):
    """Workbench fetches /mcp-manifest before it has any credentials, and it
    holds tool descriptions, never notes."""
    with _client(tmp_path, bearer_token="sekret") as c:
        marker = "the-secret-marker-9b1c"
        assert c.post("/notes", json={"content": marker},
                      headers={"Authorization": "Bearer sekret"}).status_code == 200
        assert c.get("/").status_code == 200
        assert c.get("/health").status_code == 200
        r = c.get("/mcp-manifest")
        assert r.status_code == 200
        assert "note_to_self" in r.text
        assert marker not in r.text, "the manifest is tool descriptions, never notes"


def test_the_token_can_be_a_pointer_to_an_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("MARGIN_TOK", "from-env")
    with _client(tmp_path, bearer_token_env="MARGIN_TOK") as c:
        assert c.get("/notes").status_code == 401
        assert c.get("/notes", headers={"Authorization": "Bearer from-env"}).status_code == 200


def test_the_pointers_load_from_yaml_and_env(tmp_path, monkeypatch):
    for name in ("SEREN_MARGIN_BEARER_TOKEN", "SEREN_MARGIN_BEARER_TOKEN_ENV",
                 "SEREN_MARGIN_BEARER_TOKEN_KEYRING", "SEREN_MARGIN_CONFIG"):
        monkeypatch.delenv(name, raising=False)
    cfg_file = tmp_path / "seren-margin.yaml"
    cfg_file.write_text("server:\n  bearer_token_env: MY_TOK\n", encoding="utf-8")
    cfg = load_config(str(cfg_file))
    assert cfg.bearer_token_env == "MY_TOK"
    monkeypatch.setenv("SEREN_MARGIN_BEARER_TOKEN", "inline-from-env")
    cfg = load_config(str(cfg_file))
    assert cfg.resolve_bearer() == "inline-from-env", "inline beats the env pointer"


def test_an_empty_kind_from_the_manifest_default_is_unkinded():
    """The manifest used to default `kind` to 'observation', so every note
    written through Workbench looked categorised when nobody had chosen
    anything. The default is empty now, and empty buckets as unkinded."""
    assert bucket_kind("") == bucket_kind(None) == "_unkinded"
