#!/usr/bin/env python
"""Image -> textured 3D mesh (GLB) with Microsoft TRELLIS.2.

Stable interface used by the rest of the pipeline:

    image_to_3d.py --image in.png --out out.glb [--resolution 512|1024|1536]
                   [--seed N] [--faces N] [--texture-size N] [--keep-background]
                   [--low-vram | --no-low-vram] [--remesh auto|on|off] [--flat-material]
                   [--preview views.png] [--variant FACES:TEX:PATH]
    image_to_3d.py --batch assets.jsonl --out-dir DIR [--only id,id] [--skip-existing]

Batch mode loads the models once and writes DIR/<id>.glb, <id>.json and
<id>_preview.png per line of the JSON Lines file. A line is
{"id": ..., "image": ...} plus any of resolution, seed, faces, texture_size,
keep_background, remesh, remesh_resolution, flat_material, max_tokens; missing keys take the
command-line values. When the GPU runs out of memory an asset is retried at the
next lower resolution (1536 -> 1024 -> 512) unless --no-fallback is given.

Recommended on an 11-12 GB card: characters --resolution 1536 --faces 20000,
props --resolution 1024 with the face budget the game asks for, plus
--flat-material for stylised re-shading. See docs/logs/trellis2-setup.md.

Run it with the TRELLIS.2 environment's interpreter, through the GPU wrapper:

    tools/gpu_run.sh image_to_3d <label> [--gpu N] -- \
        "$FORGE_RUNTIME/envs/trellis2/bin/python" pipeline/image_to_3d.py --image ... --out ...

The TRELLIS.2 checkout is found at $FORGE_RUNTIME/repos/TRELLIS.2 (override with
$TRELLIS2_REPO). Weights come from the normal Hugging Face cache; set
HF_HUB_OFFLINE=1 once everything is downloaded.

Differences from the upstream example, and why:
- Background removal: upstream's pipeline.json names briaai/RMBG-2.0 (gated,
  non-commercial). We swap in ZhengPeng7/BiRefNet (MIT), loaded lazily, so an
  RGBA input with a real alpha channel never loads a background model at all.
- Low-VRAM mode (auto when the visible GPU has < LOW_VRAM_THRESHOLD_GIB free):
  every sub-model lives in CPU RAM and is moved to the GPU only while it runs
  (upstream's own `low_vram` switch), plus exact-math memory savings in the
  sparse VAE decoders and in the GLB export, and a remesh-free export fallback.
- Checkpoints load without the random weight initialisation upstream does first
  (warm load ~108 s -> ~15-20 s).
- Turing GPUs (compute capability < 8.0, e.g. RTX 2080 Ti): no flash-attn and
  no native bf16, so attention runs on xformers and the bf16 DiTs are cast to fp16.

The last line printed on stdout is one JSON object with timings and mesh stats.
Exit status is non-zero on any failure.
"""
import argparse
import json
import os
import sys
import time
import traceback

MODEL_ID = "microsoft/TRELLIS.2-4B"
BG_MODEL_ID = "ZhengPeng7/BiRefNet"          # MIT; replaces gated briaai/RMBG-2.0
# Image encoder named by pipeline.json (gated, DINOv3 License). People without
# access can set FORGE_DINOV3_MODEL to an ungated transformers-format copy of
# the same weights; docs/logs/trellis2-setup.md says how we verified ours.
DINOV3_DEFAULT = "facebook/dinov3-vitl16-pretrain-lvd1689m"
# Below this much free VRAM -> low-VRAM path. Upstream's own default is
# low_vram=True (pipeline.json has no low_vram key); keeping every sub-model
# resident ran out of memory on the 24 GB 3090 at 1024 for a bulky input, so
# only cards with room for all weights *and* the 1024 decode skip it.
LOW_VRAM_THRESHOLD_GIB = 40.0
PIPELINE_TYPES = {512: "512", 1024: "1024_cascade", 1536: "1536_cascade"}


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--image", help="input image (PNG/JPG; RGBA with real alpha skips background removal)")
    p.add_argument("--out", help="output .glb path")
    p.add_argument("--batch", help="JSON Lines file, one asset per line: {id, image, ...per-asset settings}; "
                                   "the models are loaded once for the whole file")
    p.add_argument("--out-dir", help="batch mode: directory for <id>.glb, <id>.json and <id>_preview.png")
    p.add_argument("--only", help="batch mode: comma-separated ids to run")
    p.add_argument("--skip-existing", action="store_true", help="batch mode: keep models that were already made from the same image and settings")
    p.add_argument("--no-fallback", action="store_true",
                   help="fail instead of retrying at the next lower resolution when the GPU runs out of memory")
    p.add_argument("--resolution", type=int, default=512, choices=sorted(PIPELINE_TYPES),
                   help="voxel resolution of the generated shape (default 512)")
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--faces", type=int, default=20000, help="target face count after decimation (default 20000)")
    p.add_argument("--texture-size", type=int, default=1024, help="baked texture size in pixels (default 1024)")
    p.add_argument("--keep-background", action="store_true",
                   help="do not remove the background; use the whole image as the object")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--low-vram", dest="low_vram", action="store_true", default=None,
                   help="force the low-VRAM path (sub-models on the GPU only while they run)")
    g.add_argument("--no-low-vram", dest="low_vram", action="store_false",
                   help="force everything resident on the GPU (needs ~24 GB)")
    p.add_argument("--max-tokens", type=int, default=None,
                   help="cap on sparse shape tokens in the 1024/1536 cascade; above it the high-res stage "
                        "steps down by 128 (upstream default 49152; low-VRAM default scales with free memory)")
    p.add_argument("--remesh", choices=["auto", "on", "off"], default="auto",
                   help="how the raw mesh is turned into the final one. on: dual-contouring remesh, then decimate "
                        "(one closed skin; thin panels can tear at low face counts). off: decimate the raw mesh "
                        "(keeps thin parts; can leave an inner and an outer shell glued together). "
                        "auto (default): export both ways and keep the one with fewer open or over-shared edges")
    p.add_argument("--no-remesh", action="store_true", help="same as --remesh off")
    p.add_argument("--remesh-resolution", default="auto",
                   help="grid of the dual-contouring remesh: a number (multiple of 32), 'native' (the generation "
                        "resolution, as upstream does), or auto: 256 up to 6k faces, 512 up to 60k, else native. "
                        "The texture is baked from the full-resolution surface either way")
    p.add_argument("--flat-material", action="store_true",
                   help="drop the metallic-roughness map and export metallic 0 / roughness 1 "
                        "(stylised assets: TRELLIS.2 predicts ~0.4-0.5 metallic on flat cartoon inputs)")
    p.add_argument("--webp", action="store_true", help="store textures as WebP (EXT_texture_webp) instead of PNG")
    p.add_argument("--preview", default=None, help="also write a 4-view PNG render of the exported GLB here")
    p.add_argument("--save-raw", default=None, help=argparse.SUPPRESS)  # dev: dump the decoded mesh (torch.save)
    p.add_argument("--variant", action="append", default=[], metavar="FACES:TEXTURE:PATH",
                   help="extra GLB exported from the same generation (repeatable)")
    args = p.parse_args(argv)
    if args.no_remesh:
        args.remesh = "off"
    if args.batch:
        if not args.out_dir or args.image or args.out:
            p.error("--batch takes --out-dir, not --image/--out")
    elif not (args.image and args.out):
        p.error("need --image and --out, or --batch and --out-dir")
    return args


