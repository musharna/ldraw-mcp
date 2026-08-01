"""
High-quality LDraw rendering via headless Blender + ImportLDraw.

The 'see the actual model' protocol: point at an LDraw model, render it
with real part geometry (studs, slopes, window glass) in Blender, and
stitch the views into one PNG.

Availability requires: a blender binary, the io_scene_importldraw addon,
and an LDraw parts library (~/.ldraw). Everything degrades gracefully:
callers check is_available() or catch LDrawRenderError.

Environment variables:
  LDRAW_MCP_BLENDER   path to the blender binary (overrides PATH lookup)
  LDRAW_MCP_DISABLE   set to "1" to force is_available() to False
  LDRAW_LIBRARY_PATH   path to the LDraw parts library
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional, Sequence

BLENDER_SCRIPT = Path(__file__).parent / "blender_script.py"
DEFAULT_AZIMUTHS = (-60.0, 120.0)

# A render that produces a well-formed PNG of nothing is the failure mode that
# shipped as issue #14: blender exits 0, the file exists, and every layer above
# reports success. Fixing the clip planes removed one *cause* of an empty scene;
# it did nothing to make an empty scene *detectable*. Hidden objects, a misaimed
# camera, an import that yields no geometry, or dead lights all still render a
# valid blank PNG. This is the output postcondition the render path never had.
#
# The predicate is the largest per-channel extrema spread, not a count of
# differing pixels, because spread does not depend on how much of the frame the
# model fills -- a single dark pixel swings it. That keeps a small part rendered
# at high resolution from reading as blank. MEASURED on real renders
# (200x200, cycles, 16 samples, white world):
#
#   blank frame, model inside the near clip plane ...   6   <- sampling noise
#   model covering ~1% of the frame .................. 207
#   model covering ~33% of the frame ................. 232
#   published 0.2.1 render, 1200x600 ................. 245
#
# 24 sits ~4x above the observed noise floor and ~8x below the sparsest real
# render. It is deliberately not the midpoint: a false positive breaks a working
# render, while a false negative merely restores the pre-guard behaviour.
BLANK_FRAME_MAX_SPREAD = 24


class LDrawRenderError(Exception):
    """Blender render failed or is unavailable."""


def find_blender() -> Optional[str]:
    env = os.environ.get("LDRAW_MCP_BLENDER")
    if env and Path(env).exists():
        return env
    found = shutil.which("blender")
    if found:
        return found
    local = Path.home() / ".local" / "bin" / "blender"
    return str(local) if local.exists() else None


def ldraw_library_dir() -> Optional[Path]:
    env = os.environ.get("LDRAW_LIBRARY_PATH")
    candidates = [Path(env)] if env else []
    candidates += [Path.home() / ".ldraw", Path("/usr/share/ldraw")]
    for c in candidates:
        if (c / "parts").is_dir():
            return c
    return None


def is_available() -> bool:
    if os.environ.get("LDRAW_MCP_DISABLE") == "1":
        return False
    return find_blender() is not None and ldraw_library_dir() is not None


def render_ldraw(
    ldr_path: str,
    output_png: str,
    azimuths: Sequence[float] = DEFAULT_AZIMUTHS,
    samples: int = 32,
    resolution: int = 800,
    timeout: int = 600,
) -> str:
    """Render an .ldr file to a single side-by-side PNG of `azimuths` views."""
    blender = find_blender()
    library = ldraw_library_dir()
    if blender is None or library is None:
        raise LDrawRenderError(
            "render unavailable (need blender + ImportLDraw addon "
            "+ LDraw library in ~/.ldraw; run `ldraw-mcp-setup`)"
        )
    with tempfile.TemporaryDirectory() as td:
        prefix = str(Path(td) / "view")
        cmd = [
            blender,
            "-b",
            "--factory-startup",
            "-P",
            str(BLENDER_SCRIPT),
            "--",
            "--ldr",
            str(ldr_path),
            "--out",
            prefix,
            "--azimuths=" + ",".join(str(a) for a in azimuths),
            "--samples",
            str(samples),
            "--resolution",
            str(resolution),
            "--ldraw-dir",
            str(library),
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise LDrawRenderError(f"blender render timed out after {timeout}s")
        views = [Path(f"{prefix}_{i}.png") for i in range(len(azimuths))]
        missing = [v for v in views if not v.exists()]
        if proc.returncode != 0 or missing:
            tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-15:])
            raise LDrawRenderError(
                f"blender render failed (exit {proc.returncode}, "
                f"missing {len(missing)} view(s)):\n{tail}"
            )
        # Checked per view rather than on the stitched result: one blank view
        # beside one good one still stitches into an image full of contrast, so
        # a check after _stitch would miss exactly half the failure.
        blank = [v for v in views if _frame_spread(v) <= BLANK_FRAME_MAX_SPREAD]
        if blank:
            raise LDrawRenderError(
                f"blender exited 0 but {len(blank)} of {len(views)} view(s) "
                "rendered blank -- no visible geometry. The model may be "
                "empty, hidden, or outside the camera frustum."
            )
        _stitch(views, output_png)
    return output_png


def _frame_spread(png_path) -> int:
    """Largest per-channel value range in a frame. Near zero means nothing drawn."""
    from PIL import Image

    # From each band's histogram rather than getextrema(), which returns a
    # (lo, hi) pair for one band but a tuple of pairs for several -- a union
    # that has to be narrowed at every call site to mean anything.
    with Image.open(png_path) as im:
        bands = im.convert("RGB").split()
    spread = 0
    for band in bands:
        used = [value for value, count in enumerate(band.histogram()) if count]
        spread = max(spread, used[-1] - used[0])
    return spread


def _stitch(view_paths, output_png: str) -> None:
    """Combine view PNGs horizontally into one image."""
    from PIL import Image

    images = [Image.open(p) for p in view_paths]
    height = max(im.height for im in images)
    width = sum(im.width for im in images)
    combined = Image.new("RGB", (width, height), (255, 255, 255))
    x = 0
    for im in images:
        combined.paste(im, (x, (height - im.height) // 2))
        x += im.width
    combined.save(output_png)
