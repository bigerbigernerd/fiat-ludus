"""Assemble the final game character: rigged mesh + retargeted clips -> one GLB.

    $FORGE_BLENDER -b -P pipeline/blender/build_character.py -- \
        --rigged rigged.glb          # mesh already skinned to some skeleton (UniRig merge output,
                                     # fitted library skeleton, or a hand-made rig)
        --anim assets/animations/ual_standard.glb --clips assets/animations/clips.json \
        --out character.glb [--height 1.7] [--sheet sheet.png] [--report report.json]
        [--labels labels.json]       # optional fixed bone labels for the target skeleton

Steps: import rigged GLB -> label its bones (by name, else by topology) -> import the
animation library -> retarget each clip (retarget.py) -> keep only the target
armature + mesh -> scale so the rest pose is --height tall with feet on the
origin -> one NLA track per clip -> export GLB (Y-up, character facing +Z).
"""
import argparse
import json
import math
import os
import sys
import time

import bpy
from mathutils import Matrix, Vector

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import retarget as rt  # noqa: E402
import render_util as ru  # noqa: E402


def parse():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--rigged", required=True)
    p.add_argument("--anim", required=True)
    p.add_argument("--clips", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--height", type=float, default=1.7)
    p.add_argument("--fps", type=int, default=30)
    p.add_argument("--sheet", default="")
    p.add_argument("--report", default="")
    p.add_argument("--labels", default="")
    p.add_argument("--image-format", default="WEBP", choices=["AUTO", "JPEG", "WEBP"],
                   help="texture encoding in the GLB; WEBP keeps a 2K character texture under 1 MB")
    return p.parse_args(argv)


def import_glb(path):
    before = set(bpy.data.objects)
    acts_before = set(bpy.data.actions)
    bpy.ops.import_scene.gltf(filepath=path)
    new = [o for o in bpy.data.objects if o not in before]
    acts = [a for a in bpy.data.actions if a not in acts_before]
    return new, acts


def clear_anim(obj):
    if obj.animation_data:
        obj.animation_data.action = None
        for t in list(obj.animation_data.nla_tracks):
            obj.animation_data.nla_tracks.remove(t)


def reset_pose(arm):
    for pb in arm.pose.bones:
        pb.matrix_basis = Matrix.Identity(4)


def apply_object_transform(arm, meshes):
    """Bake armature-object transform into rest pose so the armature object is identity."""
    if arm.matrix_world == Matrix.Identity(4):
        return
    bpy.ops.object.select_all(action="DESELECT")
    for o in [arm] + meshes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)


def normalise_height(arm, meshes, height):
    """Scale + move so the rest-pose mesh is `height` tall, feet at z=0, centred in x/y."""
    pts = []
    for o in meshes:
        mw = o.matrix_world
        pts += [mw @ v.co for v in o.data.vertices]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    s = height / (hi.z - lo.z)
    M = Matrix.Diagonal((s, s, s, 1)) @ Matrix.Translation((-(lo.x + hi.x) / 2, -(lo.y + hi.y) / 2, -lo.z))
    arm.matrix_world = M @ arm.matrix_world
    for o in meshes:
        if o.parent != arm:
            o.matrix_world = M @ o.matrix_world
    bpy.context.view_layer.update()
    apply_object_transform(arm, meshes)
    return s


def build_clip_action(src, tgt, src_lab, tgt_lab, spec, name, fps_scale):
    """spec: {"source": ["A", "B", ...], "loop": bool, "root_motion": bool}"""
    act = bpy.data.actions.new(name)
    offset = 0
    for part in spec["source"]:
        sa = bpy.data.actions.get(part)
        if sa is None:
            raise RuntimeError(f"animation '{part}' not in library")
        f0, f1 = (int(round(x)) for x in sa.frame_range)
        rt.retarget_action(src, tgt, src_lab, tgt_lab, sa, (f0, f1), name,
                           root_motion=spec.get("root_motion", False),
                           frame_offset=offset - f0, dst_action=act)
        offset += (f1 - f0) + 1
    return act, offset


