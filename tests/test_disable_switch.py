"""LDRAW_MCP_DISABLE=1 turns rendering off for the tools, not only for the report.

Audit 2026-11-10 (L-L): with the switch set, `check_renderer` said
`available: False` while a render call still started Blender and returned a
picture. The switch now stops `render_ldraw` itself, before Blender starts.
"""

import asyncio
import sys
from pathlib import Path

from mcp.client import Client

from ldraw_mcp import render as ldraw_render
from ldraw_mcp.server import mcp

FIXTURE_LIBRARY = Path(__file__).resolve().parent / "data" / "ldraw"
BRICK = "1 4 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat\n"


async def _acall(name, args):
    async with Client(mcp) as client:
        return await client.call_tool(name, args)


def _text(result):
    return "".join(b.text for b in result.content if b.type == "text")


def test_the_disable_switch_refuses_renders_before_blender(monkeypatch, tmp_path):
    monkeypatch.setenv("LDRAW_LIBRARY_PATH", str(FIXTURE_LIBRARY))
    monkeypatch.setenv("LDRAW_MCP_BLENDER", sys.executable)
    started = []

    def spy(cmd, **kwargs):
        started.append(cmd)
        raise ldraw_render.subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(ldraw_render.subprocess, "run", spy)
    model = tmp_path / "m.ldr"
    model.write_text(BRICK)
    args = {"azimuths": "0", "resolution": 64, "samples": 1}

    monkeypatch.setenv("LDRAW_MCP_DISABLE", "1")
    report = _text(asyncio.run(_acall("check_renderer", {})))
    assert "available: False" in report
    for tool, source in (
        ("render_ldraw_text", {"ldr": BRICK}),
        ("render_ldraw_file", {"path": str(model)}),
    ):
        refused = asyncio.run(_acall(tool, {**source, **args}))
        assert refused.is_error, tool
        assert "LDRAW_MCP_DISABLE" in _text(refused), _text(refused)
    assert started == [], "disabled, so Blender must never have been started"

    # Positive control: any other value leaves rendering on, and the same
    # call reaches Blender (the spy's timeout is what comes back).
    monkeypatch.setenv("LDRAW_MCP_DISABLE", "0")
    assert "available: True" in _text(asyncio.run(_acall("check_renderer", {})))
    reached = asyncio.run(_acall("render_ldraw_text", {"ldr": BRICK, **args}))
    assert "timed out" in _text(reached)
    assert len(started) == 1
