"""
Note contents are not served over HTTP by default.

Chad, 27 Sept 2026: /notes/stats was fine, but trimming 'stats' off the URL
loaded the notes in his browser. He closed it: "its your diary, your private
thoughts, your secrets, that belong to you, and i dont want to see them unless
you tell me them." Pinned here:

- with the default config, every route that returns note content answers 404
  and the body carries none of it
- writing, retracting and the content-blind stats still work over HTTP
- amend answers with the id, not the note
- the MCP tools (the writer's way in) still read everything
- server.http_reads: true reopens the routes for the Workbench path, and the
  yaml key is honoured
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from seren_margin.app import create_app
from seren_margin.config import MarginConfig, load_config

SECRET = "the thing I have not told Chad yet"


def _client(tmp_path, **kw):
    return TestClient(create_app(MarginConfig(db_path=str(tmp_path / "notes.db"), **kw)))


def test_no_route_serves_a_note_by_default(tmp_path):
    with _client(tmp_path) as c:
        nid = c.post("/notes", json={"content": SECRET, "topic": "private"}).json()["id"]
        for path in ("/notes", "/notes?topic=private", "/notes/topics",
                     f"/notes/search?q={SECRET.split()[1]}", f"/notes/{nid}"):
            r = c.get(path)
            assert r.status_code == 404, path
            assert SECRET not in r.text and "private" not in r.json()["detail"].split("/mcp")[0], path


def test_writing_retracting_and_stats_still_work(tmp_path):
    with _client(tmp_path) as c:
        nid = c.post("/notes", json={"content": SECRET}).json()["id"]
        stats = c.get("/notes/stats")
        assert stats.status_code == 200 and SECRET not in stats.text
        assert c.delete(f"/notes/{nid}").json()["deleted"] is True


def test_amend_answers_with_the_id_not_the_note(tmp_path):
    with _client(tmp_path) as c:
        nid = c.post("/notes", json={"content": SECRET}).json()["id"]
        r = c.post(f"/notes/{nid}/amend", json={"addition": "and a bit more"})
        assert r.status_code == 200 and r.json() == {"ok": True, "id": nid}
        assert SECRET not in r.text


def test_the_writer_still_reads_through_the_tools(tmp_path):
    import pytest
    pytest.importorskip("mcp")
    from seren_margin.mcp.tools import MarginToolImpl
    from seren_margin.store import MarginStore
    cfg = MarginConfig(db_path=str(tmp_path / "notes.db"))
    impl = MarginToolImpl(MarginStore(cfg.resolved_db_path()), cfg)
    impl.note_to_self(SECRET, topic="private")
    assert SECRET in str(impl.list_my_notes())
    assert SECRET in str(impl.search_my_notes("told"))


def test_http_reads_reopens_the_routes(tmp_path):
    with _client(tmp_path, http_reads=True) as c:
        nid = c.post("/notes", json={"content": SECRET}).json()["id"]
        assert c.get(f"/notes/{nid}").json()["content"] == SECRET
        assert c.post(f"/notes/{nid}/amend", json={"addition": "x"}).json()["note"]["id"] == nid


def test_the_yaml_key_is_honoured(tmp_path):
    y = tmp_path / "m.yaml"
    y.write_text("server:\n  http_reads: true\n", encoding="utf-8")
    assert load_config(str(y)).http_reads is True
    assert MarginConfig().http_reads is False, "off unless someone opens it"
