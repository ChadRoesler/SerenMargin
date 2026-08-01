"""The ImportError fallback in app.py must not drift from the real contract.

SerenMargin is standalone by design: seren-meninges is NOT a core dependency,
only the [updates] extra pulls it. So app.py carries its own tiny
``updates_payload`` fallback for a bare install. That fallback duplicates the
seven-key shape by necessity - when meninges is absent there is no
UpdateStatus to reuse - and duplication drifts.

This test reads the fallback's literal dict straight out of the source with
ast and compares its keys to the real UpdateStatus. No import gymnastics, and
it fails the moment either side grows or loses a key.
"""
from __future__ import annotations

import ast
import pathlib

import pytest


def _fallback_keys() -> set[str]:
    """Keys of the dict literal returned by the fallback in app.py."""
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "seren_margin" / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        # the fallback is the async def updates_payload defined inside the
        # `except ImportError:` handler at module scope
        if isinstance(node, ast.Try):
            for handler in node.handlers:
                for sub in ast.walk(handler):
                    if (isinstance(sub, ast.AsyncFunctionDef)
                            and sub.name == "updates_payload"):
                        for ret in ast.walk(sub):
                            if isinstance(ret, ast.Return) and isinstance(ret.value, ast.Dict):
                                return {k.value for k in ret.value.keys
                                        if isinstance(k, ast.Constant)}
    pytest.fail("no ImportError fallback for updates_payload found in app.py")


def test_fallback_exists_at_all():
    """If someone 'tidies up' the try/except, a bare `pip install seren-margin`
    starts failing at import. Catch that here, not in an operator's terminal."""
    assert _fallback_keys(), "app.py must keep its meninges-absent fallback"


def test_fallback_keys_match_the_real_contract():
    updates = pytest.importorskip(
        "seren_meninges.updates", reason="meninges not installed (bare install)")
    real = set(updates.UpdateStatus(status="x", distribution="y").as_dict())
    assert _fallback_keys() == real, (
        "app.py's offline fallback drifted from seren_meninges UpdateStatus - "
        "a renderer reading one shape would break on the other")


def test_fallback_never_claims_an_update():
    """update_available must be a hard False. 'I could not check' rendering as
    a green tick is the exact failure this whole feature exists to avoid."""
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "seren_margin" / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "updates_payload":
            for ret in ast.walk(node):
                if isinstance(ret, ast.Return) and isinstance(ret.value, ast.Dict):
                    for k, v in zip(ret.value.keys, ret.value.values):
                        if isinstance(k, ast.Constant) and k.value == "update_available":
                            assert isinstance(v, ast.Constant) and v.value is False
                            found = True
    assert found, "fallback must set update_available explicitly to False"
