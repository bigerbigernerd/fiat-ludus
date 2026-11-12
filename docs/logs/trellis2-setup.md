# TRELLIS.2 setup log (image → textured GLB)

Stage `image_to_3d` of the ai-game-forge pipeline: one concept image in, one
textured, game-ready GLB out, using Microsoft TRELLIS.2-4B.

Machine: Ubuntu 24.04, 52 cores, 251 GB RAM, NVIDIA driver 595.99.
GPU 0 = RTX 3090 24 GB (speed/quality reference), GPU 1 = RTX 2080 Ti 11 GB
(**the target**: the stage must run on an 11–12 GB card). No sudo, no system
CUDA toolkit, host gcc 13.3. All times UTC, 2026-11-12. Every GPU job went
through `tools/gpu_run.sh`; the numbers called "ledger" below are from
`cost/ledger.jsonl` (nvidia-smi sampled once a second, so short spikes can be
missed; "torch peak" is `torch.cuda.max_memory_allocated()`).

Paths: `$FORGE_RUNTIME` (= `~/forge-runtime` here) and the Hugging Face cache
(`$HF_HOME`; this machine sets it globally to a cache on a spinning disk, and we
did not override it).

---

## 1. Result in one screen

- Works on the **11 GB RTX 2080 Ti at 512, 1024 and 1536** (1536 = the model's
  top setting) with our wrapper `pipeline/image_to_3d.py`. Upstream's README says
  "at least 24 GB"; out of the box, 1024 ran out of memory on both cards for
  bulky inputs (see §5).
- What it took on the 2080 Ti (Turing, sm_75): xformers instead of flash-attn,
  the bf16 DiTs cast to fp16, upstream's own `low_vram` sub-model offloading, and
  five exact-math memory fixes in our wrapper (no upstream file edited).
- Same seed, fp16 on the 2080 Ti vs bf16 on the 3090: at 512 the meshes are
  nearly identical (987,510 vs 987,886 raw faces) and the renders are
  indistinguishable; at 1024/1536 they are different-but-equivalent samples.
  Running in 11 GB did not cost quality.
- Recommended for an 11 GB card (details §7):

```sh
cd "$FORGE_ROOT" && . ./env.sh
HF_HUB_OFFLINE=1 tools/gpu_run.sh image_to_3d hero --gpu 1 -- \
  "$FORGE_RUNTIME/envs/trellis2/bin/python" pipeline/image_to_3d.py \
  --image assets/concepts/out/hero.png --out assets/models/hero.glb \
  --resolution 1536 --faces 20000 --texture-size 1024 --flat-material
```

(props: `--resolution 1024 --faces 3000`). Add
`FORGE_DINOV3_MODEL=<ungated copy>` if your account has no DINOv3 access (§4).

---

## 2. Versions and licences

| Component | Version / commit | Licence (checked at source) |
|---|---|---|
| TRELLIS.2 code | `microsoft/TRELLIS.2` @ `75fbf0183001ed9876c8dbb35de6b68552ee08bd` (2026-06-05) | MIT (repo `LICENSE`) |
| TRELLIS.2-4B weights | HF `microsoft/TRELLIS.2-4B` @ `af44b45f2e35a493886929c6d786e563ec68364d` | MIT (model card tag `license:mit`) |
| sparse-structure decoder | HF `microsoft/TRELLIS-image-large` `ckpts/ss_dec_conv3d_16l8_fp16` @ `25e0d31f…` | MIT |
| o-voxel (in the TRELLIS.2 repo) | same commit; Eigen submodule `21e4582d` | MIT (Eigen: MPL-2.0) |
| CuMesh | `JeffreyXiang/CuMesh` @ `12289e1062f0603f2f0d0771b02e1395d247f26f` (+ cubvh `ce92267a`, Eigen `e63d9f6c`) | MIT |
| FlexGEMM | `JeffreyXiang/FlexGEMM` @ `6dd94a859c26ee8246888502eada3dd8ad85532e` | MIT |
| nvdiffrast | `NVlabs/nvdiffrast` tag `v0.4.0` (`253ac4fc`) | NVIDIA Source Code License (1-Way Commercial) — read it before shipping a product |
| image encoder | DINOv3 ViT-L/16 `facebook/dinov3-vitl16-pretrain-lvd1689m` (gated) | DINOv3 License (custom, Meta, 2025-08-19); redistribution allowed under its terms |
| background removal | `ZhengPeng7/BiRefNet` @ `e2bf8e4460fc8fa32bba5ea4d94b3233d367b0e4` | MIT (model card + GitHub `LICENSE`) |
| *not used* `briaai/RMBG-2.0` | — | gated; card licence `bria-rmbg-2.0`, linking to CC BY-NC 4.0 (non-commercial) |
| *not built* nvdiffrec renderutils | — | NVIDIA Source Code License; only needed for upstream's PBR preview video |
| PyTorch | 2.6.0+cu124, torchvision 0.21.0+cu124, triton 3.2.0 | BSD-3 |
| xformers | 0.0.29.post3 (cu124 wheel) | BSD-3 |
| flash-attn | 2.7.3 prebuilt wheel `cu12torch2.6cxx11abiFALSE-cp310` | BSD-3 |
| CUDA toolkit (build only) | conda-forge `cuda-nvcc 12.4.131`, `cuda-version 12.4`, gcc/g++ 12.4.0 | NVIDIA EULA |
| Python deps | python 3.10.22, transformers 4.57.6, huggingface_hub 0.36.2, trimesh 5.2.0, numpy 2.2.6, opencv-python-headless 5.0.0.93, utils3d `9a4eb15e` | various OSS |