def main():
    a = parse()
    t0 = time.time()
    warnings = []
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.render.fps = a.fps

    # --- target character -------------------------------------------------------------
    objs, tacts = import_glb(a.rigged)
    arms = [o for o in objs if o.type == "ARMATURE"]
    if len(arms) != 1:
        raise SystemExit(f"expected one armature in {a.rigged}, found {len(arms)}")
    tgt = arms[0]
    tgt.name = "Armature"
    meshes = [o for o in objs if o.type == "MESH" and any(m.type == "ARMATURE" for m in o.modifiers)]
    for o in objs:
        if o.type not in ("ARMATURE",) and o not in meshes:
            bpy.data.objects.remove(o, do_unlink=True)
    for ac in tacts:
        bpy.data.actions.remove(ac)
    clear_anim(tgt)
    reset_pose(tgt)
    apply_object_transform(tgt, meshes)
    scale = normalise_height(tgt, meshes, a.height)

    def label():
        if a.labels:
            with open(a.labels) as f:
                return json.load(f), "file"
        lab = rt.label_by_names(tgt)
        if lab is not None:
            return lab, "names"
        return rt.label_by_topology(tgt), "topology"

    tgt_lab, how = label()
    # Facing check: toes point where the character looks. Everything downstream assumes
    # Blender -Y (glTF +Z). If the feet point elsewhere, turn the rig in 90 degree steps.
    yaw = rt.facing_correction(tgt, tgt_lab)
    if yaw:
        warnings.append(f"character did not face +Z; turned it by {yaw} degrees (from its feet)")
        R = Matrix.Rotation(math.radians(yaw), 4, "Z")
        tgt.matrix_world = R @ tgt.matrix_world
        for o in meshes:
            if o.parent != tgt:
                o.matrix_world = R @ o.matrix_world
        bpy.context.view_layer.update()
        apply_object_transform(tgt, meshes)
        tgt_lab, how = label()

    # --- animation library -----------------------------------------------------------
    with open(a.clips) as f:
        clips = json.load(f)["clips"]
    sobjs, sacts = import_glb(a.anim)
    src = [o for o in sobjs if o.type == "ARMATURE"][0]
    src.name = "LibraryRig"
    for o in sobjs:
        if o.type != "ARMATURE":
            bpy.data.objects.remove(o, do_unlink=True)
    clear_anim(src)
    src_lab = rt.label_by_names(src)
    if src_lab is None:
        raise SystemExit("could not label the animation library skeleton by names")
    pairs = rt.pair_labels(src_lab, tgt_lab)
    unmapped = [b.name for b in tgt.data.bones if b.name not in {p[0] for p in pairs}]

    # --- retarget ----------------------------------------------------------------------
    made = []
    for cname, spec in clips.items():
        act, nframes = build_clip_action(src, tgt, src_lab, tgt_lab, spec, cname, 1.0)
        made.append((cname, act, nframes))
    tgt.animation_data.action = None
    bpy.data.objects.remove(src, do_unlink=True)
    for ac in sacts:
        if ac.users == 0:
            bpy.data.actions.remove(ac)

    # one NLA track per clip, muted, so the exporter writes them as separate animations
    ad = tgt.animation_data
    for cname, act, n in made:
        tr = ad.nla_tracks.new()
        tr.name = cname
        st = tr.strips.new(cname, 0, act)
        st.name = cname
        if hasattr(st, "action_slot") and act.slots:
            st.action_slot = act.slots[0]
        tr.mute = True
    ad.action = None
    reset_pose(tgt)
    sc.frame_set(0)

    # --- export ------------------------------------------------------------------------
    bpy.ops.object.select_all(action="DESELECT")
    for o in [tgt] + meshes:
        o.select_set(True)
    bpy.ops.export_scene.gltf(
        filepath=a.out, export_format="GLB", use_selection=True, export_yup=True,
        export_animations=True, export_animation_mode="NLA_TRACKS", export_skins=True,
        export_def_bones=False, export_anim_slide_to_zero=True, export_force_sampling=True,
        export_optimize_animation_size=True, export_reset_pose_bones=True,
        export_image_format=a.image_format)

    rep = {
        "out": os.path.basename(a.out),
        "bones": len(tgt.data.bones),
        "labels_from": how,
        "labels": tgt_lab,
        "mapped_bones": len(pairs),
        "unmapped_bones": len(unmapped),
        "clips": {c: round((n - 1) / a.fps, 3) for c, _, n in made},
        "scale_applied": round(scale, 5),
        "seconds": round(time.time() - t0, 1),
        "warnings": warnings,
    }

    # --- contact sheet -------------------------------------------------------------------
    if a.sheet:
        rep["sheet"] = os.path.basename(a.sheet)
        contact_sheet(tgt, meshes, made, a.sheet)
    print("BUILD " + json.dumps(rep))
    if a.report:
        with open(a.report, "w") as f:
            json.dump(rep, f, indent=1)


def contact_sheet(arm, meshes, made, path, per_clip=5, res=(220, 300)):
    import numpy as np
    sc = bpy.context.scene
    ru.setup_render("CYCLES", res=res)
    sc.cycles.samples = 8
    ad = arm.animation_data
    for tr in ad.nla_tracks:
        tr.mute = True
    lo, hi = ru.world_bbox(meshes)
    span = max(hi.z - lo.z, 1e-3)
    lo2 = Vector((lo.x - 0.3 * span, lo.y - 0.3 * span, lo.z - 0.05 * span))
    hi2 = Vector((hi.x + 0.3 * span, hi.y + 0.3 * span, hi.z + 0.25 * span))
    tmpdir = path + ".frames"
    os.makedirs(tmpdir, exist_ok=True)
    rows = []
    for cname, act, n in made:
        ad.action = act
        if act.slots:
            ad.action_slot = act.slots[0]
        row = []
        for k in range(per_clip):
            f = int(round(k * (n - 1) / max(1, per_clip - 1)))
            sc.frame_set(f)
            yaw = 0 if k % 2 == 0 else 35
            ru.frame_camera(lo2, hi2, yaw_deg=yaw, margin=1.0)
            fp = os.path.join(tmpdir, f"{cname}_{k}.png")
            ru.render_to(fp)
            img = bpy.data.images.load(fp)
            px = np.array(img.pixels[:], dtype=np.float32).reshape(res[1], res[0], 4)
            bpy.data.images.remove(img)
            row.append(px)
        rows.append(np.concatenate(row, axis=1))
    ad.action = None
    sheet = np.concatenate(rows[::-1], axis=0)   # Blender images are bottom-up
    h, w = sheet.shape[:2]
    out = bpy.data.images.new("sheet", w, h, alpha=True)
    out.pixels.foreach_set(sheet.ravel())
    out.filepath_raw = path
    out.file_format = "PNG"
    out.save()
    for f in os.listdir(tmpdir):
        os.remove(os.path.join(tmpdir, f))
    os.rmdir(tmpdir)


main()
