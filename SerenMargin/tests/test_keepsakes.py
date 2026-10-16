"""
Pictures kept with notes (seren_margin.keepsakes, the `keepsakes` table, the
three tools and note_to_self's `keep`). 15 Oct 2026.

Pinned here:
- a picture is kept WITH a note: the bytes under their hash in the margin's
  own folder, the row in notes.db; the note lists it, look_at_my_note shows it
  as image content, retracting the note takes it with it
- the margin reaches only the shelves keepsakes.sources names: a path outside
  them, a URL under no listed prefix, and an empty list are each refused with
  a reason - and the note is still written
- what is kept is decided by the bytes: a text file is refused as a keepsake
- the HTTP routes: keeping is a write, the rows and the bytes are diary
  contents (off without http_reads), the bytes come back nosniff in a sandbox
- snapshots: the folder rides along, hardlinked from the previous snapshot,
  the manifest counts and names hashes only; a rehearsal checks every picture
  the notes name is in the copy; a new box gets the pictures back; a snapshot
  from before the folder existed still restores
- stats stay content-blind: a count and a size
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from seren_margin.app import create_app
from seren_margin.config import BackupConfig, KeepsakesConfig, MarginConfig, load_config
from seren_margin.keepsakes import SourceRefused, fetch, resolve_source

PNG = bytes.fromhex("89504e470d0a1a0a") + b"\x00" * 100
JPG = b"\xff\xd8\xff\xe0" + b"\x01" * 100
MP4 = b"\0\0\0\x18ftypisom" + b"\0" * 100


def _gallery(tmp_path: Path) -> Path:
    g = tmp_path / "gallery" / "2026-10-15"
    g.mkdir(parents=True)
    (g / "lantern.png").write_bytes(PNG)
    (g / "houseboat.jpg").write_bytes(JPG)
    (g / "clip.mp4").write_bytes(MP4)
    (g / "notes.txt").write_text("a text is a note")
    return tmp_path / "gallery"


def _cfg(tmp_path: Path, sources=None, **kw) -> MarginConfig:
    return MarginConfig(db_path=str(tmp_path / "margin" / "notes.db"),
                        backup=BackupConfig(every_hours=0),
                        keepsakes=KeepsakesConfig(sources=[str(s) for s in (sources or [])]), **kw)


@pytest.fixture
def impl(tmp_path):
    pytest.importorskip("mcp")
    from seren_margin.mcp.tools import MarginToolImpl
    cfg = _cfg(tmp_path, sources=[_gallery(tmp_path)])
    app = create_app(cfg)
    with TestClient(app) as c:
        yield MarginToolImpl(c.app.state.store, cfg), tmp_path


# ── kept with the note ──────────────────────────────────────────────────────

def test_a_picture_is_kept_with_the_note_and_shown_back(impl):
    from mcp.server.fastmcp import Image
    tools, tmp = impl
    gallery = tmp / "gallery" / "2026-10-15"
    r = tools.note_to_self("the lantern, reaching", topic="art", keep=[str(gallery / "lantern.png"), str(gallery / "houseboat.jpg")])
    assert r["ok"] and len(r["kept"]) == 2 and r["refused"] == []
    (a, b) = r["kept"]
    assert a["mime"] == "image/png" and a["bytes"] == len(PNG) and a["file"] == f"keepsake-{a['digest'][:8]}.png"
    assert a["source"].endswith("lantern.png") and b["mime"] == "image/jpeg"
    folder = tmp / "margin" / "keepsakes"
    assert (folder / a["digest"][:2] / a["digest"]).read_bytes() == PNG, "named by its hash and nothing else"
    assert not any(p.suffix for p in folder.rglob("*") if p.is_file()), "no filename says what a picture is"

    note = tools.list_my_notes()["notes"][0]
    assert [k["digest"] for k in note["keepsakes"]] == [a["digest"], b["digest"]], "the rows ride with the note"
    assert tools.amend_note(r["id"], "still reaching")["note"]["keepsakes"][0]["digest"] == a["digest"], "and with its amended echo"
    assert tools.search_my_notes("lantern")["notes"][0]["keepsakes"][1]["digest"] == b["digest"]
    assert "data" not in json.dumps(note) or len(json.dumps(note)) < 2000, "the rows, not the bytes"

    look = tools.look_at_my_note(r["id"])
    assert isinstance(look, list) and look[0]["shown"] == 2 and look[0]["not_shown"] == []
    assert look[0]["content"].startswith("the lantern, reaching")
    assert [type(x) for x in look[1:]] == [Image, Image] and look[1].data == PNG and look[1]._mime_type == "image/png"

    # a clip is kept, listed, and not shown as a picture
    r2 = tools.keep_with_note(r["id"], str(gallery / "clip.mp4"), caption="the first frames")
    assert r2["ok"] and r2["kept"][0]["mime"] == "video/mp4" and r2["kept"][0]["caption"] == "the first frames"
    look = tools.look_at_my_note(r["id"])
    assert look[0]["shown"] == 2 and look[0]["not_shown"][0]["mime"] == "video/mp4"

    # dropping one, and retracting the note, take the bytes with them
    assert tools.drop_from_note(r["id"], b["digest"])["ok"]
    assert not tools.drop_from_note(r["id"], b["digest"])["ok"]
    assert (folder / b["digest"][:2] / b["digest"]).exists(), "within the grace period the bytes stay"
    assert tools.store.sweep_keepsakes(grace=0) == [b["digest"]]
    assert tools.retract_note(r["id"])["ok"]
    assert tools.store.referenced_digests() == set()
    assert tools.store.sweep_keepsakes(grace=0) == sorted([a["digest"], r2["kept"][0]["digest"]])
    assert tools.look_at_my_note(r["id"]) == {"ok": False, "id": r["id"], "error": "no note with that id"}


def test_the_same_picture_on_two_notes_is_one_file_and_survives_the_first_notes_retraction(impl):
    tools, tmp = impl
    src = str(tmp / "gallery" / "2026-10-15" / "lantern.png")
    one = tools.note_to_self("one", keep=[src])
    two = tools.note_to_self("two", keep=[src])
    d = one["kept"][0]["digest"]
    assert two["kept"][0]["digest"] == d and tools.store.keepsakes.count() == 1
    tools.retract_note(one["id"])
    tools.store.sweep_keepsakes(grace=0)
    assert tools.store.keepsakes.has(d) and tools.look_at_my_note(two["id"])[0]["shown"] == 1


def test_over_mcp_the_look_is_a_text_block_and_image_blocks(impl):
    """Through FastMCP itself, the way the writer gets it."""
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ImageContent, TextContent
    from seren_margin.mcp.tools import register_tools
    tools, tmp = impl
    r = tools.note_to_self("a bird", keep=[str(tmp / "gallery" / "2026-10-15" / "lantern.png")])
    mcp = FastMCP("t")
    register_tools(mcp, tools.store, tools.config)
    got = asyncio.run(mcp.call_tool("look_at_my_note", {"id": r["id"]}))
    blocks = got[0] if isinstance(got, tuple) else got
    assert [type(b) for b in blocks] == [TextContent, ImageContent]
    assert json.loads(blocks[0].text)["shown"] == 1 and blocks[1].mimeType == "image/png"
    assert f"keepsake-{r['kept'][0]['digest'][:8]}.png" in blocks[0].text, "the name a harness keeps it under"
    got = asyncio.run(mcp.call_tool("list_my_notes", {}))
    blocks = got[0] if isinstance(got, tuple) else got
    assert [type(b) for b in blocks] == [TextContent], "a listing never carries the bytes"


# ── where the margin may reach ──────────────────────────────────────────────

def test_only_the_shelves_the_config_names(impl, tmp_path):
    tools, tmp = impl
    outside = tmp_path / "elsewhere.png"
    outside.write_bytes(PNG)
    r = tools.note_to_self("still written", keep=[str(outside), "https://example.test/a.png", str(tmp / "gallery" / "2026-10-15" / "notes.txt"),
                                                  str(tmp / "gallery" / "nope.png")])
    assert r["ok"] and tools.store.get(r["id"]).content == "still written", "the note lands whatever happens to the pictures"
    whys = {x["source"].rsplit(os.sep, 1)[-1].rsplit("/", 1)[-1]: x["why"] for x in r["refused"]}
    assert "not under a folder the sources allow" in whys["elsewhere.png"]
    assert "no URL prefixes are configured" in whys["a.png"]
    assert "a text is a note" in whys["notes.txt"]
    assert whys["nope.png"].startswith("no file at")
    assert r["kept"] == [] and tools.store.keepsakes.count() == 0

    # a symlink out of the gallery resolves to where it points, and is refused
    link = tmp / "gallery" / "2026-10-15" / "sneaky.png"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("no symlinks here")
    with pytest.raises(SourceRefused, match="not under a folder"):
        resolve_source(str(link), tools.config.keepsakes)


def test_nothing_configured_means_nothing_kept_and_says_so(tmp_path):
    cfg = KeepsakesConfig()
    with pytest.raises(SourceRefused, match=r"no folders are configured"):
        resolve_source(str(Path(__file__)), cfg)
    with pytest.raises(SourceRefused, match="say where"):
        resolve_source("  ", cfg)


def test_a_url_under_an_allowed_prefix_is_fetched_no_further_than_the_limit(tmp_path):
    from seren_sinew.blobs import BlobTooLarge
    cfg = KeepsakesConfig(sources=["https://pictures.test/"], max_bytes=150)
    assert resolve_source("https://pictures.test/a/b.png", cfg) == "https://pictures.test/a/b.png"
    with pytest.raises(SourceRefused, match="not under a prefix"):
        resolve_source("https://other.test/b.png", cfg)
    with pytest.raises(SourceRefused, match="not under a prefix"):
        resolve_source("http://pictures.test/b.png", cfg), "http is not https"

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/big.png":
            return httpx.Response(200, content=PNG * 3)
        if req.url.path == "/gone.png":
            return httpx.Response(404)
        return httpx.Response(200, content=PNG)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert fetch("https://pictures.test/a.png", cfg, client=client) == PNG
    with pytest.raises(BlobTooLarge):
        fetch("https://pictures.test/big.png", cfg, client=client)
    with pytest.raises(SourceRefused, match="answered 404"):
        fetch("https://pictures.test/gone.png", cfg, client=client)


def test_the_margin_can_keep_nothing_when_told(tmp_path):
    cfg = _cfg(tmp_path, sources=[_gallery(tmp_path)])
    cfg.keepsakes.enabled = False
    with TestClient(create_app(cfg)) as c:
        nid = c.post("/notes", json={"content": "x"}).json()["id"]
        r = c.post(f"/notes/{nid}/keepsakes", json={"source": str(tmp_path / "gallery" / "2026-10-15" / "lantern.png")})
        assert r.status_code == 400 and "keeps no pictures" in r.json()["detail"]
        assert not (tmp_path / "margin" / "keepsakes").exists()
        assert c.get("/stores").json()["stores"][0]["name"] == "notes" and len(c.get("/stores").json()["stores"]) == 1


# ── over HTTP ───────────────────────────────────────────────────────────────

def test_the_routes_keep_as_a_write_and_serve_the_rest_as_diary(tmp_path):
    gallery = _gallery(tmp_path)
    src = str(gallery / "2026-10-15" / "lantern.png")
    with TestClient(create_app(_cfg(tmp_path, sources=[gallery]))) as c:
        nid = c.post("/notes", json={"content": "x"}).json()["id"]
        r = c.post(f"/notes/{nid}/keepsakes", json={"source": src, "caption": "private words"})
        assert r.status_code == 200
        k = r.json()["keepsake"]
        assert set(k) == {"digest", "bytes", "mime", "file"}, "the caption stays in the diary"
        assert c.post("/notes/nope/keepsakes", json={"source": src}).status_code == 404
        assert c.post(f"/notes/{nid}/keepsakes", json={"source": str(tmp_path / "x.png")}).status_code == 400
        assert c.get(f"/notes/{nid}/keepsakes").status_code == 404, "the rows are diary contents"
        assert c.get(f"/notes/{nid}/keepsakes/{k['digest']}").status_code == 404, "so are the bytes"
        assert c.get("/notes/stats").json()["keepsakes"] == {"count": 1, "bytes": len(PNG)}
        assert c.delete(f"/notes/{nid}/keepsakes/{'0' * 64}").status_code == 404
        assert c.delete(f"/notes/{nid}/keepsakes/{k['digest']}").status_code == 200
        assert c.get("/notes/stats").json()["keepsakes"] == {"count": 0, "bytes": 0}
    with TestClient(create_app(_cfg(tmp_path, sources=[gallery], http_reads=True))) as c:
        nid = c.post("/notes", json={"content": "x"}).json()["id"]
        k = c.post(f"/notes/{nid}/keepsakes", json={"source": src}).json()["keepsake"]
        rows = c.get(f"/notes/{nid}/keepsakes").json()
        assert rows["count"] == 1 and rows["keepsakes"][0]["digest"] == k["digest"]
        assert c.get(f"/notes/{nid}").json()["keepsakes"][0]["file"] == k["file"]
        r = c.get(f"/notes/{nid}/keepsakes/{k['digest']}")
        assert r.status_code == 200 and r.content == PNG and r.headers["content-type"] == "image/png"
        assert r.headers["x-content-type-options"] == "nosniff" and r.headers["content-security-policy"].startswith("sandbox")
        assert r.headers["content-disposition"] == f'inline; filename="{k["file"]}"'
        assert c.get(f"/notes/{nid}/keepsakes/{'0' * 64}").status_code == 404
        assert c.get(f"/notes/{nid}/keepsakes/not-a-digest").status_code == 404
        other = c.post("/notes", json={"content": "y"}).json()["id"]
        assert c.get(f"/notes/{other}/keepsakes/{k['digest']}").status_code == 404, "only through a note that keeps it"


# ── snapshots ───────────────────────────────────────────────────────────────

def test_keepsakes_ride_in_the_snapshot_by_hash_and_come_back_on_a_new_box(tmp_path):
    from seren_sinew.stores import verify_snapshot
    gallery = _gallery(tmp_path)
    cfg = _cfg(tmp_path, sources=[gallery])
    with TestClient(create_app(cfg)) as c:
        nid = c.post("/notes", json={"content": "the secret picture", "topic": "private"}).json()["id"]
        k = c.post(f"/notes/{nid}/keepsakes", json={"source": str(gallery / "2026-10-15" / "lantern.png"),
                                                     "caption": "for seren"}).json()["keepsake"]
        snap = c.post("/stores/snapshot").json()["snapshot"]
        assert snap["counts"] == {"notes": 1, "keepsakes": 1}
        man = json.loads((Path(snap["path"]) / "manifest.json").read_text())
        assert man["stores"] == ["notes", "keepsakes"]
        paths = [f["path"] for f in man["files"]]
        assert f"raw/keepsakes/{k['digest'][:2]}/{k['digest']}" in paths
        blob = json.dumps(man)
        for word in ("lantern", "for seren", "secret", "private", ".png"):
            assert word not in blob, f"the manifest must not say {word!r}"
        # the second snapshot shares the picture with the first
        c.post("/notes", json={"content": "another"})
        snap2 = c.post("/stores/snapshot").json()["snapshot"]
        a = Path(snap["path"]) / "raw" / "keepsakes" / k["digest"][:2] / k["digest"]
        b = Path(snap2["path"]) / "raw" / "keepsakes" / k["digest"][:2] / k["digest"]
        assert os.path.samefile(a, b) and verify_snapshot(Path(snap2["path"])) == []
        rep = c.post(f"/stores/snapshots/{snap2['id']}/rehearse").json()
        assert rep["ok"] and rep["check"]["counts"] == {"notes": 2, "keepsakes": 1}, rep

    # a new box: the words and the picture, back together
    new = MarginConfig(db_path=str(tmp_path / "new" / "notes.db"), keepsakes=KeepsakesConfig(sources=[]),
                       backup=BackupConfig(every_hours=0, restore_from=snap2["path"], restore_reason="the old box died"))
    with TestClient(create_app(new)) as c:
        st = c.app.state.store
        (note,) = [n for n in st.list_all() if n.content == "the secret picture"]
        assert note.keepsakes[0].caption == "for seren" and st.keepsakes.read(note.keepsakes[0].digest) == PNG
        assert (tmp_path / "new" / "keepsakes" / k["digest"][:2] / k["digest"]).is_file()


def test_a_snapshot_from_before_there_were_keepsakes_still_restores(tmp_path):
    """A margin with snapshots from before there were keepsakes: a new box restored from one
    gets the notes, and an empty keepsakes folder, not a refusal."""
    cfg = _cfg(tmp_path)
    with TestClient(create_app(cfg)) as c:
        c.post("/notes", json={"content": "from before"})
        snap = c.post("/stores/snapshot").json()["snapshot"]
        man = json.loads((Path(snap["path"]) / "manifest.json").read_text())
        assert man["stores"] == ["notes"] and man["skipped"] == [{"name": "keepsakes", "why": "nothing on disk yet"}]
    new = MarginConfig(db_path=str(tmp_path / "new" / "notes.db"),
                       backup=BackupConfig(every_hours=0, restore_from=snap["path"], restore_reason="moving"))
    with TestClient(create_app(new)) as c:
        assert [n.content for n in c.app.state.store.list_all()] == ["from before"]
        assert c.get("/notes/stats").json()["keepsakes"] == {"count": 0, "bytes": 0}


def test_a_rehearsal_notices_a_picture_missing_from_the_copy(tmp_path):
    gallery = _gallery(tmp_path)
    with TestClient(create_app(_cfg(tmp_path, sources=[gallery]))) as c:
        nid = c.post("/notes", json={"content": "x"}).json()["id"]
        k = c.post(f"/notes/{nid}/keepsakes", json={"source": str(gallery / "2026-10-15" / "lantern.png")}).json()["keepsake"]
        snap = c.post("/stores/snapshot").json()["snapshot"]
        raw = Path(snap["path"]) / "raw" / "keepsakes" / k["digest"][:2] / k["digest"]
        man_path = Path(snap["path"]) / "manifest.json"
        man = json.loads(man_path.read_text())
        man["files"] = [f for f in man["files"] if not f["path"].endswith(k["digest"])]
        man_path.write_text(json.dumps(man))
        raw.unlink()                                          # the snapshot verifies, and the picture is gone
        rep = c.post(f"/stores/snapshots/{snap['id']}/rehearse").json()
        assert not rep["ok"] and any("1 of the 1 keepsake(s)" in p for p in rep["problems"]), rep


# ── config ──────────────────────────────────────────────────────────────────

def test_the_yaml_blocks_are_honoured(tmp_path):
    y = tmp_path / "m.yaml"
    y.write_text("server:\n  db_path: '%s'\nbackup:\n  compress: xz\nkeepsakes:\n  max_bytes: 5\n  sources:\n    - /some/gallery\n    - https://\n  nonsense: 1\n"
                 % str(tmp_path / "notes.db").replace("\\", "/"), encoding="utf-8")
    cfg = load_config(str(y))
    assert cfg.backup.compress == "xz" and cfg.keepsakes.max_bytes == 5
    assert cfg.keepsakes.sources == ["/some/gallery", "https://"]
    d = MarginConfig()
    assert d.keepsakes.sources == [] and d.keepsakes.enabled and d.backup.compress == "gz"
    assert d.resolved_keepsakes_dir() == d.resolved_db_path().resolve().parent / "keepsakes"