## 3. Setup, in the order it happened

### 3.1 Clone (07:45–08:05)

```sh
git clone --recursive https://github.com/microsoft/TRELLIS.2 "$FORGE_RUNTIME/repos/TRELLIS.2"
```

Took **20 minutes**, almost all of it the Eigen submodule from gitlab.com.
`setup.sh` was read before use and **not run**: it creates a named conda env,
calls `sudo apt install`, installs `pillow-simd`, and clones extensions into
`/tmp/extensions`. We did the same steps by hand into a prefix env.

### 3.2 Conda env with a CUDA 12.4 toolkit — first attempt failed

```sh
mamba create -p $FORGE_RUNTIME/envs/trellis2 -c nvidia/label/cuda-12.4.1 -c conda-forge \
    python=3.10 cuda-nvcc cuda-cudart-dev cuda-libraries-dev ...
```

```
warning  libmamba Could not load repodata.json for nvidia/label/cuda-12.4.1/linux-64. Deleting cache, and retrying.
  + cuda-nvcc        13.5.37  conda-forge
  + libcublas     13.10.0.20  conda-forge
  + gcc_impl_linux-64 15.3.0  conda-forge
```

The NVIDIA channel's index failed to load, and the solver silently took
**CUDA 13.5 + gcc 15** from conda-forge instead. Noticed only while it was
downloading; killed and deleted. **Fix: pin `cuda-version=12.4`** and use
conda-forge only (it has the full 12.4 set):

```sh
mamba create -y -p $FORGE_RUNTIME/envs/trellis2 --override-channels -c conda-forge \
    python=3.10 cuda-version=12.4 cuda-nvcc cuda-cudart-dev cuda-cccl cuda-nvrtc-dev \
    cuda-profiler-api libcublas-dev libcusparse-dev libcusolver-dev libcurand-dev gxx_linux-64=12
```

7 min 15 s (mostly download), 2.5 GB. gcc 12 is inside nvcc 12.4's supported
range. conda-forge puts the CUDA headers in `$PREFIX/targets/x86_64-linux/include`,
not `$PREFIX/include`, so the build needs `CPATH` (see `build-env.sh` below).

### 3.3 PyTorch and Python dependencies (uv)

```sh
uv pip install --python $E/bin/python torch==2.6.0 torchvision==0.21.0 \
    --index-url https://download.pytorch.org/whl/cu124                      # 12 min 44 s (download)
uv pip install --python $E/bin/python \
    --extra-index-url https://download.pytorch.org/whl/cu124 --index-strategy unsafe-best-match \
    torch==2.6.0+cu124 torchvision==0.21.0+cu124 xformers==0.0.29.post3 \
    "https://github.com/Dao-AILab/flash-attention/releases/download/v2.7.3/flash_attn-2.7.3+cu12torch2.6cxx11abiFALSE-cp310-cp310-linux_x86_64.whl" \
    imageio imageio-ffmpeg tqdm easydict opencv-python-headless ninja trimesh "transformers==4.57.*" \
    zstandard plyfile kornia timm einops safetensors huggingface_hub pygltflib "numpy<2.3" setuptools wheel \
    "git+https://github.com/EasternJournalist/utils3d.git@9a4eb15e4021b67b12c460c7057d642626897ec8"   # 7 min 12 s
```

flash-attn is the official prebuilt wheel (no 30-minute source build).
xformers is needed because flash-attn does not run on Turing (§5.1).
`transformers>=4.56` is required for `DINOv3ViTModel`. Skipped from upstream's
`--basic` list: gradio, tensorboard, pandas, lpips (demo/training only),
pillow-simd (needs `sudo apt install libjpeg-dev`).

### 3.4 CUDA extensions — all built first time

`$FORGE_RUNTIME/envs/trellis2/build-env.sh` (sourced before each build):

```sh
E=$FORGE_RUNTIME/envs/trellis2
export PATH="$E/bin:$PATH" CUDA_HOME="$E"
export CPATH="$E/targets/x86_64-linux/include" LIBRARY_PATH="$E/targets/x86_64-linux/lib:$E/lib"
export CC="$E/bin/x86_64-conda-linux-gnu-gcc" CXX="$E/bin/x86_64-conda-linux-gnu-g++" CUDAHOSTCXX="$CXX"
export TORCH_CUDA_ARCH_LIST="7.5;8.6" MAX_JOBS=16      # 2080 Ti + 3090
```

