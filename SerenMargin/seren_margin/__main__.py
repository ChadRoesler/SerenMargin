"""Entry point for `python -m seren_margin` or the `seren-margin` script.

Accepts --config / -c to match the SerenMemory convention (Memory leads, the
rest follow), so the installer can pass the config path explicitly and a buddy
who learned one service knows this one.
"""
from __future__ import annotations

import argparse

import uvicorn

from .app import create_app
from .config import load_config


def main() -> None:
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

    print(f"[seren-margin] listening on {cfg.host}:{cfg.port}")
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="info")


if __name__ == "__main__":
    main()