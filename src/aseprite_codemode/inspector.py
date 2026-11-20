"""Single-pass sprite exporter, multi-frame quantitative QA, and diagnostic visual renderer."""

from __future__ import annotations

import functools
import io
import json
import math
import os
import subprocess
import tempfile
from collections import Counter, deque
from typing import Any

from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont

MAX_FRAMES = 16
MAX_COLUMNS = 4
TARGET_FRAME_PX = 384
MIN_AUTO_SCALE = 6
MAX_AUTO_FRAME_PX = 768
MAX_SHEET_PX = 1568
MAX_EXPLICIT_PX = 8192
RULER_W = 30
RULER_H = 18
TITLE_H = 18
GAP = 16

CHECKER = ((236, 236, 236, 255), (250, 250, 250, 255))
MINOR_LINE = (0, 0, 0, 38)
MAJOR_LINE = (0, 0, 0, 110)
LABEL = (90, 90, 90, 255)
TITLE = (30, 30, 30, 255)

_NAMED_BACKGROUNDS: dict[str, tuple[int, int, int, int]] = {
    "dark": (34, 34, 40, 255),
    "light": (230, 230, 234, 255),
    "mid": (128, 128, 128, 255),
}


@functools.cache
def _font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    return ImageFont.load_default(size=size)


def parse_background(background: str) -> tuple[str, tuple[int, int, int, int] | None] | str:
    """Parse `'checker'`, `'dark'`, `'light'`, or `#RGB` / `#RRGGBB` / `#RRGGBBAA`."""
    raw = (background or "checker").strip().lower()
    if raw == "checker":
        return ("checker", None)
    if raw in _NAMED_BACKGROUNDS:
        return ("solid", _NAMED_BACKGROUNDS[raw])
    h = raw.lstrip("#")
    if len(h) in (3, 4):
        h = "".join(c * 2 for c in h)
    if len(h) not in (6, 8):
        return (
            f"Invalid background '{background}': use 'checker', 'dark', 'light', "
            "'mid', or a hex color like '#7A746C'"
        )
    try:
        r = int(h[0:2], 16)
        g = int(h[2:4], 16)
        b = int(h[4:6], 16)
        a = int(h[6:8], 16) if len(h) == 8 else 255
    except ValueError:
        return f"Invalid hex background color '{background}'"
    return ("solid", (r, g, b, a))


def parse_region_str(value: str) -> tuple[int, int, int, int] | str | None:
    """Parse `'x,y,w,h'` into a tuple, None when empty, or an error message."""
    if not value or not value.strip():
        return None
    parts = value.split(",")
    if len(parts) != 4:
        return f"Invalid region '{value}': use \"x,y,w,h\" in sprite pixels"
    try:
        x, y, w, h = (int(p.strip()) for p in parts)
    except ValueError:
        return f"Invalid region '{value}': use \"x,y,w,h\" in sprite pixels"
    if w <= 0 or h <= 0:
        return f"Invalid region '{value}': width and height must be > 0"
    return x, y, w, h


def _parse_frames(frames: str, total: int, max_frames: int = MAX_FRAMES) -> list[int] | str:
    """Parse '', '3', '1-4', or '1,3,6-8' into unique 1-based frame numbers."""
    if not frames or not frames.strip():
        return list(range(1, min(total, max_frames) + 1))
    picked: list[int] = []
    for part in frames.split(","):
        part = part.strip()
        try:
            a, b = (int(v) for v in part.split("-", 1)) if "-" in part else (int(part), int(part))
        except ValueError:
            return f"Invalid frames '{frames}': use e.g. \"3\", \"1-4\" or \"1,3,6-8\""
        if a > b:
            return f"Invalid range '{part}': first frame must not exceed last frame"
        for f in (a, b):
            if f < 1 or f > total:
                return f"Frame {f} out of range (sprite has {total} frames)"
        if len(picked) + (b - a + 1) > max_frames:
            return f"Too many frames; inspect at most {max_frames} frames at once"
        picked.extend(range(a, b + 1))
    return list(dict.fromkeys(picked))