```sh
git clone -b v0.4.0 https://github.com/NVlabs/nvdiffrast.git      $E/src/nvdiffrast
git clone --recursive https://github.com/JeffreyXiang/CuMesh.git   $E/src/CuMesh
git clone --recursive https://github.com/JeffreyXiang/FlexGEMM.git $E/src/FlexGEMM
pip install --no-build-isolation --no-deps $E/src/nvdiffrast                         # 35 s
pip install --no-build-isolation --no-deps $E/src/CuMesh                             # 4 min 11 s
pip install --no-build-isolation --no-deps $E/src/FlexGEMM                           # 1 min 36 s
pip install --no-build-isolation --no-deps $FORGE_RUNTIME/repos/TRELLIS.2/o-voxel    # 1 min 39 s
```

One hiccup: CuMesh's nested submodule (cubvh → Eigen) failed with
`fatal: remote error: GitLab is currently unable to handle this request due to load`.
Fix: fill it from the Eigen clone we already had —

```sh
cd $E/src/CuMesh/third_party/cubvh
git -c protocol.file.allow=always \
    -c submodule.third_party/eigen.url=$FORGE_RUNTIME/repos/TRELLIS.2/.git/modules/o-voxel/third_party/eigen \
    submodule update --init
```

`--no-deps` on o-voxel matters: its `pyproject.toml` lists CuMesh and FlexGEMM
as `git+https` dependencies and would otherwise rebuild them from unpinned HEAD.
Only warning seen: `There are no x86_64-conda-linux-gnu-g++ version bounds defined for CUDA version 12.4` (harmless).

### 3.5 Import / kernel check (`tools/gpu_run.sh … --gpu 1` and `--gpu 0`)

Import tests must run with a GPU visible: FlexGEMM sets up Triton autotuners at
import time and fails with `RuntimeError: 0 active drivers ([]). There should only be one.`
when `CUDA_VISIBLE_DEVICES` is empty. Results:

| | RTX 2080 Ti (7.5) | RTX 3090 (8.6) |
|---|---|---|
| `torch.cuda.is_bf16_supported()` | **True** (emulation!) / False with `including_emulation=False` | True / True |
| flash-attn fp16 | `FlashAttention only supports Ampere GPUs or newer.` | OK |
| xformers fp16 / bf16 | OK / `No operator found for memory_efficient_attention_forward` | OK / OK |
| 10× 4096³ matmul bf16 vs fp16 | 0.229 s vs 0.095 s | 0.157 s vs 0.164 s |
| nvdiffrast, CuMesh simplify, FlexGEMM | OK | OK |

`cumesh.CuMesh.simplify(N)` targets **faces** (2,000 → 1,904–1,984 faces);
o-voxel's `to_glb` docstring says "vertices", which is wrong.

### 3.6 Weights (downloaded by a separate job, not by us)

`microsoft/TRELLIS.2-4B` (16.2 GB, 8 safetensors; the two encoders, 1.4 GB, are
not needed for image-to-3D), `TRELLIS-image-large/ckpts/ss_dec_conv3d_16l8_fp16`
(148 MB), the DINOv3 copy (1.2 GB) and BiRefNet (444 MB; downloaded by us,
52 s). On this machine's shared ~9 MB/s link the queue took 1 h 48 min for
TRELLIS.2-4B alone (an earlier attempt over Xet stalled and was restarted over
plain HTTPS with `HF_HUB_DISABLE_XET=1`).

Disk: env 9.0 GB (2.5 GB toolkit + PyTorch/CUDA wheels + 4 built extensions),
repo 709 MB (.git 671 MB), weights 17.8 GB used of 18.2 GB downloaded.

## 4. The two gated models in `pipeline.json`

```json
"image_cond_model": {"name": "DinoV3FeatureExtractor", "args": {"model_name": "facebook/dinov3-vitl16-pretrain-lvd1689m"}},
"rembg_model":      {"name": "BiRefNet",               "args": {"model_name": "briaai/RMBG-2.0"}}
```

Both are constructed eagerly in `Trellis2ImageTo3DPipeline.from_pretrained`, so
the pipeline does not even load without access to both.

**Background removal.** `preprocess_image` uses the input's alpha channel when
it is not all-255 and only otherwise calls the rembg model. The repo's own
`trellis2/pipelines/rembg/BiRefNet.py` wrapper defaults to `ZhengPeng7/BiRefNet`
(MIT). Our wrapper replaces `trellis2.pipelines.rembg.BiRefNet` at runtime with a
lazy stand-in that ignores the configured name, uses `ZhengPeng7/BiRefNet`, and
loads it only when an image actually needs it (RGBA with real alpha never loads
it). No upstream file edited. BiRefNet uses `trust_remote_code`; we read its
`birefnet.py` imports first (torch, timm, torchvision; no network or shell calls).
`--keep-background` gives the image an alpha channel that is opaque everywhere
except one pixel, so upstream's crop/normalise code runs without background removal.

**DINOv3.** Our token got HTTP 403 (manual approval). The owner decided to use an
ungated copy of the identical weights. Verification (all recorded 08:4x UTC):

