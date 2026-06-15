"""SerenMargin - private notes-to-self for an AI assistant.

See README.md for the full ethos. The short version: the model writes notes;
the model decides when to bring them up; the human sees them only on
offer-and-accept. Standalone service, opt-in by deploy (not by config flag).
"""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    # The real version is baked into the wheel by setuptools-scm at build time
    # (from the git tag) and read back here via the installed metadata. This
    # replaces the old hardcoded "0.1.0" that would report the same string
    # forever regardless of the tag.
    __version__: str = version("seren-margin")
except PackageNotFoundError:
    # Running from a source checkout without an install (or a tagless tree).
    __version__ = "0.0.0.dev0"
