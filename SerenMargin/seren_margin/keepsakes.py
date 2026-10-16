"""
seren_margin.keepsakes
══════════════════════

Where a keepsake may be taken from, and taking it: Margin's door onto
seren_sinew.blobs. The rule, the resolution and the fetch live there, shared
with Memory and Loci; this binds them to the margin's own `keepsakes:` block
(KeepsakesConfig.sources, max_bytes).

A note can keep a picture (keep_with_note, or note_to_self's `keep`). The
writer names where it is - a path, or a URL - and keepsakes.sources says
whether the margin may reach there: a tool that reads any path the writer
names is a tool that reads any file on the box, and one that fetches any URL
is a hand on every service on the LAN. A diary needs neither. Nothing in the
list means nothing can be kept, and the refusal says so.

What comes back is checked by its first bytes: a picture, a clip, a sound or
a PDF is kept; anything else is refused, because a text file is a note, and
an SVG or an HTML is a script wearing a picture's extension.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional, Union

from seren_sinew.blobs import KEPT_PREFIXES, SourceRefused, kind_kept
from seren_sinew.blobs import fetch as _fetch
from seren_sinew.blobs import resolve_source as _resolve_source

from .config import KeepsakesConfig


def resolve_source(ref: str, cfg: KeepsakesConfig) -> Union[Path, str]:
    """The path a reference names, under one of keepsakes.sources' folders,
    or the URL itself under one of its prefixes. SourceRefused otherwise."""
    return _resolve_source(ref, cfg.sources)


def fetch(ref: str, cfg: KeepsakesConfig, client: Optional[Any] = None) -> Union[Path, bytes]:
    """Bring the bytes: the path (the blob store streams it) or, for a URL,
    the body, read no further than keepsakes.max_bytes."""
    return _fetch(ref, cfg.sources, cfg.max_bytes, client=client)


__all__ = ["SourceRefused", "resolve_source", "fetch", "kind_kept", "KEPT_PREFIXES"]
