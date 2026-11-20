"""Synchronous Python SDK (`ase`), closure sandbox proxies, and Lua batch builder."""

from __future__ import annotations

import asyncio
import concurrent.futures
import importlib
import inspect
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Callable, Literal

from .catalog import ToolCatalog, ToolSpec
from .config import RuntimeConfig
from .grid import PixelGrid
from .loader import list_upstream_categories
from .security import (
    CONDITIONAL_PATH_PARAM_NAMES,
    PATH_PARAM_NAMES,
    PREVIEW_SERVER_TOOLS,
    RAW_LUA_TOOLS,
    PolicyBlockedToolError,
    WorkspacePathGuard,
    WorkspaceViolationError,
    looks_like_filesystem_path,
)

_OUTPUT_FILE_PARAMS = frozenset({"output_filename", "data_filename"})
_OUTPUT_DIR_PARAMS = frozenset({"output_directory"})

_EXACT_ERROR_MESSAGES = frozenset({
    "Width and height must be > 0",
    "Duration must be > 0",
    "Colors list cannot be empty",
    "Layer names list cannot be empty",
    "Colors must use #RRGGBB values",
})

_PREFIX_ERROR_MARKERS = (
    "Failed",
    "ERROR:",
    "ERROR ",
    "Invalid ",
    "Script failed",
    "Script cannot be empty",
    "Unsupported ",
    "Too many frames",
    "Image would be larger than",
)

_ANI_DIRECTIONS = {
    "forward": "AniDir.FORWARD",
    "reverse": "AniDir.REVERSE",
    "pingpong": "AniDir.PING_PONG",
    "pingpong_reverse": "AniDir.PING_PONG_REVERSE",
}


class AsepriteToolError(RuntimeError):
    """Raised in strict mode when an upstream aseprite-mcp tool returns an error string."""


def is_upstream_error_message(value: Any) -> bool:
    """Return True if `value` is a recognized error string returned by upstream aseprite-mcp."""
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text:
        return False
    if text in _EXACT_ERROR_MESSAGES:
        return True
    if text.startswith(_PREFIX_ERROR_MARKERS):
        return True
    if text.startswith(("File ", "Directory ")) and text.endswith(" not found"):
        return True
    if text.startswith("Output file ") and "already exists" in text:
        return True
    if text.startswith("Frame ") and "out of range" in text:
        return True
    if text.startswith("Region ") and "is outside" in text:
        return True
    return False


@dataclass
class PreviewEntry:
    """Metadata for a preview PNG image captured during script execution."""

    index: int
    path: Path
    label: str
    source_tool: str
    text: str


class PreviewCollector:
    """Captures Image objects returned by tools like `view_sprite` into PNG files."""

    def __init__(self, preview_dir: Path | None, max_images: int = 4) -> None:
        self.preview_dir = Path(preview_dir).resolve() if preview_dir else None
        if self.preview_dir is not None:
            self.preview_dir.mkdir(parents=True, exist_ok=True)
        self.max_images = max(1, int(max_images))
        self.entries: list[PreviewEntry] = []
        self._in_memory_images: list[tuple[str, bytes, str]] = []
        self.last_status: Literal["idle", "captured", "limit_reached"] = "idle"

    @property
    def count(self) -> int:
        """Total number of captured preview images (disk + memory)."""
        return len(self.entries) + len(self._in_memory_images)

    @staticmethod
    def _is_image_obj(obj: Any) -> bool:
        data = getattr(obj, "data", None)
        return isinstance(data, (bytes, bytearray)) and hasattr(obj, "to_image_content")

    def record_image(
        self,
        png_bytes: bytes,
        *,
        source_tool: str,
        label: str = "",
        text: str = "",
    ) -> str:
        """Persist preview bytes (up to `max_images`) and return a descriptive note."""
        if self.count >= self.max_images:
            self.last_status = "limit_reached"
            return f"[Preview limit ({self.max_images}) reached; extra image omitted]"

        idx = self.count + 1
        self.last_status = "captured"
        if self.preview_dir is None:
            self._in_memory_images.append((label or f"preview_{idx}", png_bytes, text))
            return f"[Preview #{idx} captured in memory ({len(png_bytes)} bytes)]"

        out_path = self.preview_dir / f"preview_{idx}.png"
        out_path.write_bytes(png_bytes)
        self.entries.append(
            PreviewEntry(
                index=idx,
                path=out_path,
                label=label or f"preview_{idx}",
                source_tool=source_tool,
                text=text,
            )
        )
        return f"[Preview #{idx} saved for visual inspection]"

    def extract_from_result(
        self,
        result: Any,
        *,
        source_tool: str,
        label: str = "",
    ) -> Any:
        """Convert FastMCP `Image` or `[text, Image]` tool results into clean text."""
        self.last_status = "idle"

        if self._is_image_obj(result):
            return self.record_image(bytes(result.data), source_tool=source_tool, label=label)

        if isinstance(result, (list, tuple)):
            text_parts: list[str] = []
            images: list[bytes] = []
            other_items: list[Any] = []
            for item in result:
                if self._is_image_obj(item):
                    images.append(bytes(item.data))
                elif isinstance(item, str):
                    text_parts.append(item)
                else:
                    other_items.append(item)
            if images:
                joined_text = "\n".join(text_parts).strip()
                notes = [
                    self.record_image(
                        img_bytes,
                        source_tool=source_tool,
                        label=label,
                        text=joined_text,
                    )
                    for img_bytes in images
                ]
                combined = [joined_text] if joined_text else []
                combined.extend(notes)
                return "\n".join(combined)
            if other_items:
                return result

        return result

    def metadata_list(self) -> list[dict[str, Any]]:
        """Serialize captured preview entries for worker JSON output."""
        return [
            {
                "index": e.index,
                "path": str(e.path),
                "label": e.label,
                "source_tool": e.source_tool,
                "text": e.text,
            }
            for e in self.entries
        ]


