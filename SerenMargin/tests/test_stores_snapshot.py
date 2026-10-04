"""
Margin snapshots its own database, and keeps the snapshots to itself.

The margin is private (http_reads is off: 'its your diary... i dont want to
see them unless you tell me them'). A backup must not become a way round
that. Pinned here:

- GET /stores and a snapshot's listing say nothing of what a note says
- the snapshot is the database copied whole; there is no plain-text export
- the archive - the diary itself - is refused over HTTP unless
  backup.allow_pull is on, and the yaml key is honoured
- backup.enabled: false turns it off; no route restores or deletes
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from seren_margin.app import create_app
from seren_margin.config import BackupConfig, MarginConfig, load_config

SECRET = "the thing I have not told the user yet"


def _client(tmp_path, **backup):
    cfg = MarginConfig(db_path=str(tmp_path / "store" / "notes.db"), backup=BackupConfig(every_hours=0, **backup))
    (tmp_path / "store").mkdir(exist_ok=True)
    return TestClient(create_app(cfg))


def test_listing_and_taking_a_snapshot_say_nothing_of_the_notes(tmp_path):
    with _client(tmp_path) as c:
        c.post("/notes", json={"content": SECRET, "topic": "private"})
        d = c.get("/stores")
        assert d.status_code == 200 and SECRET not in d.text and "private" not in d.text.replace("private notes", "")
        assert d.json()["stores"][0]["kind"] == "sqlite"
        assert Path(d.json()["snapshots"]["dir"]) == (tmp_path / "store" / "backups" / "seren-margin").resolve()
        r = c.post("/stores/snapshot", json={"reason": "by hand"})
        assert r.status_code == 200 and SECRET not in r.text
        snap = r.json()["snapshot"]
        assert snap["counts"] == {"notes": 1}
        root = Path(snap["path"])
        man = (root / "manifest.json").read_text(encoding="utf-8")
        assert SECRET not in man and json.loads(man)["exports"] == {}, "no plain-text export of a diary"
        assert not (root / "export").exists()
        con = sqlite3.connect(root / "raw" / "notes" / "notes.db")
        assert con.execute("SELECT content FROM notes").fetchone()[0] == SECRET, "the database, whole"
        con.close()
        assert SECRET not in c.get("/stores/snapshots").text


def test_the_archive_stays_on_the_box_unless_asked_to_travel(tmp_path):
    with _client(tmp_path) as c:
        c.post("/notes", json={"content": SECRET})
        sid = c.post("/stores/snapshot").json()["snapshot"]["id"]
        r = c.get(f"/stores/snapshots/{sid}/archive")
        assert r.status_code == 403 and "whole diary" in r.json()["error"] and "allow_pull" in r.json()["error"]
        assert SECRET.encode() not in r.content
    with _client(tmp_path, allow_pull=True) as c:
        sid = c.post("/stores/snapshot").json()["snapshot"]["id"]
        r = c.get(f"/stores/snapshots/{sid}/archive")
        assert r.status_code == 200 and r.headers["content-type"] == "application/gzip"


def test_the_yaml_block_is_honoured(tmp_path):
    y = tmp_path / "m.yaml"
    y.write_text("server:\n  db_path: '%s'\nbackup:\n  allow_pull: true\n  every_hours: 6\n  keep_daily: 3\n"
                 % str(tmp_path / "notes.db").replace("\\", "/"), encoding="utf-8")
    cfg = load_config(str(y))
    assert cfg.backup.allow_pull is True and cfg.backup.every_hours == 6 and cfg.backup.keep_daily == 3
    assert MarginConfig().backup.allow_pull is False, "off unless someone turns it on"


def test_off_and_no_restore_or_delete(tmp_path):
    with _client(tmp_path, enabled=False) as c:
        assert c.get("/stores").status_code == 404
    with _client(tmp_path) as c:
        sid = c.post("/stores/snapshot").json()["snapshot"]["id"]
        for path in ("/stores/restore", f"/stores/snapshots/{sid}"):
            assert c.post(path).status_code in (404, 405) and c.delete(path).status_code in (404, 405)


def test_a_rehearsal_counts_the_notes_and_says_nothing_of_them(tmp_path):
    """A restore's dry run (seren_sinew.stores). It needs no allow_pull: the
    copy never leaves the box and the report is a count."""
    from seren_sinew.stores import pack_snapshot
    with _client(tmp_path) as c:
        c.post("/notes", json={"content": SECRET, "topic": "private"})
        snap = c.post("/stores/snapshot").json()["snapshot"]
        c.post("/notes", json={"content": "written after", "topic": "later"})
        r = c.post(f"/stores/snapshots/{snap['id']}/rehearse")
        assert r.status_code == 200 and SECRET not in r.text, r.text
        rep = r.json()
        assert rep["ok"] and rep["check"] == {"counts": {"notes": 1}} and rep["live_store_touched"] is False, rep
        assert len(c.app.state.store.list_all()) == 2
        r = c.post("/stores/rehearse", content=pack_snapshot(Path(snap["path"])))
        assert r.status_code == 200 and r.json()["ok"] and SECRET not in r.text, "a stash can send it back to be checked"
        assert not list((Path(snap["path"]).parent / ".rehearsal").iterdir())
