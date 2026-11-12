"""Small helpers shared by the Blender scripts: camera framing and rendering.

Imported by other scripts in this folder (they add the folder to sys.path).
"""
import math

import bpy
from mathutils import Vector


def world_bbox(objs):
    pts = []
    dg = bpy.context.evaluated_depsgraph_get()
    for o in objs:
        if o.type != "MESH":
            continue
        ev = o.evaluated_get(dg)
        me = ev.to_mesh()
        mw = ev.matrix_world
        pts += [mw @ v.co for v in me.vertices]
        ev.to_mesh_clear()
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return lo, hi


def setup_render(engine="CYCLES", res=(640, 800), transparent=False):
    sc = bpy.context.scene
    sc.render.engine = engine
    sc.render.resolution_x, sc.render.resolution_y = res
    sc.render.resolution_percentage = 100
    sc.render.film_transparent = transparent
    sc.render.image_settings.file_format = "PNG"
    if engine == "BLENDER_WORKBENCH":
        sh = sc.display.shading
        sh.light = "STUDIO"
        sh.color_type = "TEXTURE"
        sh.show_cavity = False
        sh.show_shadows = False
    elif engine == "CYCLES":
        sc.cycles.device = "CPU"
        sc.cycles.samples = 16
    if sc.world is None:
        sc.world = bpy.data.worlds.new("World")
    sc.world.color = (0.82, 0.80, 0.76)
    sc.view_settings.view_transform = "Standard"
    if engine != "BLENDER_WORKBENCH":
        add_lights()


def add_lights():
    if "KeyLight" in bpy.data.objects:
        return
    sun = bpy.data.lights.new("KeyLight", "SUN")
    sun.energy = 3.0
    o = bpy.data.objects.new("KeyLight", sun)
    o.rotation_euler = (math.radians(50), 0, math.radians(30))
    bpy.context.scene.collection.objects.link(o)
    w = bpy.context.scene.world
    w.use_nodes = True
    bg = w.node_tree.nodes.get("Background")
    if bg:
        bg.inputs[0].default_value = (0.82, 0.80, 0.76, 1)
        bg.inputs[1].default_value = 0.8


def ensure_camera():
    sc = bpy.context.scene
    cam = sc.camera
    if cam is None:
        cd = bpy.data.cameras.new("Cam")
        cam = bpy.data.objects.new("Cam", cd)
        sc.collection.objects.link(cam)
        sc.camera = cam
    cam.data.type = "ORTHO"
    return cam


def frame_camera(lo, hi, yaw_deg=0.0, margin=1.15):
    """Orthographic camera looking at the bbox centre. yaw 0 = looking from -Y toward +Y
    (Blender front view, i.e. at a character facing -Y)."""
    cam = ensure_camera()
    c = (lo + hi) / 2
    size = max(hi.z - lo.z, (hi.x - lo.x), (hi.y - lo.y)) * margin
    cam.data.ortho_scale = size
    yaw = math.radians(yaw_deg)
    d = 10.0
    # Camera rotated (90, 0, yaw) looks along (-sin yaw, cos yaw, 0); back it off from the centre.
    cam.location = (c.x + math.sin(yaw) * d, c.y - math.cos(yaw) * d, c.z)
    cam.rotation_euler = (math.radians(90), 0, yaw)
    cam.data.clip_end = 100
    return cam


def render_to(path):
    bpy.context.scene.render.filepath = path
    bpy.ops.render.render(write_still=True)
