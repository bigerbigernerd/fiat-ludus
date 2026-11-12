"""Check a rigged GLB/FBX and render it in a few non-rest poses.

    $FORGE_BLENDER -b -P pipeline/blender/verify_rig.py -- --in rigged.glb \
        [--stills out_prefix] [--report report.json] [--engine CYCLES]

Checks: one armature; bone count and names; every mesh vertex has weights; weights sum
to 1 (after the 4-influence limit glTF applies); materials and image textures present.
Stills: rest, "arms" (upper arms raised 45 deg, forearms swung 60 deg forward), "knee" (left
thigh raised 60 deg, shin bent 70 deg, chest twisted 20 deg, right arm raised 45 deg), each from the front and from
three-quarter view, written as <prefix>_<pose>.png (two views side by side).
"""
import argparse
import json
import math
import os
import sys

import bpy
from mathutils import Matrix, Quaternion, Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render_util as ru  # noqa: E402
import retarget as rt  # noqa: E402


def parse():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="inp", required=True)
    p.add_argument("--stills", default="")
    p.add_argument("--report", default="")
    p.add_argument("--engine", default="CYCLES")
    return p.parse_args(argv)


def weight_stats(ob):
    gi = {g.index: g.name for g in ob.vertex_groups}
    n = len(ob.data.vertices)
    no_w = 0
    bad_sum = 0
    max_inf = 0
    sums = []
    for v in ob.data.vertices:
        ws = sorted((g.weight for g in v.groups if g.weight > 0), reverse=True)
        max_inf = max(max_inf, len(ws))
        if not ws:
            no_w += 1
            continue
        s4 = sum(ws[:4])
        s = sum(ws)
        sums.append(s)
        if abs(s - 1.0) > 1e-3:
            bad_sum += 1
    return {"vertices": n, "unweighted": no_w, "sum_not_1": bad_sum,
            "max_influences": max_inf,
            "min_sum": round(min(sums), 4) if sums else None,
            "max_sum": round(max(sums), 4) if sums else None}


def rotate_world(arm, bone, axis, deg):
    """Rotate pose bone about a world axis through its head (applied on top of current pose)."""
    pb = arm.pose.bones[bone]
    bpy.context.view_layer.update()
    M = pb.matrix.copy()
    R = Matrix.Rotation(math.radians(deg), 4, axis)
    head = M.translation.copy()
    newM = Matrix.Translation(head) @ R @ Matrix.Translation(-head) @ M
    pb.matrix = newM
    bpy.context.view_layer.update()


def main():
    a = parse()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if a.inp.lower().endswith(".fbx"):
        bpy.ops.import_scene.fbx(filepath=a.inp)
    else:
        bpy.ops.import_scene.gltf(filepath=a.inp)
    objs = list(bpy.context.scene.objects)
    arms = [o for o in objs if o.type == "ARMATURE"]
    meshes = [o for o in objs if o.type == "MESH"]
    rep = {"file": os.path.basename(a.inp), "armatures": len(arms), "meshes": len(meshes)}
    if len(arms) != 1:
        rep["error"] = "expected exactly one armature"
        print("VERIFY " + json.dumps(rep))
        sys.exit(1)
    arm = arms[0]
    if arm.animation_data:
        arm.animation_data.action = None
    rep["bones"] = len(arm.data.bones)
    rep["bone_names"] = [b.name for b in arm.data.bones]
    rep["skinned_meshes"] = []
    for m in meshes:
        has_mod = any(md.type == "ARMATURE" and md.object == arm for md in m.modifiers)
        st = weight_stats(m)
        st["name"] = m.name
        st["armature_modifier"] = has_mod
        unknown = [g.name for g in m.vertex_groups if g.name not in arm.data.bones]
        st["groups_without_bone"] = len(unknown)
        mats = []
        for mat in m.data.materials:
            if mat is None:
                continue
            imgs = []
            if mat.node_tree:
                for n in mat.node_tree.nodes:
                    if n.type == "TEX_IMAGE" and n.image is not None:
                        imgs.append({"name": n.image.name, "size": list(n.image.size)})
            mats.append({"name": mat.name, "images": imgs})
        st["materials"] = mats
        rep["skinned_meshes"].append(st)
    lab = rt.label_by_names(arm)
    how = "names"
    if lab is None:
        try:
            lab = rt.label_by_topology(arm)
            how = "topology"
        except ValueError as e:
            lab, how = None, f"failed: {e}"
    rep["labels_from"] = how
    rep["labels"] = lab

    if a.stills:
        ru.setup_render(a.engine, res=(420, 520))
        skinned = [m for m in meshes if any(md.type == "ARMATURE" for md in m.modifiers)] or meshes
        for m in meshes:
            if m not in skinned:
                m.hide_render = True
        lo, hi = ru.world_bbox(skinned)
        span = hi.z - lo.z
        lo2 = lo - Vector((0.2, 0.2, 0.02)) * span
        hi2 = hi + Vector((0.2, 0.2, 0.05)) * span
        poses = ["rest"]
        if lab:
            poses += ["arms", "knee"]
        for pose in poses:
            for pb in arm.pose.bones:
                pb.matrix_basis = Matrix.Identity(4)
            bpy.context.view_layer.update()
            if pose == "arms":
                for s, sign in (("L", 1), ("R", -1)):
                    # raise the upper arm 45 deg (about the forward axis Y) and swing the
                    # forearm 60 deg forward; works for T- and A-pose rest poses alike
                    rotate_world(arm, lab[f"upper_arm.{s}"], "Y", -45 * sign)
                    rotate_world(arm, lab[f"forearm.{s}"], "Z", -60 * sign)
            elif pose == "knee":
                rotate_world(arm, lab["thigh.L"], "X", 60)
                rotate_world(arm, lab["shin.L"], "X", -70)
                sp = lab.get("spine") or []
                if sp:
                    rotate_world(arm, sp[-1], "Z", 20)
                rotate_world(arm, lab["upper_arm.R"], "Y", 45)
            files = []
            for yaw in (0, 40):
                ru.frame_camera(lo2, hi2, yaw_deg=yaw, margin=1.0)
                fp = f"{a.stills}_{pose}_{yaw}.png"
                ru.render_to(fp)
                files.append(fp)
            _hstack(files, f"{a.stills}_{pose}.png")
        rep["stills"] = [f"{os.path.basename(a.stills)}_{p}.png" for p in poses]
    print("VERIFY " + json.dumps(rep))
    if a.report:
        json.dump(rep, open(a.report, "w"), indent=1)


def _hstack(files, out):
    import numpy as np
    arrs = []
    for f in files:
        img = bpy.data.images.load(f)
        w, h = img.size
        arrs.append(np.array(img.pixels[:], dtype=np.float32).reshape(h, w, 4))
        bpy.data.images.remove(img)
        os.remove(f)
    s = np.concatenate(arrs, axis=1)
    h, w = s.shape[:2]
    im = bpy.data.images.new("stack", w, h, alpha=True)
    im.pixels.foreach_set(s.ravel())
    im.filepath_raw = out
    im.file_format = "PNG"
    im.save()


main()
