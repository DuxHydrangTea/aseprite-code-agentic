"""Unit tests for RuntimeConfig loading and clamping."""

from __future__ import annotations

from pathlib import Path

from aseprite_codemode.config import (
    DEFAULT_ASEPRITE_TIMEOUT_SECONDS,
    DEFAULT_CODE_TIMEOUT_SECONDS,
    MAX_CODE_TIMEOUT_SECONDS,
    MIN_CODE_TIMEOUT_SECONDS,
    load_config,
)


def test_load_config_defaults(monkeypatch, tmp_path: Path) -> None:
    for key in (
        "ASEPRITE_PATH",
        "ASEPRITE_WORKSPACE",
        "ASEPRITE_TIMEOUT_SECONDS",
        "ASEPRITE_CODE_TIMEOUT_SECONDS",
        "ASEPRITE_ALLOW_RAW_LUA",
        "ASEPRITE_ALLOW_PREVIEW_SERVER",
        "ASEPRITE_WARM_WORKER",
        "ASEPRITE_MCP_REPO",
        "ASEPRITE_MAX_OUTPUT_CHARS",
        "ASEPRITE_MAX_PREVIEW_IMAGES",
    ):
        monkeypatch.delenv(key, raising=False)

    cfg = load_config(
        overrides={"workspace_dir": tmp_path / "ws"},
        load_env_file=False,
    )
    assert cfg.workspace_dir == (tmp_path / "ws").resolve()
    assert cfg.workspace_dir.is_dir()
    assert cfg.aseprite_path == "aseprite"
    assert cfg.aseprite_timeout_seconds == DEFAULT_ASEPRITE_TIMEOUT_SECONDS
    assert cfg.code_timeout_seconds == DEFAULT_CODE_TIMEOUT_SECONDS
    assert cfg.allow_raw_lua is False
    assert cfg.allow_preview_server is False
    assert cfg.warm_worker is False
    assert cfg.max_output_chars == 16000
    assert cfg.max_preview_images == 4


def test_load_config_env_overrides_and_booleans(monkeypatch, tmp_path: Path) -> None:
    ws = tmp_path / "custom_ws"
    monkeypatch.setenv("ASEPRITE_WORKSPACE", str(ws))
    monkeypatch.setenv("ASEPRITE_PATH", "C:/custom/aseprite.exe")
    monkeypatch.setenv("ASEPRITE_TIMEOUT_SECONDS", "45")
    monkeypatch.setenv("ASEPRITE_CODE_TIMEOUT_SECONDS", "120")
    monkeypatch.setenv("ASEPRITE_ALLOW_RAW_LUA", "true")
    monkeypatch.setenv("ASEPRITE_ALLOW_PREVIEW_SERVER", "yes")
    monkeypatch.setenv("ASEPRITE_WARM_WORKER", "1")
    monkeypatch.setenv("ASEPRITE_MAX_OUTPUT_CHARS", "4096")
    monkeypatch.setenv("ASEPRITE_MAX_PREVIEW_IMAGES", "2")

    cfg = load_config(load_env_file=False)
    assert cfg.workspace_dir == ws.resolve()
    assert cfg.aseprite_path == "C:/custom/aseprite.exe"
    assert cfg.aseprite_timeout_seconds == 45.0
    assert cfg.code_timeout_seconds == 120.0
    assert cfg.allow_raw_lua is True
    assert cfg.allow_preview_server is True
    assert cfg.warm_worker is True
    assert cfg.max_output_chars == 4096
    assert cfg.max_preview_images == 2


def test_invalid_nan_negative_and_clamped_timeouts(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ASEPRITE_WORKSPACE", str(tmp_path))

    for bad in ("not-a-number", "-5", "0", "nan", "inf", "-inf"):
        monkeypatch.setenv("ASEPRITE_TIMEOUT_SECONDS", bad)
        monkeypatch.setenv("ASEPRITE_CODE_TIMEOUT_SECONDS", bad)
        cfg = load_config(load_env_file=False)
        assert cfg.aseprite_timeout_seconds == DEFAULT_ASEPRITE_TIMEOUT_SECONDS
        assert cfg.code_timeout_seconds == DEFAULT_CODE_TIMEOUT_SECONDS

    monkeypatch.setenv("ASEPRITE_CODE_TIMEOUT_SECONDS", "0.05")
    assert load_config(load_env_file=False).code_timeout_seconds == MIN_CODE_TIMEOUT_SECONDS

    monkeypatch.setenv("ASEPRITE_CODE_TIMEOUT_SECONDS", "9999")
    assert load_config(load_env_file=False).code_timeout_seconds == MAX_CODE_TIMEOUT_SECONDS
