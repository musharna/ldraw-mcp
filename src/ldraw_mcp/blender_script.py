"""
Blender-side script: import an LDraw .ldr and render PNG views.

Runs INSIDE Blender (never import this from the package):
  blender -b -P blender_script.py -- \
      --ldr model.ldr --out /dir/prefix --azimuths -60,120 [--samples 32]

Renders one PNG per azimuth to <prefix>_<i>.png using Cycles CPU (works
headless everywhere, including WSL2 without a GPU). Camera orbits the
centre of the model's bounding box at a fixed elevation, far enough out
that the bounding sphere fits the frame (see framing_distance).

Requires the ImportLDraw addon (io_scene_importldraw) and an LDraw
parts library (default ~/.ldraw).
"""

import argparse
import importlib.util
import json
import math
import os
import sys
from pathlib import Path

import bpy


def _load_reference_rule():
    """The package's bom.py, loaded by path: the one place the reference rule
    lives, shared with bill_of_materials. Blender's Python does not have the
    package installed, and bom.py needs nothing beyond the standard library."""
    path = Path(__file__).resolve().parent / "bom.py"
    spec = importlib.util.spec_from_file_location("ldraw_mcp_bom", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses look the module up by name
    spec.loader.exec_module(module)
    return module


ldraw_bom = _load_reference_rule()


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--ldr", required=True)
    p.add_argument("--out", required=True, help="Output path prefix (no extension)")
    p.add_argument("--azimuths", default="-60,120")
    p.add_argument("--elevation", type=float, default=22.0)
    p.add_argument("--samples", type=int, default=32)
    p.add_argument("--resolution", type=int, default=800)
    p.add_argument("--ldraw-dir", default=str(Path.home() / ".ldraw"))
    return p.parse_args(argv)


def enable_addon():
    import addon_utils

    # enable() signals a missing add-on by returning None (it prints the cause and
    # does not raise), so success is the return value, not the absence of an error.
    for name in ("io_scene_importldraw", "importldraw"):
        module = addon_utils.enable(name, default_set=True)
        if module is not None:
            return module
    raise RuntimeError("ImportLDraw addon not found in Blender addons")


# The one line render.py reads back: a JSON object after this prefix. Printed
# once, after the import, on every run that gets that far.
REPORT_PREFIX = "LDRAW_MCP_REPORT "


def watch_file_lookups(addon, model_dir):
    """Record every file the importer looks for and does not find, and refuse
    every reference the parts list refuses.

    ImportLDraw resolves each referenced file through `FileSystem.locate`; on
    None it logs "Missing file" and SKIPS the reference, so the part is simply
    absent from the picture. Watching that one call reports exactly what the
    importer dropped, which a second lookup of our own could disagree with
    (it searches the model's directory, `parts/`, `p/`, `models/` and the
    unofficial folders, in its own order).

    The same call is where a reference becomes a path, so the reference rule
    is applied there, through bom.py's own functions: the name first, then,
    for a file found in the model's folder, where it really is. A refused
    reference is not looked up (the importer skips it as missing) and is
    reported under "refused", which stops the render.
    """
    name = addon.__name__ + ".loadldraw.loadldraw"
    loader = sys.modules.get(name)
    file_system = getattr(loader, "FileSystem", None)
    original = getattr(file_system, "locate", None)
    if original is None:
        # Fail loud: without the hook a missing part would vanish unreported.
        raise RuntimeError(
            f"ImportLDraw has no {name}.FileSystem.locate to watch; this add-on "
            "version is not one ldraw-mcp knows how to check for missing parts"
        )
    model_dir = os.path.abspath(model_dir)
    resolved_dir = Path(model_dir).resolve()
    missing, refused = [], []

    def locate(filename, rootPath=None):
        try:
            ldraw_bom.check_reference(filename)
            found = original(filename, rootPath)
            if found is not None and Path(os.path.abspath(found)).is_relative_to(
                model_dir
            ):
                ldraw_bom.check_inside(
                    Path(found), resolved_dir, ldraw_bom.normalise_name(filename)
                )
        except ldraw_bom.LDrawModelError as exc:
            if str(exc) not in refused:
                refused.append(str(exc))
            return None
        if found is None and filename not in missing:
            missing.append(filename)
        return found

    file_system.locate = locate
    return missing, refused


def scene_bounds():
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        for corner in obj.bound_box:
            world = obj.matrix_world @ __import__("mathutils").Vector(corner)
            for i in range(3):
                lo[i] = min(lo[i], world[i])
                hi[i] = max(hi[i], world[i])
    if lo[0] == float("inf"):
        raise RuntimeError("no mesh objects imported — empty or failed import")
    return lo, hi


# How far outside the model's bounding sphere the frame stops: 1.1 leaves the
# sphere's outline at 90% of the half-frame, so even the sphere is 5% clear of
# every edge, and the model, which lies inside it, is clearer still.
FRAME_MARGIN = 1.1


def framing_distance(lo, hi, cam_data, res_x, res_y):
    """Camera-to-centre distance at which the whole model is in frame from ANY
    azimuth and elevation, with the camera aimed at the centre of the bounds.

    The bounding sphere is the quantity that does not depend on the view: every
    point of the model lies inside it, and a sphere of radius r seen from
    distance d fills a cone of half-angle asin(r / d) around the line of sight,
    perspective included. Keeping that cone inside the NARROWER half of the
    field of view keeps the model inside the frame.

    The longest side, which this used to scale (`2.2 x span`), does not bound a
    box's diagonal: an 80 x 80 x 76 LDU cube has a sphere radius of 0.85 x its
    longest side and came back cut off at two edges.
    """
    radius = 0.5 * math.dist(lo, hi)
    # cam_data.angle is the field of view across the sensor-fit axis, which for
    # the default AUTO fit is the larger render dimension; the other axis is
    # narrower by the aspect ratio.
    half_wide = cam_data.angle / 2
    half_narrow = math.atan(math.tan(half_wide) * min(res_x, res_y) / max(res_x, res_y))
    return FRAME_MARGIN * radius / math.sin(half_narrow)


def main():
    args = parse_args()
    enable_addon()

    bpy.ops.wm.read_factory_settings(use_empty=True)
    addon = enable_addon()  # factory reset drops enabled addons
    missing, refused = watch_file_lookups(addon, os.path.dirname(args.ldr))

    # the addon's look setup assumes a world exists; empty scenes have none
    world = bpy.data.worlds.new("World")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (1, 1, 1, 1)
    world.node_tree.nodes["Background"].inputs[1].default_value = 1.0
    bpy.context.scene.world = world

    bpy.ops.import_scene.importldraw(
        filepath=args.ldr,
        ldrawPath=args.ldraw_dir,
        addEnvironment=False,  # its ground plane would dominate the bounds
        positionCamera=False,
        importCameras=False,
        positionOnGround=True,
        useLogoStuds=False,
        smoothParts=True,
        addGaps=True,
        bevelEdges=True,
        look="normal",
    )
    # Before scene_bounds, so a model whose every part is missing still says
    # which ones.
    print(
        REPORT_PREFIX + json.dumps({"missing": missing, "refused": refused}),
        flush=True,
    )
    if refused:
        sys.exit(3)  # a refused model is not rendered, not even in part

    lo, hi = scene_bounds()
    center = [(a + b) / 2 for a, b in zip(lo, hi)]

    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = args.samples
    scene.cycles.device = "CPU"
    scene.render.resolution_x = args.resolution
    scene.render.resolution_y = args.resolution
    scene.render.film_transparent = False

    # sun light
    sun = bpy.data.objects.new("Sun", bpy.data.lights.new("Sun", type="SUN"))
    sun.data.energy = 4.0
    sun.rotation_euler = (math.radians(50), 0, math.radians(-40))
    scene.collection.objects.link(sun)

    cam_data = bpy.data.cameras.new("Cam")
    distance = framing_distance(lo, hi, cam_data, args.resolution, args.resolution)
    # Clip planes must come from the same scene scale the camera distance does.
    # ImportLDraw imports at real-world metre scale, so a small model spans
    # centimetres and sits *inside* Blender's default 0.1 m near plane — it gets
    # clipped away and every frame renders as the bare world. Deriving both ends
    # from `distance` keeps any model size framed, rather than special-casing
    # small ones with a fixed tiny near plane.
    cam_data.clip_start = distance / 100.0
    cam_data.clip_end = distance * 100.0
    cam = bpy.data.objects.new("Cam", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam

    elev = math.radians(args.elevation)
    for i, azim_str in enumerate(args.azimuths.split(",")):
        azim = math.radians(float(azim_str))
        cam.location = (
            center[0] + distance * math.cos(elev) * math.cos(azim),
            center[1] + distance * math.cos(elev) * math.sin(azim),
            center[2] + distance * math.sin(elev),
        )
        direction = [c - l for c, l in zip(center, cam.location)]
        import mathutils

        cam.rotation_euler = (
            mathutils.Vector(direction).to_track_quat("-Z", "Y").to_euler()
        )
        scene.render.filepath = f"{args.out}_{i}.png"
        bpy.ops.render.render(write_still=True)
        print(f"RENDERED {scene.render.filepath}")


if __name__ == "__main__":
    main()
