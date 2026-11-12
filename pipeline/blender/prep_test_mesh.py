"""Turn a rigged CC0 character into a static test mesh for the rigging stage.

Run headless:
    $FORGE_BLENDER -b -P pipeline/blender/prep_test_mesh.py -- \
        --in character.gltf --out static.glb [--voxel 0.012 --faces 40000 --bake 1024]

Default mode strips the armature (mesh keeps its rest shape), joins all mesh
parts into one object and exports a static GLB with the original materials.

With --voxel the joined mesh is voxel-remeshed into one fused watertight blob
(no clean topology, limbs and hair merged), decimated, given an automatic UV
layout, and the original colours are baked into a single texture. That imitates
what an image-to-3D model such as TRELLIS.2 hands us.
"""
import argparse
import sys

import bpy


def parse():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--in", dest="inp", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--voxel", type=float, default=0.0, help="voxel size in metres (0 = keep topology)")
    p.add_argument("--faces", type=int, default=40000, help="target triangle count after remesh")
    p.add_argument("--bake", type=int, default=1024, help="baked texture size")
    p.add_argument("--arms-down", type=float, default=0.0,
                   help="lower both upper arms by this many degrees before stripping (T-pose -> A-pose)")
    p.add_argument("--rigged-out", default="",
                   help="also export the (re-posed) original rig with the pose applied as rest: "
                        "ground truth for testing the retargeting")
    return p.parse_args(argv)