- The HF tree API is readable for the gated repo without access. Meta's
  `model.safetensors`: git oid `5fe30316feaa07b25590197bfad7216e9747863d`,
  1,212,559,808 bytes (LFS sha256 hidden). `camenduru/dinov3-vitl16-pretrain-lvd1689m`:
  same git oid, same size, LFS sha256 `dcb2e451…8179`; `config.json`,
  `preprocessor_config.json`, `LICENSE.md` have identical oids.
- Our downloaded file's `sha256sum` = `dcb2e451…8179`; the Git-LFS pointer text
  built from that hash (`version …/oid sha256:dcb2…/size 1212559808`) hashes with
  `git hash-object` to `5fe30316…` — i.e. **byte-identical to Meta's file**.
- Second copy, `Comfy-Org/TRELLIS.2` `clip_vision/dino_v3_vit_l.safetensors`
  (1,212,559,776 bytes): compared tensor by tensor — 415/415 names, dtypes and
  shapes equal, sha256 of every tensor's raw bytes equal; the only difference is
  the header's `__metadata__` (`{"format": "pt"}` vs none, 32 bytes).
- Sanity run of the encoder on the 2080 Ti: features (1, 1029, 1024) at 512 and
  (1, 4101, 1024) at 1024, finite, 1.33 GiB peak.

The wrapper reads the encoder id from `FORGE_DINOV3_MODEL` (default: Meta's
official id) and swaps it in by subclassing `DinoV3FeatureExtractor` at runtime.
People without access can set it to an ungated transformers-format copy after
verifying it as above. The DINOv3 License still applies to such a copy. At 08:47
the official repo started answering 206 for our token (access granted); we kept
the verified copy rather than download 1.2 GB again.

## 5. Making it fit: 2080 Ti (11 GB) and the 3090

### 5.1 Turing specifics
- `ATTN_BACKEND=xformers` (dense and sparse attention); flash-attn is Ampere+.
- The five 1.3B DiTs are stored bf16 (`"dtype": "bfloat16"` in their configs).
  xformers has no bf16 kernels on sm_75, and bf16 matmul is emulated (2.4× slower
  than fp16). The wrapper calls upstream's own `model.convert_to(torch.float16)`
  on them. No NaNs/overflow seen in any run; outputs match the 3090's bf16 runs
  (§1). The VAE decoders are already fp16.

### 5.2 Upstream's low-VRAM switch
`pipeline.json` has no `low_vram` key, so upstream defaults to `low_vram=True`:
every sub-model sits in CPU RAM and is moved to the GPU only for its stage. That
is what makes the 4B model fit at all (weights alone are ~15 GB). Our wrapper's
`--no-low-vram` keeps everything resident; it ran out of memory **on the 24 GB
3090** for the mossling (22.9 GB peak) and crate (23.9 GB) at 1024. So the
auto-switch only goes all-resident with ≥ 40 GB free; on both our cards it picks
low-VRAM.

### 5.3 Where 11 GB ran out, and the fixes (all in `pipeline/image_to_3d.py`)

1. **GLB export, `CuMesh.simplify`** (T.png at 1024, 13.3M raw faces):
   `[CuMesh] CUDA error … out of memory` with nvidia-smi at 10.3 GB. Cause:
   CuMesh allocates with `cudaMalloc`, outside PyTorch's caching allocator, while
   PyTorch kept freed blocks cached. Re-running only the export from a saved raw
   mesh with `torch.cuda.empty_cache()` before each CuMesh call: 42 s, 5.1 GB
   (nvidia-smi). Fix: wrap `CuMesh.init/simplify/fill_holes/remove_duplicate_faces/uv_unwrap`
   and `cumesh.remeshing.remesh_narrow_band_dc` to empty the cache first.
   (Tried and dropped: pre-decimating the raw mesh to 3M faces — the remesh step
   rebuilds ~21.7M faces from the 1024 grid anyway, torch peak 7.9 vs 8.3 GiB.)
2. **Texture decoder** (`tex_slat_decoder`, mossling and crate at 1024):
   `Tried to allocate 1.20 GiB … 9.92 GiB is allocated by PyTorch`. The sparse
   VAE decoders are the generation peak at 1024. Exact-math rewrites applied at
   runtime: `LayerNorm32` in row chunks (upstream casts the whole tensor to fp32),
   in-place SiLU on freshly normalised tensors, the ConvNeXt MLP (C→4C→C) in row
   chunks, the up-sampling block adds its skip path after its last conv instead
   of keeping it alive through it, and the decoded mesh parked in CPU RAM while
   the texture decoder runs.
3. **Export remesh** (crate at 1024, 16.2M raw faces): narrow-band dual
   contouring needed another 3.83 GiB. Fix: on OOM, retry with upstream's other
   export branch (`remesh=False`: simplify + clean-up). First version of the
   fallback ran inside the `except` block and OOMed again in `simplify` — the
   traceback still held the failed attempt's tensors; retrying after leaving the
   block (plus `gc.collect()`) fixed it. The JSON line reports `"remeshed"`.
