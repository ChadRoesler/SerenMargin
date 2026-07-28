"""MCP mount tests.

Gated on the `mcp` package. Exercises the real mount path (the three transport
fixes) and the missing-state guard, without wrestling the streamable-HTTP ASGI
transport through a test client.

Sibling of SerenLoci's tests/test_mcp_mount.py - same three footguns, so the
same three assertions.
"""
from __future__ import annotations

import pytest

pytest.importorskip("mcp")

from fastapi import FastAPI

from seren_margin.config import MarginConfig
from seren_margin.mcp.server import mount_mcp_routes, _count_tools
from seren_margin.mcp.tools import TOOL_NAMES
from seren_margin.store import MarginStore


@pytest.fixture
def app_with_store(tmp_path):
    cfg = MarginConfig(db_path=str(tmp_path / "notes.db"))
    app = FastAPI()
    app.state.store = MarginStore(cfg.resolved_db_path())
    app.state.cfg = cfg
    return app


def test_mount_requires_store_and_cfg():
    """Mounting before store/cfg are on app.state is a hard error - the guard
    against mounting outside the lifespan."""
    with pytest.raises(RuntimeError):
        mount_mcp_routes(FastAPI())


def test_mount_succeeds_and_registers_every_tool(app_with_store):
    mcp = mount_mcp_routes(app_with_store)
    assert mcp is not None
    assert _count_tools(mcp) == len(TOOL_NAMES)


def test_mount_sets_streamable_path_to_root(app_with_store):
    """Bug-1 fix: the sub-app's own path is pushed to root so mount('/mcp')
    resolves to exactly '/mcp', not '/mcp/mcp'."""
    mcp = mount_mcp_routes(app_with_store)
    if hasattr(mcp.settings, "streamable_http_path"):
        assert mcp.settings.streamable_http_path == "/"


def test_mount_attaches_route(app_with_store):
    mount_mcp_routes(app_with_store)
    paths = [getattr(r, "path", "") for r in app_with_store.routes]
    assert any("/mcp" in p for p in paths)


def test_mount_path_is_overridable(app_with_store, monkeypatch):
    monkeypatch.setenv("SEREN_MARGIN_MCP_MOUNT", "margin-mcp")
    mount_mcp_routes(app_with_store)
    paths = [getattr(r, "path", "") for r in app_with_store.routes]
    assert any("/margin-mcp" in p for p in paths)


def test_host_check_defaults_off(app_with_store, monkeypatch):
    """Bug-3 fix: DNS-rebinding protection defaults OFF, matching Memory and
    Loci. One service in the family behaving differently under the same env is
    its own kind of bug."""
    monkeypatch.delenv("SEREN_MARGIN_MCP_ALLOWED_HOSTS", raising=False)
    monkeypatch.delenv("SEREN_MARGIN_MCP_ALLOWED_ORIGINS", raising=False)
    mcp = mount_mcp_routes(app_with_store)
    ts = getattr(mcp.settings, "transport_security", None)
    if ts is not None:
        assert ts.enable_dns_rebinding_protection is False


def test_host_check_rearms_from_env(app_with_store, monkeypatch):
    monkeypatch.setenv("SEREN_MARGIN_MCP_ALLOWED_HOSTS", "margin-box:7421")
    mcp = mount_mcp_routes(app_with_store)
    ts = getattr(mcp.settings, "transport_security", None)
    if ts is not None:
        assert ts.enable_dns_rebinding_protection is True
        assert "margin-box:7421" in ts.allowed_hosts


def test_session_manager_is_exposed_for_the_lifespan(app_with_store):
    """Bug-2 fix depends on this handle existing: Starlette never fires a
    mounted sub-app's lifespan, so app.py has to run the task group itself."""
    mcp = mount_mcp_routes(app_with_store)
    assert getattr(mcp, "session_manager", None) is not None


def test_app_starts_with_mcp_mounted(tmp_path):
    """End to end: the real create_app lifespan mounts MCP and the HTTP API
    still works alongside it."""
    from fastapi.testclient import TestClient
    from seren_margin.app import create_app

    cfg = MarginConfig(db_path=str(tmp_path / "notes.db"))
    with TestClient(create_app(cfg)) as c:
        assert c.get("/health").json()["ok"] is True
        assert c.post("/notes", json={"content": "mounted"}).status_code == 200
        assert any("/mcp" in getattr(r, "path", "")
                   for r in c.app.routes)
