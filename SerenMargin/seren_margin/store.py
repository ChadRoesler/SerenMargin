"""Sqlite store for MarginNotes. Tiny, file-based, no embeddings needed.

Schema is deliberately simple - notes-to-self aren't a task queue, they're a
private thought-space that lives until the writer retracts it. The only
lifecycle control is delete.

WHY THERE IS SEARCH HERE (and why it's FTS5 and not vectors):
    A thought-space you can only read newest-first is a drawer, not a mind. At
    ten notes recency is fine; at four hundred it's useless - the note you want
    is the one you wrote in March about a thing that just came up again.
    So: full-text search.

    FTS5 ships inside sqlite (stdlib). It costs zero new dependencies, zero
    torch, zero model downloads, and it runs on the Nano floor. A semantic
    embedder would find "that thing about being tired" -> "worried he isn't
    sleeping", which FTS5 will miss - that's the real tradeoff and it's the
    right one to take here. Notes-to-self are written by the same mind that
    reads them back, in its own vocabulary, so lexical recall lands far more
    often than it would against someone else's prose. Revisit if that stops
    being true.

    Degrades gracefully: a python built against a sqlite without FTS5 falls
    back to LIKE scanning. Slower, dumber, still correct. Postel-as-kindness
    applied to the storage engine.

Thread-safety: each method opens its own short-lived connection. Sqlite is
fine for this workload (low write rate, single writer in practice).
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Optional

from ._diag import diag
from .models import (
    AMEND_SEPARATOR,
    MarginNote,
    NoteStats,
    TopicSummary,
    bucket_kind,
    humanize_age,
)


SCHEMA = """
CREATE TABLE IF NOT EXISTS notes (
    id          TEXT PRIMARY KEY,
    content     TEXT NOT NULL,
    topic       TEXT,
    kind        TEXT,
    ts          REAL NOT NULL,
    amended_at  REAL,
    extra       TEXT
);
CREATE INDEX IF NOT EXISTS idx_notes_ts ON notes(ts DESC);
CREATE INDEX IF NOT EXISTS idx_notes_topic ON notes(topic);
"""

# Columns added after v0.1.0 shipped. Applied by _migrate() as idempotent
# ALTER TABLE ADD COLUMNs.
#
# ADD COLUMN with no NOT NULL and no default is the one schema change sqlite
# does in O(1) without rewriting the table, and existing rows simply read NULL -
# which is the correct value for "this note has never been amended". So an
# operator with a year of notes upgrades instantly and loses nothing. Anything
# that would REWRITE rows does not belong in a startup path on a database whose
# owner, by design, isn't reading it closely enough to notice damage.
_ADDED_COLUMNS: dict[str, str] = {
    "amended_at": "REAL",
}

# Standalone FTS5 index rather than an external-content table.
#
# External-content FTS5 (content='notes') avoids duplicating the text on disk,
# but it requires trigger-maintained rowid alignment and the fiddly
# `INSERT INTO notes_fts(notes_fts, rowid, ...) VALUES('delete', ...)` dance on
# every delete - which silently corrupts the index if you ever get it wrong.
# Notes are small and few. Paying a second copy of the text to get an index
# that's obviously-correct-by-inspection is the right trade for a service whose
# whole point is that its operator won't be reading the data to notice drift.
FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    id UNINDEXED,
    content,
    topic,
    tokenize='porter unicode61'
);
"""

# Query scaffolding that carries no discriminating signal. Stripped from long
# queries only - see _fts_query.
#
# Inherited from SerenLoci's stopword-bleed fix (its
# tests/test_fts_stopword_bleed.py). Same bug class applies here: OR-ing every
# token of a natural-language question lets "what"/"does"/"the" match half the
# corpus and drown the one word that actually identifies what you're after.
_STOPWORDS = {
    "a", "about", "an", "and", "any", "are", "as", "at", "be", "been", "but",
    "by", "can", "did", "do", "does", "for", "from", "had", "has", "have",
    "how", "i", "if", "in", "is", "it", "its", "me", "my", "of", "on", "or",
    "so", "than", "that", "the", "their", "them", "then", "there", "these",
    "they", "this", "to", "was", "we", "were", "what", "when", "where",
    "which", "who", "why", "will", "with", "would", "you", "your",
}

