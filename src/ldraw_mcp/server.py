"""
ldraw-mcp server — render and query LDraw models over MCP.

Tools:
  render_ldraw_file(path, ...)  -> PNG image of the model
  render_ldraw_text(ldr, ...)   -> PNG image of inline LDraw content
  check_renderer()              -> availability diagnostics
  bill_of_materials(path | ldr) -> part x colour x quantity rows
  lookup_color(query)           -> colour code <-> name / RGB (LDConfig.ldr)
  search_parts(query, limit)    -> parts by description substring

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
from functools import wraps
from pathlib import Path
from typing import Any

# mcp 2.x renamed FastMCP to MCPServer and removed mcp.server.fastmcp entirely.
# Same class, same decorator, same kwargs — a rename, not a rewrite. Image moved
# with it.
from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from . import bom as ldraw_bom
from . import render as ldraw_render

mcp = MCPServer("ldraw")

#: The exceptions a caller can do something about. Every one of them carries an
#: instruction rather than a diagnosis - install Blender, run the setup, fix the
#: path, fix the azimuth - which is why they have to arrive intact.
#: `LDrawRenderError` is the render stack refusing; `ValueError` is an argument
#: out of range or `float()` on an azimuth that is not a number.
#: `LDrawModelError` is the query tools refusing a model or a library: a
#: reference cycle, a malformed line, a library that is not installed.
#:
#: `OSError` is the whole family, not `FileNotFoundError` alone. Every OSError
#: these tools can raise is the filesystem answering about a path the caller
#: or the caller's configuration named: a model that is not there, is not
#: readable, is a directory, has a name the filesystem rejects; a
#: `LDRAW_MCP_BLENDER` that cannot be executed (#41). Listing members one at a
#: time is how PermissionError and IsADirectoryError stayed masked after
#: FileNotFoundError was added. A bug is a TypeError, an AttributeError, a
#: KeyError - none of them here, so they stay masked, which is what masking is
#: for.
_REFUSALS = (
    ldraw_render.LDrawRenderError,
    ldraw_bom.LDrawModelError,
    OSError,
    ValueError,
)


def _surfaces_refusals(tool):
    """Re-raise an anticipated refusal as ToolError so its text reaches the model.

    mcp 2.1 treats any exception out of a tool that is not a ToolError as a
    crash: it answers `Error executing tool <name>` and leaves the message in
    the server log, where the model that called the tool cannot read it. Under
    2.0 the text went through whatever the type was, which is why these tools
    could raise ordinary exceptions and nothing here had to say so. The pin is
    `mcp>=2,<3` with no lockfile, so a fresh install has been resolving 2.1
    and masking all of it.

    `render` keeps raising `LDrawRenderError` - it is usable without MCP, and
    `ldraw-mcp-setup` imports it - so the conversion happens here, once, at the
    boundary where MCP starts.
    """

    @wraps(tool)
    def surfaced(*args, **kwargs):
        try:
            return tool(*args, **kwargs)
        except _REFUSALS as exc:
            raise ToolError(str(exc)) from exc

    return surfaced


@mcp.tool()
@_surfaces_refusals
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


def _existing_path(path: str) -> Path:
    """The caller's model path, expanded, or a refusal that names it."""
    # expanduser raises RuntimeError for an unknown ~user, and exists() raises
    # OSError for a name the filesystem rejects (e.g. ENAMETOOLONG). Both are the
    # caller's path being unusable, so they refuse like a missing file does.
    try:
        p = Path(path).expanduser()
        exists = p.exists()
    except (RuntimeError, OSError) as exc:
        raise ValueError(
            f"unusable LDraw path {path[:200]!r}: {getattr(exc, 'strerror', None) or exc}"
        ) from exc
    if not exists:
        raise FileNotFoundError(f"no such LDraw file: {p}")
    return p


def _render(ldr_path: str, azimuths: str, resolution: int, samples: int) -> Image:
    azims = [float(a) for a in azimuths.split(",")]
    with tempfile.TemporaryDirectory() as td:
        out = str(Path(td) / "render.png")
        ldraw_render.render_ldraw(
            ldr_path, out, azimuths=azims, resolution=resolution, samples=samples
        )
        return Image(data=Path(out).read_bytes(), format="png")


@mcp.tool()
@_surfaces_refusals
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
    return _render(str(_existing_path(path)), azimuths, resolution, samples)


@mcp.tool()
@_surfaces_refusals
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


@mcp.tool()
@_surfaces_refusals
def bill_of_materials(path: str = "", ldr: str = "") -> dict[str, Any]:
    """Bill of materials for an LDraw model: part x colour x quantity rows.

    Give `path` (a .ldr/.mpd file) or `ldr` (inline LDraw text), not both. MPD
    sub-models are expanded with their multiplicity, and colour 16 inherits the
    colour of the line that referenced the sub-model. Part descriptions and
    colour names come from the LDraw library when one is installed; without it
    the counts are the same and those fields are null. A colour code that
    LDConfig.ldr does not define is listed in `unknown_color_codes`, and a part
    the library lacks in `parts_not_in_library`.
    """
    if bool(path) == bool(ldr):
        raise ValueError("give exactly one of `path` and `ldr`")
    library = ldraw_render.ldraw_library_dir()
    if path:
        p = _existing_path(path)
        if not p.is_file():
            raise ValueError(f"not a file: {p}")
        return ldraw_bom.bill_of_materials(path=p, library=library)
    return ldraw_bom.bill_of_materials(text=ldr, library=library)


@mcp.tool()
@_surfaces_refusals
def lookup_color(query: str) -> dict[str, Any]:
    """Look up LDraw colours in the library's LDConfig.ldr.

    `query` is a colour code (`4`, or a direct colour `0x2FF8800`) or part of a
    name (`dark blue`, `Trans_Red`). Returns code, name, RGB, edge colour, alpha
    and finish for each match; an unknown code returns no matches.
    """
    return ldraw_bom.lookup_colour(query, ldraw_render.ldraw_library_dir())


@mcp.tool()
@_surfaces_refusals
def search_parts(query: str, limit: int = 50) -> dict[str, Any]:
    """Search the LDraw library's parts by description or file-name substring.

    Case-insensitive, runs of spaces collapsed (`brick 2 x 4` matches the
    header `Brick  2 x  4`). Returns at most `limit` parts (max 200) plus the
    total number of matches.
    """
    return ldraw_bom.search_parts(query, ldraw_render.ldraw_library_dir(), limit)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
