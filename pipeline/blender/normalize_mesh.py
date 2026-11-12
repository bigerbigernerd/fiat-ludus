"""Normalise a static character mesh before rigging.

    $FORGE_BLENDER -b -P pipeline/blender/normalize_mesh.py -- --in in.glb --out norm.glb \
        [--height 1.7] [--yaw 0] [--report report.json]

- joins every mesh into one object and applies all transforms (armatures and
  animation in the input are discarded),
- rotates by --yaw degrees about the vertical axis (use it when the model does
  not face glTF +Z),
- scales so the mesh is --height units tall, puts the feet at the origin and
  centres it horizontally.

Output stays glTF Y-up, character facing +Z (Blender -Y).
"""
import argparse
import json
import math
import sys

import bpy
from mathutils import Matrix, Vector


def parse():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="inp", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--height", type=float, default=1.7)
    p.add_argument("--yaw", type=float, default=0.0)
    p.add_argument("--report", default="")
    return p.parse_args(argv)


def main():
    a = parse()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if a.inp.lower().endswith((".glb", ".gltf")):
        bpy.ops.import_scene.gltf(filepath=a.inp)
    elif a.inp.lower().endswith(".obj"):
        bpy.ops.wm.obj_import(filepath=a.inp)
    elif a.inp.lower().endswith(".fbx"):
        bpy.ops.import_scene.fbx(filepath=a.inp)
    else:
        raise SystemExit(f"unsupported input: {a.inp}")

    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not meshes:
        raise SystemExit("no mesh in input")
    for o in meshes:
        for m in list(o.modifiers):
            o.modifiers.remove(m)
        o.vertex_groups.clear()
        o.shape_key_clear()
        mw = o.matrix_world.copy()
        o.parent = None
        o.matrix_world = mw
    for o in list(bpy.context.scene.objects):
        if o.type != "MESH":
            bpy.data.objects.remove(o, do_unlink=True)
    bpy.ops.object.select_all(action="DESELECT")
    for o in meshes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1:
        bpy.ops.object.join()
    ob = bpy.context.view_layer.objects.active
    ob.name = "Character"
    ob.data.name = "Character"
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

    me = ob.data
    # Weld vertices that glTF split at UV/normal seams so the surface is one connected
    # piece again; otherwise seam vertices can get different skin weights and crack.
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    zs0 = [v.co.z for v in me.vertices]
    weld = 1e-4 * (max(zs0) - min(zs0))       # 0.17 mm on a 1.7 m character
    bpy.ops.mesh.remove_doubles(threshold=weld)
    bpy.ops.object.mode_set(mode="OBJECT")
    if a.yaw:
        me.transform(Matrix.Rotation(math.radians(a.yaw), 4, "Z"))
    zs = [v.co.z for v in me.vertices]
    lo_z, hi_z = min(zs), max(zs)
    h0 = hi_z - lo_z
    s = a.height / h0
    xs = [v.co.x for v in me.vertices]
    ys = [v.co.y for v in me.vertices]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    me.transform(Matrix.Diagonal((s, s, s, 1)) @ Matrix.Translation((-cx, -cy, -lo_z)))
    me.update()

    bpy.ops.export_scene.gltf(filepath=a.out, export_format="GLB", use_selection=False,
                              export_animations=False, export_skins=False, export_yup=True)
    tris = sum(len(p.vertices) - 2 for p in me.polygons)
    rep = {"verts": len(me.vertices), "weld_threshold": round(weld, 6), "tris": tris, "materials": len(me.materials),
           "input_height": round(h0, 4), "scale": round(s, 5),
           "uv_layers": len(me.uv_layers)}
    print("NORMALIZE " + json.dumps(rep))
    if a.report:
        with open(a.report, "w") as f:
            json.dump(rep, f)


main()
