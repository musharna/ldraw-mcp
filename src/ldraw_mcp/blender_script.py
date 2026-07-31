"""
Blender-side script: import an LDraw .ldr and render PNG views.

Runs INSIDE Blender (never import this from the package):
  blender -b -P blender_script.py -- \
      --ldr model.ldr --out /dir/prefix --azimuths -60,120 [--samples 32]

Renders one PNG per azimuth to <prefix>_<i>.png using Cycles CPU (works
headless everywhere, including WSL2 without a GPU). Camera orbits the
model bounding box at a fixed elevation.

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

    for name in ("io_scene_importldraw", "importldraw"):
        try:
            addon_utils.enable(name, default_set=True)
            return
        except Exception:
            continue
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
    span = max(b - a for a, b in zip(lo, hi))
    distance = span * 2.2

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