ITEM_KEYS = ("resolution", "seed", "faces", "texture_size", "keep_background", "remesh",
             "remesh_resolution", "flat_material", "max_tokens")


def read_batch(args):
    """One argparse-like namespace per line of the batch file."""
    only = set(filter(None, (args.only or "").split(",")))
    here = os.path.dirname(os.path.abspath(args.batch))
    items = []
    with open(args.batch) as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            row = json.loads(line)
            unknown = set(row) - {"id", "image", "note", *ITEM_KEYS}
            if "id" not in row or "image" not in row or unknown:
                raise SystemExit(f"{args.batch}:{n}: need id and image; unknown keys: {sorted(unknown)}")
            if only and row["id"] not in only:
                continue
            if row.get("resolution", args.resolution) not in PIPELINE_TYPES:
                raise SystemExit(f"{args.batch}:{n}: resolution must be one of {sorted(PIPELINE_TYPES)}")
            if row.get("remesh", "auto") not in ("auto", "on", "off"):
                raise SystemExit(f"{args.batch}:{n}: remesh must be auto, on or off")
            image = row["image"] if os.path.exists(row["image"]) else os.path.join(here, row["image"])
            item = argparse.Namespace(**vars(args))
            for key in ITEM_KEYS:
                if key in row:
                    setattr(item, key, row[key])
            item.id, item.image = row["id"], image
            item.out = os.path.join(args.out_dir, f"{row['id']}.glb")
            item.preview = os.path.join(args.out_dir, f"{row['id']}_preview.png")
            item.variant, item.save_raw = [], None
            items.append(item)
    missing = only - {i.id for i in items}
    if missing:
        raise SystemExit(f"--only names ids that are not in {args.batch}: {sorted(missing)}")
    return items