4. **Load time** (not memory): warm-cache load took 108 s, of which ~95 s was
   `torch.nn.init` filling five 1.3B-parameter DiTs with random numbers on the
   CPU (cProfile: 13 s `normal_` + 9 s `uniform_` per DiT) right before
   `load_state_dict` overwrites them. The wrapper loads checkpoints with
   initialisation disabled and checks every parameter came from the checkpoint
   (falls back to upstream's loader otherwise). Warm load 108 s → 15–22 s; same
   raw mesh to the face (2,942,974).
5. **Caches**: FlexGEMM's autotune cache and Triton's kernel cache default to
   `~/.flex_gemm` and `~/.triton`; the wrapper points them at
   `$FORGE_RUNTIME/envs/trellis2/cache/`. The first run on each GPU model pays
   for autotuning (512 generate: 83 s cold vs 35–40 s warm on the 2080 Ti).

Also replaced: upstream's cascade only lowers the high-res stage while the token
count exceeds `max_num_tokens` for 1536 (never below 1024). In low-VRAM mode the
same loop may go below 1024 (`--max-tokens`, default 49152 as upstream). We did
not need it: the largest input (crate) had 34,800 tokens.

## 6. Measurements

### 6.1 Load time (cold vs warm page cache; weights on a spinning disk)

T.png, 512, wall seconds inside the job:

| | load (disk → RAM → model) | generate |
|---|---|---|
| 2080 Ti, cold cache, upstream loader | 256 s | 83 s (first run: Triton/FlexGEMM autotune) |
| 2080 Ti, warm cache, upstream loader | 108 s | 36 s |
| 2080 Ti, warm cache, our loader (no random init) | 15 s | 40 s |
| 3090, cold cache, our loader | 155 s | 30 s |

"Cold" = page cache dropped for the weight files with `posix_fadvise(DONTNEED)`
(no root needed). Cold load is disk-bound: ~16 GB at ~105 MB/s. Every run holds
the GPU lock during the load, so on a shared GPU it pays to keep the weights on
an SSD or warm. Warm loads in the batch below were 15–30 s.

### 6.2 Per-asset results (seed 42 unless noted)

Concept images from `assets/concepts/out/` (1024², flat cartoon, white
background → BiRefNet). "full GLB" = 500k-face target, 2048² textures
(upstream app defaults); "light GLB" = game target (hero 20k, mossling 4k,
props 3k) with 1024² textures, both exported from the same generation.
Peak MiB is the ledger's nvidia-smi peak for the whole job. 2080 Ti rows run
the low-VRAM path; the 3090 rows ran the same low-VRAM path (the auto-switch
picks it on 24 GB, see §5.2). Remesh "no" = export fell back to upstream's
no-remesh branch after an OOM; "(on)" = run predates that field (remesh on).

| run | result | res | tokens | load s | generate s | export s (full) | job s (ledger) | peak MiB (ledger) | raw faces | full GLB faces / MB | light GLB faces / MB | remesh |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| hero_1024_2080ti | ok | 1024 | 4149 | 17.21 | 50.34 | 19.06 | 103.2 | 3839 | 3057786 | 475627 / 28.7 | 19737 / 3.5 | yes |
| hero_1024_3090 | ok | 1024 | 4161 | 22.5 | 57.63 | 32.05 | 129.0 | 4202 | 2887078 | 486193 / 27.9 | 19743 / 3.2 | (on) |
| hero_1536_2080ti | ok | 1536 | 10154 | 14.7 | 104.79 | 42.45 | 181.4 | 6533 | 6604098 | 459061 / 27.6 | 19245 / 3.5 | yes |
| hero_1536_3090 | ok | 1536 | 10136 | 22.66 | 59.7 | 28.52 | 130.8 | 7092 | 6800830 | 482277 / 29.2 | 19197 / 3.4 | (on) |
| hero_1536_2080ti-s1 | ok | 1536 | 10131 | 21.75 | 88.06 | 10.93 | 131.2 | 5371 | 7037668 | 19831 / 3.7 | - / - | yes |
| hero_1536_2080ti-s2 | ok | 1536 | 9654 | 131.19 | 99.97 | 9.76 | 250.4 | 4671 | 6306382 | 19977 / 3.3 | - / - | yes |
| hero_1536_3090-s1 | ok | 1536 | 10148 | 22.7 | 60.29 | 11.37 | 104.3 | 4184 | 7156862 | 19312 / 3.7 | - / - | yes |
| hero_1536_3090-s2 | ok | 1536 | 9748 | 22.13 | 70.5 | 10.97 | 114.1 | 5096 | 6490712 | 18909 / 3.4 | - / - | yes |
| mossling_1024_2080ti | ok | 1024 | 21639 | 16.34 | 213.54 | 305.49 | 572.9 | 10711 | 16525705 | 466144 / 29.7 | 3825 / 2.4 | no |
| mossling_1024_3090 | ok | 1024 | 21752 | 22.33 | 131.99 | 62.88 | 251.6 | 18570 | 16136288 | 487304 / 36.8 | 3775 / 2.6 | yes |
| crate_1024_2080ti | ok | 1024 | 34800 | 20.4 | 421.02 | 43.42 | 523.9 | 8887 | 16187399 | 468323 / 22.9 | 2986 / 2.0 | no |
| crate_1024_3090 | ok | 1024 | 37498 | 22.92 | 245.81 | 39.37 | 340.1 | 16538 | 15642618 | 486617 / 23.6 | 2842 / 2.1 | yes |
| tree_broadleaf_1024_2080ti | ok | 1024 | - | 29.72 | 183.18 | 43.12 | 280.5 | 7849 | 10458866 | 461047 / 24.3 | 2835 / 2.1 | (on) |
| tree_broadleaf_1024_3090 | ok | 1024 | - | 22.38 | 122.72 | 69.56 | 238.4 | 7895 | 10331382 | 462997 / 24.8 | 2922 / 2.2 | (on) |
| lantern_1024_2080ti | ok | 1024 | 7133 | 29.34 | 73.54 | 27.31 | 149.8 | 4771 | 7211652 | 489663 / 28.0 | 2931 / 2.6 | (on) |
| lantern_1024_2080ti-c2s | ok | 1024 | 7133 | 14.9 | 68.43 | 9.82 | 102.5 | 3839 | 7211652 | 19919 / 3.4 | - / - | yes |
| lantern_1024_3090 | ok | 1024 | 7042 | 22.05 | 56.34 | 21.83 | 120.2 | 5526 | 7005536 | 469881 / 26.9 | 2904 / 2.5 | (on) |


Notes on the numbers:
- The wrapper changed during the session (each fix in §5.3 was added after a
  failure); the tree and the first lantern row predate the up-sampling-block fix.
  Re-running the lantern with it gave the same raw mesh (7,211,652 faces,
  7,133 tokens) and peak 4,771 → 3,839 MiB.
- Generation time scales with the shape token count (sparse attention): 4k
  tokens ≈ 50 s, 10k ≈ 90–105 s, 22k ≈ 215 s, 35k ≈ 420 s on the 2080 Ti.
  Flat, full-frame cartoon props (crate, mossling) are the expensive ones.
- The 2080 Ti is 1.0–1.7× the 3090's generation time on the same input (some
  3090 runs paid first-time FlexGEMM autotuning for new shapes, so that ratio is
  approximate).
