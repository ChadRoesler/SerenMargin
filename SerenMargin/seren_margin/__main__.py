"""Entry point for `python -m seren_margin` or the `seren-margin` script.

Accepts --config / -c to match the SerenMemory convention (Memory leads, the
rest follow), so the installer can pass the config path explicitly and a buddy
who learned one service knows this one.
"""
from __future__ import annotations

import argparse
import sys

import uvicorn
from seren_meninges.exposure import enforce_exposure

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
    # FIRST: the diary on the LAN with nothing in front of it is the one
    # configuration this service must never boot into quietly. Margin has no
    # token to offer, so the message says so and names the override.
    enforce_exposure(cfg.host, cfg.port, service="seren-margin",
                     allow_open_lan=cfg.allow_open_lan, env_prefix="SEREN_MARGIN",
                     supports_token=False, log=diag)
    app = create_app(cfg)
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info")


if __name__ == "__main__":
    main()
