"""Paint the concept image back onto a generated model, seen from the front.

Runs inside Blender (only bpy and the numpy that ships with it):

    "$FORGE_BLENDER" -b --python pipeline/blender/project_image.py -- \
        --in hero.glb --image hero_concept.png --out hero_face.glb \
        [--box x0,y0,x1,y1] [--texture-size 2048] [--debug face_check.png]

Why: an image-to-3D model sees a full-body character at about 1000 px, so the
face gets roughly 100 px and the eyes come back as smudges. The concept image
the model was generated from still has the face. Its front view and the model's
front view show the same thing, so the image can be projected onto the texture.

What it does:
  1. finds the figure in the image (alpha, or everything not connected to a
     white border) and lines its outline up with the model's front outline;
  2. renders the model's current texture from the front and searches the shift
     and scale inside the box that best match the image (correlation of
     brightness), so eyes land on eyes;
  3. for every texel in the box that faces the front and is not hidden behind
     other geometry, replaces the colour with the image's colour, fading out
     towards grazing angles and the box border;
  4. writes a new GLB with the repainted base-colour texture.

--box is the part of the image to project, as fractions of the image size
(left, top, right, bottom). Default: the head, from the top of the figure down
to the neck, which is found in the outline. Geometry is not changed.

Prints one JSON line. --debug writes "before | after | image" of the box.
"""

import argparse
import json
import math
import os
import sys
import time

import bpy
import numpy as np


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    ap = argparse.ArgumentParser(prog="project_image.py")
    ap.add_argument("--in", dest="src", required=True)
    ap.add_argument("--image", required=True, help="the concept image the model was generated from")
    ap.add_argument("--out", required=True)
    ap.add_argument("--box", help="left,top,right,bottom as fractions of the image; default: the figure's head")
    ap.add_argument("--head", default="auto",
                    help="height of the default box as a fraction of the figure, or auto: down to the neck, "
                         "found as the narrowest row of the outline below the head's widest row")
    ap.add_argument("--texture-size", type=int, default=0, help="resample the texture to this size first; 0 keeps it")
    ap.add_argument("--facing", default="0.05,0.35",
                    help="cosine of the angle to the viewer where projection starts and where it is full")
    ap.add_argument("--feather", type=float, default=0.03, help="fade at the box border, fraction of box height")
    ap.add_argument("--no-align", action="store_true", help="trust the outline fit; skip the brightness search")
    ap.add_argument("--image-format", default="AUTO", choices=["AUTO", "JPEG", "WEBP"])
    ap.add_argument("--debug", help="write a before/after/image strip of the box here")
    return ap.parse_args(argv)


# ---------------------------------------------------------------- images
def load_pixels(image):
    """Blender image -> float32 (H, W, 4), first row = top of the picture."""
    w, h = image.size
    buf = np.empty(w * h * 4, np.float32)
    image.pixels.foreach_get(buf)
    return buf.reshape(h, w, 4)[::-1].copy()


def new_image(name, pixels):
    """float32 (H, W, 4), top row first -> packed 8-bit Blender image."""
    h, w = pixels.shape[:2]
    image = bpy.data.images.new(name, w, h, alpha=True)
    image.pixels.foreach_set(np.ascontiguousarray(pixels[::-1]).ravel())
    image.pack()
    return image


def save_png(path, pixels):
    image = new_image("debug", pixels)
    image.filepath_raw = path
    image.file_format = "PNG"
    image.save()
    bpy.data.images.remove(image)


def resize(pixels, w, h):
    """Bilinear resample of (H, W, C)."""
    sh, sw = pixels.shape[:2]
    ys = (np.arange(h) + 0.5) * sh / h - 0.5
    xs = (np.arange(w) + 0.5) * sw / w - 0.5
    return bilinear(pixels, *np.meshgrid(xs, ys))


