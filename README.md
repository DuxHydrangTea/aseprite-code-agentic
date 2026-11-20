# `aseprite-codemode`

Agentic Code Execution (**Code Mode**) MCP server wrapping [`diivi/aseprite-mcp`](https://github.com/diivi/aseprite-mcp).

Instead of loading **123 separate MCP tool schemas** into the LLM context window and paying one LLM round-trip per pixel/layer/frame call, `aseprite-codemode` exposes **4 MCP tools** and lets the AI write concise Python scripts against a pre-loaded synchronous `ase` SDK inside a workspace-confined subprocess sandbox.

See [docs/PLAN_AND_SPEC.md](docs/PLAN_AND_SPEC.md) for the full architecture, tool specifications, caveats, and test matrix.

---

## Exposed MCP Tools (Strictly 4)

1. `search_tools(query, category, detail, limit)` — Progressive discovery across all 123 upstream tools in 21 categories (default `limit=12`, omits `categories` summary when filtered).
2. `execute_code(code, timeout_seconds, auto_view, ...)` — Execute Python code in an isolated subprocess (cold or optional `ASEPRITE_WARM_WORKER=1`) with a closure-confined `ase` SDK (`ase.Grid`, `ase.batch`, `ase.<cat>.<tool>`, `ase.copy`, `ase.read_grid`, `ase.show`, `ase.path`, `ase.list_files`), strict upstream error checking (`ase.strict = True` / `tool_warnings`), process-tree timeout enforcement, and optional automatic PNG visual feedback.
3. `inspect_sprite(filename, frames, layer_name, region, scale, include_lint, include_colors, tag, background, include_qa_strip, check_motion)` — Single-pass visual render + QA strip (`1x native`, `grayscale (value)`, `silhouette`, optional `seam` onion-skin) + multi-frame bounds, palette drift, lint, and motion ground-line/diff metrics.
4. `get_guide(topic)` — On-demand guides (`overview`, `pixel_art`, `animation`, `idle`, `environment`, `batch_lua`, `security`).

---

## Setup

### 1. Clone upstream `diivi/aseprite-mcp` (if not already present)

```powershell
git clone --depth 1 https://github.com/diivi/aseprite-mcp.git vendor/aseprite-mcp
```

### 2. Install dependencies with `uv`

```powershell
uv sync --dev
```

*(Note: `pyproject.toml` pins `mcp[cli]>=1.6.0,<2` because upstream `diivi/aseprite-mcp` imports `mcp.server.fastmcp` from MCP v1.x.)*

### 3. Configure `.env`

Copy `.env.example` to `.env` and set `ASEPRITE_PATH` to your local `aseprite.exe`:

```env
ASEPRITE_PATH=C:\projects\aseprite\build\bin\aseprite.exe
ASEPRITE_WORKSPACE=C:\projects\aseprite-codemode\workspace
ASEPRITE_TIMEOUT_SECONDS=60
ASEPRITE_CODE_TIMEOUT_SECONDS=60
ASEPRITE_ALLOW_RAW_LUA=0
ASEPRITE_ALLOW_PREVIEW_SERVER=0
ASEPRITE_WARM_WORKER=0
```

---

## MCP Client Configuration (`.mcp.json`)

```json
{
  "mcpServers": {
    "aseprite-codemode": {
      "command": "uv",
      "args": [
        "--directory",
        "C:/projects/aseprite-codemode",
        "run",
        "aseprite-codemode"
      ],
      "env": {
        "ASEPRITE_PATH": "C:/projects/aseprite/build/bin/aseprite.exe",
        "ASEPRITE_WORKSPACE": "C:/projects/aseprite-codemode/workspace"
      }
    }
  }
}
```

---

## Usage Example (inside `execute_code`)

```python
with ase.batch("slime.aseprite", create_size=(16, 16)) as b:
    b.set_palette(["#140C1C", "#346524", "#6DAA2C", "#DEEED6"])
    b.add_layer("body")
    b.draw_rows(
        "body", 1, 4, 6,
        [
            "..GGGG..",
            ".GLLLLG.",
            "GGGGGGGG",
            "GKGGGGKG",
            "GGGGGGGG",
            ".DDDDDD.",
        ],
        {"G": "#6DAA2C", "L": "#DEEED6", "D": "#346524", "K": "#140C1C"},
    )

result = {"created": True, "files": ase.list_files()}
```

Pass `auto_view="slime.aseprite"` to `execute_code` to receive the rendered PNG preview directly in the MCP response.

## Pixel art skill for Codex

With this MCP server connected, use `$aseprite-pixel-art` to draw a new asset, revise a copy of an existing sprite, or ask for a read-only critique. Include the asset's intended size, viewpoint and display context when those details are known.

The [skill](.agents/skills/aseprite-pixel-art/SKILL.md) includes a 32×32 courier with idle and walk tags, annotated pixel cleanup examples, and guidance for checking color, silhouettes and timing. See the [evaluation](docs/PIXEL_ART_SKILL_EVAL.md) for the paired trials.

---

## Running Tests

```powershell
# Unit tests (no Aseprite binary required)
uv run pytest tests/unit -v

# Integration tests against real aseprite.exe
$env:ASEPRITE_PATH="C:\projects\aseprite\build\bin\aseprite.exe"
uv run pytest tests/integration -v

# Full test suite
uv run pytest -v
```
