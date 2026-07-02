"""
Studio-quality LDraw rendering via headless Blender + ImportLDraw.

The 'see the actual model' protocol: point at an LDraw model, render it
with real part geometry (studs, slopes, window glass) in Blender, and
stitch the views into one PNG.

Availability requires: a blender binary, the io_scene_importldraw addon,
and an LDraw parts library (~/.ldraw). Everything degrades gracefully:
callers check is_available() or catch StudioRenderError.

Environment variables:
  STUDIO_MCP_BLENDER   path to the blender binary (overrides PATH lookup)
  STUDIO_MCP_DISABLE   set to "1" to force is_available() to False
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


class StudioRenderError(Exception):
    """Blender render failed or is unavailable."""


def find_blender() -> Optional[str]:
    env = os.environ.get("STUDIO_MCP_BLENDER")
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
    if os.environ.get("STUDIO_MCP_DISABLE") == "1":
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
        raise StudioRenderError(
            "studio render unavailable (need blender + ImportLDraw addon "
            "+ LDraw library in ~/.ldraw; run `studio-mcp-setup`)"
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
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout
            )
        except subprocess.TimeoutExpired:
            raise StudioRenderError(f"blender render timed out after {timeout}s")
        views = [Path(f"{prefix}_{i}.png") for i in range(len(azimuths))]
        missing = [v for v in views if not v.exists()]
        if proc.returncode != 0 or missing:
            tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-15:])
            raise StudioRenderError(
                f"blender render failed (exit {proc.returncode}, "
                f"missing {len(missing)} view(s)):\n{tail}"
            )
        _stitch(views, output_png)
    return output_png


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
