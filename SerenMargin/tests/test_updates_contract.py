"""Margin must use the SHARED update payload, not a local copy of it.

History, because the invariant reversed and the reason matters:

Margin used to keep its own ``updates_payload`` fallback behind a
``try/except ImportError``, because seren-meninges was an optional dependency
here and a bare ``pip install seren-margin`` had to start without it. That
fallback duplicated the seven-key payload shape, and this test existed to stop
the two copies drifting.

seren-meninges is a CORE dependency now - update checking moved into the
shared core so it is present everywhere rather than being decided by whichever
transitive dependency happened to supply httpx. The fallback became dead code
and was removed, so the thing worth pinning flipped: there must be exactly ONE
definition of that payload, and Margin must not grow a second one back.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

APP = pathlib.Path(__file__).resolve().parents[1] / "seren_margin" / "app.py"


def _tree() -> ast.Module:
    return ast.parse(APP.read_text(encoding="utf-8"))


def test_app_imports_the_shared_payload_helper():
    """One source of truth. If this import goes away, something local replaced
    it and the shape can drift from seren_meninges again."""
    src = APP.read_text(encoding="utf-8")
    assert "from seren_meninges.updates import updates_payload" in src, (
        "app.py must import updates_payload from seren_meninges - meninges is "
        "a core dependency now, so there is no reason for a local copy"
    )


def test_app_does_not_define_its_own_updates_payload():
    """The regression this file now guards. A local def means a second copy of
    the seven-key contract with nothing keeping the two in step."""
    for node in ast.walk(_tree()):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            assert node.name != "updates_payload", (
                "app.py defines its own updates_payload. That fallback existed "
                "only while seren-meninges was optional; it is core now, so a "
                "local definition is a duplicate contract waiting to drift."
            )


def test_the_shared_helper_still_has_the_shape_margin_serves():
    """Margin hands this dict straight out of GET /. If seren_meninges changes
    the key set, Margin's consumers break - so assert the shape from here too,
    where a failure names Margin rather than a library."""
    updates = pytest.importorskip("seren_meninges.updates")
    keys = set(updates.UpdateStatus(status="x", distribution="y").as_dict())
    assert keys == {"status", "distribution", "installed", "latest",
                    "update_available", "detail", "checked_at"}


def test_root_route_actually_serves_the_payload():
    """Belt and braces: the helper being imported is not the same as it being
    wired into the response."""
    src = APP.read_text(encoding="utf-8")
    assert '"updates": await updates_payload(' in src, (
        "GET / must call updates_payload - importing it is not enough"
    )
