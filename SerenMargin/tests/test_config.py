"""Tests for seren_margin.config.load_config().

Covers:
    - missing file -> defaults
    - well-formed YAML (server: block) -> applied
    - malformed YAML -> fallback, no crash
    - bad single value -> that key falls back, others applied
    - env var overrides YAML
    - unknown server key -> ignored
    - 'tools' section silently ignored (different reader's job)
    - --config path argument resolves (Memory-rhyming entry point)
    - the removed notes_days key is treated as unknown, not resurrected

NOTE ON THE notes_days REMOVAL: these tests used notes_days as their "second
key" for multi-key override cases. It was a dead field - nothing consumed it -
while the README told operators notes aged out after 30 days. It's gone; the
multi-key cases now use db_path, and there's an explicit test that a leftover
notes_days in someone's existing YAML is ignored-with-a-log rather than
crashing them on upgrade.
"""
from __future__ import annotations

import pytest

from seren_margin.config import MarginConfig, load_config


_ENV_KEYS = ("SEREN_MARGIN_DB", "SEREN_MARGIN_HOST", "SEREN_MARGIN_PORT")


# Helper to point load_config() at a controlled tmpdir config file.
@pytest.fixture
def cfg_path(tmp_path, monkeypatch):
    p = tmp_path / "seren-margin.yaml"
    monkeypatch.setenv("SEREN_MARGIN_CONFIG", str(p))
    # Clear any per-key env vars from the host so tests are deterministic.
    for k in _ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    return p


def test_defaults_when_no_yaml_and_no_env(monkeypatch, tmp_path):
    # Point at a path that doesn't exist; load_config should not crash.
    monkeypatch.setenv("SEREN_MARGIN_CONFIG", str(tmp_path / "nope.yaml"))
    for k in _ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    cfg = load_config()
    defaults = MarginConfig()
    assert cfg.host == defaults.host
    assert cfg.port == defaults.port
    assert cfg.db_path == defaults.db_path


def test_yaml_overrides_defaults(cfg_path):
    cfg_path.write_text(
        "server:\n"
        "  host: 0.0.0.0\n"
        "  port: 9999\n"
        "  db_path: /tmp/some-db.sqlite\n"
    )
    cfg = load_config()
    assert cfg.host == "0.0.0.0"
    assert cfg.port == 9999
    assert cfg.db_path == "/tmp/some-db.sqlite"


def test_config_arg_resolves(tmp_path, monkeypatch):
    """The --config path argument is highest priority and used even when the
    env var points elsewhere. Proves the Memory-rhyming entry point works."""
    for k in _ENV_KEYS + ("SEREN_MARGIN_CONFIG",):
        monkeypatch.delenv(k, raising=False)
    explicit = tmp_path / "explicit.yaml"
    explicit.write_text("server:\n  port: 4242\n")
    cfg = load_config(str(explicit))
    assert cfg.port == 4242


def test_config_arg_beats_env(tmp_path, monkeypatch):
    """--config wins over $SEREN_MARGIN_CONFIG (explicit beats ambient)."""
    for k in _ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    env_cfg = tmp_path / "env.yaml"
    env_cfg.write_text("server:\n  port: 1111\n")
    arg_cfg = tmp_path / "arg.yaml"
    arg_cfg.write_text("server:\n  port: 2222\n")
    monkeypatch.setenv("SEREN_MARGIN_CONFIG", str(env_cfg))
    cfg = load_config(str(arg_cfg))
    assert cfg.port == 2222


def test_env_overrides_yaml(cfg_path, monkeypatch):
    cfg_path.write_text("server:\n  port: 9999\n  db_path: /tmp/from-yaml.db\n")
    monkeypatch.setenv("SEREN_MARGIN_PORT", "5555")
    cfg = load_config()
    # env wins for port
    assert cfg.port == 5555
    # yaml wins for keys env didn't override
    assert cfg.db_path == "/tmp/from-yaml.db"


def test_malformed_yaml_falls_back_silently(cfg_path, capsys):
    # Garbage that yaml.safe_load will reject
    cfg_path.write_text("server:\n  port: [this is, not, valid: yaml\n")
    cfg = load_config()
    # Should still get default port back
    assert cfg.port == MarginConfig().port
    # Should have logged something to stderr-ish (stdout in our impl)
    captured = capsys.readouterr()
    assert "failed to parse" in (captured.out + captured.err)


def test_bad_single_value_only_drops_that_key(cfg_path, capsys):
    cfg_path.write_text(
        "server:\n"
        "  port: not-a-number\n"
        "  db_path: /tmp/still-applied.db\n"
    )
    cfg = load_config()
    # port falls back, db_path applied
    assert cfg.port == MarginConfig().port
    assert cfg.db_path == "/tmp/still-applied.db"
    captured = capsys.readouterr()
    assert "ignored bad value for 'port'" in (captured.out + captured.err)


def test_unknown_server_key_ignored(cfg_path, capsys):
    cfg_path.write_text(
        "server:\n"
        "  port: 8888\n"
        "  enable_skynet: true\n"
    )
    cfg = load_config()
    assert cfg.port == 8888
    captured = capsys.readouterr()
    assert "unknown server key 'enable_skynet'" in (captured.out + captured.err)


def test_leftover_notes_days_is_ignored_not_fatal(cfg_path, capsys):
    """Upgrade path: an operator's existing YAML still has notes_days in it.
    It must be logged-and-skipped like any other unknown key, and must NOT
    reappear as a config attribute."""
    cfg_path.write_text(
        "server:\n"
        "  port: 8888\n"
        "  notes_days: 30\n"
    )
    cfg = load_config()
    assert cfg.port == 8888
    assert not hasattr(cfg, "notes_days")
    captured = capsys.readouterr()
    assert "unknown server key 'notes_days'" in (captured.out + captured.err)


def test_tools_section_ignored(cfg_path):
    """The 'tools:' block is a different reader's job; SerenMargin must not
    crash on it and must not pull values from it.
    """
    cfg_path.write_text(
        "server:\n"
        "  port: 7777\n"
        "tools:\n"
        "  note_to_self:\n"
        "    max_content_chars: 2048\n"
    )
    cfg = load_config()
    assert cfg.port == 7777
    # Nothing from tools: section bleeds into server fields
    assert cfg.db_path == MarginConfig().db_path


def test_non_mapping_top_level_falls_back(cfg_path, capsys):
    cfg_path.write_text("- just\n- a\n- list\n")
    cfg = load_config()
    assert cfg.port == MarginConfig().port
    captured = capsys.readouterr()
    assert "must be a mapping" in (captured.out + captured.err)


def test_empty_yaml_uses_defaults(cfg_path):
    cfg_path.write_text("")
    cfg = load_config()
    assert cfg.port == MarginConfig().port


def test_host_default_is_localhost():
    """Margin is private notes - it must NOT inherit Memory's 0.0.0.0 default.
    Follow-the-leader on structure, not on the security default.

    Mounting an MCP surface does not change this: the transport got wider, the
    listener did not.
    """
    assert MarginConfig().host == "127.0.0.1"


def test_default_port_is_7421():
    assert MarginConfig().port == 7421
