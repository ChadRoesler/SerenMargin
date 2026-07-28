"""Search tests - the FTS5 index, the query builder, and the LIKE fallback.

The query-builder tests are the important half. Retrieval quality shows at
SCALE (a big board where "what"/"the" match half the rows), which a 5-note
fixture structurally cannot demonstrate - that's exactly why SerenLoci's
stopword bleed didn't reproduce in the minimal case and had to be caught in the
field. So the mechanism is pinned directly here rather than inferred from
end-to-end behaviour.
"""
from __future__ import annotations

import sqlite3

import pytest

from seren_margin.models import MarginNote
from seren_margin.store import MarginStore, _fts_query, _NO_MATCH


@pytest.fixture
def store(tmp_path):
    return MarginStore(tmp_path / "notes.db")


def _seed(store: MarginStore) -> None:
    store.add(MarginNote(
        content="Chad mentioned the Behemoth tattoo sits over the collarbone",
        topic="chad", kind="observation"))
    store.add(MarginNote(
        content="the consolidator redraft budget feels too tight at 3",
        topic="serenmemory", kind="question"))
    store.add(MarginNote(
        content="ask about the Tabletop Simulator port when he's rested",
        topic="superdudebros", kind="reminder"))
    store.add(MarginNote(
        content="he underclaims his own intelligence as a setup to the swing",
        topic="chad", kind="observation"))


# ── end-to-end retrieval ────────────────────────────────────────────────────

def test_fts_is_available_in_this_environment(store):
    """Guard: if this flips, the rest of the FTS tests are silently testing the
    LIKE fallback instead and would still pass. Fail loudly rather than lie."""
    assert store.has_fts, "sqlite lacks FTS5; FTS assertions below are void"


def test_search_finds_by_content_word(store):
    _seed(store)
    hits, finder = store.search("collarbone")
    assert finder == "fts"
    assert len(hits) == 1
    assert "Behemoth" in hits[0].content


def test_search_finds_by_topic(store):
    _seed(store)
    hits, _ = store.search("superdudebros")
    assert len(hits) == 1
    assert "Tabletop" in hits[0].content


def test_search_matches_stem_via_porter(store):
    """tokenize='porter' means 'feels' in the note is reachable as 'feel'."""
    _seed(store)
    hits, _ = store.search("feel")
    assert any("redraft budget" in h.content for h in hits)


def test_natural_language_question_finds_the_named_thing(store):
    """The stopword-bleed shape, end to end: a full question whose only
    discriminating token is 'Behemoth'."""
    _seed(store)
    hits, _ = store.search("what was that thing about the Behemoth again?")
    assert hits
    assert "Behemoth" in hits[0].content


def test_empty_query_returns_nothing_not_everything(store):
    _seed(store)
    hits, _ = store.search("   ")
    assert hits == []


def test_search_miss_is_empty_not_error(store):
    _seed(store)
    hits, _ = store.search("zzzzquux")
    assert hits == []


def test_retracted_note_leaves_the_index(store):
    """The FTS row must go with the note. A standalone index that isn't cleaned
    up on delete is how a 'retracted' private note keeps turning up in search."""
    n = store.add(MarginNote(content="ephemeral thought about pelicans"))
    assert store.search("pelicans")[0]
    assert store.delete(n.id) is True
    hits, _ = store.search("pelicans")
    assert hits == []


def test_index_survives_reopen_and_backfills(tmp_path):
    """A notes.db written before the index existed gets backfilled on open."""
    db = tmp_path / "notes.db"
    s1 = MarginStore(db)
    n = s1.add(MarginNote(content="written before the index existed"))
    # Simulate the pre-FTS database: drop the index table entirely.
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE notes_fts")
        conn.commit()

    s2 = MarginStore(db)  # reopen -> recreate + backfill
    hits, _ = s2.search("index existed")
    assert [h.id for h in hits] == [n.id]


def test_query_operators_in_text_do_not_blow_up(store):
    """FTS5 syntax appearing in a QUERY must be treated as words, not syntax.
    Unquoted, 'NOT' / '*' / '^' raise OperationalError mid-search."""
    store.add(MarginNote(content="a note about NOT giving up"))
    for q in ['NOT', 'up NOT', '"', '^foo', 'a * b', 'x OR', 'NEAR(a b)']:
        hits, finder = store.search(q)
        assert isinstance(hits, list)  # no exception is the assertion


# ── the query builder, pinned directly ──────────────────────────────────────

def test_fts_query_short_query_ands_raw_tokens():
    """<=3 tokens is deliberate; every word is required, stopwords included."""
    assert _fts_query("behemoth tattoo") == '"behemoth" "tattoo"'
    assert _fts_query("the redraft budget") == '"the" "redraft" "budget"'


def test_fts_query_long_query_strips_scaffolding_and_ors():
    q = _fts_query("what was that thing about the Behemoth again?")
    assert " OR " in q
    assert '"behemoth"' in q
    assert '"what"' not in q and '"about"' not in q and '"the"' not in q


def test_fts_query_all_stopword_sentence_keeps_its_tokens():
    """Filtering must never erase the query into a guaranteed miss."""
    q = _fts_query("what is the of on for the")
    assert _NO_MATCH not in q
    assert " OR " in q


def test_fts_query_empty_yields_no_match_sentinel():
    assert _fts_query("   ") == _NO_MATCH
    assert _fts_query("!!! ???") == _NO_MATCH


def test_fts_query_quotes_every_token():
    """The quoting is what defuses FTS5 operators. If a token ever escapes
    unquoted, a note-shaped query becomes a syntax error at runtime."""
    q = _fts_query("NOT OR NEAR AND something else here")
    for bare in (" NOT ", " NEAR ", " AND "):
        assert bare not in f" {q} "


# ── LIKE fallback ───────────────────────────────────────────────────────────

def test_like_fallback_returns_results(store, monkeypatch):
    """Force the no-FTS5 path and prove it still answers correctly."""
    _seed(store)
    monkeypatch.setattr(store, "has_fts", False)
    hits, finder = store.search("collarbone")
    assert finder == "like"
    assert len(hits) == 1
    assert "Behemoth" in hits[0].content


def test_like_fallback_ands_tokens(store, monkeypatch):
    _seed(store)
    monkeypatch.setattr(store, "has_fts", False)
    # Both tokens present in one note -> hit.
    assert store.search("Behemoth collarbone")[0]
    # Tokens from two different notes -> no single note has both.
    assert store.search("Behemoth Tabletop")[0] == []


def test_like_fallback_escapes_wildcards(store, monkeypatch):
    """A bare '%' query must not match every note."""
    store.add(MarginNote(content="one hundred percent"))
    store.add(MarginNote(content="literally 50% done"))
    monkeypatch.setattr(store, "has_fts", False)
    # '%' isn't a \w token so it's dropped entirely -> empty, not everything.
    assert store.search("%")[0] == []
