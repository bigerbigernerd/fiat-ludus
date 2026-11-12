"""Fit the animation library's own skeleton into a character mesh.

    $FORGE_BLENDER -b -P pipeline/blender/fit_skeleton.py -- \
        --mesh norm.glb                 # static, normalised character (normalize_mesh.py)
        --joints rigged.glb             # any skinned/armature file whose skeleton marks the
                                        # joint positions (UniRig output, or ground truth)
        --template assets/animations/ual_standard.glb
        --weights transfer|heat         # transfer: re-assign the --joints file's skin weights
                                        #   to the template bones by label (UniRig skinning
                                        #   kept); heat: Blender automatic weights
        --out fitted.glb [--report r.json]

The template skeleton (Quaternius UAL, Rigify-style "DEF-" names) is moved joint by
joint onto the labelled landmarks of the --joints skeleton. Each template bone keeps its
own twist: its rest matrix is rotated by the shortest arc that turns the template
direction into the landmark direction. Bones without a landmark (fingers, root, spine
in-betweens) follow the nearest placed ancestor with the same rotation and scale.

The result is a mesh skinned to the library skeleton, so every library clip maps onto
it by bone name; build_character.py then only has to correct for the different rest
pose (e.g. A-pose arms), not for different bones.
"""
import argparse
import json
import os
import sys

import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import retarget as rt  # noqa: E402


def parse():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--mesh", required=True)
    p.add_argument("--joints", required=True)
    p.add_argument("--template", required=True)
    p.add_argument("--weights", choices=["transfer", "heat"], default="transfer")
    p.add_argument("--labels", default="")
    p.add_argument("--out", required=True)
    p.add_argument("--report", default="")
    p.add_argument("--fbx", default="", help="also write the fitted rig as FBX (input for UniRig's "
                                             "skin model when it should skin *this* skeleton)")
    return p.parse_args(argv)


def import_glb(path):
    before = set(bpy.data.objects)
    if path.lower().endswith(".fbx"):
        bpy.ops.import_scene.fbx(filepath=path)
    else:
        bpy.ops.import_scene.gltf(filepath=path)
    return [o for o in bpy.data.objects if o not in before]


def bake_object_transforms(objs):
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)


def fill_unweighted(ob):
    """Bone heat leaves loose parts (eyes, floating accessories) unweighted: give each such
    vertex the weights of the nearest weighted vertex. Returns how many were filled."""
    from mathutils.kdtree import KDTree
    vs = ob.data.vertices
    good = [v for v in vs if any(g.weight > 1e-4 for g in v.groups)]
    bad = [v for v in vs if not any(g.weight > 1e-4 for g in v.groups)]
    if not bad or not good:
        return len(bad)
    kd = KDTree(len(good))
    for i, v in enumerate(good):
        kd.insert(v.co, i)
    kd.balance()
    for v in bad:
        _, i, _ = kd.find(v.co)
        for g in good[i].groups:
            ob.vertex_groups[g.group].add([v.index], g.weight, "REPLACE")
    return len(bad)


def remove_all(objs):
    for o in objs:
        try:
            bpy.data.objects.remove(o, do_unlink=True)
        except ReferenceError:      # already removed (e.g. merged by a join)
            pass


def landmarks(arm, lab):
    """Joint positions (armature space) per semantic key, plus end points."""
    B = arm.data.bones
    k = rt.keyed(lab)
    pts = {key: B[n].head_local.copy() for key, n in k.items()}
    # chain end points: tail of the last bone of a chain if its tail is meaningful
    for s in rt.SIDES:
        if f"hand.{s}" in k:
            pts[f"hand_end.{s}"] = B[k[f"hand.{s}"]].tail_local.copy()
        last = f"toe.{s}" if f"toe.{s}" in k else f"foot.{s}"
        if last in k:
            pts[f"{last}_end"] = B[k[last]].tail_local.copy()
    if "head" in k:
        pts["head_end"] = B[k["head"]].tail_local.copy()
    return pts


