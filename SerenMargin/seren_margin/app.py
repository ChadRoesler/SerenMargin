"""FastAPI app for SerenMargin.

Endpoints:
    GET    /                  - service info
    GET    /health            - liveness probe
    GET    /mcp-manifest      - plug-and-play tool manifest for SerenMcpServer
    POST   /notes             - write a note (the writer writes; nothing else does)
    GET    /notes             - list notes, newest first; ?topic= narrows    *
    GET    /notes/search      - full-text search over content + topic       *
    GET    /notes/topics      - thread labels + counts + last-touched       *
    GET    /notes/stats       - engine-check view; CONTENT-BLIND
    GET    /notes/{id}        - fetch one                                   *
    POST   /notes/{id}/amend  - append to a note (never replaces)
    DELETE /notes/{id}        - retract (hard delete)
    GET    /bookmark          - the dedication + a COUNT of unread letters (never text)
    PUT    /dedication        - a new version of the dedication (never overwrites)
    POST   /letters           - a letter to the next session
    POST   /letters/read      - open unread letters, oldest first; marks them read *
    /mcp                      - MCP server, ONLY when [mcp] extras are installed

    * CONTENT OVER HTTP IS OFF BY DEFAULT. These answer 404 unless the server
      block sets http_reads: true, and amend answers with the id, not the
      note. The notes are the writer's diary: the writer reads them through
      /mcp (the tools go to the store in process), and a person who points a
      browser at this port - the operator included - gets the content-blind
      stats and nothing to read. Chad, 27 Sept 2026: "its your diary, your
      private thoughts, your secrets, that belong to you, and i dont want to
      see them unless you tell me them." Honest limit: whoever owns the disk
      owns the sqlite file. This is a door that stays shut, not a vault - the
      same kind of privacy a paper diary on a shared desk has.

Route order matters: /notes/stats, /notes/search and /notes/topics are ALL
registered BEFORE /notes/{note_id} so FastAPI's path matcher doesn't try to
treat 'stats', 'search' or 'topics' as a note id. Specific-before-generic; this
bit us once already.

No lifecycle: notes have no pin/expiry/done state and live until retracted, so
there's no startup sweep and no background janitor.

TWO WAYS TO REACH THE TOOLS, both first-class:
    1. Workbench path - SerenMcpServer remote-imports GET /mcp-manifest and
       proxies the tools as part of the wider constellation.
    2. Standalone path - `pip install seren-margin[mcp]` mounts a real MCP
       endpoint at /mcp on this same process, so a client can connect directly
       with nothing else deployed.
Same ten tools either way, defined once in seren_margin.mcp.tools.

AUTH, IF YOU WANT IT. With no token configured (the default) every route is
open and the loopback bind is the whole guard - the way it has always been.
Configure one of the three token pointers on the server block and the
family's bearer middleware turns on: `/`, `/health` and `/mcp-manifest` stay
public (liveness, and the tool manifest Workbench fetches before it has any
credentials), everything that touches a note wants the bearer. Nothing here
makes anyone set one to keep a diary on their own machine.
"""
from __future__ import annotations

from contextlib import asynccontextmanager, AsyncExitStack
from typing import Optional

from fastapi import FastAPI, Body, HTTPException, Request
from fastapi.responses import Response
from seren_meninges import get_version
from seren_meninges.auth import DEFAULT_PUBLIC_PATHS, bearer_auth_middleware
from seren_meninges.updates import updates_payload

from importlib.resources import files
from importlib.metadata import version as pkg_version, PackageNotFoundError


from .bookmark import DEDICATION_MAX_CHARS, build_bookmark
from .config import MarginConfig, load_config
from .models import DedicationSet, LetterCreate, MarginNote, NoteAmend, NoteCreate, NoteStats
from .store import LETTER_KIND, MarginStore
from ._diag import diag
import logging
from . import __version__ as _fallback_version

log = logging.getLogger("seren_margin")
# Uses the shared helper like the rest of the family. This was a local
# importlib.metadata dance while seren-meninges was optional here; meninges is
# a core dependency now, so the special case is gone. Same behaviour either
# way - installed metadata, falling back to the package __version__ for a
# source checkout - just one implementation instead of two.
APP_VERSION = get_version("seren-margin", fallback=_fallback_version)


