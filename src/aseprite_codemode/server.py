"""FastMCP server exposing strictly 4 Agentic Code Execution tools for Aseprite."""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP, Image

from .catalog import ToolCatalog
from .config import RuntimeConfig, load_config
from .inspector import inspect_sprite_file
from .loader import load_upstream_aseprite_mcp
from .sandbox_runner import SandboxRunner
from .security import WorkspacePathGuard, WorkspaceViolationError

SERVER_INSTRUCTIONS = """\
Aseprite Code Mode server: control Aseprite by writing Python scripts instead of calling 123 separate MCP tools.

Workflow:
1. Call `get_guide("overview")` or `get_guide("pixel_art")` before major tasks.
2. Call `search_tools(query=..., category=...)` to discover `ase.<category>.<tool>(...)` signatures on demand.
3. Call `execute_code(code=..., auto_view="sprite.aseprite")` to run multi-step Python logic in a workspace-confined sandbox.
4. Call `inspect_sprite(filename, ...)` for 1-pass visual QA (1x native, grayscale value, silhouette, multi-frame lint/drift, motion ground-line & seam check).

Mini Cheat-Sheet (pixels 0-based, frames & stack_index 1-based):
- `g = ase.Grid(w, h)` / `ase.Grid.from_rows(rows)`: pure-Python 2D canvas (`.rect`, `.line`, `.ellipse`, `.stamp`, `.outline`, `.shift`, `.flip_x`, `.bounds()`, `.bottom_y()`, `.to_rows()`)
- `with ase.batch(filename, create_size=(w, h)) as b:` -> `.set_palette`, `.add_layer`, `.set_layer_props`, `.add_frame(empty=True)`, `.copy_cel`, `.shift_cel`, `.draw_rows`, `.draw_rect`, `.draw_ellipse`, `.draw_line`, `.fill_area`, `.replace_color`, `.outline`, `.set_frame_duration`, `.set_tag`
- `ase.copy(src, dst, overwrite=False)` (byte-for-byte copy preserving layers/tags)
- `ase.read_grid(filename, x=0, y=0, width=0, height=0, frame_index=1, layer_name="")` -> `{"grid": PixelGrid, "rows", "legend", ...}`
- `ase.show(filename, frames="", layer_name="", region="", scale=0)`
- `ase.palette.generate_color_ramp(base_color, steps=5, hue_shift_degrees=20, lightness_range=0.5)`
- `ase.drawing.draw_rows(filename, layer_name, frame_index, x, y, rows, legend, outline=None, create_if_missing=True)`
- `ase.animation.get_sprite_info(filename)` / `ase.export.export_sprite(filename, output_filename, format="png")` / `ase.export.export_tag(filename, tag_name, output_filename, ...)`
- Upstream tool errors raise `AsepriteToolError` by default (`ase.strict = False` collects into `tool_warnings`); assign `result = ...` to return JSON.
"""

_WORKSPACE_LOCKS: dict[tuple[Path, int], asyncio.Lock] = {}
_WORKSPACE_LOCKS_GUARD = threading.Lock()


def _get_workspace_lock(workspace_dir: Path) -> asyncio.Lock:
    """Return an asyncio.Lock scoped to `(resolved_workspace_dir, current_event_loop)`."""
    loop_id = id(asyncio.get_running_loop())
    key = (workspace_dir.resolve(), loop_id)
    with _WORKSPACE_LOCKS_GUARD:
        lock = _WORKSPACE_LOCKS.get(key)
        if lock is None:
            lock = asyncio.Lock()
            _WORKSPACE_LOCKS[key] = lock
        return lock