def bilinear(pixels, x, y):
    """Sample (H, W, C) at float pixel-centre coordinates (x right, y down)."""
    h, w = pixels.shape[:2]
    x = np.clip(x, 0, w - 1)
    y = np.clip(y, 0, h - 1)
    x0 = np.floor(x).astype(np.int32)
    y0 = np.floor(y).astype(np.int32)
    x1 = np.minimum(x0 + 1, w - 1)
    y1 = np.minimum(y0 + 1, h - 1)
    fx = (x - x0)[..., None]
    fy = (y - y0)[..., None]
    top = pixels[y0, x0] * (1 - fx) + pixels[y0, x1] * fx
    bottom = pixels[y1, x0] * (1 - fx) + pixels[y1, x1] * fx
    return top * (1 - fy) + bottom * fy


def box_blur(a, radius):
    if radius < 1:
        return a
    k = 2 * radius + 1
    pad = np.pad(a, radius, mode="edge")
    c = np.cumsum(np.pad(pad, ((1, 0), (0, 0))), axis=0)
    a = (c[k:] - c[:-k]) / k
    c = np.cumsum(np.pad(a, ((0, 0), (1, 0))), axis=1)
    return (c[:, k:] - c[:, :-k]) / k


def figure_mask(pixels):
    """True where the picture shows the figure. Uses alpha when there is one,
    otherwise everything that is not near-white background reachable from the
    border (so white clothes and eye whites inside the outline stay figure)."""
    alpha = pixels[..., 3]
    if alpha.min() < 0.5:
        return alpha > 0.5
    white = pixels[..., :3].min(axis=2) > 0.9
    reach = np.zeros_like(white)
    reach[0], reach[-1], reach[:, 0], reach[:, -1] = white[0], white[-1], white[:, 0], white[:, -1]
    count = -1
    while True:
        for _ in range(16):
            grown = reach.copy()
            grown[1:] |= reach[:-1]
            grown[:-1] |= reach[1:]
            grown[:, 1:] |= reach[:, :-1]
            grown[:, :-1] |= reach[:, 1:]
            reach = grown & white
        now = int(reach.sum())
        if now == count:
            break
        count = now
    return ~reach


def luminance(rgb):
    return rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114


