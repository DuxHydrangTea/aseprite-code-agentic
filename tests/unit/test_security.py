"""Unit tests for WorkspacePathGuard, ASTSafetyChecker, and build_sanitized_env."""

from __future__ import annotations

from pathlib import Path

import pytest

from aseprite_codemode.config import RuntimeConfig
from aseprite_codemode.security import (
    ASTSafetyChecker,
    UnsafeCodeError,
    WorkspacePathGuard,
    WorkspaceViolationError,
    build_sanitized_env,
)


def test_path_guard_allows_workspace_paths(tmp_workspace: Path) -> None:
    guard = WorkspacePathGuard(tmp_workspace)

    assert Path(guard.resolve_path("slime.aseprite")) == (tmp_workspace / "slime.aseprite").resolve()
    assert Path(guard.resolve_path("nested/dir/hero.aseprite")) == (
        tmp_workspace / "nested" / "dir" / "hero.aseprite"
    ).resolve()
    assert Path(guard.resolve_path(str(tmp_workspace / "abs.aseprite"))) == (
        tmp_workspace / "abs.aseprite"
    ).resolve()
    assert Path(guard.resolve_path("foo..bar.aseprite")) == (
        tmp_workspace / "foo..bar.aseprite"
    ).resolve()
    assert guard.resolve_path("", allow_empty=True) == ""


@pytest.mark.parametrize(
    "bad_path",
    [
        "",
        "   ",
        "../outside.aseprite",
        "sub/../../outside.aseprite",
        "~/secret.aseprite",
        "foo\x00bar.aseprite",
        r"C:\Windows\System32\drivers\etc\hosts",
        r"D:\evil\sprite.aseprite",
        "D:relative_drive.aseprite",
        r"\\server\share\evil.aseprite",
    ],
)
def test_path_guard_rejects_escapes(tmp_workspace: Path, bad_path: str) -> None:
    guard = WorkspacePathGuard(tmp_workspace)
    with pytest.raises(WorkspaceViolationError):
        guard.resolve_path(bad_path, allow_empty=False)


def test_path_guard_rejects_raw_fd_and_bytes(tmp_workspace: Path) -> None:
    guard = WorkspacePathGuard(tmp_workspace)

    with pytest.raises(WorkspaceViolationError):
        guard.safe_open(3, "r")

    with pytest.raises(WorkspaceViolationError):
        guard.safe_open(b"bytes_path.txt", "r")


def test_path_guard_rejects_symlink_escape(tmp_workspace: Path, tmp_path: Path) -> None:
    guard = WorkspacePathGuard(tmp_workspace)

    outside_file = tmp_path / "secret.txt"
    outside_file.write_text("top-secret", encoding="utf-8")
    link_path = tmp_workspace / "escape_link.txt"

    try:
        link_path.symlink_to(outside_file)
    except OSError as exc:
        pytest.skip(f"Symlink creation unavailable on this host: {exc}")

    with pytest.raises(WorkspaceViolationError):
        guard.resolve_path("escape_link.txt")


def test_ast_checker_allows_safe_code() -> None:
    code = """
import math
import json
from colorsys import hsv_to_rgb

pts = [(int(math.cos(i) * 4), int(math.sin(i) * 4)) for i in range(6)]
rgb = hsv_to_rgb(0.5, 0.8, 0.9)
result = json.dumps({"pts": len(pts), "rgb": rgb})
"""
    tree = ASTSafetyChecker.check(code)
    assert tree is not None


@pytest.mark.parametrize(
    "unsafe_code",
    [
        "",
        "   \n  ",
        "def broken(:",
        "import os",
        "import subprocess",
        "import aseprite_codemode",
        "from aseprite_codemode import worker",
        "from aseprite_codemode.security import _BUILTIN_OPEN",
        "from pathlib import Path",
        "from . import something",
        "eval('1+1')",
        "exec('x = 1')",
        "compile('x=1', '', 'exec')",
        "__import__('os')",
        "globals()",
        "locals()",
        "getattr(ase, '__dict__')",
        "x = ().__class__.__bases__",
        "x = __builtins__",
    ],
)
def test_ast_checker_blocks_unsafe_constructs(unsafe_code: str) -> None:
    with pytest.raises(UnsafeCodeError):
        ASTSafetyChecker.check(unsafe_code)


def test_build_sanitized_env_strips_secrets(monkeypatch, test_config: RuntimeConfig) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret-ant")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_secret")
    monkeypatch.setenv("CUSTOM_SECRET_VAR", "do-not-leak")

    env = build_sanitized_env(test_config)
    assert "OPENAI_API_KEY" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert "GITHUB_TOKEN" not in env
    assert "CUSTOM_SECRET_VAR" not in env
    assert env["ASEPRITE_PATH"] == test_config.aseprite_path
    assert env["ASEPRITE_WORKSPACE"] == str(test_config.workspace_dir)
    assert env["ASEPRITE_ALLOW_RAW_LUA"] == "0"
    assert env["ASEPRITE_ALLOW_PREVIEW_SERVER"] == "0"
    assert env["PYTHONUTF8"] == "1"
    assert str(test_config.upstream_repo_path) in env["PYTHONPATH"]
