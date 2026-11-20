"""Security guards, AST checker, path confinement, and sanitized subprocess env."""

from __future__ import annotations

import ast
import builtins
import os
from pathlib import Path
from typing import IO, Any

from .config import RuntimeConfig

_BUILTIN_OPEN = builtins.open


class SecurityError(Exception):
    """Base error for sandbox and policy violations."""


class WorkspaceViolationError(SecurityError):
    """Raised when a path resolves outside the confined workspace directory."""


class UnsafeCodeError(SecurityError):
    """Raised when agent-submitted Python code violates AST sandbox rules."""


class PolicyBlockedToolError(SecurityError):
    """Raised when calling an upstream tool disabled by runtime policy."""


# Verified against all 123 tools in diivi/aseprite-mcp.
PATH_PARAM_NAMES: frozenset[str] = frozenset({
    "filename",
    "output_filename",
    "source_filename",
    "target_filename",
    "data_filename",
    "output_directory",
    "directory",
    "image_path",
})

# Parameters whose values may be either a symbolic name (e.g. "Arial") or a filesystem path.
CONDITIONAL_PATH_PARAM_NAMES: frozenset[str] = frozenset({"font"})

# Non-path parameters (including names containing path-like substrings such as "out", "dir", "name", "source", "target").
NON_PATH_PARAM_NAMES: frozenset[str] = frozenset({
    "layer_name",
    "layer_names",
    "target_layer_name",
    "group",
    "group_name",
    "parent_group",
    "name",
    "new_name",
    "tag",
    "tag_name",
    "direction",
    "source_frame",
    "source_frame_index",
    "target_frame",
    "target_frame_index",
    "data_format",
    "format",
    "out_of_range_action",
    "out_of_range_opacity",
    "outline",
    "outline_color",
    "outline_diagonal",
    "outline_width",
    "max_out_of_range",
    "layer_frame_ranges",
    "required_layers",
    "ensure_layers",
    "report_bounds",
    "report_cels",
    "report_only",
    "create_if_missing",
    "create_missing_cels",
    "create_missing_frames",
    "frame_index",
    "from_frame",
    "to_frame",
    "start_frame",
    "end_frame",
    "frame_a",
    "frame_b",
    "frames",
    "layer_order",
    "overlap_pairs",
    "ignore_full_canvas_overlaps",
})

PREVIEW_SERVER_TOOLS: frozenset[str] = frozenset({
    "start_preview_server",
    "stop_preview_server",
})

RAW_LUA_TOOLS: frozenset[str] = frozenset({
    "run_lua_script",
})

SAFE_IMPORT_MODULES: frozenset[str] = frozenset({
    "math",
    "cmath",
    "json",
    "re",
    "random",
    "colorsys",
    "itertools",
    "functools",
    "collections",
    "dataclasses",
    "typing",
    "statistics",
    "decimal",
    "fractions",
    "string",
    "textwrap",
    "copy",
    "enum",
    "datetime",
})

BANNED_CALL_NAMES: frozenset[str] = frozenset({
    "eval",
    "exec",
    "compile",
    "__import__",
    "breakpoint",
    "input",
    "globals",
    "locals",
    "vars",
    "getattr",
    "setattr",
    "delattr",
    "memoryview",
})

BANNED_NAMES: frozenset[str] = frozenset({
    "__builtins__",
    "__import__",
    "__loader__",
    "__spec__",
    "__subclasses__",
})

_ESSENTIAL_OS_ENV_KEYS: tuple[str, ...] = (
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "PATH",
    "TEMP",
    "TMP",
    "HOME",
    "USERPROFILE",
    "LOCALAPPDATA",
    "APPDATA",
    "LANG",
    "LC_ALL",
    "PYTHONUTF8",
)

_FONT_EXTENSIONS = (".ttf", ".otf", ".ttc")


def looks_like_filesystem_path(value: str) -> bool:
    """Return True when a symbolic/path parameter (such as `font`) looks like a path."""
    if not value:
        return False
    lower = value.lower()
    return (
        "/" in value
        or "\\" in value
        or value.startswith("~")
        or ".." in value
        or ":" in value
        or lower.endswith(_FONT_EXTENSIONS)
    )


