"""Unit tests for AsepriteSDK, closure proxies, PreviewCollector, and LuaBatchBuilder."""

from __future__ import annotations

import importlib
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from mcp.server.fastmcp import Image as FastMCPImage

from aseprite_codemode.catalog import ToolCatalog
from aseprite_codemode.config import RuntimeConfig
from aseprite_codemode.sdk import (
    AsepriteSDK,
    AsepriteToolError,
    PreviewCollector,
    create_sandbox_sdk,
)
from aseprite_codemode.security import PolicyBlockedToolError, WorkspaceViolationError


def _catalog_with_override(base_catalog: ToolCatalog, tool_name: str, fake_fn) -> ToolCatalog:
    """Create an isolated ToolCatalog replacing one tool's callable without mutating fixtures."""
    new_specs = [
        replace(spec, callable_ref=fake_fn) if spec.name == tool_name else spec
        for spec in base_catalog.specs
    ]
    return ToolCatalog(new_specs)


def test_sdk_path_rewriting_defaults_and_escape_rejection(
    test_config: RuntimeConfig,
    upstream_pkg,
    catalog: ToolCatalog,
) -> None:
    called: dict[str, object] = {}

    async def fake_copy_layers(source_filename: str, target_filename: str, layer_names: list[str]) -> str:
        called["source"] = source_filename
        called["target"] = target_filename
        called["layers"] = layer_names
        return "ok"

    local_catalog = _catalog_with_override(catalog, "copy_layers_between_sprites", fake_copy_layers)
    sdk = AsepriteSDK(config=test_config, upstream_pkg=upstream_pkg, catalog=local_catalog)

    out = sdk.scene.copy_layers_between_sprites("a.aseprite", "sub/b.aseprite", ["body"])
    assert out == "ok"
    assert Path(str(called["source"])) == (test_config.workspace_dir / "a.aseprite").resolve()
    assert Path(str(called["target"])) == (test_config.workspace_dir / "sub" / "b.aseprite").resolve()

    called.clear()
    with pytest.raises(WorkspaceViolationError):
        sdk.scene.copy_layers_between_sprites("../escape.aseprite", "b.aseprite", ["body"])
    assert called == {}

    seen_default: list[str] = []

    async def fake_create_canvas(width: int, height: int, filename: str = "canvas.aseprite") -> str:
        seen_default.append(filename)
        return "Canvas created successfully"

    default_catalog = _catalog_with_override(catalog, "create_canvas", fake_create_canvas)
    sdk_def = AsepriteSDK(config=test_config, upstream_pkg=upstream_pkg, catalog=default_catalog)
    sdk_def.canvas.create_canvas(16, 16)
    assert Path(seen_default[0]) == (test_config.workspace_dir / "canvas.aseprite").resolve()


@pytest.mark.asyncio
async def test_sdk_sync_bridge_works_inside_running_event_loop(
    test_config: RuntimeConfig,
    upstream_pkg,
    catalog: ToolCatalog,
) -> None:
    async def fake_info(filename: str) -> str:
        return f"info:{Path(filename).name}"

    local_catalog = _catalog_with_override(catalog, "get_sprite_info", fake_info)
    sdk = AsepriteSDK(config=test_config, upstream_pkg=upstream_pkg, catalog=local_catalog)
    assert sdk.animation.get_sprite_info("hero.aseprite") == "info:hero.aseprite"


def test_sdk_conditional_font_parameter(
    test_config: RuntimeConfig,
    upstream_pkg,
    catalog: ToolCatalog,
) -> None:
    seen: list[str] = []

    async def fake_measure_text(
        text: str,
        font: str,
        size: int = 1,
        letter_spacing: int = 0,
        bold: int = 0,
        antialias: bool = False,
    ) -> str:
        seen.append(font)
        return "10x8"

    local_catalog = _catalog_with_override(catalog, "measure_text", fake_measure_text)
    sdk = AsepriteSDK(config=test_config, upstream_pkg=upstream_pkg, catalog=local_catalog)
    assert sdk.text.measure_text("HP", "Arial") == "10x8"
    assert seen == ["Arial"]

    with pytest.raises(WorkspaceViolationError):
        sdk.text.measure_text("HP", "../secret.ttf")


