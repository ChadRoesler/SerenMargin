"""HTTP-only mode - the [mcp]-extra-not-installed path.

The whole point of the conditional mount is that SerenMargin runs perfectly
well without the mcp SDK: the HTTP API and the /mcp-manifest workbench path are
both fully functional. That fallback is the common case for anyone who installs
plain `seren-margin`, so it gets tested rather than assumed.

Simulated with monkeypatch.setattr, NOT sys.modules manipulation - the latter
leaks across tests and produces failures in unrelated files that take an hour to
trace back here. (Learned the hard way in SerenMemory.)
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import seren_margin.app as app_mod
from seren_margin.app import create_app
from seren_margin.config import MarginConfig


@pytest.fixture
def cfg(tmp_path):
    return MarginConfig(db_path=str(tmp_path / "notes.db"))


def _break_mount(monkeypatch, exc: Exception):
    """Make the lifespan's `from .mcp.server import mount_mcp_routes` resolve to
    something that raises. The import inside the lifespan pulls the attribute
    off the module at call time, so patching the module attribute is enough."""
    pytest.importorskip("mcp")
    import seren_margin.mcp.server as server_mod

    def boom(app):
        raise exc

    monkeypatch.setattr(server_mod, "mount_mcp_routes", boom)


def _output(capsys) -> str:
    """Diagnostics go to stderr via _diag.diag (flushed, so a supervisor can't
    lose them). Read both streams so this doesn't re-break if that moves."""
    cap = capsys.readouterr()
    return cap.out + cap.err


def test_app_starts_when_mcp_import_fails(cfg, monkeypatch, capsys):
    """ImportError -> HTTP-only mode, no crash."""
    _break_mount(monkeypatch, ImportError("No module named 'mcp'"))
    with TestClient(create_app(cfg)) as c:
        assert c.get("/health").json()["ok"] is True
    assert "HTTP-only mode" in _output(capsys)


def test_app_starts_when_mount_raises_unexpectedly(cfg, monkeypatch, capsys):
    """A non-ImportError from the mount must also not take the service down.
    Private notes staying reachable matters more than the MCP surface being up.
    """
    _break_mount(monkeypatch, RuntimeError("transport exploded"))
    with TestClient(create_app(cfg)) as c:
        assert c.get("/health").json()["ok"] is True
    assert "MCP mount failed" in _output(capsys)


def test_full_http_api_works_without_mcp(cfg, monkeypatch):
    """Every route stays functional in HTTP-only mode - write, list, search,
    stats, fetch, retract."""
    _break_mount(monkeypatch, ImportError("No module named 'mcp'"))
    with TestClient(create_app(cfg)) as c:
        nid = c.post("/notes", json={
            "content": "still works without the SDK",
            "topic": "fallback"}).json()["id"]

        assert c.get("/notes").json()["count"] == 1
        assert c.get("/notes/search", params={"q": "SDK"}).json()["count"] == 1
        assert c.get("/notes/stats").json()["total"] == 1
        assert c.get(f"/notes/{nid}").status_code == 200
        assert c.delete(f"/notes/{nid}").status_code == 200


def test_manifest_still_served_without_mcp(cfg, monkeypatch):
    """The workbench remote-import path does NOT depend on the mcp extra - it's
    just YAML over HTTP. Someone running the full constellation shouldn't have
    to install an SDK this service won't use."""
    _break_mount(monkeypatch, ImportError("No module named 'mcp'"))
    with TestClient(create_app(cfg)) as c:
        r = c.get("/mcp-manifest")
        assert r.status_code == 200
        assert "note_to_self" in r.text
        # Placeholders substituted, not shipped raw.
        assert "__BASE_URL__" not in r.text
        assert "__VERSION__" not in r.text


def test_no_mcp_route_when_mount_fails(cfg, monkeypatch):
    _break_mount(monkeypatch, ImportError("No module named 'mcp'"))
    with TestClient(create_app(cfg)) as c:
        mounted = [r for r in c.app.routes
                   if getattr(r, "path", "") == "/mcp"]
        assert mounted == []


def test_module_imports_without_mcp_installed():
    """seren_margin.app must import cleanly with no mcp anywhere - the import of
    .mcp.server is deliberately INSIDE the lifespan, not at module top."""
    src = (app_mod.__file__)
    with open(src, "r", encoding="utf-8") as f:
        head = f.read().split("def create_app", 1)[0]
    assert "from .mcp" not in head, (
        "mcp imported at module scope - that breaks plain `pip install "
        "seren-margin` at import time, not at mount time")