class WorkspacePathGuard:
    """Confines every filesystem read/write path to a designated workspace directory."""

    def __init__(self, workspace_dir: Path | str) -> None:
        self.workspace_dir: Path = Path(workspace_dir).expanduser().resolve()
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

    def resolve_path(self, raw_path: str, *, allow_empty: bool = False) -> str:
        """Resolve `raw_path` inside `workspace_dir` or raise `WorkspaceViolationError`."""
        if not isinstance(raw_path, str):
            raise WorkspaceViolationError(f"Expected string path, got {type(raw_path).__name__}")
        if "\x00" in raw_path:
            raise WorkspaceViolationError("NUL byte not allowed in path")
        if raw_path == "":
            if allow_empty:
                return ""
            raise WorkspaceViolationError("Path cannot be empty")
        if raw_path.strip() == "":
            raise WorkspaceViolationError("Path cannot be blank whitespace")

        if raw_path.startswith("~"):
            candidate = Path(raw_path).expanduser().resolve(strict=False)
        else:
            p = Path(raw_path)
            if p.drive and not p.is_absolute():
                raise WorkspaceViolationError(
                    f"Drive-relative path '{raw_path}' is not allowed outside workspace"
                )
            candidate = p if p.is_absolute() else (self.workspace_dir / p)

        try:
            resolved = candidate.resolve(strict=False)
        except OSError as exc:
            raise WorkspaceViolationError(f"Invalid path '{raw_path}': {exc}") from exc

        if not resolved.is_relative_to(self.workspace_dir):
            raise WorkspaceViolationError(
                f"Access denied: path '{raw_path}' resolves outside workspace '{self.workspace_dir}'"
            )

        return str(resolved)

    def relative_posix(self, path: str | Path) -> str:
        """Return a POSIX path relative to `workspace_dir`."""
        resolved = Path(self.resolve_path(str(path)))
        return resolved.relative_to(self.workspace_dir).as_posix()

    def safe_open(self, file: Any, mode: str = "r", *args: Any, **kwargs: Any) -> IO[Any]:
        """Replacement for `builtins.open` confined to `workspace_dir`."""
        if isinstance(file, int):
            raise WorkspaceViolationError("Opening raw integer file descriptors is not permitted")
        if isinstance(file, bytes):
            raise WorkspaceViolationError("Bytes paths are not permitted; pass a string path")
        path_str = os.fspath(file)
        if not isinstance(path_str, str):
            raise WorkspaceViolationError("Path must be a string or str PathLike")
        safe_path = self.resolve_path(path_str, allow_empty=False)
        return _BUILTIN_OPEN(safe_path, mode, *args, **kwargs)


class ASTSafetyChecker:
    """Static AST validator enforcing import and builtin restrictions before execution."""

    @staticmethod
    def check(code: str) -> ast.Module:
        """Validate `code` and return parsed AST, or raise `UnsafeCodeError`."""
        if not isinstance(code, str) or not code.strip():
            raise UnsafeCodeError("Code must be a non-empty string")

        try:
            tree = ast.parse(code, mode="exec")
        except SyntaxError as exc:
            raise UnsafeCodeError(f"Syntax error at line {exc.lineno}: {exc.msg}") from exc

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".", 1)[0]
                    if top not in SAFE_IMPORT_MODULES:
                        raise UnsafeCodeError(
                            f"Import of module '{alias.name}' is not allowed in Code Mode sandbox"
                        )
            elif isinstance(node, ast.ImportFrom):
                if node.level > 0 or not node.module:
                    raise UnsafeCodeError("Relative imports are not allowed in Code Mode sandbox")
                top = node.module.split(".", 1)[0]
                if top not in SAFE_IMPORT_MODULES:
                    raise UnsafeCodeError(
                        f"Import from module '{node.module}' is not allowed in Code Mode sandbox"
                    )
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id in BANNED_CALL_NAMES:
                    raise UnsafeCodeError(
                        f"Calling '{node.func.id}()' is not allowed in Code Mode sandbox"
                    )
            elif isinstance(node, ast.Attribute):
                if node.attr.startswith("__") and node.attr.endswith("__"):
                    raise UnsafeCodeError(
                        f"Access to dunder attribute '{node.attr}' is not allowed in Code Mode sandbox"
                    )
            elif isinstance(node, ast.Name):
                if node.id in BANNED_NAMES:
                    raise UnsafeCodeError(
                        f"Reference to '{node.id}' is not allowed in Code Mode sandbox"
                    )

        return tree


def build_sanitized_env(
    config: RuntimeConfig,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Construct an allowlisted subprocess environment stripping host secrets."""
    env: dict[str, str] = {}
    for key in _ESSENTIAL_OS_ENV_KEYS:
        val = os.environ.get(key)
        if val is not None:
            env[key] = val

    src_dir = Path(__file__).resolve().parents[1]
    pythonpath_entries = [str(src_dir), str(config.upstream_repo_path)]

    env.update({
        "ASEPRITE_PATH": config.aseprite_path,
        "ASEPRITE_WORKSPACE": str(config.workspace_dir),
        "ASEPRITE_TIMEOUT_SECONDS": str(config.aseprite_timeout_seconds),
        "ASEPRITE_MCP_REPO": str(config.upstream_repo_path),
        "ASEPRITE_ALLOW_RAW_LUA": "1" if config.allow_raw_lua else "0",
        "ASEPRITE_ALLOW_PREVIEW_SERVER": "1" if config.allow_preview_server else "0",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUTF8": "1",
        "PYTHONPATH": os.pathsep.join(pythonpath_entries),
    })

    if extra:
        env.update(extra)

    return env
