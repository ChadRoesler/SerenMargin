"""FastAPI app for SerenMargin.

Endpoints:
    GET    /                  - service info
    GET    /health            - liveness probe (Halls integration check)
    GET    /mcp-manifest       - plug-and-play tool manifest for SerenMcpServer
    POST   /notes             - write a note (model writes; no system writes)
    GET    /notes             - list all notes, newest first (corkboard view)
    GET    /notes/stats       - engine-check view; CONTENT-BLIND
    GET    /notes/{id}        - fetch one
    DELETE /notes/{id}        - hard delete

Route order matters: /notes/stats is registered BEFORE /notes/{note_id} so
FastAPI's path matcher doesn't try to treat 'stats' as an id.

No lifecycle: notes have no pin/expiry/done state and live until deleted, so
there's no startup sweep and no background janitor.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Body, HTTPException, Request
from fastapi.responses import Response

from importlib.resources import files
from importlib.metadata import version as pkg_version, PackageNotFoundError

from . import __version__
from .config import MarginConfig, load_config
from .models import MarginNote, NoteCreate, NoteStats
from .store import MarginStore


def create_app(config: Optional[MarginConfig] = None) -> FastAPI:
    cfg = config or load_config()
    store = MarginStore(cfg.resolved_db_path())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Nothing to sweep - notes live until deleted. Just stash handles.
        app.state.store = store
        app.state.cfg = cfg
        yield

    app = FastAPI(
        title="SerenMargin",
        description="Private notes-to-self. Standalone, opt-in, opinionated.",
        version=__version__,
        lifespan=lifespan,
    )

    @app.get("/")
    async def root():
        return {
            "name": "SerenMargin",
            "version": __version__,
            "ethos": "private by default, transparent in mechanism, opt-in by deploy",
            "stats_endpoint": "/notes/stats",
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
        drift on a release.
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
    async def list_notes(limit: int = 100):
        notes = store.list_all(limit=limit)
        return {"entries": [n.model_dump() for n in notes], "count": len(notes)}

    @app.get("/notes/stats", response_model=NoteStats)
    async def get_stats():
        """Engine-check view. CONTENT-BLIND - returns shape, not text.

        For operators who want to validate the service is working without
        breaking their stated relational choice not to read individual notes.
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