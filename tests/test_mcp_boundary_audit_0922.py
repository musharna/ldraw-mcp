"""Findings A-E of the 2026-09-22 MCP bug audit, each driven through a real client.

A  an OSError that is not FileNotFoundError (PermissionError, IsADirectoryError,
   a Blender path that cannot be exec'd - issue #41) was masked as
   `Error executing tool <name>`.
B  resolution / samples / azimuths reached Blender unchecked; Blender clamps a
   resolution of 3 to 4 and the tool reported success with a 4x4 image.
C  nothing bounded them from above, so a resolution of 10**6 ran into the
   600 s timeout instead of being refused.
D  render_ldraw_text leaked its temp .ldr when the write itself failed.
E  serverInfo.version was '' because the server was built without one.

Every negative sits beside a positive control in the same test, so a harness
that refuses everything cannot read as "fixed".
"""

import asyncio
import os
import re
import stat
import sys
import tempfile
from importlib.metadata import version
from pathlib import Path

import pytest
from mcp.client import Client

from ldraw_mcp import render as ldraw_render
from ldraw_mcp.server import _REFUSALS, mcp

FIXTURE_LIBRARY = Path(__file__).resolve().parent / "data" / "ldraw"
BRICK = "1 4 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat\n"


async def _acall(name, args):
    async with Client(mcp) as client:
        return await client.call_tool(name, args)


def _call(name, args):
    return asyncio.run(_acall(name, args))


def _text(result):
    return "".join(b.text for b in result.content if hasattr(b, "text"))


def _masked(result):
    """mcp 2.x answers a crash with exactly `Error executing tool <name>`; a
    ToolError gets the same prefix FOLLOWED by its message."""
    return re.fullmatch(r"Error executing tool \w+", _text(result).strip()) is not None


def _rows(result):
    return {
        (r["part"], r["color_code"]): r["quantity"]
        for r in result.structured_content["rows"]
    }


# ------------------------------------------------------------------ A


def test_the_whole_oserror_family_is_a_refusal_and_a_bug_is_not():
    """The class, not one more member of it: every OSError subclass is a
    statement about the caller's filesystem or configuration."""
    for exc in (PermissionError, IsADirectoryError, NotADirectoryError, OSError):
        assert issubclass(exc, _REFUSALS), exc
    for exc in (TypeError, AttributeError, KeyError):
        assert not issubclass(exc, _REFUSALS), exc


@pytest.mark.skipif(
    sys.platform == "win32" or os.geteuid() == 0,
    reason="permission bits do not bind root",
)
def test_an_unreadable_model_is_named_not_masked(monkeypatch, tmp_path):
    monkeypatch.setattr(ldraw_render, "ldraw_library_dir", lambda: None)
    model = tmp_path / "locked.ldr"
    model.write_text(BRICK)

    model.chmod(0)
    try:
        refused = _call("bill_of_materials", {"path": str(model)})
    finally:
        model.chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert refused.is_error
    assert not _masked(refused)
    assert "Permission denied" in _text(refused) and "locked.ldr" in _text(refused)

    readable = _call("bill_of_materials", {"path": str(model)})
    assert not readable.is_error, _text(readable)
    assert _rows(readable) == {("3001.dat", 4): 1}


