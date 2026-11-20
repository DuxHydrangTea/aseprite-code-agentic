"""Unit tests for subprocess SandboxRunner and child worker isolation."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from aseprite_codemode.config import RuntimeConfig
from aseprite_codemode.sandbox_runner import SandboxRunner


def test_sandbox_executes_safe_code_and_tracks_files(test_config: RuntimeConfig) -> None:
    runner = SandboxRunner(test_config)

    res1 = runner.run(
        """
import json
with open("meta.json", "w", encoding="utf-8") as f:
    json.dump({"ok": True}, f)
print("hello sandbox")
result = {"count": 2, "files": ase.list_files()}
"""
    )
    assert res1.status == "ok"
    assert "hello sandbox" in res1.stdout
    assert res1.files_created == ["meta.json"]
    assert res1.files_modified == []
    assert res1.return_value == {"count": 2, "files": ["meta.json"]}

    res2 = runner.run(
        """
with open("meta.json", "w", encoding="utf-8") as f:
    f.write('{"ok": true, "v": 2}')
result = "updated"
"""
    )
    assert res2.status == "ok"
    assert res2.files_created == []
    assert res2.files_modified == ["meta.json"]
    assert res2.return_value == "updated"


def test_sandbox_blocks_open_traversal_unsafe_imports_and_private_escapes(
    test_config: RuntimeConfig,
    tmp_path: Path,
) -> None:
    runner = SandboxRunner(test_config)
    outside = tmp_path / "outside.txt"

    res_ast = runner.run("import os\nresult = os.getcwd()")
    assert res_ast.status == "security_error"
    assert res_ast.error_type == "UnsafeCodeError"

    res_pkg = runner.run("from aseprite_codemode import worker")
    assert res_pkg.status == "security_error"
    assert res_pkg.error_type == "UnsafeCodeError"

    res_priv1 = runner.run("x = ase._upstream_pkg")
    assert res_priv1.status == "error"
    assert res_priv1.error_type == "AttributeError"

    res_priv2 = runner.run("ase.script._allow_raw_lua = True")
    assert res_priv2.status == "error"
    assert res_priv2.error_type == "AttributeError"

    res_open = runner.run('with open("../outside.txt", "w") as f:\n    f.write("nope")')
    assert res_open.status == "security_error"
    assert res_open.error_type == "WorkspaceViolationError"
    assert not outside.exists()


def test_sandbox_lazy_loading_and_strict_tool_warnings(test_config: RuntimeConfig) -> None:
    runner = SandboxRunner(test_config)

    res_strict = runner.run('ase.animation.get_sprite_info("nonexistent.aseprite")')
    assert res_strict.status == "error"
    assert res_strict.error_type == "AsepriteToolError"

    res_warn = runner.run(
        """
ramp = ase.palette.generate_color_ramp("#346524", steps=3)
ase.strict = False
msg = ase.animation.get_sprite_info("nonexistent.aseprite")
result = {"ramp": ramp, "msg": msg, "warnings": ase.tool_warnings}
"""
    )
    assert res_warn.status == "ok"
    assert len(res_warn.tool_warnings) == 1
    assert "ase.animation.get_sprite_info" in res_warn.tool_warnings[0]
    assert res_warn.return_value["warnings"] == res_warn.tool_warnings


def test_sandbox_enforces_timeout_and_truncates_output(test_config: RuntimeConfig) -> None:
    cfg = replace(test_config, max_output_chars=300)
    runner = SandboxRunner(cfg)

    res_timeout = runner.run("while True:\n    pass", timeout_seconds=1.0)
    assert res_timeout.status == "timeout"
    assert res_timeout.error_type == "TimeoutExpired"

    res_trunc = runner.run("print('A' * 1000)")
    assert res_trunc.status == "ok"
    assert len(res_trunc.stdout) <= 300
    assert res_trunc.stdout.endswith("...[truncated]")


def test_sandbox_captures_runtime_exception(test_config: RuntimeConfig) -> None:
    runner = SandboxRunner(test_config)
    res = runner.run("x = 1 / 0")
    assert res.status == "error"
    assert res.error_type == "ZeroDivisionError"
    assert res.traceback is not None
    assert "ZeroDivisionError" in res.traceback


def test_warm_worker_isolates_state_and_recovers_after_timeout(test_config: RuntimeConfig) -> None:
    cfg = replace(test_config, warm_worker=True)
    runner = SandboxRunner(cfg)
    try:
        r1 = runner.run("leaked_var = 999\ng = ase.Grid(4, 4).rect(0, 0, 2, 2, 'X')\nresult = g.bottom_y()")
        assert r1.status == "ok"
        assert r1.return_value == 1

        r2 = runner.run("try:\n    _ = leaked_var\n    result = True\nexcept NameError:\n    result = False")
        assert r2.status == "ok"
        assert r2.return_value is False

        r_timeout = runner.run("while True:\n    pass", timeout_seconds=1.0)
        assert r_timeout.status == "timeout"

        r3 = runner.run("result = 'recovered'")
        assert r3.status == "ok"
        assert r3.return_value == "recovered"
    finally:
        runner.close()