def file_sha256(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def up_to_date(item):
    """A model on disk counts only if it was made from this very image with these
    settings. Otherwise a new concept image would silently keep the old model."""
    try:
        with open(os.path.splitext(item.out)[0] + ".json") as fh:
            meta = json.load(fh)
    except (OSError, ValueError):
        return False
    if not (os.path.exists(item.out) and meta.get("ok")):
        return False
    wanted = {"resolution": item.resolution, "seed": item.seed, "faces_target": item.faces,
              "texture_size": item.texture_size, "image_sha256": file_sha256(item.image)}
    return all(meta.get(k) == v for k, v in wanted.items()) and (meta.get("export") or {}).get("remesh") == item.remesh


def log(msg):
    print(f"[image_to_3d] {msg}", file=sys.stderr, flush=True)


def gib(n):
    return round(n / 2**30, 2)


def setup_env(args):
    """Decide attention backend / precision / low-VRAM mode before importing trellis2."""
    os.environ.setdefault("OPENCV_IO_ENABLE_OPENEXR", "1")
    os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
    # FlexGEMM's autotune results and Triton's compiled kernels default to
    # ~/.flex_gemm and ~/.triton; keep them next to the environment instead.
    cache = os.path.join(sys.prefix, "cache")
    os.environ.setdefault("FLEX_GEMM_AUTOTUNE_CACHE_PATH", os.path.join(cache, "flex_gemm", "autotune_cache.json"))
    os.environ.setdefault("TRITON_CACHE_DIR", os.path.join(cache, "triton"))
    import torch
    if not torch.cuda.is_available():
        raise RuntimeError("no CUDA device visible")
    cap = torch.cuda.get_device_capability(0)
    free, total = torch.cuda.mem_get_info(0)
    info = {
        "gpu_name": torch.cuda.get_device_name(0),
        "compute_capability": f"{cap[0]}.{cap[1]}",
        "gpu_total_gib": gib(total),
        "gpu_free_gib_at_start": gib(free),
    }
    ampere_plus = cap >= (8, 0)
    # flash-attn 2 needs sm80+; xformers covers Turing.
    os.environ.setdefault("ATTN_BACKEND", "flash_attn" if ampere_plus else "xformers")
    info["attn_backend"] = os.environ["ATTN_BACKEND"]
    # FORGE_TRELLIS2_DTYPE=float16 forces the Turing precision on any card (for A/B tests).
    info["dit_dtype"] = os.environ.get("FORGE_TRELLIS2_DTYPE") or ("bfloat16" if ampere_plus else "float16")
    low = args.low_vram
    if low is None:
        low = gib(free) < LOW_VRAM_THRESHOLD_GIB
        info["low_vram_reason"] = f"auto: {gib(free)} GiB free vs threshold {LOW_VRAM_THRESHOLD_GIB}"
    else:
        info["low_vram_reason"] = "forced by flag"
    info["low_vram"] = bool(low)
    return torch, info


def find_repo():
    repo = os.environ.get("TRELLIS2_REPO")
    if not repo:
        runtime = os.environ.get("FORGE_RUNTIME") or os.path.join(os.path.expanduser("~"), "forge-runtime")
        repo = os.path.join(runtime, "repos", "TRELLIS.2")
    if not os.path.isdir(os.path.join(repo, "trellis2")):
        raise RuntimeError(f"TRELLIS.2 checkout not found at {repo} (set FORGE_RUNTIME or TRELLIS2_REPO)")
    return repo


class LazyBiRefNet:
    """Stand-in for trellis2.pipelines.rembg.BiRefNet that ignores the configured
    model name (gated RMBG-2.0), uses MIT-licensed BiRefNet, and only loads it
    the first time a background actually has to be removed."""

    def __init__(self, model_name=None, **_):
        self.requested = model_name
        self.impl = None
        self.device = "cpu"

    def _load(self):
        if self.impl is None:
            from trellis2.pipelines.rembg.BiRefNet import BiRefNet
            log(f"loading background model {BG_MODEL_ID} (config asked for {self.requested})")
            self.impl = BiRefNet(BG_MODEL_ID)
            self.impl.to(self.device)
        return self.impl

    def to(self, device):
        self.device = device
        if self.impl is not None:
            self.impl.to(device)

    def cuda(self):
        self.to("cuda")

    def cpu(self):
        self.to("cpu")

    def __call__(self, image):
        impl = self._load()
        impl.to("cuda")
        try:
            return impl(image)
        finally:
            if self.device == "cpu":
                impl.to("cpu")


def prepare_image(pipe, path, keep_background):
    from PIL import Image
    import numpy as np
    img = Image.open(path)
    img.load()
    if keep_background:
        # Whole frame is the object: give it a full alpha that is not all-255
        # (upstream treats an all-opaque alpha as "no alpha" and would run
        # background removal), then reuse upstream's crop/normalise code.
        rgb = img.convert("RGB")
        a = np.full((rgb.height, rgb.width), 255, np.uint8)
        a[0, 0] = 254
        rgba = Image.fromarray(np.dstack([np.array(rgb), a]), "RGBA")
        return pipe.preprocess_image(rgba), "kept"
    if img.mode in ("LA", "P"):
        img = img.convert("RGBA")
    if img.mode == "RGBA" and not np.all(np.array(img)[:, :, 3] == 255):
        return pipe.preprocess_image(img), "input alpha"
    return pipe.preprocess_image(img.convert("RGB")), f"removed with {BG_MODEL_ID}"


def mesh_health(tri):
    """How far the mesh is from one closed skin. Vertices are merged by position
    first (UV seams split them); then every edge should belong to exactly two
    triangles. `open` edges have one (a hole or a sheet border), `shared` edges
    have more than two (two shells glued together, or folded-over faces)."""
    import numpy as np
    v = np.asarray(tri.vertices, dtype=np.float64)
    f = np.asarray(tri.faces, dtype=np.int64)
    step = max(float(np.ptp(v, axis=0).max()), 1e-12) * 1e-5
    _, inverse = np.unique(np.round(v / step).astype(np.int64), axis=0, return_inverse=True)
    f = inverse.reshape(-1)[f]
    f = f[(f[:, 0] != f[:, 1]) & (f[:, 1] != f[:, 2]) & (f[:, 0] != f[:, 2])]
    edges = np.sort(np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]]), axis=1)
    _, counts = np.unique(edges, axis=0, return_counts=True)
    n = max(len(counts), 1)
    return {"edges": int(len(counts)), "open": int((counts == 1).sum()), "shared": int((counts > 2).sum()),
            "bad_edge_ratio": round(float((counts != 2).sum() / n), 4)}


REMESH_GRID = {"resolution": None}   # read by the patched remesh_narrow_band_dc; None = caller's value


def remesh_grid(setting, faces, native):
    """Grid for the dual-contouring remesh. Upstream remeshes on the generation grid
    (1024 or 1536), which costs gigabytes and minutes for a mesh that is decimated to a
    few thousand triangles right after; a coarse grid gives the same closed skin, and
    because its walls are at least two cells thick, thin panels survive decimation."""
    if setting == "native":
        return native
    if setting == "auto":
        return min(native, 256 if faces <= 6000 else 512 if faces <= 60000 else native)
    return min(native, int(setting))


def _install_coarse_remesh():
    """Let export_glb choose the remesh grid without editing upstream: to_glb() passes
    resolution = generation grid and scale = (resolution + 3*band) / resolution * size."""
    import cumesh.remeshing
    original = cumesh.remeshing.remesh_narrow_band_dc

    def remesh(vertices, faces, center, scale, resolution, band=1, **kw):
        grid = REMESH_GRID["resolution"]
        if grid and grid != resolution:
            size = scale * resolution / (resolution + 3 * band)
            scale, resolution = (grid + 3 * band) / grid * size, grid
        return original(vertices, faces, center=center, scale=scale, resolution=resolution, band=band, **kw)

    cumesh.remeshing.remesh_narrow_band_dc = remesh


