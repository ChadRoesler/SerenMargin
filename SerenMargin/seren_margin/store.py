"""Sqlite store for MarginNotes. Tiny, file-based, no embeddings needed.

Schema is deliberately simple - notes-to-self aren't semantic-search material,
they're corkboard items that live until deleted. The only index targets the
one access pattern: list by recency.

Thread-safety: each method opens its own short-lived connection. Sqlite is
fine for this workload (low write rate, single writer in practice).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Optional

from .models import MarginNote, NoteStats


SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id          TEXT PRIMARY KEY,
    content     TEXT NOT NULL,
    topic       TEXT,
    kind        TEXT,
    ts          REAL NOT NULL,
    extra       TEXT
);
CREATE INDEX IF NOT EXISTS idx_notes_ts ON notes(ts DESC);
"""


class MarginStore:
    """Sqlite-backed note store."""

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            conn.commit()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ── writes ────────────────────────────────────────────────────────────
    def add(self, note: MarginNote) -> MarginNote:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO notes
                       (id, content, topic, kind, ts, extra)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    note.id, note.content, note.topic, note.kind, note.ts,
                    json.dumps(note.extra or {}),
                ),
            )
            conn.commit()
        return note

    def delete(self, note_id: str) -> bool:
        """Hard delete - the one lifecycle control that stays. The model
        retracts a note when it's done with it; nothing else removes notes.
        """
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
            conn.commit()
            return cur.rowcount > 0

    # ── reads ─────────────────────────────────────────────────────────────
    def get(self, note_id: str) -> Optional[MarginNote]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        return _row_to_note(row) if row else None

    def list_all(self, limit: int = 200) -> list[MarginNote]:
        """All notes, newest first. They live until deleted, so there's no
        active/done distinction to filter on - this is the corkboard view.
        """
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM notes ORDER BY ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_row_to_note(r) for r in rows]

    # ── stats (content-blind) ─────────────────────────────────────────────
    def stats(self) -> NoteStats:
        """Engine-check shape. No content text appears in this response."""
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
            kind_rows = conn.execute(
                """SELECT COALESCE(kind, '_unkinded') AS k, COUNT(*) AS c
                   FROM notes GROUP BY k""",
            ).fetchall()
            kinds = {r["k"]: r["c"] for r in kind_rows}

        return NoteStats(total=total, kinds=kinds)


def _row_to_note(row: sqlite3.Row) -> MarginNote:
    return MarginNote(
        id=row["id"],
        content=row["content"],
        topic=row["topic"],
        kind=row["kind"],
        ts=row["ts"],
        extra=json.loads(row["extra"] or "{}"),
    )