_NO_MATCH = '"__seren_margin_no_match__"'


def _fts_query(raw: str) -> str:
    """Build an FTS5 MATCH expression from free text.

    Every token is double-quoted, which makes it an FTS5 *string* and defuses
    the query operators (NOT / OR / NEAR / * / ^) that would otherwise let a
    note's own phrasing turn into syntax and raise OperationalError mid-search.

    Two branches, matching SerenLoci:
      <= 3 tokens : AND every raw token. A short query is deliberate; the
                    caller means all of those words, stopwords included.
      >  3 tokens : it's a sentence. Drop the scaffolding and OR the rest, so
                    recall stays wide but the discriminating terms aren't
                    outvoted by "what" and "the".

    Never returns an empty expression: an all-stopword long query keeps its raw
    tokens (filtering must not erase the query), and only genuinely empty input
    yields the no-match sentinel.
    """
    tokens = [t for t in re.findall(r"\w+", raw.lower()) if t]
    if not tokens:
        return _NO_MATCH

    if len(tokens) <= 3:
        return " ".join(f'"{t}"' for t in tokens)

    content = [t for t in tokens if t not in _STOPWORDS]
    # All-stopword sentence: keep everything rather than matching nothing.
    if not content:
        content = tokens
    return " OR ".join(f'"{t}"' for t in content)


