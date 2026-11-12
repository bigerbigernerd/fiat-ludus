"""Render a contact sheet of a GLB so a person or an agent can judge it by eye.

Runs inside Blender:

    "$FORGE_BLENDER" -b --python pipeline/blender/render_preview.py -- \
        --in asset.glb --out preview.png [--views 4] [--size 512] [--engine eevee|cycles|workbench]
        [--angles -40,0,40] [--top 0.17]

Renders the model from evenly spaced angles around the vertical axis (first
view = front, i.e. looking along +Y in Blender, which is -Z in the glTF file)
and stitches the views into one row. Neutral light, neutral background: this is
an inspection render, not a beauty shot. --angles gives explicit view angles in
degrees instead (0 = front, positive = the camera moves to the model's left), and
--top frames only the top fraction of the model, e.g. 0.17 for a character's face.
"""

import argparse
import json
import math
import os
import sys
import tempfile
import time

import bpy
from mathutils import Vector

ENGINES = {"eevee": "BLENDER_EEVEE", "cycles": "CYCLES", "workbench": "BLENDER_WORKBENCH"}


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser(prog="render_preview.py")
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--views", type=int, default=4)
    ap.add_argument("--size", type=int, default=512, help="pixels per view")
    ap.add_argument("--engine", default="eevee", choices=sorted(ENGINES))
    ap.add_argument("--elevation", type=float, default=15.0, help="camera elevation in degrees")
    ap.add_argument("--frame", type=int, help="animation frame to show (rigged assets)")
    ap.add_argument("--angles", help="comma-separated view angles in degrees; overrides --views")
    ap.add_argument("--cull", action="store_true",
                    help="hide back faces, as a game engine does; inside-out triangles then show as holes")
    ap.add_argument("--top", type=float, help="frame only this top fraction of the model's height")
    ap.add_argument("--pbr", action="store_true",
                    help="render the file's metallic/roughness as authored; by default only base colour is shown, "
                         "which is what the game's toon shader uses")
    return ap.parse_args(argv)


def scene_bounds():
    lo = Vector((math.inf,) * 3)
    hi = Vector((-math.inf,) * 3)
    deps = bpy.context.evaluated_depsgraph_get()
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        eval_obj = obj.evaluated_get(deps)
        for corner in eval_obj.bound_box:
            p = eval_obj.matrix_world @ Vector(corner)
            lo = Vector(map(min, lo, p))
            hi = Vector(map(max, hi, p))
    return lo, hi


def main():
    args = parse_args()
    t0 = time.time()

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=args.src)
    scene = bpy.context.scene
    if args.frame is not None:
        scene.frame_set(args.frame)

    if not args.pbr:
        # Generators often predict a half-metallic surface for a cartoon; under a
        # neutral light that renders much darker than the colours actually stored.
        for material in bpy.data.materials:
            if not material.use_nodes:
                continue
            for node in material.node_tree.nodes:
                if node.type != "BSDF_PRINCIPLED":
                    continue
                for name, value in (("Metallic", 0.0), ("Roughness", 1.0)):
                    socket = node.inputs[name]
                    for link in list(socket.links):
                        material.node_tree.links.remove(link)
                    socket.default_value = value

    if args.cull:
        for material in bpy.data.materials:
            material.use_backface_culling = True

    lo, hi = scene_bounds()
    centre = (lo + hi) / 2
    radius = max((hi - lo).length / 2, 1e-6)
    if args.top:
        part = (hi.z - lo.z) * args.top
        centre = Vector((centre.x, centre.y, hi.z - part / 2))
        radius = part * 0.75

    world = bpy.data.worlds.new("preview")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (0.78, 0.80, 0.84, 1.0)
    world.node_tree.nodes["Background"].inputs[1].default_value = 0.9
    scene.world = world

    key = bpy.data.objects.new("key", bpy.data.lights.new("key", "SUN"))
    key.data.energy = 3.0
    key.rotation_euler = (math.radians(50), 0, math.radians(35))
    scene.collection.objects.link(key)

    cam_data = bpy.data.cameras.new("cam")
    cam_data.lens = 85
    cam = bpy.data.objects.new("cam", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam

    scene.render.engine = ENGINES[args.engine]
    if args.engine == "cycles":
        scene.cycles.samples = 32
        scene.cycles.device = "CPU"
    if args.engine == "workbench":
        scene.display.shading.color_type = "TEXTURE"
        scene.display.shading.light = "STUDIO"
    scene.render.resolution_x = scene.render.resolution_y = args.size
    scene.render.image_settings.file_format = "PNG"
    scene.view_settings.view_transform = "Standard"

    fov = 2 * math.atan(cam_data.sensor_width / (2 * cam_data.lens))
    distance = radius / math.sin(fov / 2) * 1.08
    elevation = math.radians(args.elevation)

    tmp = tempfile.mkdtemp(prefix="preview_")
    tiles = []
    if args.angles:
        angles = [-math.pi / 2 + math.radians(float(a)) for a in args.angles.split(",")]
    else:
        angles = [-math.pi / 2 + 2 * math.pi * i / args.views for i in range(args.views)]
    for i, angle in enumerate(angles):
        # View 0 looks at the model's front: camera on the -Y side in Blender.
        cam.location = centre + Vector((
            distance * math.cos(elevation) * math.cos(angle),
            distance * math.cos(elevation) * math.sin(angle),
            distance * math.sin(elevation),
        ))
        cam.rotation_euler = (centre - cam.location).to_track_quat("-Z", "Y").to_euler()
        scene.render.filepath = os.path.join(tmp, f"view{i}.png")
        bpy.ops.render.render(write_still=True)
        tiles.append(scene.render.filepath)

    # Stitch with Blender's own image API; no extra Python packages needed.
    s = args.size
    sheet = bpy.data.images.new("sheet", s * len(tiles), s, alpha=True)
    canvas = [0.0] * (s * len(tiles) * s * 4)
    for i, path in enumerate(tiles):
        tile = bpy.data.images.load(path)
        px = list(tile.pixels)
        for row in range(s):
            src = row * s * 4
            dst = (row * s * len(tiles) + i * s) * 4
            canvas[dst:dst + s * 4] = px[src:src + s * 4]
    sheet.pixels = canvas
    sheet.filepath_raw = args.out
    sheet.file_format = "PNG"
    sheet.save()

    print(json.dumps({
        "stage": "preview", "in": args.src, "out": args.out, "views": len(tiles),
        "engine": args.engine, "bounds_m": [round(v, 3) for v in (hi - lo)],
        "seconds": round(time.time() - t0, 2),
    }))


if __name__ == "__main__":
    main()
