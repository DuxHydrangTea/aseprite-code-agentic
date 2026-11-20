"""Runtime configuration for aseprite-codemode."""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

DEFAULT_ASEPRITE_TIMEOUT_SECONDS = 60.0
DEFAULT_CODE_TIMEOUT_SECONDS = 60.0
MIN_CODE_TIMEOUT_SECONDS = 1.0
MAX_CODE_TIMEOUT_SECONDS = 300.0
DEFAULT_MAX_OUTPUT_CHARS = 16000
DEFAULT_MAX_PREVIEW_IMAGES = 4

_TRUTHY = frozenset({"1", "true", "yes", "on"})
_FALSY = frozenset({"0", "false", "no", "off", ""})


def project_root() -> Path:
    """Return the root directory of the aseprite-codemode repository."""
    return Path(__file__).resolve().parents[2]


def _parse_bool(raw: Any, default: bool = False) -> bool:
    if isinstance(raw, bool):
        return raw
    if raw is None:
        return default
    text = str(raw).strip().lower()
    if text in _TRUTHY:
        return True
    if text in _FALSY:
        return False
    return default


def _parse_positive_float(raw: Any, default: float) -> float:
    if raw is None or str(raw).strip() == "":
        return default
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(val) or val <= 0:
        return default
    return val


def _parse_positive_int(raw: Any, default: int, *, minimum: int = 1) -> int:
    if raw is None or str(raw).strip() == "":
        return default
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return default
    return val if val >= minimum else default


def clamp_code_timeout(seconds: float) -> float:
    """Clamp script execution timeout to [MIN_CODE_TIMEOUT_SECONDS, MAX_CODE_TIMEOUT_SECONDS]."""
    if not math.isfinite(seconds) or seconds <= 0:
        return DEFAULT_CODE_TIMEOUT_SECONDS
    return max(MIN_CODE_TIMEOUT_SECONDS, min(MAX_CODE_TIMEOUT_SECONDS, float(seconds)))


@dataclass(frozen=True)
class RuntimeConfig:
    """Resolved runtime settings for the MCP server and execution sandbox."""

    workspace_dir: Path
    aseprite_path: str
    upstream_repo_path: Path
    aseprite_timeout_seconds: float = DEFAULT_ASEPRITE_TIMEOUT_SECONDS
    code_timeout_seconds: float = DEFAULT_CODE_TIMEOUT_SECONDS
    allow_raw_lua: bool = False
    allow_preview_server: bool = False
    warm_worker: bool = False
    max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS
    max_preview_images: int = DEFAULT_MAX_PREVIEW_IMAGES


def load_config(
    overrides: dict[str, Any] | None = None,
    *,
    load_env_file: bool = True,
) -> RuntimeConfig:
    """Load configuration from `.env`, process environment, and optional overrides."""
    root = project_root()
    if load_env_file:
        env_file = root / ".env"
        if env_file.is_file():
            load_dotenv(dotenv_path=env_file, override=False)

    ov = overrides or {}

    def _get(env_key: str, ov_key: str, default: Any = None) -> Any:
        if ov_key in ov and ov[ov_key] is not None:
            return ov[ov_key]
        val = os.environ.get(env_key)
        if val is not None and val != "":
            return val
        return default

    raw_workspace = _get("ASEPRITE_WORKSPACE", "workspace_dir", root / "workspace")
    workspace_dir = Path(raw_workspace).expanduser().resolve()
    workspace_dir.mkdir(parents=True, exist_ok=True)

    aseprite_path = str(_get("ASEPRITE_PATH", "aseprite_path", "aseprite")).strip() or "aseprite"

    raw_upstream = _get("ASEPRITE_MCP_REPO", "upstream_repo_path", root / "vendor" / "aseprite-mcp")
    upstream_repo_path = Path(raw_upstream).expanduser().resolve()

    aseprite_timeout = _parse_positive_float(
        _get("ASEPRITE_TIMEOUT_SECONDS", "aseprite_timeout_seconds", None),
        DEFAULT_ASEPRITE_TIMEOUT_SECONDS,
    )
    raw_code_timeout = _parse_positive_float(
        _get("ASEPRITE_CODE_TIMEOUT_SECONDS", "code_timeout_seconds", None),
        DEFAULT_CODE_TIMEOUT_SECONDS,
    )
    code_timeout = clamp_code_timeout(raw_code_timeout)

    allow_raw_lua = _parse_bool(_get("ASEPRITE_ALLOW_RAW_LUA", "allow_raw_lua", None), False)
    allow_preview_server = _parse_bool(
        _get("ASEPRITE_ALLOW_PREVIEW_SERVER", "allow_preview_server", None), False
    )
    warm_worker = _parse_bool(_get("ASEPRITE_WARM_WORKER", "warm_worker", None), False)
    max_output_chars = _parse_positive_int(
        _get("ASEPRITE_MAX_OUTPUT_CHARS", "max_output_chars", None),
        DEFAULT_MAX_OUTPUT_CHARS,
        minimum=256,
    )
    max_preview_images = _parse_positive_int(
        _get("ASEPRITE_MAX_PREVIEW_IMAGES", "max_preview_images", None),
        DEFAULT_MAX_PREVIEW_IMAGES,
        minimum=1,
    )

    return RuntimeConfig(
        workspace_dir=workspace_dir,
        aseprite_path=aseprite_path,
        upstream_repo_path=upstream_repo_path,
        aseprite_timeout_seconds=aseprite_timeout,
        code_timeout_seconds=code_timeout,
        allow_raw_lua=allow_raw_lua,
        allow_preview_server=allow_preview_server,
        warm_worker=warm_worker,
        max_output_chars=max_output_chars,
        max_preview_images=max_preview_images,
    )
