"""Isolated child-process worker executing agent Python code with the `ase` SDK."""

from __future__ import annotations

import builtins
import contextlib
import io
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any

from .config import RuntimeConfig
from .loader import load_upstream_aseprite_mcp
from .sdk import (
    AsepriteSDK,
    AsepriteToolError,
    PreviewCollector,
    create_sandbox_sdk,
)
from .security import (
    SAFE_IMPORT_MODULES,
    ASTSafetyChecker,
    SecurityError,
    UnsafeCodeError,
    WorkspacePathGuard,
)

_SAFE_BUILTIN_NAMES: tuple[str, ...] = (
    "abs",
    "all",
    "any",
    "ascii",
    "bin",
    "bool",
    "bytearray",
    "bytes",
    "callable",
    "chr",
    "complex",
    "dict",
    "divmod",
    "enumerate",
    "filter",
    "float",
    "format",
    "frozenset",
    "hash",
    "hex",
    "int",
    "isinstance",
    "issubclass",
    "iter",
    "len",
    "list",
    "map",
    "max",
    "min",
    "next",
    "object",
    "oct",
    "ord",
    "pow",
    "print",
    "range",
    "repr",
    "reversed",
    "round",
    "set",
    "slice",
    "sorted",
    "str",
    "sum",
    "tuple",
    "type",
    "zip",
    # Constants
    "True",
    "False",
    "None",
    "NotImplemented",
    "Ellipsis",
    # Standard exceptions for structured error handling in agent code
    "ArithmeticError",
    "AssertionError",
    "AttributeError",
    "Exception",
    "FileExistsError",
    "FileNotFoundError",
    "IndexError",
    "KeyError",
    "LookupError",
    "NameError",
    "NotImplementedError",
    "OSError",
    "RuntimeError",
    "StopIteration",
    "TypeError",
    "ValueError",
    "ZeroDivisionError",
)


def _make_safe_import() -> Any:
    real_import = builtins.__import__

    def _safe_import(
        name: str,
        globals: dict[str, Any] | None = None,
        locals: dict[str, Any] | None = None,
        fromlist: tuple[str, ...] = (),
        level: int = 0,
    ) -> Any:
        if level != 0:
            raise UnsafeCodeError("Relative imports are not allowed in Code Mode sandbox")
        top = name.split(".", 1)[0]
        if top not in SAFE_IMPORT_MODULES:
            raise UnsafeCodeError(f"Import of '{name}' is not allowed in Code Mode sandbox")
        return real_import(name, globals, locals, fromlist, level)

    return _safe_import


def _build_restricted_builtins(guard: WorkspacePathGuard) -> dict[str, Any]:
    restricted: dict[str, Any] = {}
    for name in _SAFE_BUILTIN_NAMES:
        if hasattr(builtins, name):
            restricted[name] = getattr(builtins, name)
    restricted["open"] = guard.safe_open
    restricted["__import__"] = _make_safe_import()
    return restricted


def _to_json_safe(value: Any, *, depth: int = 0) -> Any:
    if depth > 6:
        return repr(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_to_json_safe(v, depth=depth + 1) for v in value]
    if isinstance(value, dict):
        return {str(k): _to_json_safe(v, depth=depth + 1) for k, v in value.items()}
    return repr(value)