def resolve_picked_frames(
    total: int,
    *,
    frames: str = "",
    tag: str = "",
    tags: list[dict[str, Any]] | None = None,
    max_frames: int = MAX_FRAMES,
) -> list[int] | str:
    """Resolve either a tag name or a frame specification into 1-based frame indices."""
    if tag and tag.strip():
        target = tag.strip()
        tag_list = tags or []
        match = next((t for t in tag_list if t.get("name") == target), None)
        if match is None:
            match = next((t for t in tag_list if str(t.get("name", "")).lower() == target.lower()), None)
        if match is None:
            avail = ", ".join(str(t.get("name", "")) for t in tag_list) or "(none)"
            return f"Tag '{tag}' not found (available tags: {avail})"
        start = max(1, int(match["from"]))
        end = min(total, int(match["to"]))
        if start > end:
            return f"Tag '{tag}' has invalid range {start}-{end}"
        picked = list(range(start, end + 1))
        return picked[:max_frames]
    return _parse_frames(frames, total, max_frames=max_frames)


def export_sprite_frames(
    aseprite_path: str,
    filename: str,
    *,
    layer_name: str = "",
    timeout_seconds: float = 60.0,
) -> tuple[dict[int, tuple[PILImage.Image, int]], dict[str, Any]] | str:
    """Export all frames + layers + tags in ONE `aseprite --batch --sheet` invocation."""
    with tempfile.TemporaryDirectory(prefix="ase_inspect_") as tmp:
        sheet_path = os.path.join(tmp, "sheet.png")
        data_path = os.path.join(tmp, "sheet.json")
        args = [aseprite_path, "--batch"]
        if layer_name:
            args.extend(["--layer", layer_name])
        args.extend([
            filename,
            "--sheet", sheet_path,
            "--sheet-type", "horizontal",
            "--data", data_path,
            "--format", "json-array",
            "--list-layers",
            "--list-tags",
        ])

        try:
            proc = subprocess.run(
                args,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return f"Failed to export sprite: Aseprite timed out after {timeout_seconds:g}s"
        except OSError as exc:
            return f"Failed to run Aseprite '{aseprite_path}': {exc}"

        if proc.returncode != 0:
            err_detail = (proc.stderr or proc.stdout or "").strip()
            return f"Failed to render sprite: {err_detail or f'exit code {proc.returncode}'}"

        if not os.path.isfile(data_path) or not os.path.isfile(sheet_path):
            return "Failed to render sprite: Aseprite produced no sheet output"

        with open(data_path, encoding="utf-8") as fh:
            data = json.load(fh)
        with PILImage.open(sheet_path) as im:
            sheet = im.convert("RGBA")

    meta = data.get("meta", {})
    raw_layers = meta.get("layers", [])
    if layer_name and not raw_layers:
        return f"Layer '{layer_name}' not found in sprite"

    raw_frames = data.get("frames", [])
    if not raw_frames:
        return "Sprite exported 0 frames"

    frames_by_num: dict[int, tuple[PILImage.Image, int]] = {}
    durations_ms: list[int] = []
    for idx, entry in enumerate(raw_frames, start=1):
        rect = entry["frame"]
        cropped = sheet.crop((rect["x"], rect["y"], rect["x"] + rect["w"], rect["y"] + rect["h"]))
        dur = int(entry.get("duration", 100))
        frames_by_num[idx] = (cropped, dur)
        durations_ms.append(dur)

    raw_tags = meta.get("frameTags", [])
    tags = [
        {
            "name": t.get("name", ""),
            "from": int(t.get("from", 0)) + 1,
            "to": int(t.get("to", 0)) + 1,
            "direction": t.get("direction", "forward"),
        }
        for t in raw_tags
    ]
    layers = [
        {
            "name": lyr.get("name", ""),
            "opacity": lyr.get("opacity", 255),
            "blend_mode": lyr.get("blendMode", "normal"),
            **({"group": lyr["group"]} if "group" in lyr else {}),
        }
        for lyr in raw_layers
    ]
    first_img = frames_by_num[1][0]
    sprite_info = {
        "width": first_img.width,
        "height": first_img.height,
        "frames": len(frames_by_num),
        "durations_ms": durations_ms,
        "layers": layers,
        "tags": tags,
    }
    return frames_by_num, sprite_info


def _pixels(img: PILImage.Image) -> list[tuple[int, int, int, int]]:
    if hasattr(img, "get_flattened_data"):
        return list(img.get_flattened_data())
    return list(img.getdata())


def compute_frame_bounds(img: PILImage.Image) -> dict[str, int] | None:
    """Compute tight non-transparent pixel bounds for an RGBA frame."""
    rgba = img.convert("RGBA") if img.mode != "RGBA" else img
    w, h = rgba.size
    px = _pixels(rgba)
    min_x, min_y = w, h
    max_x, max_y = -1, -1
    count = 0
    for idx, (_, _, _, a) in enumerate(px):
        if a > 0:
            count += 1
            x = idx % w
            y = idx // w
            if x < min_x:
                min_x = x
            if x > max_x:
                max_x = x
            if y < min_y:
                min_y = y
            if y > max_y:
                max_y = y
    if count == 0:
        return None
    return {
        "x": min_x,
        "y": min_y,
        "w": max_x - min_x + 1,
        "h": max_y - min_y + 1,
        "left_x": min_x,
        "right_x": max_x,
        "top_y": min_y,
        "bottom_y": max_y,
        "opaque_pixels": count,
    }


def compute_frame_colors(
    img: PILImage.Image,
    top: int = 16,
) -> tuple[dict[str, Any], set[str]]:
    """Compute color histogram and hex color set for an RGBA frame."""
    rgba = img.convert("RGBA") if img.mode != "RGBA" else img
    counts: Counter[tuple[int, int, int, int]] = Counter()
    opaque_pixels = 0
    for r, g, b, a in _pixels(rgba):
        if a > 0:
            opaque_pixels += 1
            counts[(r, g, b, a)] += 1

    def _to_hex(c: tuple[int, int, int, int]) -> str:
        r, g, b, a = c
        return f"#{r:02X}{g:02X}{b:02X}" if a == 255 else f"#{r:02X}{g:02X}{b:02X}{a:02X}"

    hex_set = {_to_hex(c) for c in counts}
    top_colors = [
        {"hex": _to_hex(col), "count": cnt}
        for col, cnt in counts.most_common(max(1, top))
    ]
    return {
        "unique_colors": len(counts),
        "opaque_pixels": opaque_pixels,
        "top_colors": top_colors,
    }, hex_set


def compute_frame_lint(img: PILImage.Image, max_coords: int = 20) -> dict[str, Any]:
    """Detect detached components, lone 1px dots, and orphan pixels inside an RGBA frame."""
    rgba = img.convert("RGBA") if img.mode != "RGBA" else img
    w, h = rgba.size
    px = _pixels(rgba)

    visited = [False] * (w * h)
    components: list[dict[str, Any]] = []
    lone_pixels: list[list[int]] = []
    orphan_pixels: list[list[int]] = []

    neighbors_8 = ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1))

    for y in range(h):
        for x in range(w):
            i = y * w + x
            c = px[i]
            if c[3] == 0:
                continue

            has_opaque_neighbor = False
            has_same_color_neighbor = False
            for dx, dy in neighbors_8:
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h:
                    nc = px[ny * w + nx]
                    if nc[3] > 0:
                        has_opaque_neighbor = True
                        if nc == c:
                            has_same_color_neighbor = True
            if not has_opaque_neighbor:
                lone_pixels.append([x, y])
            if not has_same_color_neighbor:
                orphan_pixels.append([x, y])

            if not visited[i]:
                visited[i] = True
                queue = deque([(x, y)])
                comp_count = 0
                min_x, min_y, max_x, max_y = x, y, x, y
                while queue:
                    cx, cy = queue.popleft()
                    comp_count += 1
                    if cx < min_x:
                        min_x = cx
                    if cx > max_x:
                        max_x = cx
                    if cy < min_y:
                        min_y = cy
                    if cy > max_y:
                        max_y = cy
                    for dx, dy in neighbors_8:
                        nx, ny = cx + dx, cy + dy
                        if 0 <= nx < w and 0 <= ny < h:
                            ni = ny * w + nx
                            if not visited[ni] and px[ni][3] > 0:
                                visited[ni] = True
                                queue.append((nx, ny))
                components.append({
                    "pixels": comp_count,
                    "bounds": {
                        "x": min_x,
                        "y": min_y,
                        "w": max_x - min_x + 1,
                        "h": max_y - min_y + 1,
                    },
                })

    components.sort(key=lambda item: -item["pixels"])
    detached = components[1:max_coords + 1] if len(components) > 1 else []
    return {
        "component_count": len(components),
        "detached_count": max(0, len(components) - 1),
        "detached": detached,
        "lone_count": len(lone_pixels),
        "lone_pixels": lone_pixels[:max_coords],
        "orphan_count": len(orphan_pixels),
        "orphan_pixels": orphan_pixels[:max_coords],
    }


