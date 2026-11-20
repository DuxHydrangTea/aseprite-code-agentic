"""CLI entrypoint for running the aseprite-codemode MCP server over stdio."""

from __future__ import annotations

from .server import mcp


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
