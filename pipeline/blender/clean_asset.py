"""Stage 3: turn a raw generated mesh into a game-ready static asset.

Runs inside Blender:

    "$FORGE_BLENDER" -b --python pipeline/blender/clean_asset.py -- \
        --in raw.glb --out clean.glb --height 2.0 --faces 20000 --texture-size 1024 [--yaw 90]

What it does, in order:
  1. imports the GLB into an empty scene and joins all meshes into one object;
  2. welds duplicate vertices and drops loose geometry;
  3. decimates to at most --faces triangles (generators emit 100k-1M);
  4. puts the base centre at the origin and scales to --height (Blender units = metres);
  5. makes triangle facing consistent and marks the material double-sided when
     the mesh is not a closed surface (open leaf sheets would otherwise vanish
     from behind in a game engine);
  6. smooths shading, shrinks textures to --texture-size;
  7. exports GLB (Y-up, as glTF requires) and optionally saves the .blend for hand editing.

Prints one JSON line with before/after statistics. Static assets only: a file
with an armature is rejected, because decimating a skinned mesh here would
silently damage its weights (rigged characters go through the rig stage).
"""

import argparse
import json
import sys
import time

import bmesh
import bpy
from mathutils import Vector


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser(prog="clean_asset.py")
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--height", type=float, help="final height in metres; omit to keep the scale")
    ap.add_argument("--faces", type=int, default=20000, help="triangle budget; 0 keeps all")
    ap.add_argument("--texture-size", type=int, default=1024, help="longest texture side; 0 keeps all")
    ap.add_argument("--yaw", type=float, default=0.0,
                    help="turn the model about the vertical axis first, degrees, counter-clockwise seen from above")
    ap.add_argument("--weld", type=float, default=1e-5, help="merge distance, relative to object size")
    ap.add_argument("--sides", default="auto", choices=["auto", "single", "double"],
                    help="draw one or both sides of each triangle; auto = double when the mesh is not closed")
    ap.add_argument("--open-edges", type=float, default=0.005,
                    help="auto: fraction of edges without two faces above which the mesh counts as open")
    ap.add_argument("--image-format", default="WEBP", choices=["AUTO", "JPEG", "WEBP"])
    ap.add_argument("--save-blend", help="also save an editable .blend here")
    return ap.parse_args(argv)


def mesh_stats(obj):
    mesh = obj.data
    mesh.calc_loop_triangles()
    return {"vertices": len(mesh.vertices), "triangles": len(mesh.loop_triangles)}


def world_bounds(obj):
    corners = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
    lo = Vector((min(c.x for c in corners), min(c.y for c in corners), min(c.z for c in corners)))
    hi = Vector((max(c.x for c in corners), max(c.y for c in corners), max(c.z for c in corners)))
    return lo, hi


def main():
    args = parse_args()
    t0 = time.time()

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=args.src)

    if any(o.type == "ARMATURE" for o in bpy.context.scene.objects):
        print(json.dumps({"stage": "clean", "error": "input has an armature; use the rig stage"}))
        sys.exit(2)

    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not meshes:
        print(json.dumps({"stage": "clean", "error": "no mesh in input"}))
        sys.exit(2)

    # One object, transforms baked in, so every later step works in world space.
    bpy.ops.object.select_all(action="DESELECT")
    for o in meshes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1:
        bpy.ops.object.join()
    obj = bpy.context.view_layer.objects.active
    bpy.ops.object.parent_clear(type="CLEAR_KEEP_TRANSFORM")
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    for other in [o for o in bpy.context.scene.objects if o is not obj]:
        bpy.data.objects.remove(other, do_unlink=True)

    if args.yaw:
        import math
        obj.rotation_mode = "XYZ"      # glTF import leaves objects in quaternion mode
        obj.rotation_euler = (0.0, 0.0, math.radians(args.yaw))
        bpy.ops.object.transform_apply(rotation=True)

    before = mesh_stats(obj)
    lo, hi = world_bounds(obj)
    size = max((hi - lo).length, 1e-9)

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=args.weld * size)
    loose = [v for v in bm.verts if not v.link_faces]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context="VERTS")
    # A game engine draws one side of each triangle. That only works for a closed
    # surface whose triangles all face outwards. Generated meshes are often open
    # sheets (leaves, torn panels): count the edges that have a triangle on one
    # side only, and mark such a mesh double-sided instead.
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    open_edges = sum(1 for e in bm.edges if len(e.link_faces) != 2)
    open_ratio = open_edges / max(len(bm.edges), 1)
    double_sided = open_ratio > args.open_edges if args.sides == "auto" else args.sides == "double"
    bm.to_mesh(obj.data)
    bm.free()
    for material in obj.data.materials:
        if material:
            material.use_backface_culling = not double_sided      # exported as glTF doubleSided

    welded = mesh_stats(obj)
    if args.faces and welded["triangles"] > args.faces:
        mod = obj.modifiers.new("decimate", "DECIMATE")
        mod.decimate_type = "COLLAPSE"
        mod.ratio = args.faces / welded["triangles"]
        mod.use_collapse_triangulate = True
        bpy.ops.object.modifier_apply(modifier=mod.name)

    # Base centre to the origin, then scale about the origin.
    lo, hi = world_bounds(obj)
    obj.location -= Vector(((lo.x + hi.x) / 2, (lo.y + hi.y) / 2, lo.z))
    bpy.ops.object.transform_apply(location=True)
    height_before = hi.z - lo.z
    if args.height and height_before > 1e-9:
        factor = args.height / height_before
        obj.scale = (factor, factor, factor)
        bpy.ops.object.transform_apply(scale=True)

    bpy.ops.object.shade_smooth()

    textures = []
    for image in bpy.data.images:
        if image.size[0] == 0 or image.type != "IMAGE":
            continue
        w, h = image.size
        entry = {"name": image.name, "from": [w, h]}
        longest = max(w, h)
        if args.texture_size and longest > args.texture_size:
            k = args.texture_size / longest
            image.scale(max(1, round(w * k)), max(1, round(h * k)))
        entry["to"] = list(image.size)
        textures.append(entry)

    if args.save_blend:
        bpy.ops.file.pack_all()
        bpy.ops.wm.save_as_mainfile(filepath=args.save_blend)

    bpy.ops.export_scene.gltf(
        filepath=args.out,
        export_format="GLB",
        export_yup=True,
        export_apply=True,
        export_image_format=args.image_format,
        export_animations=False,
    )

    lo, hi = world_bounds(obj)
    import os
    print(json.dumps({
        "stage": "clean",
        "in": args.src, "out": args.out,
        "before": before, "after": mesh_stats(obj),
        "size_m": [round(v, 3) for v in (hi - lo)],
        "textures": textures,
        "materials": len(obj.data.materials),
        "open_edge_ratio": round(open_ratio, 4), "double_sided": double_sided,
        "bytes": os.path.getsize(args.out),
        "seconds": round(time.time() - t0, 2),
        "blender": bpy.app.version_string,
    }))


if __name__ == "__main__":
    main()
