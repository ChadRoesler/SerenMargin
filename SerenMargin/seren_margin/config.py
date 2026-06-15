"""Config for SerenMargin.

Follows the SerenMemory convention (Memory leads, the rest follow) so a
buddy who set up one service already knows how to set up this one:

    * network settings live under a ``server:`` block (host/port)
    * config resolves: --config  ->  $SEREN_MARGIN_CONFIG  ->
      ~/seren-margin/seren-margin.yaml  ->  built-in defaults
    * the file is named seren-margin.yaml

Lego framing (Chad's): the YAML has a ``server:`` section that this service
reads, and (future) a ``tools:`` section that a plug-and-play MCP layer reads
when it wires note-writing tools. Same file, namespaced sections; each piece
of the stack reads its own block and ignores the rest.

Precedence (highest wins):
    1. Env vars  (deploy-time escape hatch -- systemd Environment= lines, etc.)
    2. YAML file (operator's standing config)
    3. Defaults  (sensible per-user, localhost-only)

Lenient parse (Postel-as-kindness applied to config):
    - Missing file              -> silently fall back to defaults
    - Malformed YAML            -> log + fall back to defaults (no crash)
    - Unparseable single value  -> log + that key falls back; others still apply

Note on the host default: unlike SerenMemory (which defaults host to 0.0.0.0
for trusted-LAN cluster use), SerenMargin defaults to 127.0.0.1. These are
PRIVATE notes - they must not land on the network just because the rest of
the constellation does. Follow-the-leader on structure; NOT on the security
default. Widen it yourself, on purpose, if you mean to.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel

try:
    import yaml  # type: ignore[import-untyped]
    _HAS_YAML = True
except ImportError:  # pragma: no cover - pyyaml is a hard dep, but be lenient
    _HAS_YAML = False


# -- service config model ----------------------------------------------------


class MarginConfig(BaseModel):
    """SerenMargin service config. Defaults are the Nano-floor: per-user,
    localhost-only, sqlite under the user's home directory.
    """

    # Where sqlite lives. Per-user default keeps this isolated from any
    # system-wide path. Operator can override via YAML or env.
    db_path: str = "~/.seren-margin/notes.db"

    # Bind address. Default localhost-only because this service shouldn't be
    # exposed to a network without auth in front. Operator decides whether
    # to widen. (Memory defaults 0.0.0.0; Margin does NOT - private notes.)
    host: str = "127.0.0.1"
    port: int = 7421

    # Active notes auto-expire after this many days unless pinned. Done
    # notes also age out after the same window from done_at, so a
    # "marked done by mistake" can be reversed if caught within the window.
    notes_days: int = 30

    def resolved_db_path(self) -> Path:
        return Path(self.db_path).expanduser()


# -- config file resolution --------------------------------------------------


# Default config home, matching the Seren ~/seren-<name>/seren-<name>.yaml
# convention that the installer writes to. (Memory uses the same shape.)
_DEFAULT_CONFIG_PATH = Path.home() / "seren-margin" / "seren-margin.yaml"


def _resolve_config_path(explicit_path: Optional[str] = None) -> Optional[Path]:
    """Return the path of the config YAML to read, or None if no candidate
    exists. Resolution order matches SerenMemory:

        1. explicit_path (the --config flag)
        2. $SEREN_MARGIN_CONFIG
        3. ~/seren-margin/seren-margin.yaml
        4. None -> defaults

    Doesn't crash if everything is missing - that's the default case.
    """
    # 1. explicit --config wins, used even if missing (operator can `touch`
    #    it later; the lenient loader handles absent).
    if explicit_path:
        return Path(explicit_path).expanduser()

    # 2. env override, same "use even if missing" semantics.
    env = os.getenv("SEREN_MARGIN_CONFIG")
    if env:
        return Path(env).expanduser()

    # 3. the conventional location the installer writes to.
    if _DEFAULT_CONFIG_PATH.exists():
        return _DEFAULT_CONFIG_PATH

    return None


def _load_yaml_lenient(path: Path) -> dict[str, Any]:
    """Parse YAML; on any failure return {} and log to stderr. Never crash."""
    if not path.exists():
        return {}
    if not _HAS_YAML:
        print(f"[seren-margin] config: pyyaml not installed; ignoring {path}")
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except Exception as e:
        print(f"[seren-margin] config: failed to parse {path}: {e} (using defaults)")
        return {}
    if data is None:
        return {}
    if not isinstance(data, dict):
        print(f"[seren-margin] config: {path} top-level must be a mapping; got {type(data).__name__} (using defaults)")
        return {}
    return data


def _apply_server_overrides(cfg: MarginConfig, server: dict[str, Any], *, source: str) -> None:
    """Apply per-key overrides to cfg. Each key try/except'd individually so
    one bad value doesn't take the others down.
    """
    # Whitelist of known keys to keep YAML from setting arbitrary attributes.
    # (If you add a field to MarginConfig, add it here too.)
    known = {"db_path", "host", "port", "notes_days"}
    for key, raw in server.items():
        if key not in known:
            print(f"[seren-margin] config: ignoring unknown server key '{key}' from {source}")
            continue
        try:
            # Use pydantic's validation by round-tripping through model_validate
            current = cfg.model_dump()
            current[key] = raw
            cfg.__dict__.update(MarginConfig.model_validate(current).__dict__)
        except Exception as e:
            print(f"[seren-margin] config: ignored bad value for '{key}' from {source}: {e}")


def load_config(path: Optional[str] = None) -> MarginConfig:
    """Build the runtime config. Defaults -> YAML -> env vars (each layer
    overrides the prior). Never raises on bad input; logs and falls back.

    ``path`` is the --config flag value (highest-priority config location).
    """
    cfg = MarginConfig()

    # Layer 2: YAML
    yaml_path = _resolve_config_path(path)
    if yaml_path is not None:
        data = _load_yaml_lenient(yaml_path)
        server = data.get("server")
        if isinstance(server, dict):
            _apply_server_overrides(cfg, server, source=str(yaml_path))
        elif server is not None:
            print(f"[seren-margin] config: 'server' in {yaml_path} must be a mapping; ignoring")
        # NOTE: data.get('tools') is intentionally NOT read here. That section
        # is reserved for a future plug-and-play MCP tool layer, which has its
        # own loader. Same file, different reader, by design.

    # Layer 3: env vars (highest precedence)
    env_map = {
        "SEREN_MARGIN_DB": "db_path",
        "SEREN_MARGIN_HOST": "host",
        "SEREN_MARGIN_PORT": "port",
        "SEREN_MARGIN_NOTES_DAYS": "notes_days",
    }
    env_overrides: dict[str, Any] = {}
    for env_key, attr in env_map.items():
        v = os.getenv(env_key)
        if v is not None:
            env_overrides[attr] = v
    if env_overrides:
        _apply_server_overrides(cfg, env_overrides, source="environment")

    return cfg