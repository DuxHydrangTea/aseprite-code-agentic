"""End-to-end integration tests running against a real Aseprite headless binary."""

from __future__ import annotations

import base64
import json
from dataclasses import replace
from pathlib import Path

import pytest

from aseprite_codemode.config import RuntimeConfig
from aseprite_codemode.server import create_server


@pytest.fixture()
def live_server(tmp_workspace: Path, upstream_repo_path: Path, real_aseprite_path: str):
    cfg = RuntimeConfig(
        workspace_dir=tmp_workspace,
        aseprite_path=real_aseprite_path,
        upstream_repo_path=upstream_repo_path,
        aseprite_timeout_seconds=60.0,
        code_timeout_seconds=60.0,
        allow_raw_lua=False,
        allow_preview_server=False,
    )
    return create_server(cfg), cfg


@pytest.mark.asyncio
async def test_e2e_execute_code_with_auto_view(live_server) -> None:
    srv, cfg = live_server
    code = """
ramp = ase.palette.generate_color_ramp("#346524", steps=4, hue_shift_degrees=15)
ase.canvas.create_canvas(16, 16, "slime.aseprite")
ase.canvas.add_layer("slime.aseprite", "slime")
ase.drawing.draw_rows(
    "slime.aseprite",
    "slime",
    1,
    4,
    6,
    [
        "..GGGG..",
        ".GGGGGG.",
        "GGGGGGGG",
        "GKGGGGKG",
        "GGGGGGGG",
        ".GGGGGG.",
    ],
    {"G": "#6DAA2C", "K": "#140C1C"},
)
ase.fx.outline_cel("slime.aseprite", "slime", 1, "#140C1C")
result = {"ramp": ramp, "files": ase.list_files()}
"""
    res = await srv.call_tool(
        "execute_code",
        {"code": code, "auto_view": "slime.aseprite", "view_scale": 6},
    )
    assert len(res) >= 2
    summary = json.loads(res[0].text)
    assert summary["status"] == "ok", summary
    assert "slime.aseprite" in summary["files_created"]
    assert (cfg.workspace_dir / "slime.aseprite").is_file()
    assert res[1].type == "image"
    png_bytes = base64.b64decode(res[1].data)
    assert png_bytes.startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.asyncio
async def test_e2e_expanded_batch_and_inspect(live_server) -> None:
    srv, cfg = live_server
    code = """
with ase.batch("batched.aseprite", create_size=(16, 16)) as b:
    b.set_palette(["#140C1C", "#346524", "#6DAA2C", "#DEEED6"])
    b.add_layer("body")
    b.draw_ellipse("body", 1, 8, 9, 4, 3, "#346524", filled=True)
    b.draw_line("body", 1, 4, 12, 12, 12, "#6DAA2C", thickness=1)
    b.draw_rows(
        "body",
        1,
        6,
        8,
        ["W..W"],
        {"W": "#DEEED6"},
    )
    b.draw_pixels("body", 1, [(8, 7, "#DEEED6")])
    b.outline("body", 1, "#140C1C")
    b.add_frame(count=1, duration_ms=150, empty=True)
    b.draw_rect("body", 2, 6, 6, 4, 4, "#6DAA2C", filled=True)
    b.set_frame_duration(1, 120)
    b.set_tag("idle", 1, 2, direction="pingpong")

result = {"batched": True}
"""
    exec_res = await srv.call_tool("execute_code", {"code": code})
    summary = json.loads(exec_res[0].text)
    assert summary["status"] == "ok", summary
    assert (cfg.workspace_dir / "batched.aseprite").is_file()

    insp_res = await srv.call_tool(
        "inspect_sprite",
        {"filename": "batched.aseprite", "frames": "1-2", "layer_name": "body"},
    )
    assert len(insp_res) >= 2
    report = json.loads(insp_res[0].text)
    assert report["status"] == "ok", report
    assert report["analyzed_frame_index"] == 1
    assert report["sprite_info"]["frames"] == 2
    assert report["sprite_info"]["durations_ms"] == [120, 150]
    assert report["sprite_info"]["tags"] == [
        {"name": "idle", "from": 1, "to": 2, "direction": "pingpong"}
    ]
    assert "lint" in report
    assert "color_stats" in report
    assert insp_res[1].type == "image"
    assert base64.b64decode(insp_res[1].data).startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.asyncio