class MarginStore:
    """Sqlite-backed note store."""

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self.has_fts: bool = False
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)
            self.has_fts = self._try_init_fts(conn)
            conn.commit()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ── schema migration ──────────────────────────────────────────────────
    def _migrate(self, conn: sqlite3.Connection) -> None:
        """Add any columns introduced since this database was created.

        Idempotent and additive-only: reads the live column list, adds what's
        missing, touches nothing that exists. Safe to run on every startup, safe
        to run on a fresh database (where SCHEMA already created everything and
        this is a no-op), and safe to run on a v0.1.0 database with a year of
        notes in it.

        There is no version stamp on purpose. A stamp is a second source of
        truth that can disagree with the actual schema; PRAGMA table_info IS the
        schema, so asking it directly can't drift.
        """
        have = {r["name"] for r in conn.execute("PRAGMA table_info(notes)")}
        for col, decl in _ADDED_COLUMNS.items():
            if col not in have:
                conn.execute(f"ALTER TABLE notes ADD COLUMN {col} {decl}")
                diag(f"[seren-margin] schema: added notes.{col}")

    # ── FTS bootstrap ─────────────────────────────────────────────────────
    def _try_init_fts(self, conn: sqlite3.Connection) -> bool:
        """Create the FTS index and backfill it if needed.

        Returns False (rather than raising) when the host sqlite lacks the FTS5
        module - search then falls back to LIKE. Some distro pythons genuinely
        ship without it, and a private notes service is not the place to
        hard-fail on an optional index.
        """
        try:
            conn.executescript(FTS_SCHEMA)
        except sqlite3.OperationalError as e:
            diag(f"[seren-margin] sqlite has no FTS5 ({e}); "
                  f"search will use the LIKE fallback")
            return False

        # Backfill: an existing notes.db predates this index, and a partially
        # populated index is worse than none. Cheap to detect, cheap to rebuild -
        # the table is small by construction.
        n_notes = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
        n_fts = conn.execute("SELECT COUNT(*) FROM notes_fts").fetchone()[0]
        if n_notes and n_fts != n_notes:
            conn.execute("DELETE FROM notes_fts")
            conn.execute(
                "INSERT INTO notes_fts (id, content, topic) "
                "SELECT id, content, COALESCE(topic, '') FROM notes"
            )
            diag(f"[seren-margin] rebuilt search index ({n_notes} notes)")
        return True

    # ── writes ────────────────────────────────────────────────────────────
    def add(self, note: MarginNote) -> MarginNote:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO notes
                       (id, content, topic, kind, ts, amended_at, extra)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    note.id, note.content, note.topic, note.kind, note.ts,
                    note.amended_at, json.dumps(note.extra or {}),
                ),
            )
            if self.has_fts:
                conn.execute(
                    "INSERT INTO notes_fts (id, content, topic) VALUES (?, ?, ?)",
                    (note.id, note.content, note.topic or ""),
                )
            conn.commit()
        return note

    def amend(self, note_id: str, addition: str) -> Optional[MarginNote]:
        """Append to an existing note. Returns the updated note, or None if
        there's no note with that id.

        APPEND, NOT REPLACE, and that's the whole design. A thought that
        developed - "I thought X" then later "and I was wrong about X because
        Y" - is one thought with a history, and the original half is usually the
        interesting one. An overwrite would silently destroy it; retract-and-
        rewrite would lose the original timestamp too.

        `ts` deliberately does NOT move. Ordering should reflect when a thought
        STARTED, not when it was last poked, or every amendment would drag old
        notes to the top of the board and flatten the timeline.
        """
        if not addition or not addition.strip():
            return None
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
            if row is None:
                return None

            merged = row["content"] + AMEND_SEPARATOR + addition.strip()
            stamp = time.time()
            conn.execute(
                "UPDATE notes SET content = ?, amended_at = ? WHERE id = ?",
                (merged, stamp, note_id),
            )
            if self.has_fts:
                # Re-index, or search keeps answering with the pre-amendment
                # text - a stale index on a private notes service is invisible
                # until it silently fails to find something.
                conn.execute("DELETE FROM notes_fts WHERE id = ?", (note_id,))
                conn.execute(
                    "INSERT INTO notes_fts (id, content, topic) VALUES (?, ?, ?)",
                    (note_id, merged, row["topic"] or ""),
                )
            conn.commit()
            updated = conn.execute(
                "SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        return _row_to_note(updated)

    def delete(self, note_id: str) -> bool:
        """Hard delete - the one lifecycle control that stays. The writer
        retracts a note when they're done with it; nothing else removes notes.
        """
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
            if self.has_fts:
                conn.execute("DELETE FROM notes_fts WHERE id = ?", (note_id,))
            conn.commit()
            return cur.rowcount > 0

    # ── reads ─────────────────────────────────────────────────────────────
    def get(self, note_id: str) -> Optional[MarginNote]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM notes WHERE id = ?", (note_id,)).fetchone()
        return _row_to_note(row) if row else None

    def list_all(self, limit: int = 200,
                 topic: Optional[str] = None) -> list[MarginNote]:
        """Notes, newest first. They live until retracted, so there's no
        active/done distinction to filter on - this is the whole board.

        `topic` narrows to one thread. Matched case-insensitively and with
        surrounding whitespace trimmed, because "Chad" and "chad " are the same
        thread to whoever typed them and a filter that disagrees is just a
        silent empty result.
        """
        sql = "SELECT * FROM notes"
        params: list = []
        if topic is not None:
            sql += " WHERE LOWER(TRIM(COALESCE(topic,''))) = ?"
            params.append(topic.strip().lower())
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_note(r) for r in rows]

    def list_topics(self) -> list[TopicSummary]:
        """Every thread on the board: label, note count, last-touched time.

        The cheap-orientation surface. Reading the whole board to find out what
        threads exist is the wrong shape once there are more notes than
        attention; this answers "what have I been thinking about" in one call
        without pulling any note text.

        Ordered by most-recently-touched, not alphabetically - the threads that
        are live matter more than the ones that start with 'a'. "Last touched"
        counts amendments (COALESCE(amended_at, ts)), since a thread you added
        to yesterday is live regardless of when it started.

        Untopiced notes are collected under a single `topic: null` row rather
        than dropped, so the counts always reconcile against /notes/stats.
        """
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT CASE WHEN TRIM(COALESCE(topic,'')) = '' THEN NULL
                               ELSE TRIM(topic) END           AS t,
                          COUNT(*)                            AS c,
                          MAX(COALESCE(amended_at, ts))       AS latest
                     FROM notes
                    GROUP BY t
                    ORDER BY latest DESC""",
            ).fetchall()
        return [
            TopicSummary(topic=r["t"], count=r["c"],
                         latest_ts=r["latest"],
                         latest_age=humanize_age(r["latest"]))
            for r in rows
        ]

    # ── search ────────────────────────────────────────────────────────────
    def search(self, query: str, limit: int = 20) -> tuple[list[MarginNote], str]:
        """Find notes by text. Returns (hits, finder) where finder is 'fts' or
        'like', so a caller can tell which engine actually answered.

        An empty/whitespace query returns no hits rather than everything - an
        accidental empty search should not dump the whole board.
        """
        finder = "fts" if self.has_fts else "like"
        if not query or not query.strip():
            return [], finder
        if self.has_fts:
            try:
                return self._search_fts(query, limit), "fts"
            except sqlite3.OperationalError as e:
                # A malformed MATCH expression should degrade, not 500. The
                # quoting in _fts_query is meant to make this unreachable; this
                # is the belt to that suspenders.
                diag(f"[seren-margin] FTS query failed ({e}); using LIKE fallback")
        return self._search_like(query, limit), "like"

    def _search_fts(self, query: str, limit: int) -> list[MarginNote]:
        match = _fts_query(query)
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT n.*
                     FROM notes_fts f
                     JOIN notes n ON n.id = f.id
                    WHERE notes_fts MATCH ?
                    ORDER BY bm25(notes_fts), n.ts DESC
                    LIMIT ?""",
                (match, limit),
            ).fetchall()
        return [_row_to_note(r) for r in rows]

    def _search_like(self, query: str, limit: int) -> list[MarginNote]:
        """Substring fallback for a sqlite without FTS5. ANDs the query's tokens
        across content+topic so it's at least not a naive single-string match.
        Escapes LIKE wildcards so a note containing '%' can't turn someone's
        query into an accidental full-table match.
        """
        tokens = [t for t in re.findall(r"\w+", query.lower()) if t][:8]
        if not tokens:
            return []
        clauses, params = [], []
        for t in tokens:
            esc = t.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")
            clauses.append(
                "(LOWER(content) LIKE ? ESCAPE '\\' "
                "OR LOWER(COALESCE(topic,'')) LIKE ? ESCAPE '\\')")
            params.extend([f"%{esc}%", f"%{esc}%"])
        sql = (f"SELECT * FROM notes WHERE {' AND '.join(clauses)} "
               f"ORDER BY ts DESC LIMIT ?")
        params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_note(r) for r in rows]

    # ── stats (content-blind) ─────────────────────────────────────────────
    def stats(self) -> NoteStats:
        """Engine-check shape. No note text appears in this response.

        `kind` is free-form on the way IN (the writer shouldn't have to think
        about taxonomy while jotting a thought) but is BUCKETED on the way OUT -
        see models.bucket_kind. Echoing raw kinds here would have made the
        content-blind promise false the first time the field got used as a
        second content line, which for a free-text field is a matter of when,
        not whether.
        """
        with self._conn() as conn:
            total = conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0]
            kind_rows = conn.execute(
                "SELECT kind, COUNT(*) AS c FROM notes GROUP BY kind",
            ).fetchall()

        kinds: dict[str, int] = {}
        for r in kind_rows:
            b = bucket_kind(r["kind"])
            kinds[b] = kinds.get(b, 0) + r["c"]

        return NoteStats(total=total, kinds=kinds)


def _row_to_note(row: sqlite3.Row) -> MarginNote:
    return MarginNote(
        id=row["id"],
        content=row["content"],
        topic=row["topic"],
        kind=row["kind"],
        ts=row["ts"],
        amended_at=row["amended_at"],
        extra=json.loads(row["extra"] or "{}"),
    )