def smoothstep(lo, hi, x):
    t = np.clip((x - lo) / max(hi - lo, 1e-9), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def find_head(figure, top, bottom):
    """Fraction of the figure's height taken by the head, from the outline alone.
    A fixed fraction fits one body type only: 0.145 is right for a figure seven heads
    tall and cuts a stylised three-heads-tall character's face in half. Row by row,
    measure the unbroken run of figure pixels through the body's centre line (so
    arms held away from the body do not count). The head is the first bulge, the
    shoulders the next one, and the neck the narrowest row between them. Falls back
    to 0.145 when no such row stands out."""
    height = bottom - top
    limit = int(0.5 * height)
    cols = np.nonzero(figure[top:top + max(1, int(0.2 * height))].any(axis=0))[0]
    centre = int((cols[0] + cols[-1]) // 2)
    widths = np.zeros(limit)
    for r in range(limit):
        row = figure[top + r]
        if not row[centre]:
            continue
        left = centre
        while left > 0 and row[left - 1]:
            left -= 1
        right = centre
        while right < row.shape[0] - 1 and row[right + 1]:
            right += 1
        widths[r] = right - left + 1
    widths = box_blur(widths[:, None], max(1, height // 300))[:, 0]
    crown = int(np.argmax(widths[:int(0.25 * height)]))                    # widest row of the head
    below = np.nonzero(widths[crown:] > 1.15 * widths[crown])[0]             # where the shoulders begin
    end = crown + int(below[0]) if len(below) else limit
    if end <= crown + 2:
        return 0.145
    neck = crown + int(np.argmin(widths[crown:end]))
    if widths[neck] > 0.8 * widths[crown]:                                 # no neck to be seen
        return 0.145
    return float(np.clip(neck / height, 0.08, 0.45))


# ---------------------------------------------------------------- mesh
def load_mesh():
    """Join everything into one mesh; return world-space arrays and the base-colour image node."""
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not meshes:
        raise SystemExit("no mesh in input")
    bpy.ops.object.select_all(action="DESELECT")
    for o in meshes:
        o.select_set(True)
    bpy.context.view_layer.objects.active = meshes[0]
    if len(meshes) > 1:
        bpy.ops.object.join()
    obj = bpy.context.view_layer.objects.active
    mesh = obj.data
    mesh.calc_loop_triangles()

    world = np.array(obj.matrix_world, dtype=np.float64)
    co = np.empty(len(mesh.vertices) * 3, np.float64)
    mesh.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3) @ world[:3, :3].T + world[:3, 3]
    normal = np.empty(len(mesh.vertices) * 3, np.float64)
    mesh.vertices.foreach_get("normal", normal)
    normal = normal.reshape(-1, 3) @ np.linalg.inv(world[:3, :3])
    normal /= np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-12)

    n = len(mesh.loop_triangles)
    tri_vert = np.empty(n * 3, np.int32)
    mesh.loop_triangles.foreach_get("vertices", tri_vert)
    tri_loop = np.empty(n * 3, np.int32)
    mesh.loop_triangles.foreach_get("loops", tri_loop)
    if not mesh.uv_layers.active:
        raise SystemExit("mesh has no UV map")
    uv = np.empty(len(mesh.loops) * 2, np.float64)
    mesh.uv_layers.active.data.foreach_get("uv", uv)
    tri_uv = uv.reshape(-1, 2)[tri_loop.reshape(-1, 3)]

    node = None
    for material in mesh.materials:
        if not material or not material.use_nodes:
            continue
        for bsdf in material.node_tree.nodes:
            if bsdf.type != "BSDF_PRINCIPLED":
                continue
            for link in bsdf.inputs["Base Color"].links:
                if link.from_node.type == "TEX_IMAGE" and link.from_node.image:
                    node = link.from_node
    if node is None:
        raise SystemExit("no base-colour texture found")
    return co, normal, tri_vert.reshape(-1, 3), tri_uv, node


def rasterize(points, depth, tris, width, height):
    """Z-buffer. points (N, 2) in pixels (x right, y down), smaller depth = nearer.
    Returns depth (H, W), triangle index (H, W, -1 = empty) and barycentrics (H, W, 3)."""
    zbuf = np.full((height, width), np.inf, np.float32)
    index = np.full((height, width), -1, np.int32)
    bary = np.zeros((height, width, 3), np.float32)
    lo = np.floor(points[tris].min(axis=1)).astype(np.int64)
    hi = np.ceil(points[tris].max(axis=1)).astype(np.int64)
    for t in np.nonzero((hi[:, 0] >= 0) & (hi[:, 1] >= 0) & (lo[:, 0] < width) & (lo[:, 1] < height))[0]:
        a, b, c = points[tris[t]]
        area = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        if abs(area) < 1e-12:
            continue
        x0, y0 = max(lo[t, 0], 0), max(lo[t, 1], 0)
        x1, y1 = min(hi[t, 0], width - 1), min(hi[t, 1], height - 1)
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        w0 = ((b[0] - gx) * (c[1] - gy) - (b[1] - gy) * (c[0] - gx)) / area
        w1 = ((c[0] - gx) * (a[1] - gy) - (c[1] - gy) * (a[0] - gx)) / area
        w2 = 1 - w0 - w1
        da, db, dc = depth[tris[t]]
        z = w0 * da + w1 * db + w2 * dc
        window = zbuf[y0:y1 + 1, x0:x1 + 1]
        hit = (w0 >= 0) & (w1 >= 0) & (w2 >= 0) & (z < window)
        if not hit.any():
            continue
        window[hit] = z[hit]
        index[y0:y1 + 1, x0:x1 + 1][hit] = t
        bary[y0:y1 + 1, x0:x1 + 1][hit] = np.stack([w0, w1, w2], axis=-1)[hit]
    return zbuf, index, bary


def shade_front(index, bary, tri_uv, texture):
    """Colour of each rasterised pixel, read from the texture (unlit)."""
    h, w = texture.shape[:2]
    out = np.ones(index.shape + (4,), np.float32)
    hit = index >= 0
    uv = (bary[hit][:, :, None] * tri_uv[index[hit]]).sum(axis=1)
    out[hit] = bilinear(texture, uv[:, 0] * w - 0.5, (1 - uv[:, 1]) * h - 0.5)
    return out, hit


# ---------------------------------------------------------------- alignment
def align(render_lum, render_hit, image_lum, image_fig, origin, centre):
    """Search scale and shift (image pixels) that make the image match the render inside the box.
    A render pixel p (image coordinates) reads the image at centre + s * (p - centre) + d."""
    h, w = render_lum.shape
    gx, gy = np.meshgrid(np.arange(w) + origin[0], np.arange(h) + origin[1])

    def score(s, dx, dy):
        x = np.rint(centre[0] + s * (gx - centre[0]) + dx).astype(np.int64)
        y = np.rint(centre[1] + s * (gy - centre[1]) + dy).astype(np.int64)
        ok = (x >= 0) & (y >= 0) & (x < image_lum.shape[1]) & (y < image_lum.shape[0])
        x, y = np.clip(x, 0, image_lum.shape[1] - 1), np.clip(y, 0, image_lum.shape[0] - 1)
        fig = image_fig[y, x] & ok
        both = fig & render_hit
        if both.sum() < 64:
            return -9.0, 0.0, 0.0
        a = render_lum[both]
        b = image_lum[y, x][both]
        a = a - a.mean()
        b = b - b.mean()
        ncc = float((a * b).sum() / math.sqrt(max((a * a).sum() * (b * b).sum(), 1e-12)))
        overlap = float(both.sum() / max((fig | render_hit).sum(), 1))
        return ncc + 0.5 * overlap, ncc, overlap

    best = (score(1.0, 0, 0), 1.0, 0, 0)
    for s in np.linspace(0.88, 1.12, 13):
        for dx in range(-24, 25, 4):
            for dy in range(-24, 25, 4):
                trial = (score(s, dx, dy), float(s), dx, dy)
                if trial[0][0] > best[0][0]:
                    best = trial
    _, s0, dx0, dy0 = best
    for s in np.linspace(s0 - 0.02, s0 + 0.02, 9):
        for dx in range(dx0 - 4, dx0 + 5):
            for dy in range(dy0 - 4, dy0 + 5):
                trial = (score(s, dx, dy), float(s), dx, dy)
                if trial[0][0] > best[0][0]:
                    best = trial
    start = score(1.0, 0, 0)
    (_, ncc, overlap), s, dx, dy = best
    return {"scale": round(s, 4), "dx": dx, "dy": dy, "correlation": round(ncc, 3), "overlap": round(overlap, 3),
            "correlation_before": round(start[1], 3), "overlap_before": round(start[2], 3)}


# ---------------------------------------------------------------- main
def main():
    args = parse_args()
    t0 = time.time()
    cos_lo, cos_hi = (float(v) for v in args.facing.split(","))

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=args.src)
    co, normal, tris, tri_uv, node = load_mesh()
    texture = load_pixels(node.image)
    if args.texture_size and texture.shape[0] != args.texture_size:
        texture = resize(texture, args.texture_size, args.texture_size).astype(np.float32)
    tex_h, tex_w = texture.shape[:2]
    original = texture.copy()

    concept = load_pixels(bpy.data.images.load(os.path.abspath(args.image)))
    img_h, img_w = concept.shape[:2]
    figure = figure_mask(concept)
    rows = np.nonzero(figure.any(axis=1))[0]
    cols = np.nonzero(figure.any(axis=0))[0]
    fig_top, fig_bottom, fig_left, fig_right = rows[0], rows[-1] + 1, cols[0], cols[-1] + 1

    # Front view: the viewer stands on -Y and looks along +Y (glTF front is +Z, Blender's -Y).
    # Outline fit: same height, same top, same horizontal centre.
    lo, hi = co.min(axis=0), co.max(axis=0)
    k = (fig_bottom - fig_top) / (hi[2] - lo[2])                       # image pixels per metre
    px = (fig_left + fig_right) / 2 + (co[:, 0] - (lo[0] + hi[0]) / 2) * k
    py = fig_top + (hi[2] - co[:, 2]) * k
    depth = co[:, 1]
    width_ratio = ((hi[0] - lo[0]) * k) / (fig_right - fig_left)

    if args.box:
        fx0, fy0, fx1, fy1 = (float(v) for v in args.box.split(","))
        box = [fx0 * img_w, fy0 * img_h, fx1 * img_w, fy1 * img_h]
    else:
        head_fraction = find_head(figure, fig_top, fig_bottom) if args.head == "auto" else float(args.head)
        bottom = fig_top + head_fraction * (fig_bottom - fig_top)
        head_cols = np.nonzero(figure[fig_top:int(bottom)].any(axis=0))[0]
        box = [float(head_cols[0]), float(fig_top), float(head_cols[-1] + 1), float(bottom)]
    box_w, box_h = box[2] - box[0], box[3] - box[1]
    centre = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
    margin = 32

    # Triangles that can matter: anything whose outline-fit footprint touches the box (+ search margin).
    tri_px = np.stack([px[tris], py[tris]], axis=-1)
    near = ((tri_px[..., 0].max(axis=1) >= box[0] - margin) & (tri_px[..., 0].min(axis=1) <= box[2] + margin) &
            (tri_px[..., 1].max(axis=1) >= box[1] - margin) & (tri_px[..., 1].min(axis=1) <= box[3] + margin))
    local = np.nonzero(near)[0]

    # ---- 1. render the current texture from the front, at image resolution, and align
    origin = (int(box[0]) - margin, int(box[1]) - margin)
    view_w, view_h = int(box_w) + 2 * margin, int(box_h) + 2 * margin
    pts = np.stack([px - origin[0], py - origin[1]], axis=-1)
    _, index, bary = rasterize(pts, depth, tris[local], view_w, view_h)
    front, hit = shade_front(index, bary, tri_uv[local], original)
    fit = {"scale": 1.0, "dx": 0, "dy": 0}
    if not args.no_align:
        fit = align(luminance(front), hit, luminance(concept), figure, origin, centre)
    s, dx, dy = fit["scale"], fit["dx"], fit["dy"]

    # ---- 2. depth buffer at 4x for the visibility test
    ss = 4
    zbuf, _, _ = rasterize(pts * ss, depth, tris[local], view_w * ss, view_h * ss)
    bias = 0.004 * (hi[2] - lo[2])

    # ---- 3. walk the texture: every texel of a front-facing triangle in the box
    soft = box_blur(figure.astype(np.float32), 1)
    feather = max(args.feather * box_h, 1.0)
    tri_n = normal[tris[local]]
    strongest = np.zeros((tex_h, tex_w), np.float32)
    for j, t in enumerate(local):
        if tri_n[j, :, 1].min() > -cos_lo:          # no corner faces the viewer enough
            continue
        uv = tri_uv[t] * (tex_w, tex_h)
        uv[:, 1] = tex_h - uv[:, 1]                 # texel rows run top-down
        a, b, c = uv
        area = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        if abs(area) < 1e-9:
            continue
        x0, y0 = max(int(math.floor(uv[:, 0].min() - 1)), 0), max(int(math.floor(uv[:, 1].min() - 1)), 0)
        x1 = min(int(math.ceil(uv[:, 0].max() + 1)), tex_w - 1)
        y1 = min(int(math.ceil(uv[:, 1].max() + 1)), tex_h - 1)
        if x1 < x0 or y1 < y0:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        w0 = ((b[0] - gx) * (c[1] - gy) - (b[1] - gy) * (c[0] - gx)) / area
        w1 = ((c[0] - gx) * (a[1] - gy) - (c[1] - gy) * (a[0] - gx)) / area
        w2 = 1 - w0 - w1
        # Signed distance to each edge in texels; accept a one-texel rim so island borders get painted too.
        edge = np.array([np.linalg.norm(c - b), np.linalg.norm(a - c), np.linalg.norm(b - a)])
        dist = np.minimum(np.minimum(w0 / max(edge[0], 1e-9), w1 / max(edge[1], 1e-9)),
                          w2 / max(edge[2], 1e-9)) * abs(area)
        inside = dist >= -1.0
        if not inside.any():
            continue
        w0c, w1c, w2c = (np.clip(w, 0, 1) for w in (w0, w1, w2))
        total = np.maximum(w0c + w1c + w2c, 1e-9)
        w0c, w1c, w2c = w0c / total, w1c / total, w2c / total
        v = tris[t]
        ix = w0c * px[v[0]] + w1c * px[v[1]] + w2c * px[v[2]]
        iy = w0c * py[v[0]] + w1c * py[v[1]] + w2c * py[v[2]]
        d = w0c * depth[v[0]] + w1c * depth[v[1]] + w2c * depth[v[2]]
        facing = -(w0c * tri_n[j, 0, 1] + w1c * tri_n[j, 1, 1] + w2c * tri_n[j, 2, 1])

        zx = np.clip(((ix - origin[0]) * ss).astype(np.int64), 0, view_w * ss - 1)
        zy = np.clip(((iy - origin[1]) * ss).astype(np.int64), 0, view_h * ss - 1)
        visible = d <= zbuf[zy, zx] + bias
        border = np.minimum(np.minimum(ix - box[0], box[2] - ix), np.minimum(iy - box[1], box[3] - iy))
        sx = centre[0] + s * (ix - centre[0]) + dx - 0.5
        sy = centre[1] + s * (iy - centre[1]) + dy - 0.5
        colour = bilinear(concept, sx, sy)
        in_figure = bilinear(soft[..., None], sx, sy)[..., 0]
        weight = (inside * visible * smoothstep(cos_lo, cos_hi, facing) * smoothstep(0.0, feather, border) *
                  smoothstep(0.6, 1.0, in_figure)).astype(np.float32)
        if weight.max() <= 0:
            continue
        # Blend from the untouched texture; where triangles share a texel rim the strongest weight wins.
        window = (slice(y0, y1 + 1), slice(x0, x1 + 1))
        mixed = original[window][..., :3] * (1 - weight[..., None]) + colour[..., :3] * weight[..., None]
        apply = weight > strongest[window]
        texture[window][..., :3][apply] = mixed[apply]
        strongest[window][apply] = weight[apply]

    changed = int((strongest > 0).sum())
    weight_sum = float(strongest.sum())
    node.image = new_image("base_color_projected", texture)

    if args.debug:
        scale = 4
        dpts = (np.stack([px - box[0], py - box[1]], axis=-1)) * scale
        dw, dh = int(box_w * scale), int(box_h * scale)
        _, didx, dbary = rasterize(dpts, depth, tris[local], dw, dh)
        before, _ = shade_front(didx, dbary, tri_uv[local], original)
        after, _ = shade_front(didx, dbary, tri_uv[local], texture)
        gx, gy = np.meshgrid((np.arange(dw) + 0.5) / scale + box[0], (np.arange(dh) + 0.5) / scale + box[1])
        ref = bilinear(concept, centre[0] + s * (gx - centre[0]) + dx - 0.5, centre[1] + s * (gy - centre[1]) + dy - 0.5)
        ref[..., 3] = 1.0
        save_png(os.path.abspath(args.debug), np.concatenate([before, after, ref], axis=1).astype(np.float32))

    bpy.ops.export_scene.gltf(
        filepath=args.out,
        export_format="GLB",
        export_yup=True,
        export_image_format=args.image_format,
        export_animations=False,
    )
    print(json.dumps({
        "stage": "project_image", "in": args.src, "image": args.image, "out": args.out,
        "box_px": [round(v, 1) for v in box], "head_fraction": round(box_h / (fig_bottom - fig_top), 3), "figure_px": [int(fig_left), int(fig_top), int(fig_right), int(fig_bottom)],
        "outline_width_ratio": round(float(width_ratio), 3),
        "fit": fit, "texture": [tex_w, tex_h], "texels_changed": changed,
        "mean_weight": round(weight_sum / max(changed, 1), 3),
        "triangles_in_box": int(len(local)), "bytes": os.path.getsize(args.out),
        "seconds": round(time.time() - t0, 2), "blender": bpy.app.version_string,
    }))


if __name__ == "__main__":
    main()
