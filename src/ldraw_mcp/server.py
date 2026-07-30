"""
ldraw-mcp server — render LDraw models to images over MCP.

Tools:
  render_ldraw_file(path, ...)  -> PNG image of the model
  render_ldraw_text(ldr, ...)   -> PNG image of inline LDraw content
  check_renderer()              -> availability diagnostics

Run (stdio):
  ldraw-mcp
  python -m ldraw_mcp.server

Register with Claude Code:
  claude mcp add ldraw -- ldraw-mcp

Requirements (see README.md):
  - Blender on PATH (or LDRAW_MCP_BLENDER)
  - ImportLDraw addon installed in Blender
  - LDraw parts library at ~/.ldraw (or LDRAW_LIBRARY_PATH)
  Run `ldraw-mcp-setup` to install the library + addon.
"""

import tempfile
from pathlib import Path

# mcp 2.x renamed FastMCP to MCPServer and removed mcp.server.fastmcp entirely.
# Same class, same decorator, same kwargs — a rename, not a rewrite. Image moved
# with it.
from mcp.server.mcpserver import Image, MCPServer

from . import render as ldraw_render

mcp = MCPServer("ldraw")


@mcp.tool()
def check_renderer() -> str:
    """Report whether the LDraw rendering stack is available and why not."""
    blender = ldraw_render.find_blender()
    library = ldraw_render.ldraw_library_dir()
    lines = [
        f"blender: {blender or 'NOT FOUND (install Blender or set LDRAW_MCP_BLENDER)'}",
        f"ldraw library: {library or 'NOT FOUND (run ldraw-mcp-setup or set LDRAW_LIBRARY_PATH)'}",
        f"available: {ldraw_render.is_available()}",
    ]
    return "\n".join(lines)


def _render(ldr_path: str, azimuths: str, resolution: int, samples: int) -> Image:
    azims = [float(a) for a in azimuths.split(",")]
    with tempfile.TemporaryDirectory() as td:
        out = str(Path(td) / "render.png")
        ldraw_render.render_ldraw(
            ldr_path, out, azimuths=azims, resolution=resolution, samples=samples
        )
        return Image(data=Path(out).read_bytes(), format="png")


@mcp.tool()
def render_ldraw_file(
    path: str,
    azimuths: str = "-60,120",
    resolution: int = 640,
    samples: int = 24,
) -> Image:
    """Render an LDraw model file (.ldr/.mpd/.dat) to a PNG image.

    Views are rendered at each comma-separated azimuth (degrees) and
    stitched side by side. Higher samples = cleaner but slower.
    """
    p = Path(path).expanduser()
    if not p.exists():
        raise FileNotFoundError(f"no such LDraw file: {p}")
    return _render(str(p), azimuths, resolution, samples)


@mcp.tool()
def render_ldraw_text(
    ldr: str,
    azimuths: str = "-60,120",
    resolution: int = 640,
    samples: int = 24,
) -> Image:
    """Render inline LDraw content (the text of a .ldr file) to a PNG.

    Useful for quick experiments without writing a file first.
    """
    with tempfile.NamedTemporaryFile("w", suffix=".ldr", delete=False) as f:
        f.write(ldr)
        tmp = f.name
    try:
        return _render(tmp, azimuths, resolution, samples)
    finally:
        Path(tmp).unlink(missing_ok=True)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