def _build_guide_text(topic: str, upstream_pkg: Any) -> str:
    norm = (topic or "overview").strip().lower()
    if norm == "overview":
        return (
            "Aseprite Code Mode Overview\n"
            "===========================\n"
            "Tools:\n"
            "- search_tools(query, category, detail, limit): discover `ase.<cat>.<tool>` signatures.\n"
            "- execute_code(code, timeout_seconds, auto_view, ...): run Python with pre-loaded `ase` SDK.\n"
            "- inspect_sprite(filename, frames, tag, background, include_qa_strip, check_motion, ...):\n"
            "  single-pass visual PNG (with 1x native, grayscale value, silhouette, optional seam onion-skin)\n"
            "  + per-frame bounds/color drift/lint + optional motion ground-line & diff metrics.\n"
            "- get_guide(topic): overview | pixel_art | animation | idle | environment | batch_lua | security.\n\n"
            "Inside `execute_code`:\n"
            "- Every file path is confined to WORKSPACE (`ase.path('hero.aseprite')`, `ase.copy(src, dst)`).\n"
            "- Compose parts in memory with `ase.Grid(w, h)`, assert `.bottom_y()`, then pass `.to_rows()` to `ase.batch`.\n"
            "- Read existing sprites for repair with `ase.read_grid(filename, ...)`.\n"
            "- Upstream errors raise `AsepriteToolError` by default; set `ase.strict = False` to warn instead.\n"
            "- Visual feedback: pass `auto_view='hero.aseprite'` to `execute_code` or call `ase.show('hero.aseprite')`.\n"
            "- Set `result = {...}` to return structured JSON back to your context."
        )
    if norm == "pixel_art":
        instructions = getattr(upstream_pkg, "INSTRUCTIONS", "").strip()
        return (
            "Pixel Art Workflow Guide (from diivi/aseprite-mcp)\n"
            "==================================================\n"
            f"{instructions}\n\n"
            "Code Mode Tip: assemble parts on `ase.Grid`, commit via `ase.batch`, and inspect with "
            "`inspect_sprite(..., background='#7A746C', include_qa_strip=True)`."
        )
    if norm == "batch_lua":
        return (
            "Single-Process Lua Batch Builder (`ase.batch`)\n"
            "==============================================\n"
            "Use `ase.batch(filename)` (or `ase.batch(filename, create_size=(w, h))`) to run many edits\n"
            "inside ONE `aseprite --batch --script` process instead of spawning N subprocesses:\n\n"
            "```python\n"
            "g = ase.Grid(32, 32).ellipse(16, 20, 6, 4, 'G', filled=True)\n"
            "assert g.bottom_y() == 24\n\n"
            "with ase.batch('slime.aseprite', create_size=(32, 32)) as b:\n"
            "    b.set_palette(['#140C1C', '#346524', '#6DAA2C', '#DEEED6'])\n"
            "    b.add_layer('shadow')\n"
            "    b.set_layer_props('shadow', opacity=128, stack_index=1)\n"
            "    b.add_layer('body')\n"
            "    b.draw_rows('body', 1, 0, 0, g.to_rows(), {'G': '#6DAA2C'})\n"
            "    b.fill_area('body', 1, 16, 20, '#346524')\n"
            "    b.replace_color('body', 1, '#346524', '#6DAA2C')\n"
            "    b.outline('body', 1, '#140C1C')\n"
            "    b.add_frame(count=1, duration_ms=120, empty=True)\n"
            "    b.copy_cel('body', 1, 2)\n"
            "    b.shift_cel('body', 2, 0, -2)\n"
            "    b.set_frame_duration(1, 150)\n"
            "    b.set_tag('idle', 1, 2, direction='pingpong')\n"
            "```\n\n"
            "Aseprite Lua Gotchas:\n"
            "- Frame & stack_index values in Aseprite Lua are 1-based (`spr.frames[1]`).\n"
            "- Cel images use cel-local coordinates; `ase.batch` runs `normalize_cel` so you use sprite coordinates.\n"
            "- `b.add_raw_lua(snippet)` and `ase.run_lua(script)` require `ASEPRITE_ALLOW_RAW_LUA=1`."
        )
    if norm == "security":
        return (
            "Code Mode Sandbox & Security Policy\n"
            "===================================\n"
            "- Subprocess isolation: scripts run in a child process with an allowlisted environment\n"
            "  (host secrets/API keys are stripped; cwd is WORKSPACE; timeout kills full process tree).\n"
            "- Path confinement: `open()`, `ase.copy()`, and all tool file/directory arguments are validated by WorkspacePathGuard.\n"
            "  `..` traversal, other drive letters, `~/...`, symlinks outside WORKSPACE, and raw FDs are blocked.\n"
            "- AST + closure isolation: only safe pure-Python stdlib modules are importable; private/dunder\n"
            "  attribute escapes on `ase` and `eval`/`exec`/`compile`/`__import__` are blocked.\n"
            "- Policy gates:\n"
            "  * `ASEPRITE_ALLOW_RAW_LUA=0` (default): blocks `run_lua_script`, `ase.run_lua`, `batch.add_raw_lua`.\n"
            "  * `ASEPRITE_ALLOW_PREVIEW_SERVER=0` (default): blocks `start_preview_server` and `stop_preview_server`."
        )
    valid = "overview, pixel_art, animation, idle, environment, batch_lua, security"
    return f"Unknown guide topic '{topic}'. Valid topics: {valid}"


