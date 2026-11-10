"""The camera frames the whole model, from any azimuth, without shrinking it.

Audit 2026-11-10 (L-M1): a 3-brick-high 4x4 cube (80 x 80 x 76 LDU) came back
cut off at the left and bottom edges in both default views, as a normal image.
The camera stood `2.2 x` the longest side away, which does not cover the box's
diagonal under a perspective camera. No test looked at where the model sat in
the frame, so three camera mutants (azimuth ignored, camera aimed at a corner of
the bounds, camera at 1x the longest side) survived every real-render test.

These tests render for real and measure the picture: the model's pixels must
not reach any edge, the model must sit near the middle, and it must still fill
a sensible share of the frame, so "move the camera far away" fails too.
"""

import tempfile
from pathlib import Path

import pytest

from ldraw_mcp import render as ldraw_render

needs_renderer = pytest.mark.skipif(
    not ldraw_render.is_available(),
    reason="blender/LDraw stack not installed",
)

# A red part is strongly coloured; the white world renders as a neutral grey.
# MEASURED (audit probe, 200 px, AgX): background (200, 198, 201), chroma 3;
# red brick mean (204, 106, 94), chroma 110. 30 sits far from both.
OBJECT_MIN_CHROMA = 30

# Default views plus one more, 90 degrees from the first.
AZIMUTHS = (-60.0, 120.0, 30.0)
RESOLUTION = 160

# The model's larger on-screen side must cover at least this share of the
# frame. PREDICTED with a pinhole model of the fixed camera (and matching the
# audit's real renders to within 5 px): 0.59 (2x10 plate seen end-on) to 0.83.
# Moving the camera twice as far away drops the cube to about 0.4.
MIN_FILL = 0.5
# The model's box centre must lie within this share of the frame from the
# centre. Perspective alone moves it by up to 0.05 (the tower's base is nearer
# the camera than its top).
MAX_OFF_CENTRE = 0.12


def _line(colour: int, x: int, y: int, z: int, part: str) -> str:
    return f"1 {colour} {x} {y} {z} 1 0 0 0 1 0 0 0 1 {part}\n"


# A near-cube, a tall tower and a long flat plate: each is the worst case for a
# different way of picking the camera distance from the bounds.
MODELS = {
    "cube_80x80x76": "0 cube\n"
    + "".join(
        _line(4, 0, -24 * layer, z, "3001.dat") for layer in range(3) for z in (-20, 20)
    ),
    "tower_1x1x10": "0 tower\n"
    + "".join(_line(4, 0, -24 * layer, 0, "3005.dat") for layer in range(10)),
    # 2 x 10: its width on screen changes with azimuth, which is what shows the
    # azimuth is used at all.
    "plate_2x10": "0 plate\n" + _line(4, 0, 0, 0, "3832.dat"),
}


def _object_box(view) -> dict:
    """Where the model is in one view: bounding box and edge contact, in px."""
    import numpy as np

    a = np.asarray(view.convert("RGB")).astype(int)
    mask = (a.max(axis=2) - a.min(axis=2)) > OBJECT_MIN_CHROMA
    h, w = mask.shape
    if not mask.any():
        return {"size": (w, h), "pixels": 0}
    ys, xs = np.where(mask)
    return {
        "size": (w, h),
        "pixels": int(mask.sum()),
        "box": (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())),
        "touches": {
            "top": int(mask[0].sum()),
            "bottom": int(mask[-1].sum()),
            "left": int(mask[:, 0].sum()),
            "right": int(mask[:, -1].sum()),
        },
    }


def _framing_problems(m: dict) -> list[str]:
    """Every way one view's framing is wrong; empty when it is right."""
    if m["pixels"] == 0:
        return ["no model pixels at all"]
    w, h = m["size"]
    x0, y0, x1, y1 = m["box"]
    problems = [f"model touches the {e} edge" for e, n in m["touches"].items() if n]
    fill = max(x1 - x0 + 1, y1 - y0 + 1) / max(w, h)
    if fill < MIN_FILL:
        problems.append(f"model fills {fill:.2f} of the frame, under {MIN_FILL}")
    dx = abs((x0 + x1) / 2 - (w - 1) / 2) / w
    dy = abs((y0 + y1) / 2 - (h - 1) / 2) / h
    if max(dx, dy) > MAX_OFF_CENTRE:
        problems.append(f"model centre is off by ({dx:.2f}, {dy:.2f}) of the frame")
    return problems


def _render_views(ldr_text: str, td: str):
    from PIL import Image

    model = Path(td, "model.ldr")
    model.write_text(ldr_text)
    out = Path(td, "out.png")
    ldraw_render.render_ldraw(
        str(model),
        str(out),
        azimuths=AZIMUTHS,
        samples=4,
        resolution=RESOLUTION,
        timeout=600,
    )
    stitched = Image.open(out)
    n = len(AZIMUTHS)
    w = stitched.width // n
    return [stitched.crop((i * w, 0, (i + 1) * w, stitched.height)) for i in range(n)]


@needs_renderer
@pytest.mark.parametrize("name", list(MODELS))
def test_the_model_is_whole_centred_and_large_in_every_view(name):
    with tempfile.TemporaryDirectory() as td:
        views = _render_views(MODELS[name], td)
    measured = {az: _object_box(v) for az, v in zip(AZIMUTHS, views)}
    problems = {az: _framing_problems(m) for az, m in measured.items()}
    assert not any(problems.values()), (name, problems, measured)

    if name == "plate_2x10":
        # The camera really moves with the azimuth: a long plate seen from two
        # directions 90 degrees apart is a different width on screen.
        widths = {az: m["box"][2] - m["box"][0] + 1 for az, m in measured.items()}
        ratio = widths[30.0] / widths[-60.0]
        assert abs(ratio - 1) > 0.15, (
            f"the plate is the same width ({widths}) from azimuths 90 degrees "
            "apart: the azimuth did not move the camera"
        )


def test_the_framing_check_can_fail():
    """The control: the measure runs without Blender and tells each kind of bad
    frame from a good one, so a pass above is not a measure that sees nothing."""
    import numpy as np
    from PIL import Image

    def frame(x0, y0, x1, y1):
        a = np.full((100, 100, 3), (200, 198, 201), np.uint8)
        a[y0:y1, x0:x1] = (204, 106, 94)
        return Image.fromarray(a)

    assert _framing_problems(_object_box(frame(20, 20, 80, 80))) == []
    assert _framing_problems(_object_box(frame(0, 20, 90, 80))) == [
        "model touches the left edge"
    ]
    assert _framing_problems(_object_box(frame(20, 10, 80, 100))) == [
        "model touches the bottom edge"
    ]
    assert _framing_problems(_object_box(frame(40, 40, 60, 60))) == [
        "model fills 0.20 of the frame, under 0.5"
    ]
    assert _framing_problems(_object_box(frame(1, 1, 61, 61))) == [
        "model centre is off by (0.19, 0.19) of the frame"
    ]
    assert _framing_problems(_object_box(frame(0, 0, 0, 0))) == [
        "no model pixels at all"
    ]