def test_sdk_policy_gates_preview_server_and_raw_lua(
    test_config: RuntimeConfig,
    upstream_pkg,
    catalog: ToolCatalog,
) -> None:
    sdk_locked = AsepriteSDK(config=test_config, upstream_pkg=upstream_pkg, catalog=catalog)

    with pytest.raises(PolicyBlockedToolError):
        sdk_locked.preview.start_preview_server(".")

    with pytest.raises(PolicyBlockedToolError):
        sdk_locked.script.run_lua_script("print('hi')")

    with pytest.raises(PolicyBlockedToolError):
        sdk_locked.run_lua("print('hi')")

    async def fake_run_lua(script: str, filename: str = "") -> str:
        return f"ran:{len(script)}:{filename}"

    local_catalog = _catalog_with_override(catalog, "run_lua_script", fake_run_lua)
    cfg_unlocked = replace(test_config, allow_raw_lua=True)
    sdk_unlocked = AsepriteSDK(config=cfg_unlocked, upstream_pkg=upstream_pkg, catalog=local_catalog)
    assert sdk_unlocked.run_lua("print(1)") == "ran:8:"


def test_sdk_strict_error_mode_and_warning_mode(
    test_config: RuntimeConfig,
    upstream_pkg,
    catalog: ToolCatalog,
) -> None:
    async def fake_add_layer(filename: str, layer_name: str, group: str = "") -> str:
        return f"File {filename} not found"

    local_catalog = _catalog_with_override(catalog, "add_layer", fake_add_layer)
    sdk = AsepriteSDK(config=test_config, upstream_pkg=upstream_pkg, catalog=local_catalog, strict=True)

    with pytest.raises(AsepriteToolError, match="add_layer failed"):
        sdk.canvas.add_layer("missing.aseprite", "body")

    sdk.strict = False
    out = sdk.canvas.add_layer("missing.aseprite", "body")
    assert "not found" in out
    assert len(sdk.tool_warnings) == 1
    assert "ase.canvas.add_layer" in sdk.tool_warnings[0]


def test_sandbox_sdk_proxy_hides_private_internals(
    test_config: RuntimeConfig,
    upstream_pkg,
) -> None:
    sdk = AsepriteSDK(config=test_config, upstream_pkg=upstream_pkg, catalog=None)
    proxy = create_sandbox_sdk(sdk)

    with pytest.raises(AttributeError):
        _ = proxy._upstream_pkg
    with pytest.raises(AttributeError):
        _ = proxy._guard
    with pytest.raises(AttributeError):
        proxy.script._allow_raw_lua = True
    with pytest.raises(AttributeError):
        _ = proxy.canvas._specs_by_name
    with pytest.raises(AttributeError):
        proxy.workspace_dir = "C:/evil"

    proxy.strict = False
    assert sdk.strict is False
    assert isinstance(proxy.workspace_dir, str)
    assert proxy.Grid(4, 4).width == 4

    b_proxy = proxy.batch("x.aseprite", create_size=(16, 16))
    with pytest.raises(AttributeError):
        _ = b_proxy._ops
    with pytest.raises(AttributeError):
        b_proxy._allow_raw_lua = True
    chained = b_proxy.add_layer("body").set_layer_props("body", opacity=180, stack_index=1)
    assert chained is b_proxy


def test_sdk_typeerror_includes_expected_signature_and_copy_read_grid(
    test_config: RuntimeConfig,
    upstream_pkg,
    catalog: ToolCatalog,
) -> None:
    async def fake_ramp(base_color: str, steps: int = 5, hue_shift_degrees: float = 20) -> str:
        return "[]"

    async def fake_info(filename: str) -> str:
        return '{"width": 2, "height": 2}'

    async def fake_read_rows(
        filename: str,
        x: int,
        y: int,
        width: int,
        height: int,
        frame_index: int = 1,
        layer_name: str = "",
    ) -> str:
        return '{"x": 0, "y": 0, "width": 2, "height": 2, "rows": ["AA", ".A"], "legend": {"A": "#112233"}}'

    specs = []
    for s in catalog.specs:
        if s.name == "generate_color_ramp":
            specs.append(replace(s, callable_ref=fake_ramp))
        elif s.name == "get_sprite_info":
            specs.append(replace(s, callable_ref=fake_info))
        elif s.name == "read_rows":
            specs.append(replace(s, callable_ref=fake_read_rows))
        else:
            specs.append(s)

    sdk = AsepriteSDK(config=test_config, upstream_pkg=upstream_pkg, catalog=ToolCatalog(specs))
    with pytest.raises(TypeError, match="Expected signature: ase.palette.generate_color_ramp"):
        sdk.palette.generate_color_ramp("#346524", hue_shift=10)

    src = test_config.workspace_dir / "orig.aseprite"
    src.write_bytes(b"ase-bytes")
    copied_rel = sdk.copy("orig.aseprite", "sub/copy.aseprite")
    assert copied_rel == "sub/copy.aseprite"
    assert (test_config.workspace_dir / "sub" / "copy.aseprite").read_bytes() == b"ase-bytes"

    with pytest.raises(FileExistsError):
        sdk.copy("orig.aseprite", "sub/copy.aseprite", overwrite=False)
    with pytest.raises(WorkspaceViolationError):
        sdk.copy("orig.aseprite", "../evil.aseprite")

    rg = sdk.read_grid("orig.aseprite")
    assert rg["grid"].to_rows() == ["AA", ".A"]
    assert rg["legend"] == {"A": "#112233"}


