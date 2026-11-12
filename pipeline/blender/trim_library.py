"""Cut the animation library down to the skeleton + the clips we use.

    $FORGE_BLENDER -b -P pipeline/blender/trim_library.py -- \
        --in AnimationLibrary_Godot_Standard.glb --clips assets/animations/clips.json \
        --out assets/animations/ual_standard.glb [--keep-mesh]

Keeps the armature, (optionally) the mannequin mesh, and only the source animations
named in clips.json, each as its own NLA track so they export as separate glTF
animations with their original names.
"""
import argparse
import json
import sys

import bpy


def main():
    argv = sys.argv[sys.argv.index("--") + 1:]
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="inp", required=True)
    p.add_argument("--clips", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--keep-mesh", action="store_true")
    a = p.parse_args(argv)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.render.fps = 30
    bpy.ops.import_scene.gltf(filepath=a.inp)
    want = []
    for spec in json.load(open(a.clips))["clips"].values():
        want += [s for s in spec["source"] if s not in want]
    arm = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"][0]
    for o in list(bpy.context.scene.objects):
        if o is arm:
            continue
        if a.keep_mesh and o.type == "MESH" and o.parent is arm:
            continue
        bpy.data.objects.remove(o, do_unlink=True)
    ad = arm.animation_data
    ad.action = None
    for t in list(ad.nla_tracks):
        ad.nla_tracks.remove(t)
    missing = []
    for name in want:
        act = bpy.data.actions.get(name)
        if act is None:
            missing.append(name)
            continue
        tr = ad.nla_tracks.new()
        tr.name = name
        st = tr.strips.new(name, int(act.frame_range[0]), act)
        if act.slots:
            st.action_slot = act.slots[0]
        tr.mute = True
    if missing:
        raise SystemExit(f"missing animations: {missing}")
    for pb in arm.pose.bones:
        pb.matrix_basis.identity()
    bpy.ops.export_scene.gltf(filepath=a.out, export_format="GLB", export_animations=True,
                              export_animation_mode="NLA_TRACKS", export_skins=True,
                              export_force_sampling=True, export_optimize_animation_size=True)
    print("TRIM " + json.dumps({"clips": want, "out": a.out}))


main()