def export_glb(o_voxel, pipe, mesh, res, faces, tex, path, webp, flat_material=False, remesh="auto",
               remesh_resolution="auto"):
    """Raw mesh -> decimated, textured GLB. Returns (seconds, report)."""
    import gc
    import torch
    t = time.time()
    REMESH_GRID["resolution"] = remesh_grid(remesh_resolution, faces, int(res))
    kw = dict(vertices=mesh.vertices, faces=mesh.faces, attr_volume=mesh.attrs,
              coords=mesh.coords, attr_layout=pipe.pbr_attr_layout, grid_size=res,
              aabb=[[-0.5, -0.5, -0.5], [0.5, 0.5, 0.5]],
              decimation_target=faces, texture_size=tex, verbose=False)
    report = {"remesh": remesh, "remesh_grid": REMESH_GRID["resolution"]}
    candidates = {}
    if remesh in ("auto", "on"):
        oom = None
        try:
            # upstream app.py / example.py settings
            candidates[True] = o_voxel.postprocess.to_glb(remesh=True, remesh_band=1, remesh_project=0, **kw)
        except (torch.OutOfMemoryError, RuntimeError) as e:
            if "out of memory" not in str(e):
                raise
            oom = str(e).splitlines()[0][:100]
        if oom:
            # The narrow-band dual-contouring remesh at 1024 is the export's memory
            # peak (a full-frame crate needed +3.8 GiB on an 11 GB card). Upstream's
            # other branch (simplify + clean, no remesh) needs far less. Continue
            # outside the except block so the failed attempt's tensors (held by the
            # traceback) are really freed.
            log(f"remesh ran out of memory ({oom}); exporting without remesh")
            report["remesh_out_of_memory"] = True
    if remesh in ("auto", "off") or not candidates:
        gc.collect()
        torch.cuda.synchronize()
        torch.cuda.empty_cache()
        candidates[False] = o_voxel.postprocess.to_glb(remesh=False, **kw)
    health = {used: mesh_health(glb) for used, glb in candidates.items()}
    # Fewest edges that are not shared by exactly two triangles wins; a tie goes to the remeshed skin.
    used = min(candidates, key=lambda u: (health[u]["bad_edge_ratio"], not u))
    glb = candidates[used]
    report["remeshed"] = used
    report["health"] = health[used]
    if len(candidates) > 1:
        report["rejected"] = {"remeshed": not used, **health[not used]}
    del candidates
    if flat_material:
        mat = glb.visual.material
        mat.metallicRoughnessTexture = None
        mat.metallicFactor = 0.0
        mat.roughnessFactor = 1.0
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    glb.export(path, extension_webp=webp)
    torch.cuda.empty_cache()
    return round(time.time() - t, 2), report


def mesh_stats(path):
    import trimesh
    scene = trimesh.load(path, force="scene")
    v = f = 0
    textures = {}
    for name, g in scene.geometry.items():
        v += len(g.vertices)
        f += len(g.faces)
        mat = getattr(getattr(g, "visual", None), "material", None)
        for key in ("baseColorTexture", "metallicRoughnessTexture", "normalTexture", "emissiveTexture"):
            im = getattr(mat, key, None) if mat is not None else None
            if im is not None:
                textures[key] = list(im.size)
        if mat is not None and getattr(mat, "metallicRoughnessTexture", None) is not None:
            import numpy as np
            mr = np.asarray(mat.metallicRoughnessTexture.convert("RGB"), dtype=np.float32) / 255
            textures["metallic_mean"] = round(float(mr[..., 2].mean()), 3)
            textures["roughness_mean"] = round(float(mr[..., 1].mean()), 3)
        elif mat is not None:
            textures["metallic_factor"] = getattr(mat, "metallicFactor", None)
            textures["roughness_factor"] = getattr(mat, "roughnessFactor", None)
    lo, hi = scene.bounds
    return {
        "vertices": int(v), "faces": int(f), "textures": textures,
        "bbox_min": [round(float(x), 4) for x in lo], "bbox_max": [round(float(x), 4) for x in hi],
        "up_axis": "+Y (glTF convention)", "file_bytes": os.path.getsize(path),
    }