async def test_e2e_multi_frame_loop_and_ase_show(live_server) -> None:
    srv, _ = live_server
    code = """
with ase.batch("bounce.aseprite", create_size=(16, 16)) as b:
    b.add_layer("dot")
    b.add_frame(count=3, duration_ms=100, empty=True)
    offsets = [10, 8, 6, 9]
    for frame_idx, y_pos in enumerate(offsets, start=1):
        b.draw_rect("dot", frame_idx, 6, y_pos, 4, 4, "#D04648", filled=True)

preview_msg = ase.show("bounce.aseprite", frames="1-4", scale=6)
info = ase.animation.get_sprite_info("bounce.aseprite")
result = {"preview_msg": preview_msg, "info": info}
"""
    res = await srv.call_tool("execute_code", {"code": code})
    assert len(res) >= 2
    summary = json.loads(res[0].text)
    assert summary["status"] == "ok", summary
    info = json.loads(summary["return_value"]["info"])
    assert info["frames"] == 4
    assert res[1].type == "image"
    assert base64.b64decode(res[1].data).startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.asyncio
async def test_e2e_strict_mode_and_preview_limit_with_auto_view(live_server) -> None:
    srv, cfg = live_server

    fail_code = """
ase.canvas.create_canvas(8, 8, "strict.aseprite")
ase.drawing.draw_rectangle_at("strict.aseprite", "no_such_layer", 1, 0, 0, 4, 4, "#FFFFFF", True)
"""
    fail_res = await srv.call_tool("execute_code", {"code": fail_code})
    fail_summary = json.loads(fail_res[0].text)
    assert fail_summary["status"] == "error"
    assert fail_summary["error_type"] == "AsepriteToolError"

    cfg_one_img = replace(cfg, max_preview_images=1)
    srv_one = create_server(cfg_one_img)
    limit_code = """
ase.canvas.create_canvas(8, 8, "limit.aseprite")
ase.show("limit.aseprite")
ase.strict = False
warn_msg = ase.drawing.draw_rectangle_at("limit.aseprite", "no_such_layer", 1, 0, 0, 4, 4, "#FFFFFF", True)
result = {"warn_msg": warn_msg}
"""
    limit_res = await srv_one.call_tool(
        "execute_code",
        {"code": limit_code, "auto_view": "limit.aseprite"},
    )
    limit_summary = json.loads(limit_res[0].text)
    assert limit_summary["status"] == "ok", limit_summary
    assert len(limit_summary["previews"]) == 1
    assert len(limit_summary["tool_warnings"]) == 1
    assert "max_preview_images limit" in limit_summary["stderr"]


@pytest.mark.asyncio
async def test_e2e_grid_copy_read_grid_and_batch_ops(live_server) -> None:
    srv, cfg = live_server
    code = """
body_part = ase.Grid(16, 16)
body_part.rect(5, 8, 6, 5, "G", filled=True)
body_part.set(6, 10, "W")
body_part.set(9, 10, "W")
assert body_part.bottom_y() == 12

with ase.batch("scout.aseprite", create_size=(16, 16)) as b:
    b.set_palette(["#140C1C", "#346524", "#6DAA2C", "#DEEED6"])
    b.add_layer("shadow")
    b.set_layer_props("shadow", opacity=128, stack_index=1)
    b.draw_rect("shadow", 1, 5, 13, 6, 1, "#140C1C", filled=True)
    b.add_layer("body")
    b.draw_rows("body", 1, 0, 0, body_part.to_rows(), {"G": "#6DAA2C", "W": "#DEEED6"})
    b.fill_area("body", 1, 6, 9, "#346524")
    b.replace_color("body", 1, "#346524", "#6DAA2C")
    b.add_frame(count=1, duration_ms=150, empty=True)
    b.copy_cel("body", 1, 2)
    b.shift_cel("body", 2, 0, -1)
    b.copy_cel("shadow", 1, 2)
    b.set_tag("idle", 1, 2, direction="pingpong")

copied = ase.copy("scout.aseprite", "scout_copy.aseprite")
read_back = ase.read_grid("scout_copy.aseprite", frame_index=1, layer_name="body")
patch = read_back["grid"].copy()
patch.set(7, 9, "W")
with ase.batch("scout_copy.aseprite") as b2:
    b2.draw_rows("body", 1, 0, 0, patch.to_rows(), {**read_back["legend"], "W": "#DEEED6"})

result = {"copied": copied, "bottom": body_part.bottom_y(), "w": read_back["width"]}
"""
    exec_res = await srv.call_tool("execute_code", {"code": code})
    summary = json.loads(exec_res[0].text)
    assert summary["status"] == "ok", summary
    assert summary["return_value"] == {"copied": "scout_copy.aseprite", "bottom": 12, "w": 16}
    assert (cfg.workspace_dir / "scout_copy.aseprite").is_file()