def _diff_two_frames(img_a: PILImage.Image, img_b: PILImage.Image) -> dict[str, Any]:
    a_rgba = img_a.convert("RGBA") if img_a.mode != "RGBA" else img_a
    b_rgba = img_b.convert("RGBA") if img_b.mode != "RGBA" else img_b
    w, h = a_rgba.size
    pa = _pixels(a_rgba)
    pb = _pixels(b_rgba)

    changed = 0
    union_opaque = 0
    min_x, min_y = w, h
    max_x, max_y = -1, -1

    for idx, (ca, cb) in enumerate(zip(pa, pb)):
        if ca[3] > 0 or cb[3] > 0:
            union_opaque += 1
        if ca != cb:
            changed += 1
            x = idx % w
            y = idx // w
            if x < min_x:
                min_x = x
            if x > max_x:
                max_x = x
            if y < min_y:
                min_y = y
            if y > max_y:
                max_y = y

    denom = max(1, union_opaque)
    diff_bbox = (
        {"x": min_x, "y": min_y, "w": max_x - min_x + 1, "h": max_y - min_y + 1}
        if changed > 0
        else None
    )
    return {
        "changed_pixels": changed,
        "change_pct": round((changed / denom) * 100.0, 2),
        "diff_bbox": diff_bbox,
    }