def generate_one(ctx, a, resolution, result):
    """Image -> GLB for one asset at one resolution. Fills `result`; raises on failure."""
    torch, o_voxel, pipe, token_log, low_vram = ctx
    token_log.clear()
    torch.cuda.reset_peak_memory_stats()
    t = time.time()
    image, result["background"] = prepare_image(pipe, a.image, a.keep_background)
    result["preprocess_s"] = round(time.time() - t, 2)
    max_tokens = a.max_tokens or 49152
    t = time.time()
    outputs, (shape_slat, tex_slat, res) = pipe.run(
        image, seed=a.seed, preprocess_image=False,
        pipeline_type=PIPELINE_TYPES[resolution],
        max_num_tokens=max_tokens, return_latent=True)
    torch.cuda.synchronize()
    result["generate_s"] = round(time.time() - t, 2)
    result["effective_resolution"] = int(res)
    result["shape_tokens"] = token_log.get("tokens")
    result["sparse_structure_voxels"] = token_log.get("ss_voxels")
    result["max_num_tokens"] = max_tokens
    mesh = outputs[0]
    del outputs, shape_slat, tex_slat
    mesh.simplify(16777216)  # nvdiffrast limit (upstream example does the same)
    result["raw_vertices"] = int(mesh.vertices.shape[0])
    result["raw_faces"] = int(mesh.faces.shape[0])
    if a.save_raw:
        torch.save({"vertices": mesh.vertices.cpu(), "faces": mesh.faces.cpu(), "coords": mesh.coords.cpu(),
                    "attrs": mesh.attrs.cpu(), "res": int(res)}, a.save_raw)
    if low_vram:
        torch.cuda.empty_cache()

    result["export_s"], report = export_glb(o_voxel, pipe, mesh, res, a.faces, a.texture_size,
                                            a.out, a.webp, a.flat_material, a.remesh, a.remesh_resolution)
    result["remeshed"] = report["remeshed"]
    result["export"] = report
    result["mesh"] = mesh_stats(a.out)
    variants = []
    for spec in a.variant:
        f, tx, path = spec.split(":", 2)
        secs, report = export_glb(o_voxel, pipe, mesh, res, int(f), int(tx), path, a.webp, a.flat_material,
                                  a.remesh, a.remesh_resolution)
        variants.append({"out": path, "faces_target": int(f), "texture_size": int(tx),
                         "export_s": secs, "remeshed": report["remeshed"], "export": report, "mesh": mesh_stats(path)})
    if variants:
        result["variants"] = variants
    result["torch_peak_allocated_gib"] = gib(torch.cuda.max_memory_allocated())
    result["torch_peak_reserved_gib"] = gib(torch.cuda.max_memory_reserved())
    del mesh
    if a.preview:
        t = time.time()
        render_preview(a.out, a.preview)
        result["preview"] = a.preview
        result["preview_s"] = round(time.time() - t, 2)


def release_gpu(ctx):
    """After a failed attempt: a sub-model that was on the GPU when the error
    hit is still there in low-VRAM mode. Park everything in CPU RAM again."""
    import gc
    torch, _, pipe, _, low_vram = ctx
    gc.collect()
    if low_vram:
        for model in list(pipe.models.values()) + [getattr(pipe, "image_cond_model", None)]:
            try:
                model.cpu()
            except Exception:  # noqa: BLE001 - best effort
                pass
    torch.cuda.synchronize()
    torch.cuda.empty_cache()


def run_item(ctx, a):
    """One asset, stepping down a resolution each time the GPU runs out of memory."""
    torch = ctx[0]
    result = {"ok": False, "image": a.image, "out": a.out, "resolution": a.resolution,
              "seed": a.seed, "faces_target": a.faces, "texture_size": a.texture_size}
    try:
        result["image_sha256"] = file_sha256(a.image)
    except OSError:
        pass
    if getattr(a, "id", None):
        result = {"id": a.id, **result}
    t0 = time.time()
    ladder = [r for r in sorted(PIPELINE_TYPES, reverse=True) if r <= a.resolution]
    if a.no_fallback:
        ladder = ladder[:1]
    for i, resolution in enumerate(ladder):
        failure, oom = None, False
        try:
            generate_one(ctx, a, resolution, result)
            result["ok"] = True
        except Exception as e:  # noqa: BLE001 - report every failure as JSON + non-zero exit
            traceback.print_exc()
            failure = f"{type(e).__name__}: {e}"[:2000]
            oom = "out of memory" in str(e).lower()
        if failure is None:
            break
        # Outside the except block, so the traceback no longer pins the failed attempt's tensors.
        result["torch_peak_allocated_gib"] = gib(torch.cuda.max_memory_allocated())
        release_gpu(ctx)
        if not oom or i == len(ladder) - 1:
            result["error"] = failure
            break
        result.setdefault("out_of_memory_at", []).append(resolution)
        log(f"out of memory at {resolution}; retrying at {ladder[i + 1]}")
    result["item_s"] = round(time.time() - t0, 2)
    return result