@pytest.mark.asyncio
async def test_e2e_inspect_sprite_qa_strip_and_motion_check(live_server) -> None:
    srv, _ = live_server
    code = """
with ase.batch("walker.aseprite", create_size=(16, 16)) as b:
    b.add_layer("body")
    b.add_frame(count=3, duration_ms=100, empty=True)
    b.draw_rect("body", 1, 6, 8, 4, 6, "#6DAA2C", filled=True)
    b.draw_rect("body", 2, 6, 7, 4, 7, "#6DAA2C", filled=True)
    b.draw_rect("body", 3, 6, 8, 4, 6, "#6DAA2C", filled=True)
    b.draw_rect("body", 4, 6, 8, 4, 5, "#DEEED6", filled=True)  # intentional lift + color drift
    b.set_tag("walk", 1, 4, direction="forward")
"""
    exec_res = await srv.call_tool("execute_code", {"code": code})
    assert json.loads(exec_res[0].text)["status"] == "ok"

    insp_res = await srv.call_tool(
        "inspect_sprite",
        {
            "filename": "walker.aseprite",
            "tag": "walk",
            "background": "#7A746C",
            "include_qa_strip": True,
            "check_motion": True,
        },
    )
    assert len(insp_res) == 2
    report = json.loads(insp_res[0].text)
    assert report["status"] == "ok", report
    assert report["inspected_frames"] == [1, 2, 3, 4]
    assert report["per_frame"][0]["palette_drift_colors"] == []
    assert report["per_frame"][3]["palette_drift_colors"] == ["#DEEED6"]
    assert report["motion"]["ground_line"]["bottom_y_by_frame"] == {
        "1": 13,
        "2": 13,
        "3": 13,
        "4": 12,
    }
    assert report["motion"]["ground_line"]["variance_px"] == 1
    assert report["motion"]["seam_diff"]["from"] == 4
    assert report["motion"]["seam_diff"]["to"] == 1
    assert base64.b64decode(insp_res[1].data).startswith(b"\x89PNG\r\n\x1a\n")


@pytest.mark.asyncio
async def test_e2e_warm_worker_real_aseprite(
    tmp_workspace: Path,
    upstream_repo_path: Path,
    real_aseprite_path: str,
) -> None:
    cfg = RuntimeConfig(
        workspace_dir=tmp_workspace,
        aseprite_path=real_aseprite_path,
        upstream_repo_path=upstream_repo_path,
        warm_worker=True,
    )
    srv = create_server(cfg)

    r1 = await srv.call_tool(
        "execute_code",
        {
            "code": 'secret_marker = 123\nwith ase.batch("warm.aseprite", create_size=(8, 8)) as b:\n    b.add_layer("a")\n    b.draw_rect("a", 1, 2, 2, 4, 4, "#6DAA2C")',
            "auto_view": "warm.aseprite",
        },
    )
    assert json.loads(r1[0].text)["status"] == "ok"
    assert r1[1].type == "image"

    r2 = await srv.call_tool(
        "execute_code",
        {
            "code": (
                "try:\n"
                "    _ = secret_marker\n"
                "    leaked = True\n"
                "except NameError:\n"
                "    leaked = False\n"
                'result = {"leaked": leaked, "files": ase.list_files()}'
            ),
        },
    )
    s2 = json.loads(r2[0].text)
    assert s2["status"] == "ok"
    assert s2["return_value"]["leaked"] is False
    assert "warm.aseprite" in s2["return_value"]["files"]
