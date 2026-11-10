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
import math
import sys
from pathlib import Path

import bpy


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
        if addon_utils.enable(name, default_set=True) is not None:
            return
    raise RuntimeError("ImportLDraw addon not found in Blender addons")


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
    enable_addon()  # factory reset drops enabled addons

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