def _run_coro_sync(coro_fn: Callable[[], Any]) -> Any:
    """Execute an async coroutine function synchronously, safe with or without a running loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro_fn())

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(lambda: asyncio.run(coro_fn())).result()


class LuaBatchBuilder:
    """Queues multiple sprite operations and runs them in ONE Aseprite headless invocation."""

    def __init__(
        self,
        *,
        filename: str,
        guard: WorkspacePathGuard,
        upstream_pkg: ModuleType,
        allow_raw_lua: bool,
        create_size: tuple[int, int] | None = None,
    ) -> None:
        self._guard = guard
        self._upstream_pkg = upstream_pkg
        self._allow_raw_lua = allow_raw_lua
        self._safe_filename = guard.resolve_path(filename, allow_empty=False)
        self._create_size = create_size
        self._ops: list[str] = []
        self._committed = False

        if self._create_size is not None:
            w, h = self._create_size
            if int(w) <= 0 or int(h) <= 0:
                raise ValueError("create_size width and height must be > 0")

    def __enter__(self) -> "LuaBatchBuilder":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        if exc_type is None and not self._committed and (self._ops or self._create_size is not None):
            res = self.commit()
            if not res["ok"]:
                raise RuntimeError(f"Batch commit failed: {res['output']}")
        return False

    @property
    def operation_count(self) -> int:
        return len(self._ops)

    def _lua_helpers(self) -> tuple[Any, Any, Any]:
        pkg_name = self._upstream_pkg.__name__
        commands_mod = importlib.import_module(f"{pkg_name}.core.commands")
        colors_mod = importlib.import_module(f"{pkg_name}.core.colors")
        lua_mod = importlib.import_module(f"{pkg_name}.core.lua")
        return commands_mod, colors_mod, lua_mod

    def _parse_rgba(self, color: str) -> tuple[int, int, int, int]:
        _, colors_mod, _ = self._lua_helpers()
        rgba = colors_mod.parse_hex_color(color)
        if rgba is None:
            raise ValueError(f"Invalid hex color: {color!r}")
        return rgba

    def _escape(self, text: str) -> str:
        commands_mod, _, _ = self._lua_helpers()
        return commands_mod.lua_escape(text)

    def add_layer(self, name: str, group: str = "") -> "LuaBatchBuilder":
        if not name or not name.strip():
            raise ValueError("Layer name cannot be empty")
        safe_name = self._escape(name)
        safe_group = self._escape(group)
        self._ops.append(
            f"""
        do
            local parent = nil
            if "{safe_group}" ~= "" then
                parent = find_layer(spr, "{safe_group}")
                if not parent then batch_err = "Group not found: {safe_group}"; return end
                if not parent.isGroup then batch_err = "Target is not a group: {safe_group}"; return end
            end
            local lyr = spr:newLayer()
            lyr.name = "{safe_name}"
            if parent then lyr.parent = parent end
        end
            """.strip()
        )
        return self

    def add_frame(
        self,
        count: int = 1,
        duration_ms: int | None = None,
        empty: bool = False,
    ) -> "LuaBatchBuilder":
        if int(count) <= 0:
            raise ValueError("count must be >= 1")
        dur_stmt = ""
        if duration_ms is not None:
            if int(duration_ms) <= 0:
                raise ValueError("duration_ms must be > 0")
            dur_stmt = f"fr.duration = {int(duration_ms) / 1000.0:.6f}"
        create_call = "spr:newEmptyFrame()" if empty else "spr:newFrame()"
        self._ops.append(
            f"""
        for _fi = 1, {int(count)} do
            local fr = {create_call}
            {dur_stmt}
        end
            """.strip()
        )
        return self

    def set_frame_duration(self, frame: int, duration_ms: int) -> "LuaBatchBuilder":
        if int(frame) < 1:
            raise ValueError("frame must be >= 1")
        if int(duration_ms) <= 0:
            raise ValueError("duration_ms must be > 0")
        self._ops.append(
            f"""
        do
            local idx = {int(frame)}
            if idx < 1 or idx > #spr.frames then batch_err = "Frame index out of range: " .. idx; return end
            spr.frames[idx].duration = {int(duration_ms) / 1000.0:.6f}
        end
            """.strip()
        )
        return self

    def set_tag(
        self,
        name: str,
        from_frame: int,
        to_frame: int,
        direction: str = "forward",
    ) -> "LuaBatchBuilder":
        if not name or not name.strip():
            raise ValueError("Tag name cannot be empty")
        if int(from_frame) < 1 or int(to_frame) < int(from_frame):
            raise ValueError("Require 1 <= from_frame <= to_frame")
        dir_key = direction.strip().lower()
        if dir_key not in _ANI_DIRECTIONS:
            raise ValueError(f"Unsupported direction: {direction!r}")

        safe_name = self._escape(name)
        lua_dir = _ANI_DIRECTIONS[dir_key]
        self._ops.append(
            f"""
        do
            local f1 = {int(from_frame)}
            local f2 = {int(to_frame)}
            if f1 < 1 or f2 > #spr.frames or f1 > f2 then
                batch_err = "Tag frame range out of range: " .. f1 .. "-" .. f2
                return
            end
            local tag = nil
            for _, t in ipairs(spr.tags) do
                if t.name == "{safe_name}" then tag = t; break end
            end
            if not tag then
                tag = spr:newTag(f1, f2)
            else
                tag.fromFrame = spr.frames[f1]
                tag.toFrame = spr.frames[f2]
            end
            tag.name = "{safe_name}"
            tag.aniDir = {lua_dir}
        end
            """.strip()
        )
        return self

    def draw_pixels(
        self,
        layer: str,
        frame: int,
        pixels: list[tuple[int, int, str] | dict[str, Any]],
    ) -> "LuaBatchBuilder":
        if int(frame) < 1:
            raise ValueError("frame must be >= 1")
        safe_layer = self._escape(layer)

        palette_list: list[tuple[int, int, int, int]] = []
        color_to_idx: dict[tuple[int, int, int, int], int] = {}
        flat_pts: list[str] = []

        for item in pixels:
            if isinstance(item, dict):
                px, py, col = int(item["x"]), int(item["y"]), str(item["color"])
            else:
                px, py, col = int(item[0]), int(item[1]), str(item[2])
            rgba = self._parse_rgba(col)
            cidx = color_to_idx.get(rgba)
            if cidx is None:
                palette_list.append(rgba)
                cidx = len(palette_list)
                color_to_idx[rgba] = cidx
            flat_pts.extend((str(px), str(py), str(cidx)))

        pal_entries = ", ".join(f"Color({r}, {g}, {b}, {a})" for r, g, b, a in palette_list)
        pts_entries = ", ".join(flat_pts)
        self._ops.append(
            f"""
        do
            local idx = {int(frame)}
            if idx < 1 or idx > #spr.frames then batch_err = "Frame index out of range: " .. idx; return end
            local lyr = find_layer(spr, "{safe_layer}")
            if not lyr then batch_err = "Layer not found: {safe_layer}"; return end
            local cel = normalize_cel(spr, lyr, spr.frames[idx], true)
            local img = cel.image
            local pal = {{{pal_entries}}}
            local pts = {{{pts_entries}}}
            for i = 1, #pts, 3 do
                pset(img, pts[i], pts[i + 1], pal[pts[i + 2]])
            end
        end
            """.strip()
        )
        return self

    def draw_line(
        self,
        layer: str,
        frame: int,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        color: str,
        thickness: int = 1,
    ) -> "LuaBatchBuilder":
        if int(frame) < 1:
            raise ValueError("frame must be >= 1")
        if int(thickness) < 1:
            raise ValueError("thickness must be >= 1")
        r, g, b, a = self._parse_rgba(color)
        safe_layer = self._escape(layer)
        self._ops.append(
            f"""
        do
            local idx = {int(frame)}
            if idx < 1 or idx > #spr.frames then batch_err = "Frame index out of range: " .. idx; return end
            local lyr = find_layer(spr, "{safe_layer}")
            if not lyr then batch_err = "Layer not found: {safe_layer}"; return end
            local cel = normalize_cel(spr, lyr, spr.frames[idx], true)
            local img = cel.image
            local col = Color({r}, {g}, {b}, {a})
            local rad = math.max(0, math.floor({int(thickness)} / 2))
            local function stamp(px, py)
                for oy = -rad, rad do
                    for ox = -rad, rad do
                        pset(img, px + ox, py + oy, col)
                    end
                end
            end
            local cx, cy = {int(x1)}, {int(y1)}
            local tx, ty = {int(x2)}, {int(y2)}
            local dx = math.abs(tx - cx)
            local sx = cx < tx and 1 or -1
            local dy = -math.abs(ty - cy)
            local sy = cy < ty and 1 or -1
            local err = dx + dy
            while true do
                stamp(cx, cy)
                if cx == tx and cy == ty then break end
                local e2 = 2 * err
                if e2 >= dy then err = err + dy; cx = cx + sx end
                if e2 <= dx then err = err + dx; cy = cy + sy end
            end
        end
            """.strip()
        )
        return self

    def draw_rect(
        self,
        layer: str,
        frame: int,
        x: int,
        y: int,
        w: int,
        h: int,
        color: str,
        filled: bool = True,
    ) -> "LuaBatchBuilder":
        if int(frame) < 1:
            raise ValueError("frame must be >= 1")
        if int(w) <= 0 or int(h) <= 0:
            raise ValueError("w and h must be > 0")
        r, g, b, a = self._parse_rgba(color)
        safe_layer = self._escape(layer)
        ix, iy, iw, ih = int(x), int(y), int(w), int(h)
        if filled:
            draw_loop = (
                f"for dy = 0, {ih - 1} do "
                f"for dx = 0, {iw - 1} do "
                f"pset(img, {ix} + dx, {iy} + dy, col) "
                f"end end"
            )
        else:
            draw_loop = (
                f"for dy = 0, {ih - 1} do "
                f"for dx = 0, {iw - 1} do "
                f"if dx == 0 or dy == 0 or dx == {iw - 1} or dy == {ih - 1} then "
                f"pset(img, {ix} + dx, {iy} + dy, col) "
                f"end end end"
            )
        self._ops.append(
            f"""
        do
            local idx = {int(frame)}
            if idx < 1 or idx > #spr.frames then batch_err = "Frame index out of range: " .. idx; return end
            local lyr = find_layer(spr, "{safe_layer}")
            if not lyr then batch_err = "Layer not found: {safe_layer}"; return end
            local cel = normalize_cel(spr, lyr, spr.frames[idx], true)
            local img = cel.image
            local col = Color({r}, {g}, {b}, {a})
            {draw_loop}
        end
            """.strip()
        )
        return self

    def draw_ellipse(
        self,
        layer: str,
        frame: int,
        cx: int,
        cy: int,
        rx: int,
        ry: int,
        color: str,
        filled: bool = True,
    ) -> "LuaBatchBuilder":
        if int(frame) < 1:
            raise ValueError("frame must be >= 1")
        if int(rx) <= 0 or int(ry) <= 0:
            raise ValueError("rx and ry must be > 0")
        r, g, b, a = self._parse_rgba(color)
        safe_layer = self._escape(layer)
        icx, icy, irx, iry = int(cx), int(cy), int(rx), int(ry)
        if filled:
            body = """
            for dy = -ry, ry do
                for dx = -rx, rx do
                    if in_ell(dx, dy) then pset(img, cx + dx, cy + dy, col) end
                end
            end
            """.strip()
        else:
            body = """
            for dy = -ry, ry do
                for dx = -rx, rx do
                    if in_ell(dx, dy) and not (
                        in_ell(dx - 1, dy) and in_ell(dx + 1, dy) and
                        in_ell(dx, dy - 1) and in_ell(dx, dy + 1)
                    ) then
                        pset(img, cx + dx, cy + dy, col)
                    end
                end
            end
            """.strip()
        self._ops.append(
            f"""
        do
            local idx = {int(frame)}
            if idx < 1 or idx > #spr.frames then batch_err = "Frame index out of range: " .. idx; return end
            local lyr = find_layer(spr, "{safe_layer}")
            if not lyr then batch_err = "Layer not found: {safe_layer}"; return end
            local cel = normalize_cel(spr, lyr, spr.frames[idx], true)
            local img = cel.image
            local col = Color({r}, {g}, {b}, {a})
            local cx, cy, rx, ry = {icx}, {icy}, {irx}, {iry}
            local function in_ell(x, y)
                return (x * x) / (rx * rx) + (y * y) / (ry * ry) <= 1.0
            end
            {body}
        end
            """.strip()
        )
        return self

    def draw_rows(
        self,
        layer: str,
        frame: int,
        x: int,
        y: int,
        rows: list[str],
        palette: dict[str, str],
    ) -> "LuaBatchBuilder":
        if int(frame) < 1:
            raise ValueError("frame must be >= 1")
        if not rows:
            raise ValueError("rows cannot be empty")
        width = len(rows[0])
        for i, row in enumerate(rows):
            if len(row) != width:
                raise ValueError(f"Row {i + 1} width {len(row)} does not match row 1 width {width}")

        pal_items: list[str] = []
        for ch, hex_color in palette.items():
            if len(ch) != 1:
                raise ValueError(f"Palette key {ch!r} must be a single character")
            if ch == "." or hex_color in ("", None):
                continue
            r, g, b, a = self._parse_rgba(str(hex_color))
            safe_ch = self._escape(ch)
            pal_items.append(f'["{safe_ch}"] = Color({r}, {g}, {b}, {a})')

        safe_layer = self._escape(layer)
        row_literals = ", ".join(f'"{self._escape(row)}"' for row in rows)
        pal_literal = ", ".join(pal_items)
        ix, iy = int(x), int(y)

        self._ops.append(
            f"""
        do
            local idx = {int(frame)}
            if idx < 1 or idx > #spr.frames then batch_err = "Frame index out of range: " .. idx; return end
            local lyr = find_layer(spr, "{safe_layer}")
            if not lyr then batch_err = "Layer not found: {safe_layer}"; return end
            local cel = normalize_cel(spr, lyr, spr.frames[idx], true)
            local img = cel.image
            local pal = {{{pal_literal}}}
            local rows = {{{row_literals}}}
            for ri = 1, #rows do
                local row = rows[ri]
                for ci = 1, #row do
                    local ch = string.sub(row, ci, ci)
                    local col = pal[ch]
                    if col then
                        pset(img, {ix} + ci - 1, {iy} + ri - 1, col)
                    end
                end
            end
        end
            """.strip()
        )
        return self

    def outline(
        self,
        layer: str,
        frame: int,
        color: str = "#000000",
        include_diagonals: bool = False,
    ) -> "LuaBatchBuilder":
        if int(frame) < 1:
            raise ValueError("frame must be >= 1")
        r, g, b, a = self._parse_rgba(color)
        safe_layer = self._escape(layer)
        diag_flag = "true" if include_diagonals else "false"
        self._ops.append(
            f"""
        do
            local idx = {int(frame)}
            if idx < 1 or idx > #spr.frames then batch_err = "Frame index out of range: " .. idx; return end
            local lyr = find_layer(spr, "{safe_layer}")
            if not lyr then batch_err = "Layer not found: {safe_layer}"; return end
            local cel = normalize_cel(spr, lyr, spr.frames[idx], false)
            if not cel then batch_err = "No cel at {safe_layer} frame " .. idx; return end
            local img = cel.image
            local src = img:clone()
            local outline_col = Color({r}, {g}, {b}, {a})
            local function is_transparent(px_val)
                if spr.colorMode == ColorMode.INDEXED then
                    return px_val == spr.transparentColor
                elseif spr.colorMode == ColorMode.GRAY then
                    return app.pixelColor.grayaA(px_val) == 0
                end
                return app.pixelColor.rgbaA(px_val) == 0
            end
            local function opaque(px, py)
                if px < 0 or py < 0 or px >= src.width or py >= src.height then return false end
                return not is_transparent(src:getPixel(px, py))
            end
            for py = 0, img.height - 1 do
                for px = 0, img.width - 1 do
                    if is_transparent(src:getPixel(px, py)) then
                        local touch = opaque(px - 1, py) or opaque(px + 1, py)
                                   or opaque(px, py - 1) or opaque(px, py + 1)
                        if not touch and {diag_flag} then
                            touch = opaque(px - 1, py - 1) or opaque(px + 1, py - 1)
                                 or opaque(px - 1, py + 1) or opaque(px + 1, py + 1)
                        end
                        if touch then
                            pset(img, px, py, outline_col)
                        end
                    end
                end
            end
        end
            """.strip()
        )
        return self

    def clear_cel(self, layer: str, frame: int) -> "LuaBatchBuilder":
        if int(frame) < 1:
            raise ValueError("frame must be >= 1")
        safe_layer = self._escape(layer)
        self._ops.append(
            f"""
        do
            local idx = {int(frame)}
            if idx < 1 or idx > #spr.frames then batch_err = "Frame index out of range: " .. idx; return end
            local lyr = find_layer(spr, "{safe_layer}")
            if not lyr then batch_err = "Layer not found: {safe_layer}"; return end
            local cel = lyr:cel(spr.frames[idx])
            if cel then spr:deleteCel(cel) end
        end
            """.strip()
        )
        return self

    def copy_cel(self, layer: str, src_frame: int, dst_frame: int) -> "LuaBatchBuilder":
        if int(src_frame) < 1 or int(dst_frame) < 1:
            raise ValueError("src_frame and dst_frame must be >= 1")
        safe_layer = self._escape(layer)
        self._ops.append(
            f"""
        do
            local s_idx = {int(src_frame)}
            local d_idx = {int(dst_frame)}
            if s_idx < 1 or s_idx > #spr.frames then batch_err = "Source frame out of range: " .. s_idx; return end
            if d_idx < 1 or d_idx > #spr.frames then batch_err = "Dest frame out of range: " .. d_idx; return end
            local lyr = find_layer(spr, "{safe_layer}")
            if not lyr then batch_err = "Layer not found: {safe_layer}"; return end
            local src_cel = normalize_cel(spr, lyr, spr.frames[s_idx], false)
            if not src_cel then
                local existing = lyr:cel(spr.frames[d_idx])
                if existing then spr:deleteCel(existing) end
            else
                local clone_img = src_cel.image:clone()
                local dst_cel = lyr:cel(spr.frames[d_idx])
                if dst_cel then
                    dst_cel.image = clone_img
                    dst_cel.position = Point(src_cel.position.x, src_cel.position.y)
                else
                    spr:newCel(lyr, spr.frames[d_idx], clone_img, Point(src_cel.position.x, src_cel.position.y))
                end
            end
        end
            """.strip()
        )
        return self

    def shift_cel(self, layer: str, frame: int, dx: int, dy: int) -> "LuaBatchBuilder":
        if int(frame) < 1:
            raise ValueError("frame must be >= 1")
        safe_layer = self._escape(layer)
        self._ops.append(
            f"""
        do
            local idx = {int(frame)}
            if idx < 1 or idx > #spr.frames then batch_err = "Frame index out of range: " .. idx; return end
            local lyr = find_layer(spr, "{safe_layer}")
            if not lyr then batch_err = "Layer not found: {safe_layer}"; return end
            local cel = normalize_cel(spr, lyr, spr.frames[idx], false)
            if cel then
                local new_img = Image(spr.width, spr.height, spr.colorMode)
                new_img:drawImage(cel.image, Point({int(dx)}, {int(dy)}))
                cel.image = new_img
                cel.position = Point(0, 0)
            end
        end
            """.strip()
        )
        return self

    def replace_color(
        self,
        layer: str,
        frame: int,
        old_color: str,
        new_color: str,
    ) -> "LuaBatchBuilder":
        if int(frame) < 1:
            raise ValueError("frame must be >= 1")
        or1, og1, ob1, oa1 = self._parse_rgba(old_color)
        nr1, ng1, nb1, na1 = self._parse_rgba(new_color)
        safe_layer = self._escape(layer)
        self._ops.append(
            f"""
        do
            local idx = {int(frame)}
            if idx < 1 or idx > #spr.frames then batch_err = "Frame index out of range: " .. idx; return end
            local lyr = find_layer(spr, "{safe_layer}")
            if not lyr then batch_err = "Layer not found: {safe_layer}"; return end
            local cel = normalize_cel(spr, lyr, spr.frames[idx], false)
            if cel then
                local img = cel.image
                local repl = Color({nr1}, {ng1}, {nb1}, {na1})
                local target_rgba = app.pixelColor.rgba({or1}, {og1}, {ob1}, {oa1})
                local pal = spr.palettes[1]
                for py = 0, img.height - 1 do
                    for px = 0, img.width - 1 do
                        local pv = img:getPixel(px, py)
                        local hit = false
                        if spr.colorMode == ColorMode.RGB then
                            if {oa1} == 0 then
                                hit = (app.pixelColor.rgbaA(pv) == 0)
                            else
                                hit = (pv == target_rgba)
                            end
                        elseif spr.colorMode == ColorMode.GRAY then
                            local ga = app.pixelColor.grayaA(pv)
                            if {oa1} == 0 then
                                hit = (ga == 0)
                            else
                                local gv = app.pixelColor.grayaV(pv)
                                local exp_v = math.floor(({or1} * 0.299 + {og1} * 0.587 + {ob1} * 0.114) + 0.5)
                                hit = (gv == exp_v and ga == {oa1})
                            end
                        elseif spr.colorMode == ColorMode.INDEXED then
                            if pv == spr.transparentColor then
                                hit = ({oa1} == 0)
                            elseif pal and pv >= 0 and pv < #pal then
                                local pc = pal:getColor(pv)
                                hit = (pc.red == {or1} and pc.green == {og1} and pc.blue == {ob1} and pc.alpha == {oa1})
                            end
                        end
                        if hit then pset(img, px, py, repl) end
                    end
                end
            end
        end
            """.strip()
        )
        return self

    def fill_area(
        self,
        layer: str,
        frame: int,
        x: int,
        y: int,
        color: str,
    ) -> "LuaBatchBuilder":
        if int(frame) < 1:
            raise ValueError("frame must be >= 1")
        r, g, b, a = self._parse_rgba(color)
        safe_layer = self._escape(layer)
        ix, iy = int(x), int(y)
        self._ops.append(
            f"""
        do
            local idx = {int(frame)}
            if idx < 1 or idx > #spr.frames then batch_err = "Frame index out of range: " .. idx; return end
            local lyr = find_layer(spr, "{safe_layer}")
            if not lyr then batch_err = "Layer not found: {safe_layer}"; return end
            local cel = normalize_cel(spr, lyr, spr.frames[idx], true)
            local img = cel.image
            local sx, sy = {ix}, {iy}
            if sx >= 0 and sy >= 0 and sx < img.width and sy < img.height then
                local w = img.width
                local h = img.height
                local target = img:getPixel(sx, sy)
                local repl = Color({r}, {g}, {b}, {a})
                local visited = {{}}
                local qx = {{sx}}
                local qy = {{sy}}
                local head = 1
                visited[sy * w + sx] = true
                while head <= #qx do
                    local cx = qx[head]
                    local cy = qy[head]
                    head = head + 1
                    img:putPixel(cx, cy, repl)
                    local nbrs = {{{{cx - 1, cy}}, {{cx + 1, cy}}, {{cx, cy - 1}}, {{cx, cy + 1}}}}
                    for _, n in ipairs(nbrs) do
                        local nx, ny = n[1], n[2]
                        if nx >= 0 and ny >= 0 and nx < w and ny < h then
                            local key = ny * w + nx
                            if not visited[key] and img:getPixel(nx, ny) == target then
                                visited[key] = true
                                qx[#qx + 1] = nx
                                qy[#qy + 1] = ny
                            end
                        end
                    end
                end
            end
        end
            """.strip()
        )
        return self

    def set_layer_props(
        self,
        layer: str,
        opacity: int | None = None,
        visible: bool | None = None,
        stack_index: int | None = None,
    ) -> "LuaBatchBuilder":
        if opacity is not None and (int(opacity) < 0 or int(opacity) > 255):
            raise ValueError("opacity must be between 0 and 255")
        if stack_index is not None and int(stack_index) < 1:
            raise ValueError("stack_index must be >= 1")
        safe_layer = self._escape(layer)
        stmts: list[str] = []
        if opacity is not None:
            stmts.append(f"lyr.opacity = {int(opacity)}")
        if visible is not None:
            stmts.append(f"lyr.isVisible = {'true' if visible else 'false'}")
        if stack_index is not None:
            stmts.append(f"lyr.stackIndex = {int(stack_index)}")
        body = "\n            ".join(stmts) if stmts else "-- no property changes"
        self._ops.append(
            f"""
        do
            local lyr = find_layer(spr, "{safe_layer}")
            if not lyr then batch_err = "Layer not found: {safe_layer}"; return end
            {body}
        end
            """.strip()
        )
        return self

    def set_palette(self, colors: list[str]) -> "LuaBatchBuilder":
        if not colors:
            raise ValueError("colors cannot be empty")
        rgba_list = [self._parse_rgba(c) for c in colors]
        entries = "\n            ".join(
            f"pal:setColor({idx}, Color({r}, {g}, {b}, {a}))"
            for idx, (r, g, b, a) in enumerate(rgba_list)
        )
        self._ops.append(
            f"""
        do
            local pal = Palette({len(rgba_list)})
            {entries}
            spr:setPalette(pal)
        end
            """.strip()
        )
        return self

    def add_raw_lua(self, lua_snippet: str) -> "LuaBatchBuilder":
        """Append raw Lua statements inside the batch transaction (requires ASEPRITE_ALLOW_RAW_LUA=1)."""
        if not self._allow_raw_lua:
            raise PolicyBlockedToolError(
                "Raw Lua in ase.batch().add_raw_lua() is disabled by policy. "
                "Set ASEPRITE_ALLOW_RAW_LUA=1 to enable."
            )
        if not lua_snippet or not lua_snippet.strip():
            raise ValueError("lua_snippet cannot be empty")
        self._ops.append(f"do\n{lua_snippet}\nend")
        return self

    def build_lua(self) -> str:
        """Assemble the complete headless Aseprite Lua script for all queued operations."""
        _, _, lua_mod = self._lua_helpers()
        safe_out = self._escape(self._safe_filename.replace("\\", "/"))
        if self._create_size is not None:
            w, h = int(self._create_size[0]), int(self._create_size[1])
            preamble = f"local spr = Sprite({w}, {h})"
            save_stmt = f'spr:saveAs("{safe_out}")'
        else:
            preamble = 'local spr = app.activeSprite\nif not spr then print("ERROR:No active sprite") return end'
            save_stmt = "spr:saveAs(spr.filename)"

        ops_block = "\n        ".join(self._ops) if self._ops else "-- no-op"
        return "\n".join([
            lua_mod.FIND_LAYER,
            lua_mod.NORMALIZE_CEL,
            lua_mod.PSET,
            preamble,
            "local batch_err = nil",
            "app.transaction(function()",
            f"    {ops_block}",
            "end)",
            'if batch_err then print("ERROR:" .. batch_err) return end',
            save_stmt,
            'print("OK")',
        ])

    def commit(self) -> dict[str, Any]:
        """Compile and execute all queued operations in a single Aseprite batch process."""
        if self._committed:
            raise RuntimeError("Batch builder has already been committed")
        self._committed = True

        op_count = len(self._ops)
        if self._create_size is None and not os.path.exists(self._safe_filename):
            return {
                "ok": False,
                "operations": op_count,
                "output": f"File not found: {self._safe_filename}",
            }

        if self._create_size is not None:
            Path(self._safe_filename).parent.mkdir(parents=True, exist_ok=True)

        commands_mod, _, _ = self._lua_helpers()
        script = self.build_lua()
        open_target = None if self._create_size is not None else self._safe_filename
        success, output = commands_mod.AsepriteCommand.execute_lua_script_checked(script, open_target)
        return {
            "ok": bool(success),
            "operations": op_count,
            "output": output.strip() if isinstance(output, str) else str(output),
        }


class CategoryNamespace:
    """Dynamic attribute namespace exposing synchronous wrappers for a tool category."""

    def __init__(
        self,
        category: str,
        specs: list[ToolSpec],
        *,
        guard: WorkspacePathGuard,
        collector: PreviewCollector,
        allow_raw_lua: bool,
        allow_preview_server: bool,
        on_tool_error: Callable[[ToolSpec, str], Any] | None = None,
    ) -> None:
        self._category = category
        self._guard = guard
        self._collector = collector
        self._allow_raw_lua = allow_raw_lua
        self._allow_preview_server = allow_preview_server
        self._on_tool_error = on_tool_error
        self._specs_by_name: dict[str, ToolSpec] = {s.name: s for s in specs}

    def __dir__(self) -> list[str]:
        return sorted(self._specs_by_name.keys())

    def __getattr__(self, name: str) -> Callable[..., Any]:
        spec = self._specs_by_name.get(name)
        if spec is None:
            raise AttributeError(f"Unknown tool 'ase.{self._category}.{name}'")

        def _wrapper(*args: Any, **kwargs: Any) -> Any:
            return self._invoke(spec, args, kwargs)

        _wrapper.__name__ = spec.name
        _wrapper.__doc__ = spec.docstring
        return _wrapper

    def _check_policy(self, spec: ToolSpec) -> None:
        if spec.name in PREVIEW_SERVER_TOOLS and not self._allow_preview_server:
            raise PolicyBlockedToolError(
                f"Tool '{spec.name}' is disabled by security policy (ASEPRITE_ALLOW_PREVIEW_SERVER=0)."
            )
        if spec.name in RAW_LUA_TOOLS and not self._allow_raw_lua:
            raise PolicyBlockedToolError(
                f"Tool '{spec.name}' is disabled by security policy. Set ASEPRITE_ALLOW_RAW_LUA=1 to enable."
            )

    def _sanitize_arguments(self, spec: ToolSpec, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
        sig = inspect.signature(spec.callable_ref)
        try:
            bound = sig.bind(*args, **kwargs)
        except TypeError as exc:
            raise TypeError(f"{exc}. Expected signature: {spec.signature}") from exc
        bound.apply_defaults()
        for pname, val in list(bound.arguments.items()):
            param_def = sig.parameters[pname]
            if pname in PATH_PARAM_NAMES:
                allow_empty = param_def.default == ""
                if val is None and param_def.default is None:
                    continue
                resolved = self._guard.resolve_path(val, allow_empty=allow_empty)
                bound.arguments[pname] = resolved
                if resolved:
                    if pname in _OUTPUT_DIR_PARAMS:
                        Path(resolved).mkdir(parents=True, exist_ok=True)
                    elif pname in _OUTPUT_FILE_PARAMS or (
                        spec.name == "create_canvas" and pname == "filename"
                    ):
                        Path(resolved).parent.mkdir(parents=True, exist_ok=True)
            elif pname in CONDITIONAL_PATH_PARAM_NAMES:
                if isinstance(val, str) and looks_like_filesystem_path(val):
                    bound.arguments[pname] = self._guard.resolve_path(val, allow_empty=False)
        return dict(bound.arguments)

    def _invoke(self, spec: ToolSpec, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        self._check_policy(spec)
        safe_kwargs = self._sanitize_arguments(spec, args, kwargs)
        raw_result = _run_coro_sync(lambda: spec.callable_ref(**safe_kwargs))
        label = str(safe_kwargs.get("filename") or spec.name)
        out = self._collector.extract_from_result(raw_result, source_tool=spec.name, label=label)
        if is_upstream_error_message(out) and self._on_tool_error is not None:
            self._on_tool_error(spec, out)
        return out


class AsepriteSDK:
    """Top-level `ase` SDK supporting both eager and lazy category discovery."""

    def __init__(
        self,
        *,
        config: RuntimeConfig,
        upstream_pkg: ModuleType,
        catalog: ToolCatalog | None = None,
        collector: PreviewCollector | None = None,
        strict: bool = True,
    ) -> None:
        self._config = config
        self._upstream_pkg = upstream_pkg
        self._catalog = catalog
        self._guard = WorkspacePathGuard(config.workspace_dir)
        self._collector = collector or PreviewCollector(
            preview_dir=None,
            max_images=config.max_preview_images,
        )
        self.strict: bool = bool(strict)
        self.tool_warnings: list[str] = []

        if catalog is not None:
            self._category_names: tuple[str, ...] = tuple(sorted(catalog.by_category().keys()))
        else:
            self._category_names = tuple(list_upstream_categories(config.upstream_repo_path))

        self._namespaces: dict[str, CategoryNamespace] = {}

    def _handle_tool_error(self, spec: ToolSpec, message: str) -> None:
        if self.strict:
            raise AsepriteToolError(f"{spec.sdk_call} failed: {message}")
        self.tool_warnings.append(f"{spec.sdk_call}: {message}")

    def _get_namespace(self, category: str) -> CategoryNamespace:
        ns = self._namespaces.get(category)
        if ns is not None:
            return ns
        if category not in self._category_names:
            raise AttributeError(f"Unknown Aseprite SDK attribute or category '{category}'")

        if self._catalog is not None:
            specs = self._catalog.by_category().get(category, [])
        else:
            specs = ToolCatalog.for_category(self._upstream_pkg, category)

        ns = CategoryNamespace(
            category,
            specs,
            guard=self._guard,
            collector=self._collector,
            allow_raw_lua=self._config.allow_raw_lua,
            allow_preview_server=self._config.allow_preview_server,
            on_tool_error=self._handle_tool_error,
        )
        self._namespaces[category] = ns
        return ns

    def __getattr__(self, name: str) -> CategoryNamespace:
        if name.startswith("_"):
            raise AttributeError(name)
        return self._get_namespace(name)

    def __dir__(self) -> list[str]:
        public = {
            "Grid",
            "strict",
            "tool_warnings",
            "workspace_dir",
            "path",
            "copy",
            "read_grid",
            "list_files",
            "show",
            "batch",
            "run_lua",
            "help",
            *self._category_names,
        }
        return sorted(public)

    Grid = PixelGrid

    @property
    def workspace_dir(self) -> Path:
        return self._guard.workspace_dir

    @property
    def collector(self) -> PreviewCollector:
        return self._collector

    def path(self, rel: str) -> str:
        """Resolve and validate a relative/absolute path inside the sandbox workspace."""
        return self._guard.resolve_path(rel, allow_empty=False)

    def copy(self, src: str, dst: str, overwrite: bool = False) -> str:
        """Byte-for-byte copy a file inside the workspace (preserving layers, tags, metadata)."""
        safe_src = Path(self._guard.resolve_path(src, allow_empty=False))
        safe_dst = Path(self._guard.resolve_path(dst, allow_empty=False))
        if not safe_src.is_file():
            raise FileNotFoundError(f"Source file not found: {src}")
        if safe_dst.exists() and not overwrite:
            raise FileExistsError(f"Destination file already exists: {dst}")
        safe_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(safe_src, safe_dst)
        return self._guard.relative_posix(safe_dst)

    def read_grid(
        self,
        filename: str,
        x: int = 0,
        y: int = 0,
        width: int = 0,
        height: int = 0,
        frame_index: int = 1,
        layer_name: str = "",
    ) -> dict[str, Any]:
        """Read sprite pixels into a structured dict containing a `PixelGrid` + `rows` + `legend`."""
        ix, iy, iw, ih = int(x), int(y), int(width), int(height)
        if iw <= 0 or ih <= 0:
            info_raw = self._get_namespace("animation").get_sprite_info(filename=filename)
            info = json.loads(info_raw)
            if iw <= 0:
                iw = max(1, int(info["width"]) - ix)
            if ih <= 0:
                ih = max(1, int(info["height"]) - iy)

        raw = self._get_namespace("pixel_read").read_rows(
            filename=filename,
            x=ix,
            y=iy,
            width=iw,
            height=ih,
            frame_index=frame_index,
            layer_name=layer_name,
        )
        data = json.loads(raw)
        return {
            "x": int(data["x"]),
            "y": int(data["y"]),
            "width": int(data["width"]),
            "height": int(data["height"]),
            "grid": PixelGrid.from_rows(data["rows"]),
            "rows": data["rows"],
            "legend": data["legend"],
        }

    def list_files(self, glob_pattern: str = "**/*") -> list[str]:
        """List relative POSIX file paths inside the workspace."""
        if not isinstance(glob_pattern, str) or ".." in glob_pattern.replace("\\", "/").split("/"):
            raise WorkspaceViolationError("Parent traversal '..' is not allowed in glob_pattern")
        root = self._guard.workspace_dir
        files = [
            p.relative_to(root).as_posix()
            for p in root.glob(glob_pattern)
            if p.is_file()
        ]
        return sorted(files)

    def show(
        self,
        filename: str,
        frames: str = "",
        layer_name: str = "",
        region: str = "",
        scale: int = 0,
    ) -> str:
        """Render a sprite preview PNG and attach it to the MCP response."""
        res = self._get_namespace("view").view_sprite(
            filename=filename,
            frames=frames,
            layer_name=layer_name,
            region=region,
            scale=scale,
        )
        return str(res)

    def batch(
        self,
        filename: str,
        *,
        create_size: tuple[int, int] | None = None,
    ) -> LuaBatchBuilder:
        """Create a single-process Lua batch builder for `filename`."""
        return LuaBatchBuilder(
            filename=filename,
            guard=self._guard,
            upstream_pkg=self._upstream_pkg,
            allow_raw_lua=self._config.allow_raw_lua,
            create_size=create_size,
        )

    def run_lua(self, script: str, filename: str = "") -> str:
        """Execute raw Aseprite Lua code (requires ASEPRITE_ALLOW_RAW_LUA=1)."""
        return str(self._get_namespace("script").run_lua_script(script=script, filename=filename))

    def help(
        self,
        query: str = "",
        category: str = "",
        detail: str = "signature",
        limit: int = 12,
    ) -> dict[str, Any]:
        """Search available `ase.<category>.<tool>` signatures inline."""
        if self._catalog is None:
            self._catalog = ToolCatalog.from_upstream(self._upstream_pkg)
        return self._catalog.search(query=query, category=category, detail=detail, limit=limit)


_BATCH_CHAIN_METHODS = frozenset({
    "add_layer",
    "add_frame",
    "set_frame_duration",
    "set_tag",
    "set_layer_props",
    "copy_cel",
    "shift_cel",
    "replace_color",
    "fill_area",
    "draw_pixels",
    "draw_line",
    "draw_rect",
    "draw_ellipse",
    "draw_rows",
    "outline",
    "clear_cel",
    "set_palette",
    "add_raw_lua",
})

_BATCH_DIRECT_METHODS = frozenset({"commit", "build_lua"})


def _make_batch_proxy(builder: LuaBatchBuilder) -> Any:
    """Return a closure-isolated proxy hiding internal fields of `LuaBatchBuilder`."""

    class _BatchProxy:
        __slots__ = ()

        def __enter__(self) -> "_BatchProxy":
            builder.__enter__()
            return self

        def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
            return builder.__exit__(exc_type, exc, tb)

        def __getattr__(self, name: str) -> Any:
            if name.startswith("_"):
                raise AttributeError(f"Access to private attribute '{name}' is not allowed")
            if name == "operation_count":
                return builder.operation_count
            if name in _BATCH_CHAIN_METHODS:
                method = getattr(builder, name)

                def _chained(*args: Any, **kwargs: Any) -> "_BatchProxy":
                    method(*args, **kwargs)
                    return self

                _chained.__name__ = name
                return _chained
            if name in _BATCH_DIRECT_METHODS:
                method = getattr(builder, name)

                def _direct(*args: Any, **kwargs: Any) -> Any:
                    return method(*args, **kwargs)

                _direct.__name__ = name
                return _direct
            raise AttributeError(f"Unknown batch method '{name}'")

        def __setattr__(self, name: str, value: Any) -> None:
            raise AttributeError("Setting attributes on ase.batch proxy is not allowed")

        def __dir__(self) -> list[str]:
            return sorted(_BATCH_CHAIN_METHODS | _BATCH_DIRECT_METHODS | {"operation_count"})

    return _BatchProxy()


def _make_category_proxy(ns: CategoryNamespace) -> Any:
    """Return a closure-isolated proxy hiding `CategoryNamespace` internal attributes."""
    allowed = frozenset(dir(ns))

    class _CategoryProxy:
        __slots__ = ()

        def __getattr__(self, name: str) -> Callable[..., Any]:
            if name.startswith("_") or name not in allowed:
                raise AttributeError(f"Unknown tool '{name}'")
            fn = getattr(ns, name)

            def _call(*args: Any, **kwargs: Any) -> Any:
                return fn(*args, **kwargs)

            _call.__name__ = name
            _call.__doc__ = fn.__doc__
            return _call

        def __setattr__(self, name: str, value: Any) -> None:
            raise AttributeError("Setting attributes on category namespace is not allowed")

        def __dir__(self) -> list[str]:
            return sorted(allowed)

    return _CategoryProxy()


def create_sandbox_sdk(sdk: AsepriteSDK) -> Any:
    """Wrap `AsepriteSDK` in a closure-isolated proxy safe for sandboxed `exec()`."""
    category_proxies: dict[str, Any] = {}
    categories = frozenset(sdk._category_names)

    def _path(rel: str) -> str:
        return sdk.path(rel)

    def _copy(src: str, dst: str, overwrite: bool = False) -> str:
        return sdk.copy(src, dst, overwrite=overwrite)

    def _read_grid(
        filename: str,
        x: int = 0,
        y: int = 0,
        width: int = 0,
        height: int = 0,
        frame_index: int = 1,
        layer_name: str = "",
    ) -> dict[str, Any]:
        return sdk.read_grid(
            filename=filename,
            x=x,
            y=y,
            width=width,
            height=height,
            frame_index=frame_index,
            layer_name=layer_name,
        )

    def _list_files(glob_pattern: str = "**/*") -> list[str]:
        return sdk.list_files(glob_pattern)

    def _show(
        filename: str,
        frames: str = "",
        layer_name: str = "",
        region: str = "",
        scale: int = 0,
    ) -> str:
        return sdk.show(
            filename=filename,
            frames=frames,
            layer_name=layer_name,
            region=region,
            scale=scale,
        )

    def _batch(filename: str, *, create_size: tuple[int, int] | None = None) -> Any:
        return _make_batch_proxy(sdk.batch(filename, create_size=create_size))

    def _run_lua(script: str, filename: str = "") -> str:
        return sdk.run_lua(script=script, filename=filename)

    def _help(
        query: str = "",
        category: str = "",
        detail: str = "signature",
        limit: int = 12,
    ) -> dict[str, Any]:
        return sdk.help(query=query, category=category, detail=detail, limit=limit)

    public_funcs: dict[str, Callable[..., Any]] = {
        "path": _path,
        "copy": _copy,
        "read_grid": _read_grid,
        "list_files": _list_files,
        "show": _show,
        "batch": _batch,
        "run_lua": _run_lua,
        "help": _help,
    }

    class _SandboxSDKProxy:
        __slots__ = ()

        def __getattr__(self, name: str) -> Any:
            if name.startswith("_"):
                raise AttributeError(f"Access to private attribute '{name}' is not allowed")
            if name == "Grid":
                return PixelGrid
            if name == "strict":
                return sdk.strict
            if name == "workspace_dir":
                return str(sdk.workspace_dir)
            if name == "tool_warnings":
                return list(sdk.tool_warnings)
            if name in public_funcs:
                return public_funcs[name]
            if name in categories:
                proxy = category_proxies.get(name)
                if proxy is None:
                    proxy = _make_category_proxy(sdk._get_namespace(name))
                    category_proxies[name] = proxy
                return proxy
            raise AttributeError(f"Unknown attribute 'ase.{name}'")

        def __setattr__(self, name: str, value: Any) -> None:
            if name == "strict":
                sdk.strict = bool(value)
                return
            raise AttributeError(f"Cannot set attribute '{name}' on ase SDK")

        def __dir__(self) -> list[str]:
            return sorted(
                {"Grid", "strict", "workspace_dir", "tool_warnings"}
                | set(public_funcs.keys())
                | categories
            )

    return _SandboxSDKProxy()