def compute_motion_report(
    frames_by_num: dict[int, tuple[PILImage.Image, int]],
    picked: list[int],
) -> dict[str, Any]:
    """Compute ground-line stability (`bottom_y`), frame-to-frame diffs, and loop seam diff."""
    bottom_by_frame: dict[str, int | None] = {}
    valid_bottoms: list[int] = []
    for n in picked:
        b = compute_frame_bounds(frames_by_num[n][0])
        by = b["bottom_y"] if b is not None else None
        bottom_by_frame[str(n)] = by
        if by is not None:
            valid_bottoms.append(by)

    if valid_bottoms:
        min_by = min(valid_bottoms)
        max_by = max(valid_bottoms)
        first_by = bottom_by_frame[str(picked[0])]
        last_by = bottom_by_frame[str(picked[-1])]
        seam_delta = (last_by - first_by) if (first_by is not None and last_by is not None) else None
        ground_line: dict[str, Any] = {
            "bottom_y_by_frame": bottom_by_frame,
            "min_bottom_y": min_by,
            "max_bottom_y": max_by,
            "variance_px": max_by - min_by,
            "seam_bottom_y_delta": seam_delta,
        }
    else:
        ground_line = {
            "bottom_y_by_frame": bottom_by_frame,
            "min_bottom_y": None,
            "max_bottom_y": None,
            "variance_px": 0,
            "seam_bottom_y_delta": None,
        }

    adjacent_diffs: list[dict[str, Any]] = []
    for idx in range(len(picked) - 1):
        fa, fb = picked[idx], picked[idx + 1]
        d = _diff_two_frames(frames_by_num[fa][0], frames_by_num[fb][0])
        adjacent_diffs.append({"from": fa, "to": fb, **d})

    seam_diff: dict[str, Any] | None = None
    if len(picked) >= 2:
        fa, fb = picked[-1], picked[0]
        seam_diff = {"from": fa, "to": fb, **_diff_two_frames(frames_by_num[fa][0], frames_by_num[fb][0])}

    return {
        "ground_line": ground_line,
        "adjacent_diffs": adjacent_diffs,
        "seam_diff": seam_diff,
    }