def fit(template, tlab, src_pts):
    """Move template bones (edit mode) onto landmark points. Returns placed keys."""
    tk = rt.keyed(tlab)
    name_to_key = {v: k for k, v in tk.items()}
    nxt = rt.chain_next(tlab)
    bpy.context.view_layer.objects.active = template
    bpy.ops.object.mode_set(mode="EDIT")
    eb = template.data.edit_bones
    orig = {b.name: (b.head.copy(), b.tail.copy(), b.matrix.copy(), b.length) for b in eb}

    # target spine: resample the source spine polyline (hips -> neck) at the template's
    # fractional spine positions so the template keeps its 3 spine bones
    def src_point(key):
        return src_pts.get(key)

    xform = {}   # bone name -> (rotation 3x3, scale, old head, new head) for followers
    order = sorted(eb, key=lambda b: len(b.parent_recursive))
    placed = []
    for b in order:
        key = name_to_key.get(b.name)
        h0, t0, m0, L0 = orig[b.name]
        new_head = src_point(key) if key else None
        new_tail = None
        if key is not None and new_head is not None:
            nk = nxt.get(key)
            if nk and src_point(nk) is not None:
                new_tail = src_point(nk)
            elif key in ("head",) and "head_end" in src_pts:
                new_tail = src_pts["head_end"]
        if new_head is None:
            # follow parent transform
            p = b.parent
            while p is not None and p.name not in xform:
                p = p.parent
            if p is None:
                R, s, oh, nh = Matrix.Identity(3), 1.0, Vector(), Vector()
            else:
                R, s, oh, nh = xform[p.name]
            new_head = nh + R @ ((h0 - oh) * s)
            d = R @ ((t0 - h0) * s)
            new_tail = new_head + d
            xform[b.name] = (R, s, oh, nh) if p is not None else (R, s, oh, nh)
        else:
            placed.append(key)
            if new_tail is None or (new_tail - new_head).length < 1e-5:
                # chain end: keep template direction rotated like the parent, scaled
                p = b.parent
                R, s = (xform[p.name][0], xform[p.name][1]) if p is not None and p.name in xform else (Matrix.Identity(3), 1.0)
                new_tail = new_head + R @ ((t0 - h0) * s)
            d0 = (t0 - h0)
            d1 = (new_tail - new_head)
            R = d0.normalized().rotation_difference(d1.normalized()).to_matrix()
            s = d1.length / max(d0.length, 1e-6)
            xform[b.name] = (R, s, h0, new_head)
        # apply: keep twist by rotating the template rest matrix
        R = xform[b.name][0]
        m = (R.to_4x4() @ m0.to_3x3().to_4x4())
        m.translation = new_head
        b.use_connect = False
    # second pass (all positions known): write edit bones
    for b in order:
        R, s, oh, nh = xform[b.name]
        h0, t0, m0, L0 = orig[b.name]
        key = name_to_key.get(b.name)
        if key is not None and src_point(key) is not None:
            head = src_point(key)
        else:
            head = nh + R @ ((h0 - oh) * s) if (h0 - oh).length > 0 else nh
        m = R.to_4x4() @ m0.to_3x3().to_4x4()
        m.translation = head
        b.matrix = m
        b.length = max(L0 * s, 0.005)
    bpy.ops.object.mode_set(mode="OBJECT")
    return placed


def resample_spine(src_arm, slab, tmpl_arm, tlab, pts):
    """Give the template's spine bones landmark points spread along the source spine."""
    B = src_arm.data.bones
    chain = [B[slab["hips"]].head_local]
    chain += [B[n].head_local for n in slab.get("spine", [])]
    top = B[slab["neck"]].head_local if "neck" in slab else B[slab["head"]].head_local
    chain.append(top)
    # cumulative length along source chain
    seg = [(chain[i + 1] - chain[i]).length for i in range(len(chain) - 1)]
    total = sum(seg) or 1e-6

    def at(f):
        d = f * total
        for i, L in enumerate(seg):
            if d <= L or i == len(seg) - 1:
                return chain[i].lerp(chain[i + 1], min(1.0, d / max(L, 1e-6)))
            d -= L

    TB = tmpl_arm.data.bones
    tchain = [TB[tlab["hips"]].head_local] + [TB[n].head_local for n in tlab["spine"]]
    ttop = TB[tlab["neck"]].head_local if "neck" in tlab else TB[tlab["head"]].head_local
    tseg = [(b - a).length for a, b in zip(tchain + [ttop], (tchain + [ttop])[1:])]
    ttotal = sum(tseg)
    acc = 0.0
    for i, n in enumerate(tlab["spine"]):
        acc += tseg[i]
        pts[f"spine{i}"] = at(acc / ttotal)
    return pts


