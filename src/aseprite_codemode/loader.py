"""Dynamic loader for upstream diivi/aseprite-mcp modules."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType


class UpstreamNotFoundError(RuntimeError):
    """Raised when the upstream diivi/aseprite-mcp repository cannot be located or loaded."""


def _validate_upstream_root(upstream_repo_path: Path) -> Path:
    repo = Path(upstream_repo_path).resolve()
    pkg_init = repo / "aseprite_mcp" / "__init__.py"
    if not pkg_init.is_file():
        raise UpstreamNotFoundError(
            f"Upstream diivi/aseprite-mcp not found at '{repo}'. "
            "Clone https://github.com/diivi/aseprite-mcp into vendor/aseprite-mcp "
            "or set ASEPRITE_MCP_REPO."
        )
    return repo


def list_upstream_categories(upstream_repo_path: Path) -> list[str]:
    """List upstream tool module names without importing them."""
    repo = _validate_upstream_root(upstream_repo_path)
    tools_dir = repo / "aseprite_mcp" / "tools"
    if not tools_dir.is_dir():
        return []
    return sorted(
        p.stem
        for p in tools_dir.glob("*.py")
        if not p.name.startswith("_")
    )


def load_upstream_aseprite_mcp(
    upstream_repo_path: Path,
    *,
    load_all_tools: bool = True,
) -> ModuleType:
    """Import upstream `aseprite_mcp` and optionally all tool modules.

    Our server creates its own independent `FastMCP` instance and never exposes
    upstream's `aseprite_mcp.mcp` directly.
    """
    repo = _validate_upstream_root(upstream_repo_path)
    repo_str = str(repo)
    if repo_str not in sys.path:
        sys.path.insert(0, repo_str)

    aseprite_mcp = importlib.import_module("aseprite_mcp")
    if load_all_tools:
        importlib.import_module("aseprite_mcp.tools")
    return aseprite_mcp