def _make_backdrop(
    w: int,
    h: int,
    bg_spec: tuple[str, tuple[int, int, int, int] | None],
) -> PILImage.Image:
    kind, solid = bg_spec
    if kind == "solid" and solid is not None:
        return PILImage.new("RGBA", (w, h), solid)
    a, b = bytes(CHECKER[0]), bytes(CHECKER[1])
    even = ((a + b) * (w // 2 + 1))[: w * 4]
    odd = ((b + a) * (w // 2 + 1))[: w * 4]
    data = ((even + odd) * (h // 2 + 1))[: w * h * 4]
    return PILImage.frombytes("RGBA", (w, h), data)


def _label_step(scale: int) -> int:
    for step in (1, 2, 4, 8, 16, 32, 64):
        if step * scale >= 22:
            return step
    return 128


def _margins(rulers: bool) -> tuple[int, int]:
    return (RULER_W if rulers else 0), TITLE_H + (RULER_H if rulers else 0)


def _render_frame(
    frame: PILImage.Image,
    backdrop: PILImage.Image,
    scale: int,
    grid: bool,
    origin: tuple[int, int] = (0, 0),
) -> PILImage.Image:
    w, h = frame.size
    ox, oy = origin
    flat = PILImage.alpha_composite(backdrop, frame)
    out = flat.resize((w * scale, h * scale), PILImage.Resampling.NEAREST)
    if grid and scale >= 4:
        lines = PILImage.new("RGBA", out.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(lines)
        for x in range(1, w):
            d.line([(x * scale, 0), (x * scale, h * scale)], fill=MAJOR_LINE if (ox + x) % 8 == 0 else MINOR_LINE)
        for y in range(1, h):
            d.line([(0, y * scale), (w * scale, y * scale)], fill=MAJOR_LINE if (oy + y) % 8 == 0 else MINOR_LINE)
        out = PILImage.alpha_composite(out, lines)
    return out


def _panel(
    frame: PILImage.Image,
    backdrop: PILImage.Image,
    title: str,
    scale: int,
    grid: bool,
    rulers: bool,
    origin: tuple[int, int] = (0, 0),
) -> PILImage.Image:
    w, h = frame.size
    ox, oy = origin
    body = _render_frame(frame, backdrop, scale, grid, origin)
    left, top = _margins(rulers)
    panel = PILImage.new("RGBA", (left + body.width, top + body.height), (255, 255, 255, 255))
    panel.paste(body, (left, top))
    d = ImageDraw.Draw(panel)
    d.text((left, 2), title, fill=TITLE, font=_font(13))
    if rulers:
        font = _font(11)
        step = _label_step(scale)
        for x in range(-(-ox // step) * step, ox + w, step):
            cx = left + (x - ox) * scale + scale // 2
            d.text((cx, top - 3), str(x), fill=LABEL, font=font, anchor="mb")
        for y in range(-(-oy // step) * step, oy + h, step):
            cy = top + (y - oy) * scale + scale // 2
            d.text((left - 4, cy), str(y), fill=LABEL, font=font, anchor="rm")
    return panel


def _sheet_size(fw: int, fh: int, scale: int, cols: int, rows: int, rulers: bool) -> tuple[int, int]:
    left, top = _margins(rulers)
    pw, ph = left + fw * scale, top + fh * scale
    return cols * pw + (cols + 1) * GAP, rows * ph + (rows + 1) * GAP


def _layout(n: int, fw: int, fh: int, scale: int, rulers: bool) -> tuple[int, int, int]:
    longest = max(fw, fh)
    start = max(1, TARGET_FRAME_PX // longest, min(MIN_AUTO_SCALE, MAX_AUTO_FRAME_PX // longest))
    best = None
    for cols in range(min(n, MAX_COLUMNS), 0, -1):
        rows = math.ceil(n / cols)
        s = scale or start
        if not scale:
            while s > 1 and max(_sheet_size(fw, fh, s, cols, rows, rulers)) > MAX_SHEET_PX:
                s -= 1
        fits = max(_sheet_size(fw, fh, s, cols, rows, rulers)) <= MAX_SHEET_PX
        key = (fits, s, -(cols * rows - n))
        if best is None or key > best[0]:
            best = (key, s, cols, rows)
    assert best is not None
    return best[1], best[2], best[3]


def _to_grayscale_value(frame: PILImage.Image) -> PILImage.Image:
    w, h = frame.size
    px = [
        (
            v := max(0, min(255, int(0.2126 * r + 0.7152 * g + 0.0722 * b))),
            v,
            v,
            a,
        )
        for (r, g, b, a) in _pixels(frame)
    ]
    out = PILImage.new("RGBA", (w, h))
    out.putdata(px)
    return out


def _to_silhouette(frame: PILImage.Image) -> PILImage.Image:
    w, h = frame.size
    px = [(20, 20, 24, 255) if a > 0 else (0, 0, 0, 0) for (_, _, _, a) in _pixels(frame)]
    out = PILImage.new("RGBA", (w, h))
    out.putdata(px)
    return out


def _tint_frame(frame: PILImage.Image, tint: tuple[int, int, int], alpha_scale: float = 1.0) -> PILImage.Image:
    w, h = frame.size
    tr, tg, tb = tint
    px = [
        (tr, tg, tb, max(0, min(255, int(a * alpha_scale)))) if a > 0 else (0, 0, 0, 0)
        for (_, _, _, a) in _pixels(frame)
    ]
    out = PILImage.new("RGBA", (w, h))
    out.putdata(px)
    return out


def _build_qa_strip(
    cropped: dict[int, PILImage.Image],
    picked: list[int],
    backdrop: PILImage.Image,
    bg_spec: tuple[str, tuple[int, int, int, int] | None],
    *,
    check_motion: bool,
    ground_y: int | None,
) -> PILImage.Image:
    first_img = cropped[picked[0]]
    fw, fh = first_img.size
    qa_scale = max(2, min(4, 128 // max(fw, fh, 1)))
    tiles: list[PILImage.Image] = []

    # 1. 1x native tile (up to 4 frames side-by-side)
    native_frames = picked[:4]
    nat_w = len(native_frames) * fw + max(0, len(native_frames) - 1) * 2
    nat_body = PILImage.new("RGBA", (nat_w + 2, fh + 2), (180, 180, 188, 255))
    for i, fn in enumerate(native_frames):
        comp = PILImage.alpha_composite(backdrop, cropped[fn])
        nat_body.paste(comp, (1 + i * (fw + 2), 1))
    nat_tile = PILImage.new("RGBA", (max(72, nat_body.width), TITLE_H + nat_body.height), (255, 255, 255, 255))
    ImageDraw.Draw(nat_tile).text((0, 2), "1x native", fill=TITLE, font=_font(12))
    nat_tile.paste(nat_body, (0, TITLE_H))
    tiles.append(nat_tile)

    # 2. Grayscale luminance value tile
    gray_bg_color = bg_spec[1] if (bg_spec[0] == "solid" and bg_spec[1] is not None) else (128, 128, 128, 255)
    gray_bg = PILImage.new("RGBA", (fw, fh), gray_bg_color)
    gray_panel = _panel(_to_grayscale_value(first_img), gray_bg, "grayscale (value)", qa_scale, False, False)
    tiles.append(gray_panel)

    # 3. Silhouette tile
    sil_bg = PILImage.new("RGBA", (fw, fh), (242, 242, 244, 255))
    sil_panel = _panel(_to_silhouette(first_img), sil_bg, "silhouette", qa_scale, False, False)
    tiles.append(sil_panel)

    # 4. Seam onion-skin tile when checking motion across >= 2 frames
    if check_motion and len(picked) >= 2:
        last_n, first_n = picked[-1], picked[0]
        ghost_prev = _tint_frame(cropped[last_n], (215, 65, 175), 0.55)
        ghost_curr = _tint_frame(cropped[first_n], (30, 185, 215), 0.85)
        seam_comp = PILImage.alpha_composite(backdrop, ghost_prev)
        seam_comp = PILImage.alpha_composite(seam_comp, ghost_curr)
        seam_scaled = seam_comp.resize((fw * qa_scale, fh * qa_scale), PILImage.Resampling.NEAREST)
        if ground_y is not None and 0 <= ground_y < fh:
            gy_px = (ground_y + 1) * qa_scale - 1
            ImageDraw.Draw(seam_scaled).line([(0, gy_px), (seam_scaled.width - 1, gy_px)], fill=(220, 40, 40, 220))
        seam_tile = PILImage.new("RGBA", (max(120, seam_scaled.width), TITLE_H + seam_scaled.height), (255, 255, 255, 255))
        ImageDraw.Draw(seam_tile).text((0, 2), f"seam f{last_n}->f{first_n}", fill=TITLE, font=_font(12))
        seam_tile.paste(seam_scaled, (0, TITLE_H))
        tiles.append(seam_tile)

    strip_w = sum(t.width for t in tiles) + GAP * (len(tiles) + 1)
    strip_h = max(t.height for t in tiles) + GAP
    strip = PILImage.new("RGB", (strip_w, strip_h), (255, 255, 255))
    x_cursor = GAP
    for t in tiles:
        strip.paste(t.convert("RGB"), (x_cursor, 0))
        x_cursor += t.width + GAP
    return strip


def render_inspection_sheet(
    frames_by_num: dict[int, tuple[PILImage.Image, int]],
    picked: list[int],
    filename: str,
    *,
    layer_name: str = "",
    region: tuple[int, int, int, int] | None = None,
    scale: int = 0,
    background: str = "checker",
    include_qa_strip: bool = True,
    check_motion: bool = False,
    ground_y: int | None = None,
    total_frames: int | None = None,
) -> tuple[str, bytes] | str:
    """Render zoomed sprite frames + optional QA strip into a PNG byte buffer."""
    if scale < 0 or scale > 64:
        return "scale must be between 0 (auto) and 64"
    bg_parsed = parse_background(background)
    if isinstance(bg_parsed, str):
        return bg_parsed

    sw, sh = frames_by_num[picked[0]][0].size
    box = (0, 0, sw, sh)
    if region is not None:
        rx, ry, rw, rh = region
        box = (max(0, rx), max(0, ry), min(sw, rx + rw), min(sh, ry + rh))
        if box[2] <= box[0] or box[3] <= box[1]:
            return f"Region {rx},{ry},{rw},{rh} is outside the {sw}x{sh} sprite"
    fw, fh = box[2] - box[0], box[3] - box[1]

    eff_scale, cols, rows = _layout(len(picked), fw, fh, scale, rulers=True)
    if max(_sheet_size(fw, fh, eff_scale, cols, rows, rulers=True)) > MAX_EXPLICIT_PX:
        return (
            f"Image would exceed {MAX_EXPLICIT_PX} px at {eff_scale}x; "
            "use a smaller scale, fewer frames, or scale=0"
        )

    backdrop = _make_backdrop(fw, fh, bg_parsed)
    cropped: dict[int, PILImage.Image] = {}
    panels: list[PILImage.Image] = []
    for n in picked:
        frame_img, duration = frames_by_num[n]
        crop_img = frame_img.crop(box)
        cropped[n] = crop_img
        panels.append(
            _panel(
                crop_img,
                backdrop,
                f"frame {n} · {duration}ms",
                eff_scale,
                grid=True,
                rulers=True,
                origin=box[:2],
            )
        )

    pw, ph = panels[0].size
    main_w, main_h = _sheet_size(fw, fh, eff_scale, cols, rows, rulers=True)

    qa_strip: PILImage.Image | None = None
    if include_qa_strip:
        local_gy = (ground_y - box[1]) if (ground_y is not None) else None
        qa_strip = _build_qa_strip(
            cropped,
            picked,
            backdrop,
            bg_parsed,
            check_motion=check_motion,
            ground_y=local_gy,
        )

    out_w = max(main_w, qa_strip.width if qa_strip is not None else 0)
    out_h = main_h + (qa_strip.height if qa_strip is not None else 0)
    out = PILImage.new("RGB", (out_w, out_h), (255, 255, 255))
    for i, p in enumerate(panels):
        out.paste(p.convert("RGB"), (GAP + (i % cols) * (pw + GAP), GAP + (i // cols) * (ph + GAP)))
    if qa_strip is not None:
        out.paste(qa_strip, (0, main_h))

    buf = io.BytesIO()
    out.save(buf, format="PNG")

    total = total_frames if total_frames is not None else len(frames_by_num)
    shown = ", ".join(str(n) for n in picked)
    where = f", region {box[0]},{box[1]} {fw}x{fh}" if region is not None else ""
    layer_desc = f" of layer '{layer_name}'" if layer_name else ""
    summary = (
        f"{os.path.basename(filename)}: {sw}x{sh}, {total} frame(s); "
        f"showing frame(s) {shown}{layer_desc}{where} at {eff_scale}x (bg={background})."
    )
    return summary, buf.getvalue()


def inspect_sprite_file(
    aseprite_path: str,
    safe_file: str,
    relative_name: str,
    *,
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
    timeout_seconds: float = 60.0,
) -> tuple[dict[str, Any], bytes | None]:
    """Single-pass export + multi-frame QA analytics + composite visual sheet."""
    parsed_region = parse_region_str(region)
    if isinstance(parsed_region, str):
        return {"status": "error", "filename": relative_name, "error": parsed_region}, None

    exported = export_sprite_frames(
        aseprite_path,
        safe_file,
        layer_name=layer_name,
        timeout_seconds=timeout_seconds,
    )
    if isinstance(exported, str):
        return {"status": "error", "filename": relative_name, "error": exported}, None

    frames_by_num, sprite_info = exported
    picked = resolve_picked_frames(
        sprite_info["frames"],
        frames=frames,
        tag=tag,
        tags=sprite_info.get("tags"),
    )
    if isinstance(picked, str):
        return {
            "status": "error",
            "filename": relative_name,
            "sprite_info": sprite_info,
            "error": picked,
        }, None

    base_colors: set[str] | None = None
    per_frame: list[dict[str, Any]] = []
    first_lint: dict[str, Any] | None = None
    first_colors: dict[str, Any] | None = None

    for idx, fnum in enumerate(picked):
        fimg, fdur = frames_by_num[fnum]
        bounds = compute_frame_bounds(fimg)
        color_stats, hex_set = compute_frame_colors(fimg, top=16)
        if idx == 0:
            base_colors = hex_set
            first_colors = color_stats
            drift: list[str] = []
        else:
            drift = sorted(hex_set - (base_colors or set()))

        entry: dict[str, Any] = {
            "frame": fnum,
            "duration_ms": fdur,
            "bounds": bounds,
            "unique_colors": color_stats["unique_colors"],
            "opaque_pixels": color_stats["opaque_pixels"],
            "palette_drift_colors": drift,
        }
        if include_lint:
            flint = compute_frame_lint(fimg)
            if idx == 0:
                first_lint = flint
            entry["detached_count"] = flint["detached_count"]
            entry["lone_count"] = flint["lone_count"]
            entry["orphan_count"] = flint["orphan_count"]
        per_frame.append(entry)

    motion: dict[str, Any] | None = None
    ground_y: int | None = None
    if check_motion:
        motion = compute_motion_report(frames_by_num, picked)
        ground_y = motion["ground_line"]["min_bottom_y"]

    rendered = render_inspection_sheet(
        frames_by_num,
        picked,
        safe_file,
        layer_name=layer_name,
        region=parsed_region,
        scale=scale,
        background=background,
        include_qa_strip=include_qa_strip,
        check_motion=check_motion,
        ground_y=ground_y,
        total_frames=sprite_info["frames"],
    )
    if isinstance(rendered, str):
        return {
            "status": "error",
            "filename": relative_name,
            "sprite_info": sprite_info,
            "error": rendered,
        }, None

    view_summary, png_bytes = rendered
    report: dict[str, Any] = {
        "status": "ok",
        "filename": relative_name,
        "inspected_frames": picked,
        "analyzed_frame_index": picked[0],
        "view_summary": view_summary,
        "sprite_info": sprite_info,
        "per_frame": per_frame,
    }
    if include_lint and first_lint is not None:
        report["lint"] = first_lint
    if include_colors and first_colors is not None:
        report["color_stats"] = first_colors
    if motion is not None:
        report["motion"] = motion

    return report, png_bytes
