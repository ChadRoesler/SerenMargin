"""Entry point for `python -m seren_margin` or the `seren-margin` script.

Accepts --config / -c to match the SerenMemory convention (Memory leads, the
rest follow), so the installer can pass the config path explicitly and a buddy
who learned one service knows this one.
"""
from __future__ import annotations

import argparse
import sys

import uvicorn

from ._diag import diag
from .app import create_app
from .config import load_config


def _force_utf8_stdio() -> None:
    """Make stdout/stderr UTF-8 regardless of OS locale.

    On Windows the console defaults to a legacy codepage (cp1252), so any
    non-Latin-1 character a service prints - a smart quote in a config path, an
    accented username in a home directory, an arrow in an error string - raises
    UnicodeEncodeError and can take down whatever was mid-work. PYTHONUTF8=1 in
    the service env is the primary fix; this is the in-code backstop for the
    hand-run `python -m seren_margin` case. No-op where stdio is already UTF-8.

    Parity with SerenLoci's __main__, which learned this the same way.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def main() -> None:
    _force_utf8_stdio()
    parser = argparse.ArgumentParser(
        prog="seren_margin",
        description="SerenMargin - private notes-to-self for an AI assistant.")
    parser.add_argument(
        "--config", "-c", default=None,
        help="Path to seren-margin.yaml (default: $SEREN_MARGIN_CONFIG, then "
             "~/seren-margin/seren-margin.yaml, falling back to built-in "
             "defaults).")
    args = parser.parse_args()

    cfg = load_config(args.config)
    app = create_app(cfg)

    diag(f"[seren-margin] listening on {cfg.host}:{cfg.port}")
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info")


if __name__ == "__main__":
    main()
