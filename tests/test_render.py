"""Tests for the Blender studio render wrapper (unit + optional integration)."""

import pytest

from studio_mcp import render as studio_render


def test_env_kill_switch(monkeypatch):
    monkeypatch.setenv("STUDIO_MCP_DISABLE", "1")
    assert studio_render.is_available() is False


def test_blender_env_override(monkeypatch, tmp_path):
    fake = tmp_path / "blender"
    fake.write_text("#!/bin/sh\n")
    monkeypatch.setenv("STUDIO_MCP_BLENDER", str(fake))
    assert studio_render.find_blender() == str(fake)


def test_render_unavailable_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(studio_render, "find_blender", lambda: None)
    with pytest.raises(studio_render.StudioRenderError, match="unavailable"):
        studio_render.render_ldraw("x.ldr", str(tmp_path / "out.png"))


def test_stitch_combines_views_horizontally(tmp_path):
    from PIL import Image

    paths = []
    for i, (w, h) in enumerate([(40, 30), (60, 20)]):
        p = tmp_path / f"v{i}.png"
        Image.new("RGB", (w, h), (i * 100, 0, 0)).save(p)
        paths.append(p)
    out = tmp_path / "combined.png"
    studio_render._stitch(paths, str(out))
    combined = Image.open(out)
    assert combined.size == (100, 30)


@pytest.mark.skipif(
    not studio_render.is_available(), reason="blender/LDraw stack not installed"
)
def test_real_render_single_brick(tmp_path):
    """Full pipeline: one brick .ldr through Blender to a PNG."""
    ldr = tmp_path / "brick.ldr"
    ldr.write_text("0 one brick\n1 4 10 -24 10 1 0 0 0 1 0 0 0 1 3005.dat\n")
    out = tmp_path / "brick.png"
    studio_render.render_ldraw(
        str(ldr), str(out), azimuths=(-60,), samples=4, resolution=96, timeout=180
    )
    assert out.exists() and out.stat().st_size > 500
