"""Manifest <-> implementation parity.

THE BUG THIS PREVENTS, stated plainly so nobody deletes this file wondering
what it's for: the shipped mcp-manifest.yaml advertised a `mark_note_done` tool
invoking `POST /notes/{id}/done`, and a `pinned` parameter on `note_to_self`.
Neither existed. The done-route was removed when the lifecycle was; the manifest
kept promising it. Anything that remote-imported the manifest got a tool that
404s and a field that vanished silently into pydantic's extra-field handling.

Nobody caught it because a manifest is data, not code - it can't fail to
compile, and the operator (by design) isn't reading this service's traffic.
So it gets asserted instead.

These tests run WITHOUT the mcp extra: parity is a property of the yaml file and
the roster constant, and it should be checked on every install.
"""
from __future__ import annotations

import re
from importlib.resources import files

import pytest
import yaml
from fastapi.testclient import TestClient

from seren_margin.app import create_app
from seren_margin.config import MarginConfig


@pytest.fixture(scope="module")
def manifest() -> dict:
    raw = (files("seren_margin") / "mcp-manifest.yaml").read_text(encoding="utf-8")
    return yaml.safe_load(raw)


@pytest.fixture
def client(tmp_path):
    cfg = MarginConfig(db_path=str(tmp_path / "notes.db"))
    with TestClient(create_app(cfg)) as c:
        yield c


def _tools(manifest: dict) -> dict[str, dict]:
    return {t["name"]: t for t in manifest["tools"]}


def test_manifest_parses(manifest):
    assert manifest["schema_version"] == 1
    assert manifest["tools"]


def test_roster_matches_the_implementation(manifest):
    """The load-bearing assertion. The manifest may not advertise a tool the
    code doesn't implement, and may not omit one it does."""
    pytest.importorskip("mcp")
    from seren_margin.mcp.tools import TOOL_NAMES

    assert set(_tools(manifest)) == set(TOOL_NAMES)


def test_manifest_advertises_exactly_the_six_tools(manifest):
    """Belt to the above, and runs without the mcp extra installed."""
    assert set(_tools(manifest)) == {
        "note_to_self", "list_my_notes", "list_my_topics",
        "search_my_notes", "amend_note", "retract_note"}


def test_no_fossil_tools_return(manifest):
    names = set(_tools(manifest))
    for fossil in ("mark_note_done", "pin_note", "unpin_note"):
        assert fossil not in names


def test_no_fossil_parameters_return(manifest):
    """`pinned` was advertised on note_to_self against a model that has no such
    field - pydantic dropped it silently, so the caller never learned."""
    params = {p["name"] for p in _tools(manifest)["note_to_self"]["parameters"]}
    assert "pinned" not in params
    assert params == {"content", "topic", "kind"}


def test_stats_is_not_advertised(manifest):
    """The operator's engine-check surface stays off the AI-facing manifest."""
    for tool in manifest["tools"]:
        assert "stats" not in tool["invoke"]["path"]


def _shape(path: str) -> str:
    """Normalise a path to its SHAPE for comparison: query string dropped and
    every {placeholder} collapsed to {}.

    The names deliberately don't have to match. The manifest's `{id}` is a
    manifest-level template slot bound to that tool's own `parameters` list;
    FastAPI's `{note_id}` is a function argument name. Both render to
    /notes/abc123 at call time, and forcing them to agree would be coupling two
    unrelated namespaces. What MUST agree is the method, the literal segments,
    and the arity - which is what this compares.
    """
    return re.sub(r"\{[^}]+\}", "{}", path.split("?", 1)[0])


def test_every_advertised_route_exists_on_the_app(manifest, client):
    """The actual fossil check: every path+method the manifest promises must
    correspond to a real route on the app.

    `mark_note_done` -> POST /notes/{id}/done would have failed here.
    """
    real = set()
    for route in client.app.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        if path:
            for m in methods:
                real.add((m.upper(), _shape(path)))

    for tool in manifest["tools"]:
        inv = tool["invoke"]
        key = (inv["method"].upper(), _shape(inv["path"]))
        assert key in real, (
            f"{tool['name']} advertises {inv['method']} {inv['path']}, "
            f"which has no matching route on the app")


def test_every_path_placeholder_is_a_declared_parameter(manifest):
    """The invariant the name-comparison was groping at: a {slot} in the path
    or body template must be fillable from that tool's own parameters. An
    unfillable slot ships a literal '{query}' in the URL.
    """
    for tool in manifest["tools"]:
        declared = {p["name"] for p in tool["parameters"]}
        inv = tool["invoke"]
        template = inv["path"] + inv.get("body_template", "")
        for slot in re.findall(r"\{(\w+)\}", template):
            assert slot in declared, (
                f"{tool['name']} template references {{{slot}}} but declares "
                f"only {sorted(declared)}")


def test_every_declared_parameter_is_actually_used(manifest):
    """The other direction: a parameter nothing substitutes is a parameter the
    caller fills in and the service never sees. That's how `pinned` looked
    functional while being silently dropped."""
    for tool in manifest["tools"]:
        inv = tool["invoke"]
        template = inv["path"] + inv.get("body_template", "")
        slots = set(re.findall(r"\{(\w+)\}", template))
        for p in tool["parameters"]:
            assert p["name"] in slots, (
                f"{tool['name']} declares parameter {p['name']!r} that no "
                f"template substitutes - it would be silently discarded")


def test_amend_route_advertised_by_manifest_works(client):
    """The workbench path's newest tool, exercised against the real route."""
    nid = client.post("/notes", json={"content": "original"}).json()["id"]
    r = client.post(f"/notes/{nid}/amend", json={"addition": "appended"})
    assert r.status_code == 200
    assert "appended" in r.json()["note"]["content"]


def test_topics_route_advertised_by_manifest_works(client):
    client.post("/notes", json={"content": "x", "topic": "thread"})
    r = client.get("/notes/topics")
    assert r.status_code == 200
    assert r.json()["topics"][0]["topic"] == "thread"


def test_no_auto_surface_tool_exists(manifest):
    """Stated design boundary, asserted so it can't drift in as a helpful
    addition: nothing here pushes notes at the reader unasked."""
    names = set(_tools(manifest))
    for banned in ("surface_notes", "relevant_notes", "suggest_notes",
                   "auto_recall", "inject_notes"):
        assert banned not in names


def test_retract_route_actually_deletes(client):
    """Prove the DELETE the manifest advertises does the thing the description
    claims (hard delete, gone from listing)."""
    nid = client.post("/notes", json={"content": "x"}).json()["id"]
    assert client.delete(f"/notes/{nid}").status_code == 200
    assert client.get(f"/notes/{nid}").status_code == 404


def test_search_route_exists_and_answers(client):
    """The manifest's one query-string tool. Whatever the remote-import loader
    does with `?q={query}`, the route itself must be real and working."""
    client.post("/notes", json={"content": "a note about pelicans"})
    r = client.get("/notes/search", params={"q": "pelicans"})
    assert r.status_code == 200
    assert r.json()["count"] == 1


def test_search_route_is_not_shadowed_by_the_id_route(client):
    """Route-order regression: /notes/search must not be matched as
    /notes/{note_id} with note_id='search'. That would 404 instead of
    searching, and it is exactly the bug /notes/stats already had to dodge."""
    r = client.get("/notes/search", params={"q": "anything"})
    assert r.status_code == 200
    assert "entries" in r.json()


def test_stats_route_still_not_shadowed(client):
    r = client.get("/notes/stats")
    assert r.status_code == 200
    assert "total" in r.json()