def test_a_sibling_reference_that_is_a_directory_is_named_not_masked(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(ldraw_render, "ldraw_library_dir", lambda: None)
    model = tmp_path / "refdir.ldr"
    model.write_text("1 4 0 0 0 1 0 0 0 1 0 0 0 1 sub.ldr\n")

    (tmp_path / "sub.ldr").mkdir()
    refused = _call("bill_of_materials", {"path": str(model)})
    assert refused.is_error
    assert not _masked(refused)
    assert "sub.ldr" in _text(refused)

    (tmp_path / "sub.ldr").rmdir()
    (tmp_path / "sub.ldr").write_text("1 16 0 0 0 1 0 0 0 1 0 0 0 1 3001.dat\n")
    followed = _call("bill_of_materials", {"path": str(model)})
    assert not followed.is_error, _text(followed)
    assert _rows(followed) == {("3001.dat", 4): 1}


def test_a_blender_that_cannot_be_executed_is_named_issue_41(monkeypatch, tmp_path):
    """LDRAW_MCP_BLENDER pointing at a directory: `subprocess.run` raises
    PermissionError, which used to leave the server as a bare crash."""
    monkeypatch.setenv("LDRAW_LIBRARY_PATH", str(FIXTURE_LIBRARY))
    model = tmp_path / "m.ldr"
    model.write_text(BRICK)

    not_a_program = tmp_path / "blender-dir"
    not_a_program.mkdir()
    monkeypatch.setenv("LDRAW_MCP_BLENDER", str(not_a_program))
    refused = _call("render_ldraw_file", {"path": str(model), "azimuths": "0"})
    assert refused.is_error
    assert not _masked(refused)
    assert "blender-dir" in _text(refused)

    # Positive control: an executable at the same setting IS run - its own
    # failure (exit 3) is what comes back, so the refusal above was specific
    # to not being able to execute it.
    fake = tmp_path / "fake-blender"
    fake.write_text("#!/bin/sh\nexit 3\n")
    fake.chmod(0o755)
    monkeypatch.setenv("LDRAW_MCP_BLENDER", str(fake))
    ran = _call("render_ldraw_file", {"path": str(model), "azimuths": "0"})
    assert ran.is_error
    assert "exit 3" in _text(ran)


# ------------------------------------------------------------------ B and C


@pytest.fixture
def spied_blender(monkeypatch, tmp_path):
    """A renderer that is 'available' and a subprocess spy that records the
    command and stops there, so a refusal can be told from a render."""
    monkeypatch.setenv("LDRAW_LIBRARY_PATH", str(FIXTURE_LIBRARY))
    monkeypatch.setenv("LDRAW_MCP_BLENDER", sys.executable)
    commands = []

    def spy(cmd, **kwargs):
        commands.append(cmd)
        raise ldraw_render.subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(ldraw_render.subprocess, "run", spy)
    return commands


def _render_text(**kwargs):
    return _call("render_ldraw_text", {"ldr": BRICK, **kwargs})


@pytest.mark.parametrize(
    "args, named",
    [
        ({"resolution": -5}, "resolution"),
        ({"resolution": 0}, "resolution"),
        ({"resolution": 3}, "resolution"),
        ({"samples": 0}, "samples"),
        ({"samples": -3}, "samples"),
        ({"azimuths": "inf"}, "azimuth"),
        ({"azimuths": "0,nan"}, "azimuth"),
    ],
)
def test_out_of_range_render_parameters_are_refused_before_blender(
    spied_blender, args, named
):
    refused = _render_text(**{"resolution": 64, "samples": 1, "azimuths": "0", **args})
    assert refused.is_error
    assert not _masked(refused)
    assert named in _text(refused)
    assert spied_blender == [], "refused, so Blender must never have been started"

    # positive control: the lower edges themselves reach Blender unchanged
    reached = _render_text(
        resolution=ldraw_render.MIN_RESOLUTION, samples=1, azimuths="0"
    )
    assert "timed out" in _text(reached)
    cmd = spied_blender[-1]
    assert cmd[cmd.index("--resolution") + 1] == str(ldraw_render.MIN_RESOLUTION)
    assert cmd[cmd.index("--samples") + 1] == "1"


@pytest.mark.parametrize(
    "args, named",
    [
        ({"resolution": 10**6}, "resolution"),
        ({"samples": 10**9}, "samples"),
        ({"azimuths": ",".join(["0"] * 5000)}, "azimuth"),
        # each within its own bound, together far past a render that finishes
        (
            {
                "resolution": 2048,
                "samples": 1024,
                "azimuths": ",".join(["0"] * 8),
            },
            "budget",
        ),
    ],
)
def test_render_parameters_have_upper_bounds(spied_blender, args, named):
    refused = _render_text(**{"resolution": 64, "samples": 1, "azimuths": "0", **args})
    assert refused.is_error
    assert not _masked(refused)
    assert named in _text(refused)
    assert spied_blender == []

    # positive control: the upper edge of each bound alone reaches Blender
    for edge in (
        {"resolution": ldraw_render.MAX_RESOLUTION, "samples": 1, "azimuths": "0"},
        {"resolution": 64, "samples": ldraw_render.MAX_SAMPLES, "azimuths": "0"},
        {
            "resolution": 64,
            "samples": 1,
            "azimuths": ",".join(["0"] * ldraw_render.MAX_VIEWS),
        },
    ):
        reached = _render_text(**edge)
        assert "timed out" in _text(reached), edge
    # the tools' own defaults sit well inside the budget
    assert "timed out" in _text(_render_text())


@pytest.mark.skipif(
    not ldraw_render.is_available(), reason="blender/LDraw stack not installed"
)
def test_a_real_render_through_the_tool_has_the_requested_size():
    """Real execution: Blender through the MCP boundary. The image is the size
    asked for, which is what a clamped resolution silently broke."""
    import base64
    import io

    from PIL import Image

    result = _render_text(
        ldr="1 4 10 -24 10 1 0 0 0 1 0 0 0 1 3005.dat\n",
        resolution=48,
        samples=2,
        azimuths="-60",
    )
    assert not result.is_error, _text(result)
    image = Image.open(io.BytesIO(base64.b64decode(result.content[0].data)))
    assert image.size == (48, 48)

    too_small = _render_text(resolution=3, samples=1, azimuths="0")
    assert too_small.is_error and "resolution" in _text(too_small)


# ------------------------------------------------------------------ D


def test_render_ldraw_text_leaves_no_temp_file_even_when_the_write_fails(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    seen = []

    def spy_render(ldr_path, out, **kwargs):
        seen.append(Path(ldr_path).read_text())
        Path(out).write_bytes(b"\x89PNG\r\n\x1a\nfake")

    monkeypatch.setattr(ldraw_render, "render_ldraw", spy_render)

    # a lone surrogate cannot be encoded: the WRITE raises
    failed = _render_text(ldr="0 x\n\ud800")
    assert failed.is_error and "surrogates" in _text(failed)
    assert seen == []
    assert list(tmp_path.iterdir()) == []

    # positive control: the text is written, handed over, and then removed
    ok = _render_text(ldr=BRICK)
    assert not ok.is_error, _text(ok)
    assert seen == [BRICK]
    assert list(tmp_path.iterdir()) == []


# ------------------------------------------------------------------ E


def test_server_info_reports_the_installed_package_version():
    async def info():
        async with Client(mcp) as client:
            return client.server_info

    server_info = asyncio.run(info())
    assert server_info.name == "ldraw"
    assert server_info.version == version("ldraw-mcp")
    assert server_info.version  # an empty string would equal an empty metadata field
