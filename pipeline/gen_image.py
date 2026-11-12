#!/usr/bin/env python3
"""Stage 1: concept images with Krea 2 Turbo.

    gen_image.py --prompt "a mossy boulder" --preset prop --out rock.png
    gen_image.py --batch concepts.jsonl --out-dir assets/concepts

Run with the Krea environment's Python and through the GPU wrapper:

    tools/gpu_run.sh image <label> -- "$FORGE_RUNTIME/envs/krea/bin/python" pipeline/gen_image.py ...

Batch mode exists because loading the model costs far more than one image:
a batch file is JSON Lines, one image per line, e.g.

    {"id": "rock_small", "preset": "prop", "subject": "a small mossy boulder", "seed": 7}

Keys: id (required), subject or prompt (one required), preset, seed, width,
height, steps. `prompt` is used verbatim; `subject` is wrapped by the preset.

Memory. The transformer is 26 GB in BF16 and the text encoder 9 GB, so nothing
fits a consumer card as shipped. The script never keeps two sub-models on the
GPU at once: all prompts are encoded first, then the text encoder is dropped,
then the transformer denoises every image, then it is dropped and the VAE decodes them. --memory chooses how the transformer is held:

  auto        "gguf" (works from 11 GB up)
  gguf        a GGUF-quantized transformer, all of it on the GPU: Q5_K_M (8.9 GB)
              on cards with 12 GB or more, Q4_K_M (7.5 GB) below that. Smallest
              download and fastest on small cards. Override the source with
              FORGE_KREA_GGUF_REPO / FORGE_KREA_GGUF_FILE.
  fp8         original weights on the GPU as 8-bit floats, cast per layer (about 13 GB)
  stream      original weights stay in RAM in BF16; each layer visits the GPU only
              while it computes (a few GB of VRAM, full precision, slower)
  stream-fp8  as "stream", but RAM holds 8-bit floats, halving what is copied per step
  full        original weights on the GPU (needs about 40 GB)

Only "gguf" works without the 26 GB of original transformer weights; the other
modes need the complete krea/Krea-2-Turbo repository.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

MODEL_ID = "krea/Krea-2-Turbo"
GGUF_REPO = os.environ.get("FORGE_KREA_GGUF_REPO", "vantagewithai/Krea-2-Turbo-GGUF")
GGUF_FILE = os.environ.get("FORGE_KREA_GGUF_FILE")  # unset: chosen from the card's memory
GGUF_BY_VRAM = [(12, "krea2_turbo-Q5_K_M.gguf"), (0, "krea2_turbo-Q4_K_M.gguf")]  # (min GiB, file)

# Presets describe the look in plain attributes. They never name a game, a
# studio or an artist: the style has to be reproducible from the words alone.
STYLE = (
    "stylized cel-shaded adventure game art, hand-painted, chunky simple shapes, "
    "flat warm colours with soft painted gradients, clean readable silhouette"
)
PRESETS = {
    # Inputs for image-to-3D: one object, whole object visible, even light,
    # nothing baked into the background.
    "prop": {
        "template": (
            "{subject}. A single game prop, centered, the whole object in frame with margin, "
            "three-quarter view from slightly above, " + STYLE + ", plain white background, "
            "even soft studio lighting, no ground, no cast shadow, no text"
        ),
        "width": 1024, "height": 1024,
    },
    # Rigging wants limbs separated from the body and a symmetric front view.
    "character": {
        "template": (
            "{subject}. Full-body character reference sheet showing one figure, front view, standing in an A-pose "
            "with both arms held straight and lowered about 40 degrees away from the body, a clear gap between each arm "
            "and the torso, hands open with fingers together and empty, "
            "legs slightly apart, feet flat, symmetrical, facing the camera, the whole figure in frame "
            "with margin, " + STYLE + ", plain white background, even soft studio lighting, "
            "no ground, no cast shadow, no text"
        ),
        "width": 1024, "height": 1024,
    },
    "creature": {
        "template": (
            "{subject}. A single small game creature, centered, the whole creature in frame with margin, "
            "three-quarter front view, " + STYLE + ", plain white background, even soft studio lighting, "
            "no ground, no cast shadow, no text"
        ),
        "width": 1024, "height": 1024,
    },
    "sky": {
        "template": (
            "{subject}. Seamless equirectangular 360 degree sky panorama, horizon line exactly at the "
            "vertical middle, painterly cel-shaded clouds, soft gradients, no ground objects, no sun disc, no text"
        ),
        "width": 2048, "height": 1024,
    },
    "texture": {
        "template": (
            "{subject}. Seamless tileable hand-painted game texture, top-down flat view, even lighting, "
            "no shadows, no perspective, no text"
        ),
        "width": 1024, "height": 1024,
    },
    "raw": {"template": "{subject}", "width": 1024, "height": 1024},
}


def build_prompt(job):
    if job.get("prompt"):
        return job["prompt"]
    preset = PRESETS[job.get("preset", "prop")]
    return preset["template"].format(subject=job["subject"].rstrip(". "))


def resolve_memory(memory):
    return "gguf" if memory == "auto" else memory


def pick_gguf_file():
    """Q5_K_M (8.9 GB) leaves no headroom on an 11 GB card; Q4_K_M (7.5 GB) does."""
    import torch

    if GGUF_FILE:
        return GGUF_FILE
    total_gib = torch.cuda.get_device_properties(0).total_memory / 2**30
    return next(name for floor, name in GGUF_BY_VRAM if total_gib >= floor)


def load_pipeline(memory, dtype, aux_dtype):
    from diffusers import Krea2Pipeline

    if memory != "gguf":
        return Krea2Pipeline.from_pretrained(MODEL_ID, torch_dtype=dtype)

    from diffusers import GGUFQuantizationConfig, Krea2Transformer2DModel
    from huggingface_hub import hf_hub_download

    path = hf_hub_download(GGUF_REPO, pick_gguf_file())
    transformer = Krea2Transformer2DModel.from_single_file(
        path,
        quantization_config=GGUFQuantizationConfig(compute_dtype=dtype),
        config=MODEL_ID, subfolder="transformer", torch_dtype=dtype,
    )
    return Krea2Pipeline.from_pretrained(MODEL_ID, transformer=transformer, torch_dtype=aux_dtype)


def encode_prompts(pipe, prompts, low_vram, device):
    """Encode every prompt, then remove the text encoder from the pipeline."""
    import gc

    import torch

    if low_vram:
        from diffusers.hooks import apply_group_offloading

        apply_group_offloading(pipe.text_encoder, onload_device=device,
                               offload_type="leaf_level", use_stream=True)
    else:
        pipe.text_encoder.to(device)

    encoded = []
    with torch.no_grad():
        for prompt in prompts:
            embeds, mask = pipe.encode_prompt(prompt, device=device)
            # Drop the padded positions. Text tokens carry no position in the
            # transformer and padding is masked out as a key everywhere, so
            # removing it changes nothing, and with no padding left the
            # transformer needs no attention mask (see drop_full_masks).
            keep = mask[0].bool()
            encoded.append((embeds[:, keep].to("cpu"), mask[:, keep].to("cpu")))

    pipe.text_encoder = None
    gc.collect()
    torch.cuda.empty_cache()
    return encoded


def place_transformer(pipe, memory, device, dtype):
    import torch

    fp8 = torch.float8_e4m3fn
    if memory in ("full", "gguf"):
        pipe.transformer.to(device)
    elif memory == "fp8":
        pipe.transformer.enable_layerwise_casting(storage_dtype=fp8, compute_dtype=dtype)
        pipe.transformer.to(device)
    elif memory in ("stream", "stream-fp8"):
        if memory == "stream-fp8":
            pipe.transformer.enable_layerwise_casting(storage_dtype=fp8, compute_dtype=dtype)
        pipe.transformer.enable_group_offload(
            onload_device=device, offload_type="leaf_level", use_stream=True
        )
    else:
        raise SystemExit(f"unknown --memory {memory}")



def drop_full_masks(transformer):
    """Treat an all-valid text mask as no mask.

    With any mask present, scaled-dot-product attention cannot use its
    memory-efficient kernels and builds a heads x tokens x tokens matrix:
    3.8 GiB at 1024x1024, on top of the weights.
    """
    forward = transformer.forward

    def patched(*args, encoder_attention_mask=None, **kwargs):
        if encoder_attention_mask is not None and bool(encoder_attention_mask.all()):
            encoder_attention_mask = None
        return forward(*args, encoder_attention_mask=encoder_attention_mask, **kwargs)

    transformer.forward = patched


def needs_chunked_attention(dtype):
    """PyTorch's fused attention kernels need an Ampere-class GPU for BF16
    (FP16 works from Maxwell up). Without one, attention falls back to plain
    matrix products that hold the full heads x tokens x tokens scores."""
    import torch

    major, _ = torch.cuda.get_device_capability(0)
    return major < 8 and dtype == torch.bfloat16


def install_chunked_attention(rows=512):
    """Same result as one big attention call, computed a slice of queries at a
    time, so the score matrix is rows x tokens instead of tokens x tokens."""
    import torch
    import torch.nn.functional as F

    original = F.scaled_dot_product_attention

    def chunked(query, key, value, attn_mask=None, **kwargs):
        total = query.shape[-2]
        if total <= rows:
            return original(query, key, value, attn_mask=attn_mask, **kwargs)
        parts = []
        for start in range(0, total, rows):
            mask = attn_mask
            if mask is not None and mask.shape[-2] == total:
                mask = mask[..., start:start + rows, :]
            parts.append(original(query[..., start:start + rows, :], key, value, attn_mask=mask, **kwargs))
        return torch.cat(parts, dim=-2)

    F.scaled_dot_product_attention = chunked


def decode_latents(pipe, latents, height, width, device):
    """The pipeline's own decode step, run after the transformer has left the GPU."""
    import torch

    vae = pipe.vae
    latents = pipe._unpack_latents(latents.to(device), height, width).to(vae.dtype)
    z = vae.config.z_dim
    mean = torch.tensor(vae.config.latents_mean).view(1, z, 1, 1, 1).to(latents.device, latents.dtype)
    inv_std = 1.0 / torch.tensor(vae.config.latents_std).view(1, z, 1, 1, 1).to(latents.device, latents.dtype)
    with torch.no_grad():
        image = vae.decode(latents / inv_std + mean, return_dict=False)[0][:, :, 0]
    return pipe.image_processor.postprocess(image, output_type="pil")[0]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--prompt", help="verbatim prompt (skips presets)")
    ap.add_argument("--subject", help="what to draw; wrapped by --preset")
    ap.add_argument("--preset", default="prop", choices=sorted(PRESETS))
    ap.add_argument("--out", help="output PNG (single-image mode)")
    ap.add_argument("--batch", help="JSON Lines file of jobs")
    ap.add_argument("--out-dir", help="output directory (batch mode)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--width", type=int)
    ap.add_argument("--height", type=int)
    ap.add_argument("--steps", type=int, default=8)
    ap.add_argument("--memory", default="auto", choices=["auto", "gguf", "fp8", "stream", "stream-fp8", "full"])
    ap.add_argument("--dtype", default="auto", choices=["auto", "bf16", "fp16"],
                    help="transformer compute precision; auto = bf16 on Ampere or newer cards, fp16 on older ones "
                         "(no BF16 hardware path there: 2.3x slower on a 2080 Ti)")
    ap.add_argument("--attention", default="auto", choices=["auto", "native", "chunked"],
                    help="chunked computes attention a slice at a time; auto uses it for BF16 on pre-Ampere cards")
    ap.add_argument("--skip-existing", action="store_true", help="batch mode: keep images that were already made from the same prompt, seed and size")
    ap.add_argument("--dry-run", action="store_true", help="print the prompts and exit")
    args = ap.parse_args()

    if args.batch:
        if not args.out_dir:
            ap.error("--batch needs --out-dir")
        jobs = [json.loads(line) for line in Path(args.batch).read_text().splitlines()
                if line.strip() and not line.lstrip().startswith("#")]
        out_dir = Path(args.out_dir)
        for job in jobs:
            job["out"] = str(out_dir / f"{job['id']}.png")
    else:
        if not args.out or not (args.prompt or args.subject):
            ap.error("need --out and one of --prompt / --subject (or use --batch)")
        jobs = [{"id": Path(args.out).stem, "prompt": args.prompt, "subject": args.subject,
                 "preset": args.preset, "seed": args.seed, "out": args.out}]

    for job in jobs:
        preset = PRESETS[job.get("preset", args.preset)]
        job["full_prompt"] = build_prompt({**job, "preset": job.get("preset", args.preset)})
        job["width"] = job.get("width") or args.width or preset["width"]
        job["height"] = job.get("height") or args.height or preset["height"]
        job["steps"] = job.get("steps") or args.steps
        job.setdefault("seed", args.seed)

    if args.dry_run:
        for job in jobs:
            print(f"[{job['id']}] {job['width']}x{job['height']} seed {job['seed']}\n  {job['full_prompt']}\n")
        return 0

    def up_to_date(job):
        """An image on disk counts only if it was made from this prompt, seed and size.
        Otherwise editing a line of the batch file would silently keep the old picture."""
        out = Path(job["out"])
        if not out.exists():
            return False
        try:
            meta = json.loads(out.with_suffix(".json").read_text())
        except (OSError, ValueError):
            return False
        return (meta.get("prompt") == job["full_prompt"] and meta.get("seed") == job["seed"]
                and meta.get("steps") == job["steps"] and meta.get("width") == job["width"]
                and meta.get("height") == job["height"])

    todo = [j for j in jobs if not (args.skip_existing and up_to_date(j))]
    if not todo:
        print(json.dumps({"stage": "image", "images": 0, "note": "nothing to do"}))
        return 0

    import torch

    device = torch.device("cuda")
    if args.dtype == "auto":
        args.dtype = "bf16" if torch.cuda.get_device_capability(0)[0] >= 8 else "fp16"
    dtype = torch.bfloat16 if args.dtype == "bf16" else torch.float16
    aux_dtype = torch.bfloat16  # text encoder and VAE; FP16 is not safe for them
    memory = resolve_memory(args.memory)
    # The 9 GB text encoder only fits beside nothing; on small cards stream it.
    total_gib = torch.cuda.get_device_properties(0).total_memory / 2**30
    low_vram = memory in ("stream", "stream-fp8") or total_gib < 16

    t0 = time.time()
    pipe = load_pipeline(memory, dtype, aux_dtype)
    load_s = time.time() - t0

    t0 = time.time()
    encoded = encode_prompts(pipe, [j["full_prompt"] for j in todo], low_vram, device)
    encode_s = time.time() - t0
    encode_peak = torch.cuda.max_memory_allocated() / 2**20
    torch.cuda.reset_peak_memory_stats()

    t0 = time.time()
    place_transformer(pipe, memory, device, dtype)
    drop_full_masks(pipe.transformer)
    attention = args.attention
    if attention == "auto":
        attention = "chunked" if needs_chunked_attention(dtype) else "native"
    if attention == "chunked":
        install_chunked_attention()
    place_s = time.time() - t0

    # Denoise everything first and keep only latents; the VAE stays in RAM.
    denoised = []
    for job, (embeds, mask) in zip(todo, encoded):
        gen = torch.Generator("cpu").manual_seed(int(job["seed"]))
        t0 = time.time()
        latents = pipe(
            prompt_embeds=embeds.to(device, dtype),
            prompt_embeds_mask=mask.to(device),
            num_inference_steps=job["steps"],
            guidance_scale=0.0,
            width=job["width"],
            height=job["height"],
            generator=gen,
            output_type="latent",
        ).images
        denoised.append((latents.to("cpu"), time.time() - t0))
        print(f"[gen_image] {job['id']}: denoised in {denoised[-1][1]:.1f}s", file=sys.stderr)
    denoise_peak = torch.cuda.max_memory_allocated() / 2**20

    # Swap: transformer out, VAE in.
    import gc

    pipe.transformer = None
    gc.collect()
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    pipe.vae.to(device)
    if hasattr(pipe.vae, "enable_tiling"):
        pipe.vae.enable_tiling()

    results = []
    for job, (latents, denoise_s) in zip(todo, denoised):
        out = Path(job["out"])
        out.parent.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        image = decode_latents(pipe, latents, job["height"], job["width"], device)
        decode_s = time.time() - t0
        image.save(out)
        seconds = denoise_s + decode_s
        # The prompt travels with the image so any asset can be regenerated.
        out.with_suffix(".json").write_text(json.dumps({
            "id": job["id"], "model": MODEL_ID, "prompt": job["full_prompt"],
            "seed": job["seed"], "steps": job["steps"], "width": job["width"],
            "height": job["height"], "memory": memory, "dtype": args.dtype,
            "gguf": pick_gguf_file() if memory == "gguf" else None,
            "denoise_seconds": round(denoise_s, 2), "decode_seconds": round(decode_s, 2),
        }, indent=2, ensure_ascii=False) + "\n")
        results.append({"id": job["id"], "out": str(out), "seconds": round(seconds, 2),
                        "denoise_seconds": round(denoise_s, 2), "decode_seconds": round(decode_s, 2)})
        print(f"[gen_image] {job['id']}: decoded in {decode_s:.1f}s -> {out}", file=sys.stderr)

    peak = torch.cuda.max_memory_allocated() / 2**20
    print(json.dumps({
        "stage": "image", "model": MODEL_ID, "memory": memory, "dtype": args.dtype,
        "gguf": pick_gguf_file() if memory == "gguf" else None, "attention": attention,
        "gpu": torch.cuda.get_device_name(0),
        "load_seconds": round(load_s, 1), "encode_seconds": round(encode_s, 1),
        "place_seconds": round(place_s, 1), "images": len(results),
        "mean_seconds": round(sum(r["seconds"] for r in results) / len(results), 2),
        "first_seconds": results[0]["seconds"],
        "peak_torch_mib_encode": round(encode_peak), "peak_torch_mib_denoise": round(denoise_peak),
        "peak_torch_mib_decode": round(peak),
        "results": results,
    }))
    return 0


if __name__ == "__main__":
    os.environ.setdefault("HF_HUB_ENABLE_HF_TRANSFER", "0")
    sys.exit(main())
