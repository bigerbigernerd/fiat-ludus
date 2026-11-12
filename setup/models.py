#!/usr/bin/env python3
"""The weight files the pipeline loads, and tools to list, check and fetch them.

    models.py plan     [--only GROUP ...] [--offline]   sizes + cache state, no download
    models.py download [--only GROUP ...]               fetch what is missing (resumes)
    models.py check    [--only GROUP ...]               one line per file group, for setup/check.sh

Run by setup/download_models.sh and setup/check.sh with a Python that has
huggingface_hub 0.36 (any of the trellis2/unirig/yue2 envs, or `uv run`).

Hugging Face files go to the normal cache that huggingface_hub resolves
(HF_HUB_CACHE / HUGGINGFACE_HUB_CACHE / HF_HOME); nothing here names a path.
Two small files for the music stage's vocal check go into the yue2 env instead,
because pipeline/make_bgm.py looks for them there.

Only the files the stages load are listed, not whole repos: UniRig's repo is
11.5 GB but inference reads 5.8 GB; Krea's transformer (26 GB in BF16) is
replaced by one GGUF file; TRELLIS.2-4B's two encoders (1.4 GB) are not used
for image-to-3D. Files are fetched at the repo's `main`, because that is what
the pipeline asks for at run time; when `main` has moved past the commit we
tested, a warning says so.
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import urllib.request

# --------------------------------------------------------------------------- what
# Each repo: (repo id, tested commit, gated, [files]).
# gated: None, "auto" (accept the licence on the model page; approved at once) or
# "manual" (the owner reviews each request).

KREA = ("krea/Krea-2-Turbo", "98e0fe118d17c9e3547fbb2e25acdbae2cadf7c7", "auto", [
    # pipeline/gen_image.py takes text encoder, VAE, tokenizer and configs from
    # the official repo and the transformer from a GGUF file (next entry).
    "model_index.json",
    "scheduler/scheduler_config.json",
    "tokenizer/tokenizer.json", "tokenizer/tokenizer_config.json", "tokenizer/chat_template.jinja",
    "text_encoder/config.json", "text_encoder/model.safetensors",
    "vae/config.json", "vae/diffusion_pytorch_model.safetensors",
    "transformer/config.json", "transformer/diffusion_pytorch_model.safetensors.index.json",
])
KREA_GGUF_REPO = ("vantagewithai/Krea-2-Turbo-GGUF", "390847e2835c16bdf82303731e3d35b874760d89", None)
# gen_image.py picks Q5_K_M (8.9 GB) on cards with >= 12 GiB, Q4_K_M (7.5 GB) below.
GGUF_FILES = {"q5": "krea2_turbo-Q5_K_M.gguf", "q4": "krea2_turbo-Q4_K_M.gguf"}
GGUF_MIN_GIB = 12

TRELLIS_MODELS = [  # the models pipeline.json of TRELLIS.2-4B names (all loaded eagerly)
    "ss_flow_img_dit_1_3B_64_bf16",
    "shape_dec_next_dc_f16c32_fp16",
    "slat_flow_img2shape_dit_1_3B_512_bf16",
    "slat_flow_img2shape_dit_1_3B_1024_bf16",
    "tex_dec_next_dc_f16c32_fp16",
    "slat_flow_imgshape2tex_dit_1_3B_512_bf16",
    "slat_flow_imgshape2tex_dit_1_3B_1024_bf16",
]
TRELLIS = ("microsoft/TRELLIS.2-4B", "af44b45f2e35a493886929c6d786e563ec68364d", None,
           ["pipeline.json"] + [f"ckpts/{m}.{ext}" for m in TRELLIS_MODELS for ext in ("json", "safetensors")])
TRELLIS_SSDEC = ("microsoft/TRELLIS-image-large", "25e0d31ffbebe4b5a97464dd851910efc3002d96", None,
                 ["ckpts/ss_dec_conv3d_16l8_fp16.json", "ckpts/ss_dec_conv3d_16l8_fp16.safetensors"])
# Background removal: our wrapper uses BiRefNet (MIT) instead of the gated,
# non-commercial briaai/RMBG-2.0 that pipeline.json names. trust_remote_code
# loads birefnet.py + BiRefNet_config.py from the repo.
BIREFNET = ("ZhengPeng7/BiRefNet", "e2bf8e4460fc8fa32bba5ea4d94b3233d367b0e4", None,
            ["config.json", "birefnet.py", "BiRefNet_config.py", "model.safetensors"])
DINOV3_FILES = ["config.json", "preprocessor_config.json", "model.safetensors"]
DINOV3_OFFICIAL = "facebook/dinov3-vitl16-pretrain-lvd1689m"
# Byte-identical ungated copy (verified in docs/logs/trellis2-setup.md section 4).
DINOV3_COPY = ("camenduru/dinov3-vitl16-pretrain-lvd1689m", "3c276edd87d6f6e569ff0c4400e086807d0f3881")

UNIRIG = ("VAST-AI/UniRig", "36842e2b5947e9e60f89275b83208c8e74071c63", None, [
    "skeleton/articulation-xl_quantization_256/model.ckpt",
    "skin/articulation-xl/model.ckpt",
])
OPT350M = ("facebook/opt-350m", "08ab08cc4b72ff5593870b5d527cf4230323703c", None, ["config.json"])

# yue2's loader asks snapshot_download for this file set (src/yue2/storage.py
# MODEL_FILES); having all of them cached keeps a later online call a no-op.
YUE2_LICENCES = ["LICENSE", "THIRD_PARTY_NOTICES.md",
                 "licenses/SnakeBeta-NVIDIA-MIT.txt", "licenses/stable-audio-tools-MIT.txt"]
YUE2 = ("m-a-p/YuE2-3B", "8f24312f187763b9854adeec87e437e2b03bbff1", None, [
    "config.json", "generation_config.json", "yue2_generation_config.json", "weights_manifest.json",
    "model.safetensors", "qwen.tiktoken", "modeling_yue2.py"] + YUE2_LICENCES)
YUE2_VAE = ("m-a-p/YuE2-Vae", "152733a19ad43aa67e367f9b5503ef8075bb5126", None, [
    "config.json", "weights_manifest.json", "model.safetensors", "modeling_vae.py"] + YUE2_LICENCES)
# Vocal check of make_bgm.py (CPU). Whisper is only loaded when Demucs flags a
# passage; it lives in the env because make_bgm.py passes download_root there.
WHISPER = ("Systran/faster-whisper-small", "536b0662742c02347bc0e980a01041f333bce120", None,
           ["config.json", "model.bin", "tokenizer.json", "vocabulary.txt"])

GROUPS = ["krea", "trellis2", "unirig", "yue2"]


def runtime():
    return os.environ.get("FORGE_RUNTIME") or os.path.expanduser("~/forge-runtime")


def gpu_mem_gib():
    """Total memory of each visible GPU in GiB (empty list without nvidia-smi)."""
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=20).stdout
        return [int(x) / 1024 for x in out.split()]
    except (OSError, subprocess.SubprocessError, ValueError):
        return []


def gguf_choice(arg):
    """Which GGUF files to fetch: 'q4', 'q5', 'both' or 'auto' (by the GPUs present)."""
    if arg in ("q4", "q5"):
        return [arg]
    if arg == "both":
        return ["q5", "q4"]
    mems = gpu_mem_gib()
    if not mems:
        return ["q4"]
    picks = []
    if any(m >= GGUF_MIN_GIB for m in mems):
        picks.append("q5")
    if any(m < GGUF_MIN_GIB for m in mems):
        picks.append("q4")
    return picks


def dinov3_repo():
    """(repo, tested commit or None, gated) for the image encoder in use."""
    override = os.environ.get("FORGE_DINOV3_MODEL")
    if override and override != DINOV3_OFFICIAL:
        return (override, DINOV3_COPY[1] if override == DINOV3_COPY[0] else None, None)
    return (DINOV3_OFFICIAL, None, "manual")


def plan(groups, gguf="auto"):
    """Ordered list of (group, kind, repo, commit, gated, file, extra) entries."""
    items = []
    def add(group, entry, files=None):
        repo, commit, gated = entry[:3]
        for f in files if files is not None else entry[3]:
            items.append({"group": group, "kind": "hf", "repo": repo, "commit": commit, "gated": gated, "file": f})
    if "krea" in groups:
        add("krea", KREA)
        add("krea", KREA_GGUF_REPO, [GGUF_FILES[q] for q in gguf_choice(gguf)])
    if "trellis2" in groups:
        add("trellis2", TRELLIS)
        add("trellis2", TRELLIS_SSDEC)
        add("trellis2", dinov3_repo(), DINOV3_FILES)
        add("trellis2", BIREFNET)
    if "unirig" in groups:
        add("unirig", UNIRIG)
        add("unirig", OPT350M)
    if "yue2" in groups:
        add("yue2", YUE2)
        add("yue2", YUE2_VAE)
        env = os.path.join(runtime(), "envs", "yue2")
        for f in WHISPER[3]:
            items.append({"group": "yue2", "kind": "hf", "repo": WHISPER[0], "commit": WHISPER[1], "gated": None,
                          "file": f, "cache_dir": os.path.join(env, "share", "whisper")})
        items.append({"group": "yue2", "kind": "url", "repo": "htdemucs (Demucs)", "commit": None, "gated": None,
                      "file": os.environ["HTDEMUCS_FILE"],
                      "url": os.environ["HTDEMUCS_URL"], "sha256": os.environ["HTDEMUCS_SHA256"],
                      "dest": os.path.join(env, "share", "torch-hub", "hub", "checkpoints", os.environ["HTDEMUCS_FILE"])})
    return items


# --------------------------------------------------------------------------- state

def cache_state(item):
    """(cached path or None, commit of the cached snapshot or None)."""
    if item["kind"] == "url":
        return (item["dest"] if os.path.isfile(item["dest"]) else None), None
    from huggingface_hub import try_to_load_from_cache
    path = try_to_load_from_cache(item["repo"], item["file"], cache_dir=item.get("cache_dir"), revision="main")
    if not isinstance(path, str):
        return None, None
    # .../snapshots/<commit>/<file>
    rel = os.path.normpath(path).split(os.sep)
    i = rel.index("snapshots") if "snapshots" in rel else -1
    return path, (rel[i + 1] if i >= 0 else None)


def remote_sizes(items):
    """Fill item['size'] from the Hub (one request per repo) or from a HEAD request."""
    from huggingface_hub import HfApi
    api = HfApi()
    by_repo = {}
    for it in items:
        if it["kind"] == "hf":
            by_repo.setdefault(it["repo"], []).append(it)
    for repo, its in by_repo.items():
        try:
            infos = api.get_paths_info(repo, [it["file"] for it in its], revision="main")
            sizes = {i.path: getattr(i, "size", None) for i in infos}
            head = api.model_info(repo).sha
        except Exception as exc:  # gated without access, offline, ...
            name = type(exc).__name__
            for it in its:
                it.setdefault("remote_error", name)
            continue
        for it in its:
            it["size"] = sizes.get(it["file"])
            it["main"] = head
    for it in items:
        if it["kind"] == "url":
            try:
                req = urllib.request.Request(it["url"], method="HEAD")
                with urllib.request.urlopen(req, timeout=30) as r:
                    it["size"] = int(r.headers.get("Content-Length") or 0) or None
            except Exception as exc:
                it["remote_error"] = type(exc).__name__


def human(n):
    if n is None:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1000 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1000


def gate_note(repo, gated):
    if gated == "auto":
        return (f"{repo} is gated: open https://huggingface.co/{repo} while logged in and accept the licence "
                f"(access is granted at once), then make sure HF_TOKEN (or `hf auth login`) is that account's token.")
    if gated == "manual":
        return (f"{repo} is gated with manual approval: request access at https://huggingface.co/{repo} and wait. "
                f"Or use the byte-identical ungated copy: put FORGE_DINOV3_MODEL={DINOV3_COPY[0]} in .env.local "
                f"and rerun (docs/logs/trellis2-setup.md section 4 shows how it was verified; the DINOv3 License still applies).")
    return ""


# --------------------------------------------------------------------------- commands

def cmd_plan(items, offline):
    from huggingface_hub import constants
    print(f"Hugging Face cache: {constants.HF_HUB_CACHE}")
    if not offline:
        remote_sizes(items)
    missing_total = 0
    for it in items:  # without the Hub, fall back to the size of the cached file
        path, _ = cache_state(it)
        if not it.get("size") and path:
            it["size"] = os.path.getsize(path)
    for group in GROUPS:
        its = [i for i in items if i["group"] == group]
        if not its:
            continue
        total = sum(i.get("size") or 0 for i in its)
        unknown = sum(1 for i in its if not i.get("size"))
        need = 0
        print(f"\n== {group}: {len(its)} files, {human(total)}" + (f" + {unknown} of unknown size" if unknown else ""))
        for it in its:
            path, snap = cache_state(it)
            if path and it.get("size") and os.path.getsize(path) != it["size"]:
                state = f"cached, but {human(os.path.getsize(path))} instead of {human(it['size'])}"
            elif path:
                state = "cached"
            else:
                state = "MISSING"
                need += it.get("size") or 0
            size = it.get("size") or (os.path.getsize(path) if path else None)
            note = ""
            if it.get("commit") and it.get("main") and it["main"] != it["commit"]:
                note = f"  (main is now {it['main'][:8]}, tested {it['commit'][:8]})"
            if snap and it.get("commit") and snap != it["commit"]:
                note += f"  (cached snapshot {snap[:8]}, tested {it['commit'][:8]})"
            if it.get("remote_error"):
                note += f"  (Hub: {it['remote_error']})"
            print(f"  {state:8} {human(size):>9}  {it['repo']}/{it['file']}{note}")
        print(f"  -> to download: {human(need)}")
        missing_total += need
        for repo, gated in sorted({(i["repo"], i["gated"]) for i in its if i["gated"]}):
            print("  note: " + gate_note(repo, gated))
    os.makedirs(constants.HF_HUB_CACHE, exist_ok=True)
    free = shutil.disk_usage(constants.HF_HUB_CACHE).free
    print(f"\nTotal still to download: {human(missing_total)}; free space at the cache: {human(free)}")
    if missing_total > free:
        print("Not enough free space: point HF_HOME (or HF_HUB_CACHE) at a bigger disk before downloading.")
        return 1
    return 0


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 22), b""):
            h.update(block)
    return h.hexdigest()


def fetch_url(it):
    dest = it["dest"]
    if os.path.isfile(dest) and sha256(dest) == it["sha256"]:
        return "cached"
    env_dir = os.path.join(runtime(), "envs", "yue2")
    if not os.path.isdir(os.path.join(env_dir, "conda-meta")):
        raise RuntimeError("the yue2 env does not exist yet; run setup/env_yue2.sh first")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    part = dest + ".part"
    subprocess.run(["curl", "-fL", "--progress-bar", "--retry", "5", "-C", "-", "-o", part, it["url"]], check=True)
    got = sha256(part)
    if got != it["sha256"]:
        os.remove(part)
        raise RuntimeError(f"sha256 {got} != expected {it['sha256']}; file deleted, rerun to fetch it again")
    os.replace(part, dest)
    return "downloaded"


def cmd_download(items):
    from huggingface_hub import hf_hub_download, constants
    print(f"Hugging Face cache: {constants.HF_HUB_CACHE}")
    print(f"Plain HTTPS downloads (HF_HUB_DISABLE_XET={os.environ.get('HF_HUB_DISABLE_XET', '')}); "
          "an interrupted file resumes on the next run.")
    remote_sizes(items)
    todo_all = 0
    for group in GROUPS:
        its = [i for i in items if i["group"] == group]
        if its:
            todo = sum((i.get("size") or 0) for i in its if not cache_state(i)[0])
            todo_all += todo
            print(f"  {group:9} {len(its):3} files, {human(sum(i.get('size') or 0 for i in its)):>9} in all, "
                  f"{human(todo):>9} still to download")
    os.makedirs(constants.HF_HUB_CACHE, exist_ok=True)
    free = shutil.disk_usage(constants.HF_HUB_CACHE).free
    if todo_all > free:
        print(f"Only {human(free)} free at {constants.HF_HUB_CACHE}, {human(todo_all)} needed. "
              "Point HF_HOME (or HF_HUB_CACHE) at a bigger disk and rerun.")
        return 1
    failed, blocked = [], set()
    for it in items:
        label = f"{it['repo']}/{it['file']}"
        if it["repo"] in blocked:
            continue
        if it.get("commit") and it.get("main") and it["main"] != it["commit"]:
            print(f"WARNING: {it['repo']} main is {it['main'][:8]}, the pipeline was tested with {it['commit'][:8]}")
        try:
            if it["kind"] == "url":
                state = fetch_url(it)
            else:
                path, _ = cache_state(it)
                if path:
                    state = "cached"
                else:
                    print(f"downloading {label} ({human(it.get('size'))})", flush=True)
                    hf_hub_download(it["repo"], it["file"], revision="main", cache_dir=it.get("cache_dir"))
                    state = "downloaded"
            print(f"  ok {state:10} {label}", flush=True)
        except Exception as exc:
            name = type(exc).__name__
            print(f"  FAILED {label}: {name}: {str(exc).splitlines()[0][:200] if str(exc) else ''}", flush=True)
            if name in ("GatedRepoError", "RepositoryNotFoundError") or "403" in str(exc) or "401" in str(exc):
                blocked.add(it["repo"])
                note = gate_note(it["repo"], it["gated"]) or (
                    f"{it['repo']} refused access; check that HF_TOKEN is set and has read access.")
                print("  -> " + note)
            failed.append(label)
    if failed:
        print(f"\n{len(failed)} file(s) not downloaded; fix the cause above and rerun (finished files are kept).")
        return 1
    print("\nAll listed files are present.")
    return 0


def cmd_check(items):
    """One line per repo: OK / FAIL / WARN, for setup/check.sh."""
    bad = 0
    seen = []
    for it in items:
        if key_of(it) not in seen:
            seen.append(key_of(it))
    for group, repo in seen:
        its = [i for i in items if key_of(i) == (group, repo)]
        missing, snaps, size = [], set(), 0
        for it in its:
            path, snap = cache_state(it)
            if path:
                size += os.path.getsize(path)
                if snap:
                    snaps.add(snap)
                if it["kind"] == "url" and sha256(path) != it["sha256"]:
                    missing.append(it["file"] + " (bad sha256)")
            else:
                missing.append(it["file"])
        commit = its[0].get("commit")
        what = f"weights {repo}"
        if missing:
            level = "FAIL"
            if repo == DINOV3_OFFICIAL:
                copy_ok = all(cache_state({"kind": "hf", "repo": DINOV3_COPY[0], "file": f})[0] for f in DINOV3_FILES)
                if copy_ok:
                    print(f"WARN  {group:9} {what}: not cached, but the verified copy {DINOV3_COPY[0]} is; "
                          f"set FORGE_DINOV3_MODEL={DINOV3_COPY[0]} (e.g. in .env.local) or image_to_3d.py "
                          f"will try to download the gated official repo")
                    continue
            bad += 1
            print(f"{level:5} {group:9} {what}: missing {', '.join(missing[:3])}"
                  f"{' ...' if len(missing) > 3 else ''} -> setup/download_models.sh --only {group}")
        else:
            note = ""
            if commit and snaps and snaps != {commit}:
                note = f" (snapshot {','.join(s[:8] for s in snaps)}, tested {commit[:8]})"
            print(f"{'WARN' if note else 'OK':5} {group:9} {what}: {len(its)} file(s), {human(size)}{note}")
    return 1 if bad else 0


def key_of(item):
    return (item["group"], item["repo"])


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=["plan", "download", "check"])
    ap.add_argument("--only", action="append", choices=GROUPS,
                    help="limit to one group; repeat for several (default: all)")
    ap.add_argument("--gguf", default=os.environ.get("FORGE_KREA_GGUF", "auto"), choices=["auto", "q4", "q5", "both"],
                    help="Krea GGUF file(s): auto picks by the GPUs present (Q5_K_M for >= 12 GiB, Q4_K_M below)")
    ap.add_argument("--offline", action="store_true", help="plan: do not ask the Hub for sizes")
    args = ap.parse_args()
    groups = args.only or GROUPS
    items = plan(groups, args.gguf)
    if args.command == "plan":
        return cmd_plan(items, args.offline)
    if args.command == "download":
        return cmd_download(items)
    return cmd_check(items)


if __name__ == "__main__":
    sys.exit(main())