- Mossling on the 2080 Ti peaked at **10,711 MiB of 11,264** — it fits, but
  with ~0.5 GB headroom. A 12 GB card is comfortable; on an 11 GB card that also
  drives a display, the bulkiest inputs may need `--resolution 512`.
- The mossling's 305 s export is mostly the failed remesh attempt;
  `--no-remesh` skips it.
- Old 512 smoke tests (T.png): 2080 Ti 72.7 s job, 2.9 GB peak; 3090 (then still
  all-resident) 151.5 s, 18.8 GB.

### 6.3 GLB check (pygltflib + trimesh, every output)

One mesh, one primitive, attributes POSITION/NORMAL/TEXCOORD_0; one material
with `pbrMetallicRoughness.baseColorTexture` (RGBA PNG; alpha is baked but
`alphaMode` is OPAQUE, as the README says) and `metallicRoughnessTexture`
(G = roughness, B = metallic), `metallicFactor` 1, `roughnessFactor` 1. No
extensions (PNG; `--webp` would add `EXT_texture_webp`). `doubleSided` is false
after remesh and true on the no-remesh branch. The exporter swaps axes to
**+Y up** (glTF convention); the object fills the unit cube: hero bbox
≈ x ±0.37, y ±0.50, z ±0.17. Face targets land within 1–5 % (`simplify` target
is faces). No normal map, no emissive (the T.png sample's glowing glass came out
dark).

**Metallic:** TRELLIS.2 predicts substantial metallic on these flat cartoon
inputs — texture means 0.29–0.51 for the hero, ~0.49 for the old chibi hero.
Any PBR viewer shows them too dark. `--flat-material` drops the
metallic-roughness texture and writes metallic 0 / roughness 1 (verified on
the lantern: `metallicFactor 0.0`, `roughnessFactor 1.0`, no MR texture).

## 7. Quality on the flat cartoon concepts (looked at every preview)

Previews: `pipeline/blender/render_preview.py --engine cycles` (base colour,
CPU) plus our own unlit nvdiffrast close-ups of the head.

- **Hero (tall anime character):** body is good at 1024 and 1536 on both cards —
  arms clearly separated from the torso with fingers, legs separated, boots,
  belt, sash and cross-strap all modelled, back side plausible (jacket back,
  hair). Hair comes out as layered strand clumps, readable. **The face is the
  weak point.** At 1024 the eyes are dark smudges on every sample. At 1536 it is
  sample-dependent: 3090 (bf16) seed 42 and seed 2 had readable eyes with iris
  colour, nose and mouth; seed 1 smudged. 2080 Ti (fp16) seeds 42/1/2: none
  clearly readable, seed 2 partly. To test whether fp16 is to blame we forced
  the 3090 to the 2080 Ti's precision (fp16 + xformers): seed 2 readable, seed 42
  partial, seed 1 came out with a black face and purple hair; keeping only the
  texture DiT in fp32 did **not** change that sample (the shape stages decide
  it). So with n = 3 per setting we cannot say fp16 hurts; precision changes
  which sample you get, and faces need a few seeds to pick from.
  The 20k / 1024² export keeps whatever face the sample has (eyes visible in the
  20k version of the good 1536 samples); decimation is not what loses them.
