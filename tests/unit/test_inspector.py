"""Unit tests for single-pass sprite inspection analytics and composite QA renderer."""

from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch

from PIL import Image as PILImage

from aseprite_codemode.inspector import (
    compute_frame_bounds,
    compute_frame_colors,
    compute_frame_lint,
    compute_motion_report,
    inspect_sprite_file,
    parse_background,
    render_inspection_sheet,
    resolve_picked_frames,
)


def _make_frame(w: int, h: int, pixels: dict[tuple[int, int], tuple[int, int, int, int]]) -> PILImage.Image:
    img = PILImage.new("RGBA", (w, h), (0, 0, 0, 0))
    for (x, y), rgba in pixels.items():
        img.putpixel((x, y), rgba)
    return img


def test_parse_background_and_tag_resolution() -> None:
    assert parse_background("checker") == ("checker", None)
    assert parse_background("dark") == ("solid", (34, 34, 40, 255))
    assert parse_background("#7A746C") == ("solid", (122, 116, 108, 255))
    assert isinstance(parse_background("not-a-color"), str)

    tags = [{"name": "walk", "from": 3, "to": 6, "direction": "forward"}]
    assert resolve_picked_frames(8, tag="walk", tags=tags) == [3, 4, 5, 6]
    assert resolve_picked_frames(8, tag="WALK", tags=tags) == [3, 4, 5, 6]
    assert isinstance(resolve_picked_frames(8, tag="missing", tags=tags), str)


def test_frame_bounds_colors_lint_and_motion_metrics() -> None:
    green = (109, 170, 44, 255)
    dark = (20, 12, 28, 255)
    red = (208, 70, 72, 255)

    f1 = _make_frame(
        10,
        10,
        {
            (2, 6): green,
            (3, 6): green,
            (2, 7): green,
            (3, 7): dark,  # orphan pixel (no identical 8-neighbor)
            (8, 1): red,   # lone + detached pixel
        },
    )
    f2 = _make_frame(
        10,
        10,
        {
            (2, 5): green,
            (3, 5): green,
            (2, 6): green,
            (3, 6): green,
        },
    )

    b1 = compute_frame_bounds(f1)
    assert b1 is not None
    assert b1["bottom_y"] == 7
    assert b1["opaque_pixels"] == 5

    c1, hex1 = compute_frame_colors(f1)
    assert c1["unique_colors"] == 3
    assert "#D04648" in hex1

    lint1 = compute_frame_lint(f1)
    assert lint1["detached_count"] == 1
    assert lint1["lone_count"] == 1
    assert lint1["lone_pixels"] == [[8, 1]]
    assert [3, 7] in lint1["orphan_pixels"]

    motion = compute_motion_report({1: (f1, 100), 2: (f2, 120)}, [1, 2])
    assert motion["ground_line"]["bottom_y_by_frame"] == {"1": 7, "2": 6}
    assert motion["ground_line"]["variance_px"] == 1
    assert motion["ground_line"]["seam_bottom_y_delta"] == -1
    assert motion["seam_diff"]["from"] == 2
    assert motion["seam_diff"]["to"] == 1
    assert motion["seam_diff"]["changed_pixels"] > 0


def test_render_inspection_sheet_produces_valid_png_with_qa_strip() -> None:
    green = (109, 170, 44, 255)
    f1 = _make_frame(8, 8, {(3, 5): green, (4, 5): green})
    f2 = _make_frame(8, 8, {(3, 4): green, (4, 4): green})
    frames = {1: (f1, 100), 2: (f2, 100)}

    res = render_inspection_sheet(
        frames,
        [1, 2],
        "hero.aseprite",
        background="#7A746C",
        include_qa_strip=True,
        check_motion=True,
        ground_y=5,
    )
    assert isinstance(res, tuple)
    summary, png_bytes = res
    assert "bg=#7A746C" in summary
    assert png_bytes.startswith(b"\x89PNG\r\n\x1a\n")

    with PILImage.open(io.BytesIO(png_bytes)) as rendered:
        assert rendered.width > 120
        assert rendered.height > 120


def test_inspect_sprite_file_detects_palette_drift_and_motion(tmp_path: Path) -> None:
    green = (109, 170, 44, 255)
    yellow = (218, 212, 94, 255)
    f1 = _make_frame(8, 8, {(2, 6): green, (3, 6): green})
    f2 = _make_frame(8, 8, {(2, 5): green, (3, 5): yellow})

    fake_export = (
        {1: (f1, 100), 2: (f2, 150)},
        {
            "width": 8,
            "height": 8,
            "frames": 2,
            "durations_ms": [100, 150],
            "layers": [{"name": "body", "opacity": 255, "blend_mode": "normal"}],
            "tags": [{"name": "walk", "from": 1, "to": 2, "direction": "forward"}],
        },
    )

    with patch("aseprite_codemode.inspector.export_sprite_frames", return_value=fake_export):
        report, png_bytes = inspect_sprite_file(
            "aseprite",
            str(tmp_path / "walk.aseprite"),
            "walk.aseprite",
            tag="walk",
            background="dark",
            include_qa_strip=True,
            check_motion=True,
        )

    assert report["status"] == "ok"
    assert report["inspected_frames"] == [1, 2]
    assert report["per_frame"][0]["palette_drift_colors"] == []
    assert report["per_frame"][1]["palette_drift_colors"] == ["#DAD45E"]
    assert report["motion"]["ground_line"]["variance_px"] == 1
    assert png_bytes is not None and png_bytes.startswith(b"\x89PNG\r\n\x1a\n")
