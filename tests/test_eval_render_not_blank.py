"""EVAL: does the render actually contain the model?

The existing real-render test asserts:

    assert out.exists() and out.stat().st_size > 500

**A blank grey PNG satisfies both.** That is the one thing a renderer test has
to be able to rule out, and it could not — which is how a live blank-render
defect (issue #14) went unnoticed in a published release.

This server's whole claim is that a vision-capable model gets an accurate view
of a build. An empty frame is not a weaker version of that claim; it is the
absence of it, delivered with a valid PNG and a zero exit code.

## What "blank" means here, measured

On a single 1x1 brick, a currently-broken render gives a frame whose pixels all
sit within **14** of one another (summed RGB distance from the corner pixel), and
**zero** pixels differ from the background by more than 40. A frame containing a
red brick on a grey backdrop cannot look like that: LDraw colour 4 is red, and
red against grey 197 differs by several hundred.

The threshold is therefore not a guess between two plausible numbers. It sits in
an enormous empty gap between "no object" (14) and "an object of any colour at
all" (hundreds).
"""

import tempfile
from pathlib import Path

import pytest

from ldraw_mcp import render as ldraw_render

pytestmark = pytest.mark.skipif(
    not ldraw_render.is_available(),
    reason="blender/LDraw stack not installed",
)

# The repo's own fixture line, so this measures the renderer and not a
# hand-rolled model of my own that might be malformed.
ONE_BRICK = "0 one brick\n1 4 10 -24 10 1 0 0 0 1 0 0 0 1 3005.dat\n"

# Any real object exceeds this by an order of magnitude; a blank frame measured
# 14. Nothing lives in between.
BLANK_MAX_DEVIATION = 40


def _object_pixels(png_path: str) -> tuple[int, int]:
    """Return (pixels differing from the background, max deviation)."""
    import numpy as np
    from PIL import Image

    a = np.array(Image.open(png_path).convert("RGB")).astype(int)
    deviation = np.abs(a - a[0, 0]).sum(axis=2)
    return int((deviation > BLANK_MAX_DEVIATION).sum()), int(deviation.max())


@pytest.mark.xfail(
    strict=True,
    reason=(
        "issue #14: geometry imports (Blender logs 49 vertices, 2 objects) but "
        "the frame renders empty. Marked strict so that FIXING the renderer "
        "fails this test and forces the marker off, rather than the eval "
        "quietly continuing to expect a broken render."
    ),
)
def test_a_rendered_brick_is_visible_in_the_frame():
    with tempfile.TemporaryDirectory() as td:
        ldr = Path(td, "brick.ldr")
        ldr.write_text(ONE_BRICK)
        out = str(Path(td, "brick.png"))
        ldraw_render.render_ldraw(
            str(ldr), out, azimuths=(-60,), samples=8, resolution=240, timeout=600
        )
        pixels, max_dev = _object_pixels(out)
        assert pixels > 0, (
            f"the render contains no object: every pixel is within {max_dev} of "
            "the background. The PNG exists and is well-formed, which is exactly "
            "why file-size assertions cannot catch this."
        )


def test_the_blankness_check_can_tell_a_blank_frame_from_a_drawn_one():
    """The control. Without it, the check above could be asserting nothing.

    A synthetic pair — flat grey, and the same grey with a red square — must be
    scored differently. This runs with no renderer at all, so the detector is
    verified even where Blender is unavailable.
    """
    import numpy as np
    from PIL import Image

    with tempfile.TemporaryDirectory() as td:
        flat = Path(td, "flat.png")
        Image.fromarray(np.full((60, 60, 3), 197, np.uint8)).save(flat)
        assert _object_pixels(str(flat))[0] == 0, (
            "flat grey scored as containing an object"
        )

        drawn = np.full((60, 60, 3), 197, np.uint8)
        drawn[20:40, 20:40] = (200, 30, 30)  # a red brick's worth of contrast
        path = Path(td, "drawn.png")
        Image.fromarray(drawn).save(path)
        pixels, _ = _object_pixels(str(path))
        assert pixels == 400, f"a 20x20 red square scored {pixels} object pixels"
