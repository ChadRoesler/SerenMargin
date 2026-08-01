"""FastAPI app for SerenMargin.

Endpoints:
    GET    /                  - service info
    GET    /health            - liveness probe
    GET    /mcp-manifest      - plug-and-play tool manifest for SerenMcpServer
    POST   /notes             - write a note (the writer writes; nothing else does)
    GET    /notes             - list notes, newest first; ?topic= narrows
    GET    /notes/search      - full-text search over content + topic
    GET    /notes/topics      - thread labels + counts + last-touched
    GET    /notes/stats       - engine-check view; CONTENT-BLIND
    GET    /notes/{id}        - fetch one
    POST   /notes/{id}/amend  - append to a note (never replaces)
    DELETE /notes/{id}        - retract (hard delete)
    /mcp                      - MCP server, ONLY when [mcp] extras are installed

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
Same four tools either way, defined once in seren_margin.mcp.tools.
"""
from __future__ import annotations

from contextlib import asynccontextmanager, AsyncExitStack
from typing import Optional

from fastapi import FastAPI, Body, HTTPException, Request
from fastapi.responses import Response
from seren_meninges.updates import updates_payload

from importlib.resources import files
from importlib.metadata import version as pkg_version, PackageNotFoundError


from .config import MarginConfig, load_config
from .models import MarginNote, NoteAmend, NoteCreate, NoteStats
from .store import MarginStore
from ._diag import diag
import logging
from . import __version__ as _fallback_version

log = logging.getLogger("seren_margin")
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
        return {"ok": True, "service": "seren-margin", "version": __version__}

    # ── note CRUD ─────────────────────────────────────────────────────────

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

    @app.get("/notes/{note_id}")
    async def get_note(note_id: str):
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
