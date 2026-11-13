"""server._render passes every argument through to the renderer, unchanged.

Mutation triage 2026-09-17 (issue #40): 19 survivors in ``_render`` — each
kwarg replaced with None or dropped, the output path mangled, the returned
Image built from the wrong bytes or format. Nothing observed the call to
the renderer (now ``render_ldraw_report``) because every server test goes through the MCP client with
the renderer disabled. One spy, every argument asserted.

Audit 2026-11-10 (L-M2): the renderer also reports the files it could not
find; they come back as a text block after the image, and only then.
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
        return ldraw_render.RenderResult(out, ())

    monkeypatch.setattr(ldraw_render, "render_ldraw_report", spy_render_ldraw)

    content = server._render("model.ldr", "-60,120", 320, 8)
    assert len(content) == 1, "no file was missing, so no warning"
    image = content[0]

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


def test_missing_files_come_back_as_text_after_the_image(monkeypatch):
    def spy_render_ldraw(ldr_path, out, **kwargs):
        Path(out).write_bytes(PNG)
        return ldraw_render.RenderResult(out, ("3001x_nope.dat", "s\\gone.dat"))

    monkeypatch.setattr(ldraw_render, "render_ldraw_report", spy_render_ldraw)

    image, warning = server._render("model.ldr", "0", 64, 1)
    assert image.data == PNG
    assert warning.startswith("WARNING: 2 file(s)")
    assert "3001x_nope.dat, s\\gone.dat" in warning
