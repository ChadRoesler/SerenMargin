"""SerenMargin - private notes-to-self for an AI assistant.

See README.md for the full ethos. The short version: the model writes notes;
the model decides when to bring them up; the human sees them only on
offer-and-accept. Standalone service, opt-in by deploy (not by config flag).
"""
from __future__ import annotations

# Version flows from the git tag via setuptools-scm (written to _version.py at
# build time, read here). Fallback only fires in a bare source checkout that was
# never built. Mirrors SerenLoci/SCC so the family exposes __version__ alike.
try:
    from ._version import version as __version__
except Exception:  # noqa: BLE001 - source checkout without a build
    __version__ = "0.0.0+unknown"