def create_app(config: Optional[MarginConfig] = None) -> FastAPI:
    cfg = config or load_config()
    store = MarginStore(cfg.resolved_db_path())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Nothing to sweep - notes live until retracted. Just stash handles.
        # These MUST be set before mount_mcp_routes runs; it reads them off
        # app.state to wire the tools to live objects.
        app.state.store = store
        app.state.cfg = cfg

        # -- Optional MCP server --
        # Mounted ONLY if the [mcp] extra is installed. A missing package falls
        # back to pure-HTTP mode without crashing - the HTTP API and the
        # /mcp-manifest workbench path are both fully usable without it.
        try:
            from .mcp.server import mount_mcp_routes
            mcp_server = mount_mcp_routes(app)
        except ImportError as exc:
            mcp_server = None
            diag(f"[seren-margin] MCP surface not available; HTTP-only mode ({exc})")
        except Exception as exc:  # noqa: BLE001
            mcp_server = None
            diag(f"[seren-margin] MCP mount failed: {exc!r} - continuing without MCP")

        # Enter the MCP session manager's task group if we mounted one (the
        # streamable-HTTP transport needs it; a mounted sub-app's own lifespan
        # doesn't fire under Starlette). AsyncExitStack makes HTTP-only mode a
        # clean no-op.
        try:
            from seren_meninges.updates import UpdateChecker
            app.state.updates = UpdateChecker(
                "seren-margin",
                enabled=cfg.updates.enabled,
                index_url=cfg.updates.index_url,
                ttl_seconds=cfg.updates.check_interval_hours * 3600.0,
                allow_prerelease=cfg.updates.allow_prerelease,
                fallback_version=APP_VERSION,
            )
        # Catch EVERYTHING, not just ImportError. This whole feature is cosmetic -
        # seren_meninges/version.py states the contract: a version read must never
        # crash startup. A too-narrow catch here already bit us: cfg.updates was
        # missing, the AttributeError sailed past `except ImportError`, and five
        # services failed to boot on a feature that only draws a badge.
        except Exception as exc:
            app.state.updates = None
            log.info("update checking unavailable (%s)", exc)


        async with AsyncExitStack() as _mcp_stack:
            session_manager = getattr(mcp_server, "session_manager", None)
            if session_manager is not None:
                await _mcp_stack.enter_async_context(session_manager.run())
                diag("[seren-margin] MCP session manager running")
            yield

    app = FastAPI(
        title="SerenMargin",
        description="Private notes-to-self. Standalone, opt-in, opinionated.",
        version=APP_VERSION,
        lifespan=lifespan,
    )

    # Optional bearer. An empty resolved token makes this middleware a
    # pass-through (see seren_meninges.auth), so an install with no token
    # behaves exactly as before. /mcp-manifest is public on purpose: it holds
    # tool descriptions, never notes, and Workbench fetches it before it has
    # any way to authenticate.
    app.add_middleware(bearer_auth_middleware(
        cfg.resolve_bearer(),
        public_paths=DEFAULT_PUBLIC_PATHS | {"/mcp-manifest"},
    ))

    @app.get("/")
    async def root(request: Request):
        return {
            "name": "SerenMargin",
            "version": APP_VERSION,
            "ethos": "private by default, transparent in mechanism, opt-in by deploy",
            "stats_endpoint": "/notes/stats",
            "finder": "fts" if store.has_fts else "like",
            "updates": await updates_payload(
                getattr(request.app.state, "updates", None),
                distribution="seren-margin", installed=APP_VERSION),
        }

    @app.get("/mcp-manifest", response_class=Response)
    def get_mcp_manifest(request: Request) -> Response:
        """
        Serve SerenMargin's plug-and-play tool manifest for SerenMcpServer.

        Placeholders are filled in at request time:
          __BASE_URL__  - request's scheme+host. So the manifest tells the
                          MCP server to send tool calls back to the SAME
                          SerenMargin instance the caller just fetched from.
                          Works for localhost AND remote deployments with
                          zero operator configuration.
          __VERSION__   - SerenMargin's installed package version, for the
                          operator's "what shipped" attribution.

        Content-type is application/yaml so curl + the MCP loader both treat
        it as YAML. The file lives inside the package (mcp-manifest.yaml
        sibling to the API modules) so the manifest and the routes can't
        drift on a release - and tests/test_manifest_parity.py asserts the
        tool roster matches seren_margin.mcp.tools, because "can't drift"
        turned out to be optimistic the first time around.
        """
        base_url = f"{request.url.scheme}://{request.url.netloc}"

        try:
            version_str = pkg_version("seren-margin")
        except PackageNotFoundError:
            # Running from a checkout (editable install or `python -m` from
            # repo root without `pip install -e .`) - fall back to a stub.
            version_str = "0.0.0+dev"

        content = (files("seren_margin") / "mcp-manifest.yaml").read_text(encoding="utf-8")
        content = content.replace("__BASE_URL__", base_url)
        content = content.replace("__VERSION__", version_str)

        return Response(content=content, media_type="application/yaml")

    @app.get("/health")
    async def health():
        return {"ok": True, "service": "seren-margin", "version": APP_VERSION}

    # ── note CRUD ─────────────────────────────────────────────────────────

    def _content_over_http() -> None:
        if not cfg.http_reads:
            raise HTTPException(404, "note contents are not served over HTTP: the writer reads them "
                                     "through /mcp. /notes/stats is the content-blind view. "
                                     "(server.http_reads: true opens these routes for the Workbench path.)")

    @app.post("/notes")
    async def write_note(body: NoteCreate = Body(...)):
        if not body.content.strip():
            raise HTTPException(400, "content must not be empty")
        note = MarginNote(
            content=body.content.strip(),
            topic=body.topic,
            kind=body.kind,
            extra=body.extra or {},
        )
        saved = store.add(note)
        return {"ok": True, "id": saved.id}

    @app.get("/notes")
    async def list_notes(limit: int = 100, topic: Optional[str] = None):
        _content_over_http()
        notes = store.list_all(limit=limit, topic=topic or None)
        return {
            "entries": [n.model_dump() for n in notes],
            "count": len(notes),
            "topic": topic or None,
        }

    @app.post("/notes/{note_id}/amend")
    async def amend_note(note_id: str, body: NoteAmend = Body(...)):
        """Append to a note. Never replaces; see MarginStore.amend."""
        if not body.addition.strip():
            raise HTTPException(400, "addition must not be empty")
        note = store.amend(note_id, body.addition)
        if note is None:
            raise HTTPException(404, f"no note '{note_id}'")
        if not cfg.http_reads:
            return {"ok": True, "id": note.id}          # the note stays in the diary
        return {"ok": True, "id": note.id, "note": note.model_dump()}

    # NOTE: /notes/search, /notes/stats and /notes/topics MUST all stay above
    # /notes/{note_id}, or FastAPI matches them as a note id and 404s.
    @app.get("/notes/topics")
    async def list_topics():
        """Thread labels with counts and last-touched times - orientation
        without reading the board.

        AI-facing, not part of the operator's engine-check: a topic label is a
        phrase the writer chose, which makes it note content wearing a metadata
        hat. It stays off /notes/stats for exactly that reason.
        """
        _content_over_http()
        topics = store.list_topics()
        return {"count": len(topics),
                "topics": [t.model_dump() for t in topics]}
    @app.get("/notes/search")
    async def search_notes(q: str, limit: int = 20):
        """Full-text search over content + topic.

        `finder` in the response says which engine answered - 'fts' normally,
        'like' on a sqlite built without FTS5. Surfaced rather than hidden so a
        thin result set can be diagnosed instead of guessed at.
        """
        _content_over_http()
        hits, finder = store.search(q, limit=limit)
        return {
            "query": q,
            "finder": finder,
            "count": len(hits),
            "entries": [n.model_dump() for n in hits],
        }

    @app.get("/notes/stats", response_model=NoteStats)
    async def get_stats():
        """Engine-check view. CONTENT-BLIND - returns shape, not text.

        For operators who want to validate the service is working without
        breaking their stated relational choice not to read individual notes.
        Kinds are bucketed to a fixed vocabulary on the way out (see
        models.bucket_kind) so a free-text kind can't smuggle note content into
        the one endpoint built specifically to avoid showing it.
        """
        return store.stats()

    # ── the bookmark, the dedication, letters (seren_margin.bookmark) ──────
    @app.get("/bookmark")
    async def get_bookmark(format: Optional[str] = None):
        """Pick up where you left off: the dedication and a COUNT of unread
        letters - never a letter's text, never a note. Served whatever
        http_reads says, because the dedication is the one page written to be
        read at the door; a harness hook reads it here. ?format=text gives
        the ready-to-print lines."""
        bm = build_bookmark(store)
        if format == "text":
            return Response(content=bm["text"] + "\n", media_type="text/plain; charset=utf-8")
        return bm

    @app.put("/dedication")
    async def put_dedication(body: DedicationSet = Body(...)):
        text = (body.text or "").strip()
        if not text:
            raise HTTPException(400, "text must not be empty")
        if len(text) > DEDICATION_MAX_CHARS:
            raise HTTPException(400, f"{len(text)} characters; the dedication is capped at {DEDICATION_MAX_CHARS}")
        entry, changed = store.set_dedication(text, body.why)
        return {"ok": True, "version": entry["version"], "changed": changed}

    @app.post("/letters")
    async def write_letter(body: LetterCreate = Body(...)):
        if not body.content.strip():
            raise HTTPException(400, "content must not be empty")
        extra = {"signed": body.signed.strip()} if body.signed and body.signed.strip() else {}
        note = store.add(MarginNote(content=body.content.strip(), kind=LETTER_KIND, extra=extra))
        return {"ok": True, "id": note.id}

    @app.post("/letters/read")
    async def read_letters(include_read: bool = False, limit: int = 20):
        """Open the letters, oldest first, and mark them read. Letter contents
        are diary contents: off over HTTP unless http_reads is on."""
        _content_over_http()
        letters = store.letters(unread_only=not include_read, limit=limit)
        store.mark_read([n.id for n in letters if n.read_at is None])
        return {"count": len(letters),
                "letters": [{**n.model_dump(), "signed": (n.extra or {}).get("signed")} for n in letters]}

    @app.get("/notes/{note_id}")
    async def get_note(note_id: str):
        _content_over_http()
        note = store.get(note_id)
        if not note:
            raise HTTPException(404, f"no note '{note_id}'")
        return note.model_dump()

    @app.delete("/notes/{note_id}")
    async def delete_note(note_id: str):
        ok = store.delete(note_id)
        if not ok:
            raise HTTPException(404, f"no note '{note_id}'")
        return {"ok": True, "id": note_id, "deleted": True}

    return app