def execute_job_file(job_spec_path: Path) -> int:
    """Load `job_spec_path`, run agent code inside workspace sandbox, and write `result_path`."""
    spec = json.loads(job_spec_path.read_text(encoding="utf-8"))

    workspace_dir = Path(spec["workspace_dir"]).resolve()
    preview_dir = Path(spec["preview_dir"]).resolve()
    result_path = Path(spec["result_path"]).resolve()
    upstream_repo_path = Path(spec["upstream_repo_path"]).resolve()

    # Configure upstream AsepriteCommand environment BEFORE importing aseprite_mcp.
    os.environ["ASEPRITE_PATH"] = str(spec["aseprite_path"])
    os.environ["ASEPRITE_TIMEOUT_SECONDS"] = str(spec["aseprite_timeout_seconds"])

    config = RuntimeConfig(
        workspace_dir=workspace_dir,
        aseprite_path=str(spec["aseprite_path"]),
        upstream_repo_path=upstream_repo_path,
        aseprite_timeout_seconds=float(spec["aseprite_timeout_seconds"]),
        code_timeout_seconds=float(spec.get("code_timeout_seconds", 60.0)),
        allow_raw_lua=bool(spec.get("allow_raw_lua", False)),
        allow_preview_server=bool(spec.get("allow_preview_server", False)),
        max_output_chars=int(spec.get("max_output_chars", 16000)),
        max_preview_images=int(spec.get("max_preview_images", 4)),
    )

    guard = WorkspacePathGuard(workspace_dir)
    collector = PreviewCollector(preview_dir=preview_dir, max_images=config.max_preview_images)

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    status = "ok"
    return_value: Any = None
    error_msg: str | None = None
    error_type: str | None = None
    tb_str: str | None = None
    sdk: AsepriteSDK | None = None

    try:
        upstream_pkg = load_upstream_aseprite_mcp(upstream_repo_path, load_all_tools=False)
        sdk = AsepriteSDK(
            config=config,
            upstream_pkg=upstream_pkg,
            catalog=None,
            collector=collector,
        )
        sandbox_ase = create_sandbox_sdk(sdk)

        code = str(spec["code"])
        tree = ASTSafetyChecker.check(code)
        compiled = compile(tree, "<agent_code>", "exec")

        namespace: dict[str, Any] = {
            "__builtins__": _build_restricted_builtins(guard),
            "ase": sandbox_ase,
            "AsepriteToolError": AsepriteToolError,
            "WORKSPACE": str(workspace_dir),
        }

        with contextlib.redirect_stdout(stdout_buf), contextlib.redirect_stderr(stderr_buf):
            exec(compiled, namespace, namespace)  # noqa: S102 (AST-validated + restricted builtins)

            auto_view = str(spec.get("auto_view") or "").strip()
            if auto_view:
                view_opts = dict(spec.get("view_options") or {})
                before_previews = collector.count
                try:
                    auto_res = sdk.show(auto_view, **view_opts)
                except AsepriteToolError as exc:
                    stderr_buf.write(f"auto_view failed: {exc}\n")
                    status = "error"
                    error_msg = f"auto_view failed: {exc}"
                    error_type = "AutoViewError"
                else:
                    if collector.count == before_previews:
                        if collector.last_status == "limit_reached":
                            stderr_buf.write(
                                "Note: auto_view omitted because max_preview_images limit was already reached.\n"
                            )
                        else:
                            stderr_buf.write(f"auto_view failed: {auto_res}\n")
                            status = "error"
                            error_msg = f"auto_view failed: {auto_res}"
                            error_type = "AutoViewError"

        if "result" in namespace:
            return_value = _to_json_safe(namespace["result"])

    except SecurityError as exc:
        status = "security_error"
        error_msg = str(exc)
        error_type = type(exc).__name__
        tb_str = traceback.format_exc()
    except Exception as exc:
        status = "error"
        error_msg = str(exc)
        error_type = type(exc).__name__
        tb_str = traceback.format_exc()

    payload = {
        "status": status,
        "stdout": stdout_buf.getvalue(),
        "stderr": stderr_buf.getvalue(),
        "return_value": return_value,
        "previews": collector.metadata_list(),
        "tool_warnings": list(sdk.tool_warnings) if sdk is not None else [],
        "error": error_msg,
        "error_type": error_type,
        "traceback": tb_str,
    }
    result_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return 0


def serve_loop() -> int:
    """Long-lived warm worker loop accepting newline-delimited JSON job paths on stdin."""
    proto_out = sys.stdout
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            job_path = Path(req["job_spec_path"])
            execute_job_file(job_path)
            proto_out.write(json.dumps({"ok": True}) + "\n")
        except Exception as exc:
            proto_out.write(json.dumps({"ok": False, "error": str(exc)}) + "\n")
        proto_out.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if args == ["--serve"]:
        return serve_loop()
    if len(args) != 1:
        sys.stderr.write("Usage: python -m aseprite_codemode.worker (<job_spec.json> | --serve)\n")
        return 2
    return execute_job_file(Path(args[0]))


if __name__ == "__main__":
    raise SystemExit(main())