def create_server(config: RuntimeConfig | None = None) -> FastMCP:
    """Build and configure the 4-tool `aseprite-codemode` FastMCP server."""
    cfg = config or load_config()
    upstream_pkg = load_upstream_aseprite_mcp(cfg.upstream_repo_path, load_all_tools=True)
    catalog = ToolCatalog.from_upstream(upstream_pkg)
    runner = SandboxRunner(cfg)
    guard = WorkspacePathGuard(cfg.workspace_dir)

    server = FastMCP("aseprite-codemode", instructions=SERVER_INSTRUCTIONS)

    @server.tool()
    async def search_tools(
        query: str = "",
        category: str = "",
        detail: Literal["summary", "signature", "full"] = "signature",
        limit: int = 12,
    ) -> str:
        """Discover available `ase.<category>.<tool>` SDK methods from diivi/aseprite-mcp.

        Args:
            query: Space-separated search keywords (e.g. "draw rows", "palette ramp", "tag").
            category: Optional module category filter (e.g. "drawing", "animation", "palette").
            detail: "summary" (minimal), "signature" (default), or "full" (docstring + param list).
            limit: Maximum number of matching tools to return (1-150, default 12).
        """
        data = catalog.search(query=query, category=category, detail=detail, limit=limit)
        return json.dumps(data, ensure_ascii=False, indent=2)

    @server.tool()
    async def execute_code(
        code: str,
        timeout_seconds: float = 60.0,
        auto_view: str = "",
        view_frames: str = "",
        view_layer: str = "",
        view_region: str = "",
        view_scale: int = 0,
    ):
        """Execute Python code in a workspace-confined sandbox with pre-loaded `ase` SDK.

        Args:
            code: Python code to run. Use `ase.Grid`, `ase.batch(...)`, `ase.<cat>.<tool>(...)`,
                `ase.copy(...)`, `ase.read_grid(...)`, `ase.show(...)`, and assign `result = ...`.
            timeout_seconds: Execution timeout in seconds (clamped to 1..300, default 60).
            auto_view: Optional sprite filename inside workspace to render after success for visual feedback.
            view_frames: Optional frame range for `auto_view` (e.g. "1-4").
            view_layer: Optional layer name for `auto_view`.
            view_region: Optional "x,y,w,h" crop region for `auto_view`.
            view_scale: Optional integer zoom scale for `auto_view` (0 = auto).
        """
        view_options: dict[str, Any] = {}
        if auto_view.strip():
            view_options = {
                "frames": view_frames,
                "layer_name": view_layer,
                "region": view_region,
                "scale": view_scale,
            }

        async with _get_workspace_lock(cfg.workspace_dir):
            exec_result = await asyncio.to_thread(
                runner.run,
                code,
                timeout_seconds=timeout_seconds,
                auto_view=auto_view.strip() or None,
                view_options=view_options,
            )

        items: list[Any] = [json.dumps(exec_result.to_summary_dict(), ensure_ascii=False, indent=2)]
        for preview in exec_result.previews:
            items.append(Image(data=preview.png_bytes, format="png"))
        return items

    @server.tool()
    async def inspect_sprite(
        filename: str,
        frames: str = "",
        layer_name: str = "",
        region: str = "",
        scale: int = 0,
        include_lint: bool = True,
        include_colors: bool = True,
        tag: str = "",
        background: str = "checker",
        include_qa_strip: bool = True,
        check_motion: bool = False,
    ):
        """Single-pass visual render + multi-frame QA analytics (bounds, color drift, lint, motion).

        Args:
            filename: Path to sprite inside the sandbox workspace.
            frames: Frame selection (e.g. "", "1", "1-4"). Ignored when `tag` is set.
            layer_name: Optional layer name filter.
            region: Optional "x,y,w,h" region crop for visual render.
            scale: Optional zoom scale (0 = auto).
            include_lint: Include detached/lone/orphan pixel lint analysis.
            include_colors: Include color histogram and per-frame palette drift check.
            tag: Optional animation tag name to inspect (e.g. "idle", "walk").
            background: Backdrop: "checker", "dark", "light", "mid", or hex "#RRGGBB".
            include_qa_strip: Append 1x native, grayscale luminance, and silhouette strip.
            check_motion: Include ground-line (`bottom_y`) stability, frame diffs, and seam onion-skin.
        """
        try:
            safe_file = guard.resolve_path(filename, allow_empty=False)
        except WorkspaceViolationError as exc:
            return [json.dumps({"status": "security_error", "error": str(exc)}, ensure_ascii=False)]

        if not Path(safe_file).is_file():
            return [json.dumps({"status": "error", "error": f"File not found: {filename}"}, ensure_ascii=False)]

        async with _get_workspace_lock(cfg.workspace_dir):
            report, png_bytes = await asyncio.to_thread(
                inspect_sprite_file,
                cfg.aseprite_path,
                safe_file,
                guard.relative_posix(safe_file),
                frames=frames,
                layer_name=layer_name,
                region=region,
                scale=scale,
                include_lint=include_lint,
                include_colors=include_colors,
                tag=tag,
                background=background,
                include_qa_strip=include_qa_strip,
                check_motion=check_motion,
                timeout_seconds=cfg.aseprite_timeout_seconds,
            )

        items: list[Any] = [json.dumps(report, ensure_ascii=False, indent=2)]
        if png_bytes is not None:
            items.append(Image(data=png_bytes, format="png"))
        return items

    @server.tool()
    async def get_guide(topic: str = "overview") -> str:
        """Read concise pixel-art, animation, Lua batch, or security guides on demand.

        Args:
            topic: One of "overview", "pixel_art", "animation", "idle", "environment", "batch_lua", "security".
        """
        guide_spec = catalog.get("animation_workflow_guide")
        anim_fn = guide_spec.callable_ref if guide_spec else None

        norm = (topic or "overview").strip().lower()
        if norm in ("animation", "character", "idle", "environment") and anim_fn is not None:
            use_case = "character" if norm == "animation" else norm
            return str(await anim_fn(use_case=use_case))

        return _build_guide_text(norm, upstream_pkg)

    return server


mcp = create_server()
