"""Unit tests for the 4-tool FastMCP server interface."""

from __future__ import annotations

import json
import os
from dataclasses import replace

import pytest

from aseprite_codemode.config import RuntimeConfig
from aseprite_codemode.server import SERVER_INSTRUCTIONS, create_server


def _blocks(call_result):
    return call_result[0] if isinstance(call_result, tuple) else call_result


@pytest.mark.asyncio
async def test_server_exposes_strictly_four_tools_and_cheatsheet(test_config: RuntimeConfig) -> None:
    srv = create_server(test_config)
    tools = await srv.list_tools()
    names = {t.name for t in tools}
    assert names == {"search_tools", "execute_code", "inspect_sprite", "get_guide"}
    assert len(tools) == 4
    assert "Mini Cheat-Sheet" in SERVER_INSTRUCTIONS
    assert "ase.Grid" in SERVER_INSTRUCTIONS


def test_create_server_does_not_mutate_global_env(test_config: RuntimeConfig, monkeypatch) -> None:
    monkeypatch.setenv("ASEPRITE_PATH", "C:/sentinel/aseprite.exe")
    cfg = replace(test_config, aseprite_path="C:/different/aseprite.exe")
    _ = create_server(cfg)
    assert os.environ["ASEPRITE_PATH"] == "C:/sentinel/aseprite.exe"


@pytest.mark.asyncio
async def test_server_search_and_guides(test_config: RuntimeConfig) -> None:
    srv = create_server(test_config)

    search_res = _blocks(
        await srv.call_tool(
            "search_tools",
            {"query": "draw rows", "category": "drawing", "detail": "signature", "limit": 5},
        )
    )
    payload = json.loads(search_res[0].text)
    assert payload["total_tools"] == 123
    assert payload["matched"] >= 1
    assert "categories" not in payload
    assert payload["tools"][0]["sdk_call"] == "ase.drawing.draw_rows"

    for topic in ("overview", "pixel_art", "animation", "idle", "environment", "batch_lua", "security"):
        guide_res = _blocks(await srv.call_tool("get_guide", {"topic": topic}))
        assert len(guide_res[0].text) > 50

    unknown_res = _blocks(await srv.call_tool("get_guide", {"topic": "unknown_xyz"}))
    assert "Unknown guide topic" in unknown_res[0].text


@pytest.mark.asyncio
async def test_server_execute_code_and_inspect_guards(
    test_config: RuntimeConfig,
    monkeypatch,
) -> None:
    srv = create_server(test_config)

    ok_res = _blocks(await srv.call_tool("execute_code", {"code": "result = 6 * 7"}))
    ok_data = json.loads(ok_res[0].text)
    assert ok_data["status"] == "ok"
    assert ok_data["return_value"] == 42

    blocked_res = _blocks(await srv.call_tool("execute_code", {"code": "import subprocess"}))
    blocked_data = json.loads(blocked_res[0].text)
    assert blocked_data["status"] == "security_error"

    trav_res = _blocks(await srv.call_tool("inspect_sprite", {"filename": "../outside.aseprite"}))
    trav_data = json.loads(trav_res[0].text)
    assert trav_data["status"] == "security_error"

    missing_res = _blocks(await srv.call_tool("inspect_sprite", {"filename": "missing.aseprite"}))
    missing_data = json.loads(missing_res[0].text)
    assert missing_data["status"] == "error"
    assert "not found" in missing_data["error"].lower()

    sample = test_config.workspace_dir / "hero.aseprite"
    sample.write_bytes(b"stub")

    def fake_inspect(aseprite_path, safe_file, rel_name, **kwargs):
        return (
            {
                "status": "ok",
                "filename": rel_name,
                "analyzed_frame_index": 2,
                "background": kwargs["background"],
                "check_motion": kwargs["check_motion"],
            },
            b"\x89PNG\r\n\x1a\n",
        )

    monkeypatch.setattr("aseprite_codemode.server.inspect_sprite_file", fake_inspect)
    insp_res = _blocks(
        await srv.call_tool(
            "inspect_sprite",
            {
                "filename": "hero.aseprite",
                "tag": "walk",
                "background": "#7A746C",
                "check_motion": True,
            },
        )
    )
    insp_data = json.loads(insp_res[0].text)
    assert insp_data["status"] == "ok"
    assert insp_data["analyzed_frame_index"] == 2
    assert insp_data["background"] == "#7A746C"
    assert insp_data["check_motion"] is True
    assert insp_res[1].type == "image"
