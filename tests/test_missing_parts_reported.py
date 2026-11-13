"""A part the library lacks is named beside the picture, not dropped in silence.

Audit 2026-11-10 (L-M2): a brick plus a misspelled `3001x_nope.dat` rendered
the same picture as the brick alone, with no error and no warning. ImportLDraw
logs "WARNING: Missing file 3001x_nope.dat" and skips the line; nothing read
that log. `bill_of_materials` lists the same part under `parts_not_in_library`,
so the two tools told the caller different things about one model.

The list now comes from inside Blender: the importer's own file lookup is
watched, and every name it could not find is reported back. That is the
importer's decision to drop the file, so the warning cannot disagree with the
picture. A partial picture is still a picture: it comes back with a text
block beside it, not as an error.
"""

import asyncio
import json
import subprocess
from pathlib import Path

import pytest
from mcp.client import Client

from ldraw_mcp import render as ldraw_render
from ldraw_mcp.server import mcp

DATA = Path(__file__).parent / "data"
GOOD = DATA / "good_render.png"

BRICK = "1 4 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat\n"
MISSPELLED = "1 1 0 -24 0 1 0 0 0 1 0 0 0 1 3001x_nope.dat\n"


async def _acall(name, args):
    async with Client(mcp) as client:
        return await client.call_tool(name, args)


def _call(name, args):
    return asyncio.run(_acall(name, args))


def _blocks(result):
    return [b.type for b in result.content]


def _text(result):
    return "".join(b.text for b in result.content if b.type == "text")


@pytest.mark.skipif(
    not ldraw_render.is_available(), reason="blender/LDraw stack not installed"
)
def test_a_missing_part_is_named_beside_the_picture_and_a_whole_model_is_not():
    """Real Blender through the MCP boundary, both arms in one test."""
    args = {"azimuths": "-60", "resolution": 64, "samples": 2}

    partial = _call("render_ldraw_text", {"ldr": "0 m\n" + BRICK + MISSPELLED, **args})
    assert not partial.is_error, _text(partial)
    assert _blocks(partial) == ["image", "text"], _blocks(partial)
    assert "3001x_nope.dat" in _text(partial)
    assert "missing from this picture" in _text(partial)
    # The real part is not named as missing.
    assert "3001.dat" not in _text(partial).replace("3001x_nope.dat", "")

    # Positive control: every part present, so the image comes alone.
    whole = _call("render_ldraw_text", {"ldr": "0 m\n" + BRICK, **args})
    assert not whole.is_error, _text(whole)
    assert _blocks(whole) == ["image"], _text(whole)


def _fake_blender(monkeypatch, tmp_path, stdout):
    """Stand in for blender: write good views, print `stdout`."""
    monkeypatch.setattr(ldraw_render, "find_blender", lambda: "/fake/blender")
    monkeypatch.setattr(ldraw_render, "ldraw_library_dir", lambda: tmp_path)

    def fake_run(cmd, **kwargs):
        prefix = cmd[cmd.index("--out") + 1]
        Path(f"{prefix}_0.png").write_bytes(GOOD.read_bytes())
        return subprocess.CompletedProcess(cmd, 0, stdout, "")

    monkeypatch.setattr(ldraw_render.subprocess, "run", fake_run)


def _report(**fields):
    return ldraw_render.REPORT_PREFIX + json.dumps(fields) + "\n"


def test_the_report_is_read_from_blender_and_its_absence_is_an_error(
    monkeypatch, tmp_path
):
    """Without Blender: render.py reads the names the script reports, and a
    render whose script said nothing fails rather than reading as complete."""
    model = tmp_path / "m.ldr"
    model.write_text(BRICK)
    out = tmp_path / "out.png"

    _fake_blender(
        monkeypatch, tmp_path, "noise\n" + _report(missing=["a b.dat", "s\\x.dat"])
    )
    result = ldraw_render.render_ldraw_report(str(model), str(out), azimuths=(0,))
    assert result.png == str(out) and out.exists()
    assert result.missing_files == ("a b.dat", "s\\x.dat")

    _fake_blender(monkeypatch, tmp_path, _report(missing=[]))
    assert (
        ldraw_render.render_ldraw_report(
            str(model), str(out), azimuths=(0,)
        ).missing_files
        == ()
    )

    _fake_blender(monkeypatch, tmp_path, "RENDERED view_0.png\n")
    with pytest.raises(ldraw_render.LDrawRenderError, match="did not report"):
        ldraw_render.render_ldraw_report(str(model), str(out), azimuths=(0,))


def test_the_script_prints_the_prefix_render_py_reads():
    """The two halves of the protocol live in two processes; one string."""
    assert ldraw_render.REPORT_PREFIX in ldraw_render.BLENDER_SCRIPT.read_text()