def main(argv=None):
    args = parse_args(argv)
    shared = {}
    t_start = time.time()
    items = read_batch(args) if args.batch else [args]
    if args.batch and args.skip_existing:
        items = [i for i in items if not up_to_date(i)]
    if not items:
        print(json.dumps({"ok": True, "batch": args.batch, "items": 0}), flush=True)
        return 0
    try:
        torch, info = setup_env(args)
        shared.update(info)
        repo = find_repo()
        sys.path.insert(0, repo)
        shared["trellis2_repo_commit"] = _git_head(repo)

        import o_voxel
        _install_coarse_remesh()
        _release_torch_cache_before_cumesh(torch)
        from trellis2.pipelines import rembg as rembg_mod
        rembg_mod.BiRefNet = LazyBiRefNet
        from trellis2.modules import image_feature_extractor as ife
        _redirect_dinov3(ife)
        shared["dinov3_model"] = os.environ.get("FORGE_DINOV3_MODEL") or DINOV3_DEFAULT
        from trellis2.pipelines import Trellis2ImageTo3DPipeline
        import trellis2.models as t2models
        if os.environ.get("FORGE_TRELLIS2_SLOW_INIT") != "1":
            t2models.from_pretrained = _fast_from_pretrained(t2models)

        # ---- load -------------------------------------------------------
        t = time.time()
        pipe = Trellis2ImageTo3DPipeline.from_pretrained(MODEL_ID)
        shared["load_disk_s"] = round(time.time() - t, 2)
        t = time.time()
        if info["dit_dtype"] != "bfloat16":
            # Per-stage override for A/B tests, e.g. FORGE_TRELLIS2_STAGE_DTYPE="tex=float32"
            stage_dtype = dict(kv.split("=") for kv in os.environ.get("FORGE_TRELLIS2_STAGE_DTYPE", "").split(",") if "=" in kv)
            for k, m in pipe.models.items():
                if hasattr(m, "convert_to") and getattr(m, "dtype", None) == torch.bfloat16:
                    stage = k.split("_")[0]  # sparse / shape / tex
                    m.convert_to(getattr(torch, stage_dtype.get(stage, info["dit_dtype"])))
            shared["stage_dtype_override"] = stage_dtype or None
        pipe.low_vram = info["low_vram"]
        token_log = _install_cascade_token_cap(torch, pipe)
        if info["low_vram"]:
            _apply_decoder_memory_patches(torch, pipe)
        pipe.cuda()
        torch.cuda.synchronize()
        shared["load_gpu_s"] = round(time.time() - t, 2)
        shared["load_s"] = round(shared["load_disk_s"] + shared["load_gpu_s"], 2)
        ctx = (torch, o_voxel, pipe, token_log, info["low_vram"])
    except Exception as e:  # noqa: BLE001 - report every failure as JSON + non-zero exit
        traceback.print_exc()
        failed = {"ok": False, **shared, "error": f"{type(e).__name__}: {e}"[:2000],
                  "total_s": round(time.time() - t_start, 2)}
        if not args.batch:
            failed = {"image": args.image, "out": args.out, **failed}
        print(json.dumps(failed), flush=True)
        return 1

    if not args.batch:
        result = run_item(ctx, args)
        result.pop("item_s", None)
        result = {**{k: result[k] for k in ("ok", "image", "out", "resolution", "seed", "faces_target", "texture_size")},
                  **shared, **result, "total_s": round(time.time() - t_start, 2)}
        print(json.dumps(result), flush=True)
        return 0 if result["ok"] else 1

    os.makedirs(args.out_dir, exist_ok=True)
    failed = []
    for n, item in enumerate(items, 1):
        log(f"[{n}/{len(items)}] {item.id}: resolution {item.resolution}, {item.faces} faces")
        result = run_item(ctx, item)
        if not result["ok"]:
            failed.append(item.id)
        with open(os.path.join(args.out_dir, f"{item.id}.json"), "w") as fh:
            json.dump({**shared, **result}, fh, indent=1)
        print(json.dumps(result), flush=True)
    print(json.dumps({"ok": not failed, "batch": args.batch, "items": len(items), "failed": failed, **shared,
                      "total_s": round(time.time() - t_start, 2)}), flush=True)
    return 1 if failed else 0


