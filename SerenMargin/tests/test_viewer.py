"""
The margin's viewer: the engine check in the family's clothes, and nothing
else. 15 Oct 2026 - the plain JSON status read like a mismatched spine next
to the Halls and the Loci viewer, so the margin got a page too, with one
rule: it shows counts and backups, never a note.

Pinned here:
- GET /viewer is a page on the Seren baseplate, public like every viewer
- its script calls exactly three routes: "/", "/notes/stats", "/stores" -
  every one content-blind by construction - and names no other
- the page's text carries no route that serves a note, and no note
- the token-protected routes it reads are still protected
"""
from __future__ import annotations

import re
from importlib.resources import files

from fastapi.testclient import TestClient

from seren_margin.app import create_app
from seren_margin.config import MarginConfig

SECRET = "the thing I have not told anyone yet"
ALLOWED = {"/", "/notes/stats", "/stores"}
NEVER = ("/notes/search", "/notes/topics", "/notes?", "/notes/{", "/letters", "/bookmark", "/dedication",
         "/keepsakes", "/mcp")


def _calls(script: str) -> set[str]:
    """Every route the leaf script hands to the shell's api()."""
    return set(re.findall(r"""api\(\s*["'`]([^"'`]+)["'`]""", script))


def test_the_script_reads_three_content_blind_routes_and_nothing_else():
    script = (files("seren_margin") / "viewer" / "ui" / "scripts.js").read_text(encoding="utf-8")
    assert _calls(script) == ALLOWED
    assert "fetch(" not in script, "every read goes through api(), where this test can see it"
    for route in NEVER:
        assert route not in script, route


def test_the_page_is_public_and_carries_no_note(tmp_path):
    cfg = MarginConfig(db_path=str(tmp_path / "notes.db"), bearer_token="shh")
    with TestClient(create_app(cfg)) as c:
        c.post("/notes", json={"content": SECRET, "topic": "private"}, headers={"Authorization": "Bearer shh"})
        r = c.get("/viewer")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
        assert "SerenMargin" in r.text and "Backups" in r.text and "Status" in r.text
        assert SECRET not in r.text
        for route in NEVER:
            assert route not in r.text, route
        # what the page reads is still behind the bearer
        assert c.get("/notes/stats").status_code == 401
        assert c.get("/stores").status_code == 401
        assert c.get("/notes/stats", headers={"Authorization": "Bearer shh"}).json()["total"] == 1