- **Old chibi hero (replaced):** dark reddish skin. We checked the colour space
  numerically (k-means clusters of the input vs. an unlit render of the GLB's
  base colour): skin (245,171,125) → (142,83,58) while cream (246,228,196) →
  (213,195,155) and teal (73,125,121) → (29,90,90). A missing sRGB conversion
  would map 245 → 234 in red; it went to 142, so it is not gamma. The code
  agrees: o-voxel bakes sRGB bytes (`volumetic_attr.py` stores texture bytes as
  is) and TRELLIS.2's own renderer linearises base colour with `** 2.2` before
  shading, i.e. base colour is sRGB end to end. On the new hero, flat regions
  come back within a few levels: skin (241,217,202) → (236,210,202) on the 3090
  and (249,225,211) on the 2080 Ti; whites (239,235,229) → (237,241,247); greys
  161 → 155, 186 → 184. The chibi's skin was the model's reinterpretation of that
  input (512 was worse: orange skin, yellow shirt, black scarf, on both GPUs).
  Its blank white eyes were in the full 1M-face / 2048² export too, so not
  texture size or decimation; the 512 sample of the same image had pupils.
- **Mossling:** face excellent (eyes with pupils, frown). Moss is modelled as
  overlapping leaf plates; at 4k faces gaps between plates show dark interior.
  The stone reads beige more than grey.
- **Crate:** strong. With the remesh export at 3k faces (3090 row) the slatted
  panels tore into holes; the no-remesh export (2080 Ti row) kept them clean.
  For low-poly props `--no-remesh` is worth trying first.
- **Tree:** silhouette and trunk good; foliage is clumps of cut-out leaf shards
  with dark gaps — fine for stylised foliage, but it will need an alpha-card or
  re-shade pass.
- **Lantern:** best of the set: open windows are real holes, the bell hangs
  free, the back is consistent.
- 2080 Ti vs 3090 with identical settings: at 512 the hero meshes were nearly
  identical (987,510 vs 987,886 raw faces) and renders matched; at 1024/1536 they
  are different samples of similar quality. Running in 11 GB did not change
  quality.

## 8. Recommendation for an 11–12 GB card

- Characters: `--resolution 1536 --faces 20000 --texture-size 1024`, 2–3 seeds,
  pick the one with a readable face (2080 Ti: 3–4 min per seed incl. load;
  peak 5.4–6.5 GB). 1024 gives a clean body in ~2 min but no readable face on
  a character this small in frame.
- Props: `--resolution 1024 --faces 3000 --texture-size 1024`, add
  `--no-remesh` for boxy/thin-panel props. 2.5–9 min on the 2080 Ti depending on
  bulk; peak up to 10.7 GB (mossling).
- Stylised re-shading: add `--flat-material`.
- 512 fits easily (≈3–4 GB, ~1.5 min) but colours drifted badly on the chibi.

Minimum VRAM as documented upstream: README "at least 24GB", verified on
A100/H100; the model card gives no separate number. Measured here with our
wrapper: 512 ≈ 3–4 GB, 1024 4–10.7 GB, 1536 (hero) 5.4–7.1 GB peak (nvidia-smi),
so 12 GB runs everything we tried and 8 GB should do 512 and simple 1024/1536
inputs (not tested).

## 9. Wrapper: `pipeline/image_to_3d.py`

```
image_to_3d.py --image in.png --out out.glb [--resolution 512|1024|1536] [--seed N]
               [--faces N] [--texture-size N] [--keep-background]
               [--low-vram | --no-low-vram] [--max-tokens N] [--no-remesh]
               [--flat-material] [--webp] [--preview views.png] [--variant FACES:TEX:PATH]
```

Defaults: 512, seed 42, 20k faces, 1024² textures. Prints one JSON line
(timings: `load_disk_s`, `load_gpu_s`, `preprocess_s`, `generate_s`,
`export_s`; `shape_tokens`, `raw_faces`, `effective_resolution`, `remeshed`;
`mesh` stats per output; torch peak memory; GPU/precision/backend choices) and
exits non-zero on failure. Environment: `FORGE_RUNTIME` (repo at
`$FORGE_RUNTIME/repos/TRELLIS.2`, or `TRELLIS2_REPO`), `FORGE_DINOV3_MODEL`,
`HF_HUB_OFFLINE=1` once weights are cached. Test-only switches:
`FORGE_TRELLIS2_DTYPE`, `FORGE_TRELLIS2_STAGE_DTYPE`, `FORGE_TRELLIS2_SLOW_INIT=1`.
`--keep-background` works mechanically but on a white-background concept it
makes the whole frame the object (a slab); it is for photos of objects that
fill the frame.

## 10. Things that did not work / caveats