def main():
    a = parse()
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if a.inp.lower().endswith(".fbx"):
        bpy.ops.import_scene.fbx(filepath=a.inp)
    else:
        bpy.ops.import_scene.gltf(filepath=a.inp)

    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if a.arms_down:
        from mathutils import Matrix, Vector
        import math
        for arm in [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]:
            for pb in arm.pose.bones:
                n = pb.name.lower()
                if not any(k in n for k in ("upperarm", "upper_arm")):
                    continue
                side = 1.0 if (pb.bone.head_local.x > 0) else -1.0
                # rotate about the armature-space forward axis (Y), around the shoulder joint
                rot = Matrix.Rotation(math.radians(a.arms_down) * side, 4, "Y")
                head = pb.bone.head_local
                m = Matrix.Translation(head) @ rot @ Matrix.Translation(-head) @ pb.bone.matrix_local
                pb.matrix = m
                bpy.context.view_layer.update()
        bpy.context.view_layer.update()
        for o in meshes:                   # bake the posed shape into the mesh
            bpy.context.view_layer.objects.active = o
            for m in list(o.modifiers):
                if m.type == "ARMATURE":
                    bpy.ops.object.modifier_apply(modifier=m.name)
    if a.rigged_out:
        arms = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
        for o in meshes:                   # (no-op if already applied above)
            bpy.context.view_layer.objects.active = o
            for m in list(o.modifiers):
                if m.type == "ARMATURE":
                    bpy.ops.object.modifier_apply(modifier=m.name)
        for arm in arms:
            bpy.ops.object.select_all(action="DESELECT")
            bpy.context.view_layer.objects.active = arm
            arm.select_set(True)
            bpy.ops.object.mode_set(mode="POSE")
            bpy.ops.pose.select_all(action="SELECT")
            bpy.ops.pose.armature_apply()
            bpy.ops.object.mode_set(mode="OBJECT")
        for o in meshes:
            md = o.modifiers.new("Armature", "ARMATURE")
            md.object = arms[0]
        bpy.ops.object.select_all(action="DESELECT")
        for o in meshes + arms:
            o.select_set(True)
        bpy.ops.export_scene.gltf(filepath=a.rigged_out, export_format="GLB", use_selection=True,
                                  export_animations=False, export_skins=True)
    for o in meshes:
        for m in list(o.modifiers):
            o.modifiers.remove(m)          # drops the Armature modifier: mesh stays in rest shape
        o.vertex_groups.clear()
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
    src = bpy.context.view_layer.objects.active
    src.name = "Character"
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    # Drop extra attribute layers that an image-to-3D mesh would not have.
    while len(src.data.uv_layers) > 1:
        src.data.uv_layers.remove(src.data.uv_layers[-1])

    # Image nodes whose file is missing make the Cycles bake fail silently (all black);
    # we only bake colour, so drop every image node that has no pixels.
    for mat in src.data.materials:
        if mat and mat.node_tree:
            for n in list(mat.node_tree.nodes):
                if n.type == "TEX_IMAGE" and (n.image is None or not n.image.has_data and not n.image.packed_file
                                              and not __import__("os").path.exists(bpy.path.abspath(n.image.filepath))):
                    mat.node_tree.nodes.remove(n)

    # Old FBX files often import with alpha 0 (fully transparent); test meshes are opaque.
    for mat in src.data.materials:
        if mat and mat.node_tree:
            for n in mat.node_tree.nodes:
                if n.type == "BSDF_PRINCIPLED":
                    n.inputs["Alpha"].default_value = 1.0

    out = src
    if a.voxel > 0:
        bpy.ops.object.duplicate()
        blob = bpy.context.view_layer.objects.active
        blob.name = "CharacterBlob"
        mod = blob.modifiers.new("remesh", "REMESH")
        mod.mode = "VOXEL"
        mod.voxel_size = a.voxel
        bpy.ops.object.modifier_apply(modifier=mod.name)
        tris = sum(len(p.vertices) - 2 for p in blob.data.polygons)
        if tris > a.faces:
            dec = blob.modifiers.new("dec", "DECIMATE")
            dec.ratio = a.faces / tris
            bpy.ops.object.modifier_apply(modifier=dec.name)
        blob.data.materials.clear()
        bpy.ops.object.shade_smooth()
        for uv in list(blob.data.uv_layers):
            blob.data.uv_layers.remove(uv)
        blob.data.uv_layers.new(name="UVMap")
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.uv.smart_project(angle_limit=1.15, island_margin=0.004)
        bpy.ops.object.mode_set(mode="OBJECT")

        img = bpy.data.images.new("baked_basecolor", a.bake, a.bake)
        mat = bpy.data.materials.new("Baked")
        nt = mat.node_tree
        bsdf = nt.nodes.get("Principled BSDF")
        tex = nt.nodes.new("ShaderNodeTexImage")
        tex.image = img
        bsdf.inputs["Roughness"].default_value = 0.8
        nt.nodes.active = tex
        blob.data.materials.append(mat)

        sc = bpy.context.scene
        sc.render.engine = "CYCLES"
        sc.cycles.device = "CPU"
        sc.cycles.samples = 1
        bpy.ops.object.select_all(action="DESELECT")
        src.select_set(True)
        blob.select_set(True)
        bpy.context.view_layer.objects.active = blob
        bpy.ops.object.bake(type="DIFFUSE", pass_filter={"COLOR"}, use_selected_to_active=True,
                            cage_extrusion=0.03, max_ray_distance=0.1, margin=4)
        # Link the texture only after baking: linked before, Cycles reports a circular
        # dependency and bakes nothing but black.
        nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
        px = img.pixels[:]
        mean = sum(px[0::4]) / (len(px) / 4)
        print(f"BAKE mean red={mean:.3f}")
        img.filepath_raw = a.out + ".basecolor.png"
        img.file_format = "PNG"
        img.save()
        img.pack()
        bpy.data.objects.remove(src, do_unlink=True)
        out = blob

    # Vertex colours are dropped only now: the imported materials multiply the base
    # colour by them, so removing them before the bake bakes black.
    for ca in list(out.data.color_attributes):
        out.data.color_attributes.remove(ca)
    bpy.ops.object.select_all(action="DESELECT")
    out.select_set(True)
    bpy.ops.export_scene.gltf(filepath=a.out, export_format="GLB", use_selection=True,
                              export_animations=False, export_skins=False)
    tris = sum(len(p.vertices) - 2 for p in out.data.polygons)
    print(f"PREP_OK verts={len(out.data.vertices)} tris={tris} materials={len(out.data.materials)}")


main()