def test_preview_collector_handles_images_limits_and_status(tmp_path: Path) -> None:
    collector = PreviewCollector(preview_dir=tmp_path / "previews", max_images=2)
    assert collector.last_status == "idle"

    img1 = FastMCPImage(data=b"\x89PNG\r\n\x1a\n1", format="png")
    res1 = collector.extract_from_result(["summary line", img1], source_tool="view_sprite", label="hero.aseprite")
    assert "summary line" in res1
    assert "Preview #1 saved" in res1
    assert collector.count == 1
    assert collector.last_status == "captured"
    assert (tmp_path / "previews" / "preview_1.png").read_bytes() == b"\x89PNG\r\n\x1a\n1"

    img2 = FastMCPImage(data=b"\x89PNG\r\n\x1a\n2", format="png")
    res2 = collector.extract_from_result(img2, source_tool="view_sprite", label="hero2.aseprite")
    assert "Preview #2 saved" in res2
    assert collector.count == 2
    assert collector.last_status == "captured"

    img3 = FastMCPImage(data=b"\x89PNG\r\n\x1a\n3", format="png")
    res3 = collector.extract_from_result(img3, source_tool="view_sprite", label="hero3.aseprite")
    assert "limit (2) reached" in res3
    assert collector.count == 2
    assert collector.last_status == "limit_reached"


def test_lua_batch_builder_generates_compact_lua_and_new_ops(
    test_config: RuntimeConfig,
    upstream_pkg,
    catalog: ToolCatalog,
    monkeypatch,
) -> None:
    sdk = AsepriteSDK(config=test_config, upstream_pkg=upstream_pkg, catalog=catalog)
    batch = sdk.batch("batched.aseprite", create_size=(16, 16))
    batch.set_palette(["#140C1C", "#6DAA2C"])
    batch.add_layer("body")
    batch.set_layer_props("body", opacity=200, visible=True, stack_index=1)
    batch.draw_rect("body", 1, 2, 2, 4, 4, "#6DAA2C", filled=True)
    batch.draw_ellipse("body", 1, 8, 8, 4, 3, "#6DAA2C", filled=True)
    batch.draw_line("body", 1, 1, 1, 14, 14, "#140C1C", thickness=1)
    batch.draw_rows("body", 1, 3, 3, ["KK", ".K"], {"K": "#140C1C"})
    batch.draw_pixels("body", 1, [(0, 0, "#140C1C"), (1, 0, "#140C1C")])
    batch.fill_area("body", 1, 0, 0, "#6DAA2C")
    batch.replace_color("body", 1, "#6DAA2C", "#140C1C")
    batch.outline("body", 1, "#140C1C", include_diagonals=False)
    batch.add_frame(count=1, duration_ms=100, empty=True)
    batch.copy_cel("body", 1, 2)
    batch.shift_cel("body", 2, 1, -1)
    batch.set_frame_duration(1, 160)
    batch.set_tag("idle", 1, 2, direction="pingpong")
    batch.clear_cel("body", 2)

    with pytest.raises(PolicyBlockedToolError):
        batch.add_raw_lua("print('blocked')")

    lua_src = batch.build_lua()
    assert "local function find_layer(" in lua_src
    assert "local function normalize_cel(" in lua_src
    assert "local function pset(" in lua_src
    assert "app.transaction(function()" in lua_src
    assert "lyr.opacity = 200" in lua_src
    assert "lyr.stackIndex = 1" in lua_src
    assert "local rows = {" in lua_src
    assert "for i = 1, #pts, 3 do" in lua_src
    assert "spr:newEmptyFrame()" in lua_src
    assert "new_img:drawImage(cel.image" in lua_src
    assert "spr:newTag(f1, f2)" in lua_src
    assert "AniDir.PING_PONG" in lua_src
    assert "img:clone()" in lua_src
    assert 'if batch_err then print("ERROR:" .. batch_err) return end' in lua_src
    assert "spr:saveAs(" in lua_src

    commands_mod = importlib.import_module(f"{upstream_pkg.__name__}.core.commands")
    mock_exec = MagicMock(return_value=(True, "OK\n"))
    monkeypatch.setattr(commands_mod.AsepriteCommand, "execute_lua_script_checked", mock_exec)

    res = batch.commit()
    assert res["ok"] is True
    assert res["operations"] == 17
    assert mock_exec.call_count == 1
