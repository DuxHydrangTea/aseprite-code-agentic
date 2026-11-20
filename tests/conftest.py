"""Shared pytest fixtures for unit and integration tests."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from aseprite_codemode.catalog import ToolCatalog
from aseprite_codemode.config import RuntimeConfig, load_config, project_root
from aseprite_codemode.loader import load_upstream_aseprite_mcp


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return project_root()


@pytest.fixture(scope="session")
def upstream_repo_path(repo_root: Path) -> Path:
    path = repo_root / "vendor" / "aseprite-mcp"
    if not (path / "aseprite_mcp" / "__init__.py").is_file():
        pytest.skip(f"Upstream repo not cloned at {path}")
    return path


@pytest.fixture(scope="session")
def upstream_pkg(upstream_repo_path: Path):
    return load_upstream_aseprite_mcp(upstream_repo_path)


@pytest.fixture(scope="session")
def catalog(upstream_pkg) -> ToolCatalog:
    return ToolCatalog.from_upstream(upstream_pkg)


@pytest.fixture()
def tmp_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


@pytest.fixture()
def test_config(tmp_workspace: Path, upstream_repo_path: Path) -> RuntimeConfig:
    return RuntimeConfig(
        workspace_dir=tmp_workspace,
        aseprite_path=os.environ.get("ASEPRITE_PATH", "aseprite"),
        upstream_repo_path=upstream_repo_path,
        aseprite_timeout_seconds=30.0,
        code_timeout_seconds=30.0,
        allow_raw_lua=False,
        allow_preview_server=False,
        max_output_chars=16000,
        max_preview_images=4,
    )


@pytest.fixture(scope="session")
def real_aseprite_path() -> str:
    cfg = load_config()
    candidate = cfg.aseprite_path
    if os.path.isabs(candidate) and Path(candidate).is_file():
        return candidate
    found = shutil.which(candidate)
    if found:
        return found
    pytest.skip(f"Aseprite binary not found at '{candidate}'")