- conda with `-c nvidia/label/cuda-12.4.1` (index failed to load → CUDA 13.5 + gcc 15).
- `--no-low-vram` (all sub-models resident) on 24 GB: OOM at 1024 for bulky inputs.
- Pre-decimating the raw mesh before export: does not reduce the remesh peak.
- Export fallback inside the `except` block: OOM again (traceback kept tensors alive).
- Texture DiT in fp32 on the fp16 path: no visible difference.
- Upstream's `example.py` renders a PBR video with nvdiffrec; not built (licence, not needed).
- Early test runs (before the cache redirect) wrote Triton kernels into `~/.triton/cache`
  (57 entries) and a FlexGEMM autotune file into `~/.flex_gemm` (moved into the env).
- Three early ledger rows (`ext-check-*`, `dinov3-check`, at 11:45–11:47 local)
  recorded commands with absolute home paths; later jobs pass `$FORGE_RUNTIME`
  literally. The ledger is the wrapper's file, we did not edit it.
- Not verified: a 12 GB or 8 GB card; Windows; the official (gated) DINOv3 repo
  download itself (we used the byte-identical copy).

## 11. Reproduce from scratch

```sh
# 0. paths (env.sh sets FORGE_RUNTIME; HF cache is wherever HF_HOME points)
cd ai-game-forge && . ./env.sh
R=$FORGE_RUNTIME/repos/TRELLIS.2; E=$FORGE_RUNTIME/envs/trellis2
git clone --recursive https://github.com/microsoft/TRELLIS.2 $R && git -C $R checkout 75fbf0183001ed9876c8dbb35de6b68552ee08bd

# 1. toolkit + python (conda-forge only, CUDA pinned)
mamba create -y -p $E --override-channels -c conda-forge python=3.10 cuda-version=12.4   cuda-nvcc cuda-cudart-dev cuda-cccl cuda-nvrtc-dev cuda-profiler-api   libcublas-dev libcusparse-dev libcusolver-dev libcurand-dev gxx_linux-64=12

# 2. torch + deps
uv pip install --python $E/bin/python --extra-index-url https://download.pytorch.org/whl/cu124   --index-strategy unsafe-best-match torch==2.6.0+cu124 torchvision==0.21.0+cu124 xformers==0.0.29.post3   "https://github.com/Dao-AILab/flash-attention/releases/download/v2.7.3/flash_attn-2.7.3+cu12torch2.6cxx11abiFALSE-cp310-cp310-linux_x86_64.whl"   imageio imageio-ffmpeg tqdm easydict opencv-python-headless ninja trimesh "transformers==4.57.*"   zstandard plyfile kornia timm einops safetensors huggingface_hub pygltflib "numpy<2.3" setuptools wheel   "git+https://github.com/EasternJournalist/utils3d.git@9a4eb15e4021b67b12c460c7057d642626897ec8"

# 3. CUDA extensions (sm_75 + sm_86)
cat > $E/build-env.sh <<'SH'
E=$FORGE_RUNTIME/envs/trellis2
export PATH="$E/bin:$PATH" CUDA_HOME="$E" CPATH="$E/targets/x86_64-linux/include"
export LIBRARY_PATH="$E/targets/x86_64-linux/lib:$E/lib" TORCH_CUDA_ARCH_LIST="7.5;8.6" MAX_JOBS=16
export CC="$E/bin/x86_64-conda-linux-gnu-gcc" CXX="$E/bin/x86_64-conda-linux-gnu-g++" CUDAHOSTCXX="$E/bin/x86_64-conda-linux-gnu-g++"
SH
. $E/build-env.sh
git clone -b v0.4.0 https://github.com/NVlabs/nvdiffrast.git $E/src/nvdiffrast
git clone --recursive https://github.com/JeffreyXiang/CuMesh.git $E/src/CuMesh && git -C $E/src/CuMesh checkout 12289e1062f0603f2f0d0771b02e1395d247f26f
git clone --recursive https://github.com/JeffreyXiang/FlexGEMM.git $E/src/FlexGEMM && git -C $E/src/FlexGEMM checkout 6dd94a859c26ee8246888502eada3dd8ad85532e
cd /tmp
for p in $E/src/nvdiffrast $E/src/CuMesh $E/src/FlexGEMM $R/o-voxel; do
  $E/bin/python -m pip install --no-build-isolation --no-deps $p; done

# 4. weights (into the normal HF cache)
hf download microsoft/TRELLIS.2-4B
hf download microsoft/TRELLIS-image-large --include "ckpts/ss_dec_conv3d_16l8_fp16*"
hf download ZhengPeng7/BiRefNet
hf download facebook/dinov3-vitl16-pretrain-lvd1689m        # needs approved access, or:
# hf download <ungated copy>; export FORGE_DINOV3_MODEL=<ungated copy>   (verify as in §4)

# 5. one asset on the 11 GB card
cd ai-game-forge && HF_HUB_OFFLINE=1 tools/gpu_run.sh image_to_3d crate --gpu 1 -- \
  "$FORGE_RUNTIME/envs/trellis2/bin/python" pipeline/image_to_3d.py \
  --image assets/concepts/out/crate.png --out assets/models/crate.glb \
  --resolution 1024 --faces 3000 --no-remesh --flat-material
```

Build time on this machine (52 cores): env + toolkit 7 min, PyTorch 13 min and
deps 7 min (both download-bound), extensions 6.5 min — about 35 min plus the
clone (20 min, GitLab) and 16 GB of weights.
