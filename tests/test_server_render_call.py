"""server._render passes every argument through to the renderer, unchanged.

Mutation triage 2026-09-17 (issue #40): 19 survivors in ``_render`` — each
kwarg replaced with None or dropped, the output path mangled, the returned
Image built from the wrong bytes or format. Nothing observed the call to
``render_ldraw`` because every server test goes through the MCP client with
the renderer disabled. One spy, every argument asserted.
"""

from pathlib import Path

from ldraw_mcp import render as ldraw_render
from ldraw_mcp import server

PNG = b"\x89PNG\r\n\x1a\nfake"


def test_render_passes_every_argument_through_and_returns_the_png(monkeypatch):
    calls: list[tuple[tuple, dict]] = []

    def spy_render_ldraw(ldr_path, out, **kwargs):
        calls.append(((ldr_path, out), kwargs))
        Path(out).write_bytes(PNG)  # the file must be the renderer's output

    monkeypatch.setattr(ldraw_render, "render_ldraw", spy_render_ldraw)

    image = server._render("model.ldr", "-60,120", 320, 8)

    assert len(calls) == 1
    (ldr_path, out), kwargs = calls[0]
    assert ldr_path == "model.ldr"
    assert Path(out).name == "render.png"
    assert "None" not in out
    assert kwargs == {"azimuths": [-60.0, 120.0], "resolution": 320, "samples": 8}
    assert image.data == PNG
    assert image._format == "png"
    # Positive control for the spy itself: the temp dir is gone afterwards,
    # so the bytes above came from the file the renderer wrote, not a cache.
    assert not Path(out).exists()