def transfer_weights(mesh_src, src_lab, src_arm, mesh_dst, tmpl, tlab):
    """Copy skin weights from the source rig to template bones, merged by label.

    Each source bone goes to the template bone with the same label; unlabelled source bones
    (extra spine, fingers, hair...) go to the template bone of their nearest labelled
    ancestor, or for spine bones to the nearest template spine bone by height."""
    sk = rt.keyed(src_lab)
    tk = rt.keyed(tlab)
    s_name_to_key = {v: k for k, v in sk.items()}
    TB = tmpl.data.bones
    tmpl_spine = tlab["spine"]

    def target_for(bone):
        b = bone
        while b is not None:
            key = s_name_to_key.get(b.name)
            if key is not None:
                if key.startswith("spine"):
                    z = bone.head_local.z
                    return min(tmpl_spine, key=lambda n: abs(TB[n].head_local.z - z))
                return tk.get(key)
            b = b.parent
        return tk["hips"]

    remap = {b.name: target_for(b) for b in src_arm.data.bones}
    # fingers: keep them on the hand (template finger bones get no weight) unless names match
    n = len(mesh_dst.data.vertices)
    if len(mesh_src.data.vertices) != n:
        raise RuntimeError("weight transfer needs the same mesh (vertex counts differ)")
    groups = {}
    sidx = {g.index: g.name for g in mesh_src.vertex_groups}
    mesh_dst.vertex_groups.clear()
    for b in tmpl.data.bones:
        groups[b.name] = mesh_dst.vertex_groups.new(name=b.name)
    for v in mesh_src.data.vertices:
        acc = {}
        for g in v.groups:
            sname = sidx.get(g.group)
            t = remap.get(sname)
            if t is None or g.weight <= 0:
                continue
            acc[t] = acc.get(t, 0.0) + g.weight
        tot = sum(acc.values())
        for t, w in acc.items():
            groups[t].add([v.index], w / tot, "REPLACE")
    return remap


