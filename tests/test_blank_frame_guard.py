"""
The render path returns an image; nothing ever checked that the image had
anything in it.

Issue #14 shipped because a blank render is indistinguishable from a good one
at every layer: the import succeeded, blender exited 0, and a well-formed PNG
landed on disk. The clip-plane fix in 0.2.1 removed one cause of an empty
scene. It did not make an empty scene detectable, so the next cause -- hidden
geometry, a misaimed camera, an import that yields nothing -- would reach a
user the same silent way.

## Fixtures are real, not synthesised

`blank_render_issue14.png` is the actual output of the pre-0.2.1 renderer, and
`good_render.png` is the same model through the fixed code. A synthetic flat
fill would have a spread of exactly 0; the real blank has 6, because cycles
leaves sampling noise even on an empty world. Testing against a spread-0 image
would not show whether the threshold tolerates a *noisy* blank, which is the
only kind that actually occurs.
"""

import subprocess
from pathlib import Path

import pytest

from ldraw_mcp import render as ldraw_render

DATA = Path(__file__).parent / "data"
BLANK = DATA / "blank_render_issue14.png"
GOOD = DATA / "good_render.png"


def test_the_predicate_separates_a_real_blank_from_a_real_render():
    """Both directions, in one test -- a detector that only fires is useless."""
    blank_spread = ldraw_render._frame_spread(BLANK)
    good_spread = ldraw_render._frame_spread(GOOD)

    assert blank_spread <= ldraw_render.BLANK_FRAME_MAX_SPREAD, (
        f"the real blank frame from issue #14 scored {blank_spread}, above the "
        f"{ldraw_render.BLANK_FRAME_MAX_SPREAD} threshold -- it would not be caught"
    )
    # The positive control. Without this the threshold could be raised to 255
    # and the assertion above would still pass while the guard rejected
    # every legitimate render.
    assert good_spread > ldraw_render.BLANK_FRAME_MAX_SPREAD, (
        f"a known-good render scored {good_spread} and would be rejected as blank"
    )
    # Separation, not a coin flip. Measured margin is ~39x; requiring 10x
    # leaves room for noisier scenes without accepting a near-miss.
    assert good_spread > blank_spread * 10


def _fake_blender(monkeypatch, tmp_path, sources):
    """Stand in for blender, writing `sources` as the per-view PNGs."""
    monkeypatch.setattr(ldraw_render, "find_blender", lambda: "/fake/blender")
    monkeypatch.setattr(ldraw_render, "ldraw_library_dir", lambda: tmp_path)

    def fake_run(cmd, **kwargs):
        prefix = cmd[cmd.index("--out") + 1]
        for i, src in enumerate(sources):
            Path(f"{prefix}_{i}.png").write_bytes(Path(src).read_bytes())
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(ldraw_render.subprocess, "run", fake_run)


def test_render_rejects_a_blank_view_even_though_blender_succeeded(
    monkeypatch, tmp_path
):
    """The exact issue #14 shape: exit 0, files present, frames empty."""
    _fake_blender(monkeypatch, tmp_path, [BLANK, BLANK])
    out = tmp_path / "out.png"

    with pytest.raises(ldraw_render.LDrawRenderError) as exc:
        ldraw_render.render_ldraw(str(tmp_path / "m.ldr"), str(out))

    assert "blank" in str(exc.value).lower()
    assert not out.exists(), "a rejected render must not leave output behind"


def test_render_accepts_good_views(monkeypatch, tmp_path):
    """Positive control for the test above: the guard must pass real renders.

    Without this, a guard that raised unconditionally would look correct.
    """
    _fake_blender(monkeypatch, tmp_path, [GOOD, GOOD])
    out = tmp_path / "out.png"

    result = ldraw_render.render_ldraw(str(tmp_path / "m.ldr"), str(out))

    assert result == str(out)
    assert out.exists() and out.stat().st_size > 0


def test_render_rejects_a_half_blank_pair(monkeypatch, tmp_path):
    """Why the check is per view.

    One blank view beside one good view stitches into an image with plenty of
    contrast, so the same predicate applied after _stitch would pass this.
    """
    _fake_blender(monkeypatch, tmp_path, [GOOD, BLANK])

    with pytest.raises(ldraw_render.LDrawRenderError) as exc:
        ldraw_render.render_ldraw(str(tmp_path / "m.ldr"), str(tmp_path / "out.png"))

    assert "1 of 2" in str(exc.value)
