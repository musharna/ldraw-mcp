"""Measure how much of a model's surface a set of camera azimuths reveals.

Run this before changing `DEFAULT_AZIMUTHS`. The default pair is 180 degrees
apart, which reads as redundant on a symmetric model — the panels are near-mirror
images. Measurement says otherwise: mirrored silhouettes still show *opposite*
faces, and opposing views maximise coverage. Re-spacing them to "reduce
redundancy" makes the tool show less.

Coverage is measured by ray-casting, not by comparing images: two renders can
differ a great deal while showing the same geometry from a mirrored angle, which
is precisely the confusion this tool exists to settle. For each azimuth the
camera is placed exactly as `blender_script.py` places it, a grid of rays is cast
through the frustum, and the set of (object, polygon) actually hit is recorded.
A view set's coverage is the union of those sets, over the faces reachable from
any sampled azimuth at this elevation.

Note the denominator: faces hit are reported on the *evaluated* mesh (after
bevel and smoothing modifiers), so the count exceeds the source mesh's polygon
count. Normalising against the union of all sampled azimuths keeps that
self-consistent.

Usage:

    MODEL=path/to/model.ldr blender -b --factory-startup \\
        --python tools/measure_azimuth_coverage.py

`tools/models/` holds the two models the numbers in `render.py` were measured on:
`symmetric.ldr` (two stacked 2x4 bricks — the worst case for opposing views, since
the silhouette is nearly mirror-symmetric) and `asymmetric.ldr` (three different
parts in an L). Both matter: a conclusion about view redundancy drawn only from a
symmetric model would not generalise.

Environment:
    MODEL      path to an .ldr file (required)
    RAYS       rays per axis, default 140 (140x140 per view)
    ELEV       camera elevation in degrees, default 22 (matches the renderer)
    LDRAW_DIR  LDraw parts library, default ~/.ldraw

Prints one `RESULT_JSON <json>` line. Note that Blender exits 0 even when this
script raises, so check for that line rather than the exit status.
"""

import json
import math
import os
import signal
import sys

signal.signal(
    signal.SIGALRM,
    lambda *_: (sys.stderr.write("aborting: walltime guard\n"), sys.exit(2)),
)
signal.alarm(1800)

import addon_utils  # noqa: E402
import bpy  # noqa: E402
import mathutils  # noqa: E402

MODEL = os.environ["MODEL"]
RAYS = int(os.environ.get("RAYS", "140"))
ELEV = float(os.environ.get("ELEV", "22.0"))
LDRAW_DIR = os.environ.get("LDRAW_DIR", os.path.expanduser("~/.ldraw"))

STEP = 15
AZIMUTHS = list(range(-180, 180, STEP))


def enable_importer():
    for name in ("io_scene_importldraw", "importldraw"):
        try:
            addon_utils.enable(name, default_set=True)
            return name
        except Exception:
            continue
    raise RuntimeError("ImportLDraw addon not available")


enable_importer()
bpy.ops.wm.read_factory_settings(use_empty=True)
enable_importer()

# The importer's realistic-look setup writes scene.world.color unconditionally,
# so an empty factory scene has to be given a world before importing.
bpy.context.scene.world = bpy.data.worlds.new("World")

bpy.ops.import_scene.importldraw(
    filepath=MODEL,
    ldrawPath=LDRAW_DIR,
    addEnvironment=False,
    positionCamera=False,
    importCameras=False,
    positionOnGround=True,
    useLogoStuds=False,
    smoothParts=True,
    addGaps=True,
    bevelEdges=True,
    look="normal",
)

scene = bpy.context.scene
deps = bpy.context.evaluated_depsgraph_get()

lo = [float("inf")] * 3
hi = [float("-inf")] * 3
source_faces = 0
for obj in scene.objects:
    if obj.type != "MESH":
        continue
    source_faces += len(obj.data.polygons)
    for corner in obj.bound_box:
        world = obj.matrix_world @ mathutils.Vector(corner)
        for axis in range(3):
            lo[axis] = min(lo[axis], world[axis])
            hi[axis] = max(hi[axis], world[axis])

center = mathutils.Vector([(a + b) / 2 for a, b in zip(lo, hi)])
span = max(b - a for a, b in zip(lo, hi))
distance = span * 2.2  # same framing the renderer uses


def visible_faces(azimuth_deg):
    """Set of (object_name, polygon_index) hit by a ray grid from this azimuth."""
    azim = math.radians(azimuth_deg)
    elev = math.radians(ELEV)
    origin = mathutils.Vector(
        (
            center[0] + distance * math.cos(elev) * math.cos(azim),
            center[1] + distance * math.cos(elev) * math.sin(azim),
            center[2] + distance * math.sin(elev),
        )
    )
    rot = (center - origin).to_track_quat("-Z", "Y")
    forward = rot @ mathutils.Vector((0, 0, -1))
    right = rot @ mathutils.Vector((1, 0, 0))
    up = rot @ mathutils.Vector((0, 1, 0))
    # Blender's default camera: 50mm lens on a 36mm sensor.
    half = math.tan(math.atan(36.0 / (2 * 50.0)))

    hits = set()
    for iy in range(RAYS):
        v = (2 * (iy + 0.5) / RAYS - 1) * half
        for ix in range(RAYS):
            u = (2 * (ix + 0.5) / RAYS - 1) * half
            direction = (forward + u * right + v * up).normalized()
            ok, _loc, _normal, index, obj, _mat = scene.ray_cast(
                deps, origin, direction
            )
            if ok and obj is not None and index >= 0:
                hits.add((obj.name, index))
    return hits


per_view = {a: visible_faces(a) for a in AZIMUTHS}
reachable = set().union(*per_view.values())


def coverage(views):
    seen = set()
    for a in views:
        snapped = int(round(a / STEP)) * STEP
        seen |= per_view[((snapped + 180) % 360) - 180]
    return len(seen) / len(reachable)


result = {
    "model": os.path.basename(MODEL),
    "rays_per_axis": RAYS,
    "elevation_deg": ELEV,
    "source_mesh_faces": source_faces,
    "faces_reachable_any_azimuth": len(reachable),
    "single_view_-60": round(coverage([-60]), 4),
    "current_pair_-60_120": round(coverage([-60, 120]), 4),
    # Control: a view set containing the same view twice must score exactly what
    # that view scores alone. If this differs, the harness is wrong, not the data.
    "control_duplicate_view": round(coverage([-60, -60]), 4),
    "pairs_from_-60": {str(a): round(coverage([-60, a]), 4) for a in AZIMUTHS},
    "triples": {
        str(t): round(coverage(list(t)), 4)
        for t in [(-60, 60, 180), (-60, 30, 150), (-90, -30, 90), (0, 120, -120)]
    },
}

pairs = sorted(
    (
        ((a, b), coverage([a, b]))
        for i, a in enumerate(AZIMUTHS)
        for b in AZIMUTHS[i + 1 :]
    ),
    key=lambda item: -item[1],
)
result["best_pairs"] = [[list(p), round(c, 4)] for p, c in pairs[:8]]

print("RESULT_JSON " + json.dumps(result))