def main():
    a = parse()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    rep = {"weights": a.weights}

    # landmarks from the joints file
    jobjs = import_glb(a.joints)
    jarm = [o for o in jobjs if o.type == "ARMATURE"][0]
    jmeshes = [o for o in jobjs if o.type == "MESH" and any(m.type == "ARMATURE" for m in o.modifiers)]
    if jarm.animation_data:
        jarm.animation_data.action = None
    for pb in jarm.pose.bones:
        pb.matrix_basis = Matrix.Identity(4)
    bake_object_transforms([jarm] + jmeshes)   # work in armature space == world space
    if len(jmeshes) > 1:                       # one skinned mesh object, like our input
        bpy.ops.object.select_all(action="DESELECT")
        for o in jmeshes:
            o.select_set(True)
        bpy.context.view_layer.objects.active = jmeshes[0]
        bpy.ops.object.join()
        jmeshes = [bpy.context.view_layer.objects.active]
    # Bring the joints rig into the --mesh frame (they differ when --joints is a rig made
    # at another scale, e.g. ground truth; for UniRig output this is the identity).
    mobjs = import_glb(a.mesh)
    mesh_obj = [o for o in mobjs if o.type == "MESH"][0]
    if jmeshes:
        def bbox(o):
            ps = [o.matrix_world @ v.co for v in o.data.vertices]
            return (Vector([min(p[i] for p in ps) for i in range(3)]),
                    Vector([max(p[i] for p in ps) for i in range(3)]))
        lo0, hi0 = bbox(jmeshes[0])
        lo1, hi1 = bbox(mesh_obj)
        sc = (hi1.z - lo1.z) / (hi0.z - lo0.z)
        c0 = Vector(((lo0.x + hi0.x) / 2, (lo0.y + hi0.y) / 2, lo0.z))
        c1 = Vector(((lo1.x + hi1.x) / 2, (lo1.y + hi1.y) / 2, lo1.z))
        M = Matrix.Translation(c1) @ Matrix.Diagonal((sc, sc, sc, 1)) @ Matrix.Translation(-c0)
        if abs(sc - 1) > 1e-4 or (c1 - c0).length > 1e-4:
            jarm.matrix_world = M @ jarm.matrix_world
            for o in jmeshes:
                if o.parent != jarm:
                    o.matrix_world = M @ o.matrix_world
            bpy.context.view_layer.update()
            bake_object_transforms([jarm] + jmeshes)
            rep["joints_rescaled"] = round(sc, 5)
    if a.labels:
        slab = json.load(open(a.labels))
    else:
        slab = rt.label_by_names(jarm) or rt.label_by_topology(jarm)
    yaw = rt.facing_correction(jarm, slab)
    if yaw:
        # feet do not point to -Y: turn joints rig and mesh so left/right labels are right
        import math
        R = Matrix.Rotation(math.radians(yaw), 4, "Z")
        for o in [jarm] + jmeshes + [mesh_obj]:
            if o.parent is None:
                o.matrix_world = R @ o.matrix_world
        bpy.context.view_layer.update()
        bake_object_transforms([jarm] + jmeshes)
        bake_object_transforms([mesh_obj])
        slab = json.load(open(a.labels)) if a.labels else (rt.label_by_names(jarm) or rt.label_by_topology(jarm))
        rep["turned_deg"] = yaw
    rep["source_labels"] = slab
    rep["source_bones"] = len(jarm.data.bones)
    pts = landmarks(jarm, slab)

    # template
    tobjs = import_glb(a.template)
    tmpl = [o for o in tobjs if o.type == "ARMATURE"][0]
    for o in tobjs:
        if o is not tmpl:
            bpy.data.objects.remove(o, do_unlink=True)
    if tmpl.animation_data:
        tmpl.animation_data.action = None
        for t in list(tmpl.animation_data.nla_tracks):
            tmpl.animation_data.nla_tracks.remove(t)
    for pb in tmpl.pose.bones:
        pb.matrix_basis = Matrix.Identity(4)
        pb.custom_shape = None        # UAL uses an icosphere bone shape; don't export it
    for o in list(bpy.data.objects):
        if o.type == "MESH" and o.users_collection == () and o.name.startswith("Icosphere"):
            bpy.data.objects.remove(o)
    tlab = rt.label_by_names(tmpl)
    pts = resample_spine(jarm, slab, tmpl, tlab, pts)
    # Clear the source spine keys (they index the *source* spine); resample_spine wrote
    # template-indexed spine keys over them.
    placed = fit(tmpl, tlab, pts)
    rep["placed"] = sorted(set(placed))
    tmpl.name = "Armature"

    # mesh
    if a.weights == "transfer":
        if not jmeshes:
            raise SystemExit("--weights transfer needs a skinned mesh in --joints")
        mesh = jmeshes[0]
        dst = mesh.copy()
        dst.data = mesh.data.copy()
        bpy.context.scene.collection.objects.link(dst)
        for m in list(dst.modifiers):
            dst.modifiers.remove(m)
        dst.parent = None
        rep["remap"] = transfer_weights(mesh, slab, jarm, dst, tmpl, tlab)
        remove_all(jobjs)
        bpy.data.objects.remove(mesh_obj, do_unlink=True)
        mod = dst.modifiers.new("Armature", "ARMATURE")
        mod.object = tmpl
        dst.parent = tmpl
        mesh_obj = dst
    else:
        remove_all(jobjs)
        # The glTF importer splits vertices along UV seams; bone heat sees those as
        # separate pieces, weights them differently and the seams tear open when posed.
        bpy.ops.object.select_all(action="DESELECT")
        mesh_obj.select_set(True)
        bpy.context.view_layer.objects.active = mesh_obj
        nv = len(mesh_obj.data.vertices)
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.remove_doubles(threshold=1e-5)
        bpy.ops.object.mode_set(mode="OBJECT")
        rep["welded_vertices"] = nv - len(mesh_obj.data.vertices)
        tmpl.select_set(True)
        bpy.context.view_layer.objects.active = tmpl
        bpy.ops.object.parent_set(type="ARMATURE_AUTO")
        rep["heat_unweighted_vertices"] = fill_unweighted(mesh_obj)
    mesh_obj.name = "Character"

    bpy.ops.object.select_all(action="DESELECT")
    tmpl.select_set(True)
    mesh_obj.select_set(True)
    if a.fbx:
        bpy.ops.export_scene.fbx(filepath=a.fbx, use_selection=True, add_leaf_bones=False,
                                 bake_anim=False, axis_forward="-Z", axis_up="Y")
    bpy.ops.export_scene.gltf(filepath=a.out, export_format="GLB", use_selection=True,
                              export_animations=False, export_skins=True, export_yup=True)
    rep["bones"] = len(tmpl.data.bones)
    print("FIT " + json.dumps(rep))
    if a.report:
        json.dump(rep, open(a.report, "w"), indent=1)


main()
