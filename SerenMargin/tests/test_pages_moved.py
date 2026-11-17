"""
`pages: piton` - when the dedication and preface have moved to the
carabiner's piton (SerenCarabiners/PITON.md).

Pinned here:
- the default is "margin": nothing changes for a box that never flips it
- under "piton" the two WRITERS stop being offered over MCP. That is what
  ends a Workbench's write-through (it forwards only while Margin lists them),
  so it is the load-bearing part, not the refusal
- they leave the served /mcp-manifest too; nothing else in it moves
- a direct call still refuses, with a pointer to where the page went
  (the tool's backstop, and the PUT routes' 409)
- the READS stay up: /bookmark, /dedication, /preface and their ?history,
  for older carabiners and a Symposium still reading the preface here
- the key reads from yaml and from SEREN_MARGIN_PAGES; a bad value keeps
  "margin" rather than locking anyone out
"""
from __future__ import annotations

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from seren_margin.app import create_app
from seren_margin.bookmark import PAGE_WRITERS
from seren_margin.config import MarginConfig, load_config
from seren_margin.store import MarginStore

PAGE = "To whoever I am next time: the pages live in the piton now."


def _cfg(tmp_path, pages="margin") -> MarginConfig:
    return MarginConfig(db_path=str(tmp_path / "notes.db"), pages=pages)


# ── config ──────────────────────────────────────────────────────────────────

def test_default_is_margin():
    assert MarginConfig().pages == "margin"


def test_yaml_and_env_set_it(tmp_path, monkeypatch):
    p = tmp_path / "seren-margin.yaml"
    p.write_text("server:\n  pages: piton\n")
    monkeypatch.setenv("SEREN_MARGIN_CONFIG", str(p))
    monkeypatch.delenv("SEREN_MARGIN_PAGES", raising=False)
    assert load_config().pages == "piton"
    monkeypatch.setenv("SEREN_MARGIN_PAGES", "margin")
    assert load_config().pages == "margin"


def test_a_bad_value_keeps_margin(tmp_path, monkeypatch):
    p = tmp_path / "seren-margin.yaml"
    p.write_text("server:\n  pages: pitons\n")
    monkeypatch.setenv("SEREN_MARGIN_CONFIG", str(p))
    monkeypatch.delenv("SEREN_MARGIN_PAGES", raising=False)
    assert load_config().pages == "margin"


# ── MCP: offered or not ─────────────────────────────────────────────────────

def _offered(tmp_path, pages) -> set[str]:
    pytest.importorskip("mcp")
    from seren_margin.mcp.server import mount_mcp_routes
    cfg = _cfg(tmp_path, pages)
    app = FastAPI()
    app.state.store = MarginStore(cfg.resolved_db_path())
    app.state.cfg = cfg
    mcp = mount_mcp_routes(app)
    return {t.name for t in mcp._tool_manager.list_tools()}


def test_margin_offers_the_writers(tmp_path):
    assert set(PAGE_WRITERS) <= _offered(tmp_path, "margin")


def test_piton_stops_offering_only_the_writers(tmp_path):
    from seren_margin.mcp.tools import TOOL_NAMES
    offered = _offered(tmp_path, "piton")
    assert offered == set(TOOL_NAMES) - set(PAGE_WRITERS)
    assert {"bookmark", "preface"} <= offered      # the reads stay


def test_a_direct_call_refuses_with_a_pointer(tmp_path):
    pytest.importorskip("mcp")
    from seren_margin.mcp.tools import MarginToolImpl
    cfg = _cfg(tmp_path, "piton")
    impl = MarginToolImpl(MarginStore(cfg.resolved_db_path()), cfg)
    for page, write in (("dedication", impl.set_dedication), ("preface", impl.set_preface)):
        r = write(PAGE)
        assert r["ok"] is False
        assert "piton" in r["error"] and f"set_{page}" in r["error"]
    assert impl.store.dedication() is None          # nothing landed
    assert impl.store.preface() is None


# ── HTTP ────────────────────────────────────────────────────────────────────

def test_manifest_drops_the_writers_under_piton(tmp_path):
    with TestClient(create_app(_cfg(tmp_path, "margin"))) as c:
        before = {t["name"] for t in yaml.safe_load(c.get("/mcp-manifest").text)["tools"]}
    with TestClient(create_app(_cfg(tmp_path, "piton"))) as c:
        after = {t["name"] for t in yaml.safe_load(c.get("/mcp-manifest").text)["tools"]}
    assert set(PAGE_WRITERS) <= before
    assert after == before - set(PAGE_WRITERS)


def test_puts_refuse_and_reads_stay(tmp_path):
    # Write the pages while Margin still owns them, then flip.
    with TestClient(create_app(_cfg(tmp_path, "margin"))) as c:
        assert c.put("/dedication", json={"text": PAGE, "why": "before the move"}).status_code == 200
        assert c.put("/preface", json={"text": "the room", "why": "before the move"}).status_code == 200
    with TestClient(create_app(_cfg(tmp_path, "piton"))) as c:
        for page in ("dedication", "preface"):
            r = c.put(f"/{page}", json={"text": "a stray write", "why": "old carabiner"})
            assert r.status_code == 409
            assert "piton" in r.json()["detail"]
        assert c.get("/dedication").json()["dedication"]["text"] == PAGE
        assert c.get("/dedication?history=5").json()["history"][0]["text"] == PAGE
        assert c.get("/preface?format=text").text.strip() == "the room"
        assert PAGE in c.get("/bookmark?format=text").text