def _install_cascade_token_cap(torch, pipe):
    """Replacement for Trellis2ImageTo3DPipeline.sample_shape_slat_cascade.
    Upstream lowers the high-res stage in 128 steps while the sparse token count
    exceeds max_num_tokens, but never below 1024, so the cap only acts on 1536.
    Decoder memory grows with that token count, so here the same loop may go
    below 1024 (down to 512); everything else is upstream's code. Also records
    the token count for the JSON report."""
    from trellis2.modules.sparse import SparseTensor
    log_ = {}
    orig_ss = pipe.sample_sparse_structure

    def sample_ss(*a, **k):
        coords = orig_ss(*a, **k)
        log_["ss_voxels"] = int(coords.shape[0])
        return coords

    def cascade(lr_cond, cond, flow_model_lr, flow_model, lr_resolution, resolution, coords,
                sampler_params={}, max_num_tokens=49152):
        self = pipe
        noise = SparseTensor(feats=torch.randn(coords.shape[0], flow_model_lr.in_channels).to(self.device), coords=coords)
        sampler_params = {**self.shape_slat_sampler_params, **sampler_params}
        if self.low_vram:
            flow_model_lr.to(self.device)
        slat = self.shape_slat_sampler.sample(flow_model_lr, noise, **lr_cond, **sampler_params,
                                              verbose=True, tqdm_desc="Sampling shape SLat").samples
        if self.low_vram:
            flow_model_lr.cpu()
        std = torch.tensor(self.shape_slat_normalization['std'])[None].to(slat.device)
        mean = torch.tensor(self.shape_slat_normalization['mean'])[None].to(slat.device)
        slat = slat * std + mean
        dec = self.models['shape_slat_decoder']
        if self.low_vram:
            dec.to(self.device)
            dec.low_vram = True
        hr_coords = dec.upsample(slat, upsample_times=4)
        if self.low_vram:
            dec.cpu()
            dec.low_vram = False
        hr_resolution = resolution
        while True:
            quant = torch.cat([hr_coords[:, :1],
                               ((hr_coords[:, 1:] + 0.5) / lr_resolution * (hr_resolution // 16)).int()], dim=1)
            coords = quant.unique(dim=0)
            num_tokens = coords.shape[0]
            floor = lr_resolution if self.low_vram else min(resolution, 1024)  # upstream: never below 1024
            if num_tokens < max_num_tokens or hr_resolution <= floor:
                if hr_resolution != resolution:
                    log(f"{num_tokens} tokens: resolution reduced from {resolution} to {hr_resolution} "
                        f"(max_num_tokens={max_num_tokens})")
                break
            hr_resolution -= 128
        log_["tokens"] = int(num_tokens)
        noise = SparseTensor(feats=torch.randn(coords.shape[0], flow_model.in_channels).to(self.device), coords=coords)
        if self.low_vram:
            flow_model.to(self.device)
        slat = self.shape_slat_sampler.sample(flow_model, noise, **cond, **sampler_params,
                                              verbose=True, tqdm_desc="Sampling shape SLat").samples
        if self.low_vram:
            flow_model.cpu()
        slat = slat * std + mean
        return slat, hr_resolution

    orig_shape = pipe.sample_shape_slat

    def sample_shape(cond, flow_model, coords, sampler_params={}):
        log_["tokens"] = int(coords.shape[0])
        return orig_shape(cond, flow_model, coords, sampler_params)

    pipe.sample_sparse_structure = sample_ss
    pipe.sample_shape_slat_cascade = cascade
    pipe.sample_shape_slat = sample_shape
    return log_


def _apply_decoder_memory_patches(torch, pipe, chunk_bytes=256 * 2**20):
    """Exact-math memory savings for the sparse VAE decoders, which are the
    peak at 1024 on an 11 GB card (a bulky input OOMed in tex_slat_decoder):
    - LayerNorm32 in row chunks (it is per-row; upstream makes a full fp32 copy),
    - SiLU in place on the freshly normalised tensors inside the VAE blocks,
    - the ConvNeXt block MLP (Linear 4x -> SiLU -> Linear) in row chunks,
    - the up-sampling res block adds its skip path after its last conv,
    - the decoded mesh parked in CPU RAM while the texture decoder runs."""
    import types
    import torch.nn.functional as F
    from trellis2.modules import norm as t2norm
    from trellis2.models.sc_vaes import sparse_unet_vae as suv

    base_ln_forward = torch.nn.LayerNorm.forward

    def ln32_forward(self, x):
        rows = max(1, chunk_bytes // max(1, x.shape[-1] * 4))
        if x.dim() != 2 or x.shape[0] <= rows:
            o = base_ln_forward(self, x.float())
            return o.to(x.dtype)
        out = torch.empty_like(x)
        for i in range(0, x.shape[0], rows):
            out[i:i + rows] = base_ln_forward(self, x[i:i + rows].float()).to(x.dtype)
        return out
    t2norm.LayerNorm32.forward = ln32_forward

    shim = types.SimpleNamespace(**{k: getattr(F, k) for k in dir(F) if not k.startswith("__")})
    shim.silu = lambda t, inplace=False: F.silu(t, inplace=True)
    suv.F = shim

    def convnext_forward(self, x):
        h = self.conv(x)
        feats = self.norm(h.feats)
        hidden = self.mlp[0].out_features
        rows = max(1, chunk_bytes // max(1, hidden * feats.element_size()))
        if feats.shape[0] <= rows:
            out = self.mlp(feats)
        else:
            out = torch.empty_like(feats)
            for i in range(0, feats.shape[0], rows):
                out[i:i + rows] = self.mlp(feats[i:i + rows])
        del feats
        return h.replace(out) + x
    suv.SparseConvNeXtBlock3d._forward = convnext_forward

    def c2s_forward(self, x, subdiv=None):
        # Upstream order: up-sample both h and x, then conv2, then add.
        # Here x is up-sampled only after conv2 and added in place, so the
        # up-sampled skip tensor is not alive during the largest convolution.
        if self.pred_subdiv:
            subdiv = self.to_subdiv(x)
        h = x.replace(self.norm1(x.feats))
        h = h.replace(F.silu(h.feats, inplace=True))
        h = self.conv1(h)
        sub_b = subdiv.replace(subdiv.feats > 0) if subdiv is not None else None
        h = self.updown(h, sub_b)
        h = h.replace(F.silu(self.norm2(h.feats), inplace=True))
        h = self.conv2(h)
        skip = self.skip_connection(self.updown(x, sub_b))
        h.feats.add_(skip.feats)
        del skip
        return (h, subdiv) if self.pred_subdiv else h
    suv.SparseResBlockC2S3d._forward = c2s_forward

    orig_decode_tex = pipe.decode_tex_slat
    parked = {}
    orig_decode_shape = pipe.decode_shape_slat

    def decode_shape(slat, resolution):
        meshes, subs = orig_decode_shape(slat, resolution)
        for m in meshes:
            m.vertices, m.faces = m.vertices.cpu(), m.faces.cpu()
        parked["meshes"] = meshes
        torch.cuda.empty_cache()
        return meshes, subs

    def decode_tex(slat, subs):
        ret = orig_decode_tex(slat, subs)
        for m in parked.pop("meshes", []):
            m.vertices, m.faces = m.vertices.cuda(), m.faces.cuda()
        return ret
    pipe.decode_shape_slat = decode_shape
    pipe.decode_tex_slat = decode_tex


def _release_torch_cache_before_cumesh(torch):
    """CuMesh (used by GLB export) allocates with cudaMalloc, outside PyTorch's
    caching allocator. Memory that PyTorch has cached but is not using is
    invisible to it, so on an 11 GB card the 1024 export ran out of memory in
    CuMesh.simplify while PyTorch sat on free blocks. Empty the cache first."""
    import cumesh
    import cumesh.remeshing

    def wrap(owner, name):
        fn = getattr(owner, name, None)
        if fn is None:
            return

        def wrapped(*a, **k):
            torch.cuda.synchronize()
            torch.cuda.empty_cache()
            return fn(*a, **k)
        setattr(owner, name, wrapped)

    for name in ("init", "simplify", "fill_holes", "remove_duplicate_faces", "uv_unwrap"):
        wrap(cumesh.CuMesh, name)
    wrap(cumesh.remeshing, "remesh_narrow_band_dc")


def _fast_from_pretrained(t2models):
    """Same as trellis2.models.from_pretrained, but skips the random weight
    initialisation that every checkpoint load overwrites anyway. Upstream builds
    each 1.3B DiT with torch.nn.init on the CPU (~19 s per DiT, ~95 s per run
    for five of them) before load_state_dict replaces every value."""
    import json
    import torch
    from safetensors.torch import load_file
    original = t2models.from_pretrained

    def no_init(t, *a, **k):
        return t

    def fast(path, **kwargs):
        if os.path.exists(f"{path}.json") and os.path.exists(f"{path}.safetensors"):
            config_file, model_file = f"{path}.json", f"{path}.safetensors"
        else:
            from huggingface_hub import hf_hub_download
            parts = path.split("/")
            repo_id, name = "/".join(parts[:2]), "/".join(parts[2:])
            config_file = hf_hub_download(repo_id, f"{name}.json")
            model_file = hf_hub_download(repo_id, f"{name}.safetensors")
        with open(config_file) as fh:
            config = json.load(fh)
        names = ["normal_", "uniform_", "kaiming_uniform_", "kaiming_normal_", "xavier_uniform_",
                 "xavier_normal_", "trunc_normal_", "constant_", "zeros_", "ones_"]
        saved = {n: getattr(torch.nn.init, n) for n in names}
        for n in names:
            setattr(torch.nn.init, n, no_init)
        try:
            model = t2models.__getattr__(config["name"])(**config["args"], **kwargs)
        finally:
            for n, fn in saved.items():
                setattr(torch.nn.init, n, fn)
        state = load_file(model_file)
        missing = set(model.load_state_dict(state, strict=False).missing_keys)
        params = {k for k, _ in model.named_parameters()}
        if missing & params:
            # Something the checkpoint does not cover would stay uninitialised:
            # fall back to upstream's loader (random init, then load).
            log(f"{path}: {len(missing & params)} parameters not in checkpoint; using upstream loader")
            return original(path, **kwargs)
        return model

    return fast


def _redirect_dinov3(ife):
    """Let FORGE_DINOV3_MODEL replace the image encoder id that pipeline.json names
    (facebook/dinov3-vitl16-pretrain-lvd1689m, gated) without editing upstream files."""
    override = os.environ.get("FORGE_DINOV3_MODEL")
    if not override or override == DINOV3_DEFAULT:
        return
    original = ife.DinoV3FeatureExtractor

    class DinoV3FromOverride(original):
        def __init__(self, model_name, *a, **kw):
            log(f"image encoder: {override} (pipeline.json asked for {model_name})")
            super().__init__(override, *a, **kw)

    ife.DinoV3FeatureExtractor = DinoV3FromOverride


def _git_head(repo):
    try:
        with open(os.path.join(repo, ".git", "HEAD")) as fh:
            ref = fh.read().strip()
        if ref.startswith("ref: "):
            with open(os.path.join(repo, ".git", ref[5:])) as fh:
                return fh.read().strip()
        return ref
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Preview: 4 views of the exported GLB, base colour x simple lambert, via nvdiffrast.
def render_preview(glb_path, png_path, size=512):
    import numpy as np
    import torch
    import trimesh
    import nvdiffrast.torch as dr
    from PIL import Image

    scene = trimesh.load(glb_path, force="scene")
    geom = list(scene.dump(concatenate=False)) if hasattr(scene, "dump") else list(scene.geometry.values())
    g = geom[0]
    v = torch.tensor(np.asarray(g.vertices), dtype=torch.float32, device="cuda")
    f = torch.tensor(np.asarray(g.faces), dtype=torch.int32, device="cuda")
    uv = torch.tensor(np.asarray(g.visual.uv), dtype=torch.float32, device="cuda")
    mat = g.visual.material
    tex_img = np.asarray(mat.baseColorTexture.convert("RGB"), dtype=np.float32) / 255.0
    tex = torch.tensor(tex_img, device="cuda")[None]
    uv_t = uv.clone()
    uv_t[:, 1] = 1 - uv_t[:, 1]  # glTF UV origin is top-left

    center = (v.max(0).values + v.min(0).values) / 2
    scale = (v.max(0).values - v.min(0).values).max()
    vn = (v - center) / scale * 1.6
    fn = torch.nn.functional.normalize(
        torch.cross(vn[f[:, 1].long()] - vn[f[:, 0].long()], vn[f[:, 2].long()] - vn[f[:, 0].long()], dim=1), dim=1)

    ctx = dr.RasterizeCudaContext()
    tiles = []
    for yaw in (0, 90, 180, 270):
        a = np.radians(yaw)
        pitch = np.radians(-20)
        ry = torch.tensor([[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]], dtype=torch.float32, device="cuda")
        rx = torch.tensor([[1, 0, 0], [0, np.cos(pitch), -np.sin(pitch)], [0, np.sin(pitch), np.cos(pitch)]], dtype=torch.float32, device="cuda")
        r = rx @ ry
        p = vn @ r.T
        clip = torch.cat([p[:, :2], (p[:, 2:3] * -0.3 + 0.5), torch.ones_like(p[:, :1])], 1)[None]
        rast, _ = dr.rasterize(ctx, clip, f, resolution=[size, size])
        texc, _ = dr.interpolate(uv_t[None], rast, f)
        col = dr.texture(tex, texc, filter_mode="linear")
        nrm = (fn @ r.T)
        fid = rast[..., 3].long() - 1
        n_pix = nrm[fid.clamp(min=0)]
        light = torch.nn.functional.normalize(torch.tensor([0.4, 0.6, -0.7], device="cuda"), dim=0)
        shade = 0.35 + 0.65 * (n_pix @ light).abs().unsqueeze(-1)
        img = col * shade
        mask = (rast[..., 3:4] > 0).float()
        img = img * mask + (1 - mask) * 0.92
        img = torch.flip(img[0], dims=[0])  # nvdiffrast y-up -> image rows
        tiles.append((img.clamp(0, 1).cpu().numpy() * 255).astype(np.uint8))
    os.makedirs(os.path.dirname(os.path.abspath(png_path)) or ".", exist_ok=True)
    Image.fromarray(np.concatenate(tiles, axis=1)).save(png_path)


if __name__ == "__main__":
    sys.exit(main())
