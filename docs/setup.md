# Setting up Fiat Ludus

This guide installs everything the pipeline needs on one Linux machine with one
NVIDIA GPU: Blender, four Python environments, three upstream code checkouts and
49 to 57 GB of model weights. You need no root access and no system CUDA toolkit.

Every version is pinned to what ran on the reference machine on 2026-11-12. The
scripts in `setup/` do the work. This page says what they do, how long each step
takes, and what went wrong along the way.

Where a number comes from matters, so each one is marked:

- **[measured]**: measured on the reference machine for this guide (`du -sh`,
  wall-clock time of the setup script, `nvidia-smi`).
- **[log]**: taken from a lab log in `docs/logs/`, written while the stages were
  first built.
- **[ledger]**: read from `cost/ledger.jsonl`, the per-job record that
  `tools/gpu_run.sh` writes (nvidia-smi sampled once a second).
- **[estimate]**: worked out from other numbers, not measured. The text says how.

## 1. What you need

### Hardware

The pipeline was tested on two cards. The target is the smaller one:

| GPU | Memory | Compute capability | Role |
|---|---|---|---|
| RTX 2080 Ti | 11 GB | 7.5 (Turing) | target card; every stage runs on it |
| RTX 3090 | 24 GB | 8.6 (Ampere) | reference for speed and quality |

- **GPU memory.** Peak use per stage on the 2080 Ti: concept images 9.2 to
  10.6 GB in the Krea runs recorded in `cost/ledger.jsonl` (labelled Q5_K_M;
  `gen_image.py` now picks the smaller Q4_K_M on cards under 12 GiB, and no
  ledger row shows its peak) [ledger], image-to-3D 3 to 10.7 GB (TRELLIS.2; the bulkiest
  inputs at resolution 1024 come within 0.5 GB of 11 GB), rigging about 7 GB
  (UniRig), music 5.1 to 6.5 GB (YuE2) [log]. A 12 GB card has room to spare. Below
  11 GB, only rigging was tried, and only by capping PyTorch's allocator, not on
  a real card.
- **GPU generation.** Cards from Turing (RTX 20xx) to Ada (RTX 40xx) should
  work; only Turing and Ampere were tested. The trellis2 and unirig environments
  use PyTorch 2.6 built for CUDA 12.4, which has no kernels for compute
  capability 10.0 or newer, so an RTX 50xx card will not run those stages with
  these pins [estimate, from the PyTorch build; not tried].
- **Driver.** Tested with NVIDIA driver 595.99 only.
- **CPU and RAM.** The reference machine has 52 cores and 251 GB of RAM. RAM
  use was not measured. TRELLIS.2's low-VRAM mode keeps all of its sub-models in
  RAM (about 16 GB of weights) and YuE2's low-VRAM mode swaps half the model
  through RAM, so plan for at least 32 GB [estimate].
- **Disk.** About 95 GB once installed (both GGUF files), plus up to 26 GiB of
  package cache while installing; see the table below [measured]. Put the runtime folder
  and the Hugging Face cache on an SSD if you can: on the reference machine the
  weights sit on a spinning disk, and loading TRELLIS.2 from a cold page cache
  took 256 s instead of 15 s warm [log].

### Disk space, by part

Environments, repos and Blender are `du -sh` on the reference machine, in GiB
[measured]. Weights are the byte counts of the files the pipeline loads, in GB
(10^9 bytes), as `setup/download_models.sh --dry-run` prints them [measured].

| Part | Where | Size |
|---|---|---|
| Blender 5.2.4 | `$FORGE_RUNTIME/blender/` | 1.2 GiB, plus the 368 MiB tarball in `downloads/` |
| krea env | `$FORGE_RUNTIME/envs/krea` | 6.5 GiB |
| trellis2 env | `$FORGE_RUNTIME/envs/trellis2` | 9.0 GiB (includes the CUDA 12.4 toolkit and 218 MiB of extension sources) |
| unirig env | `$FORGE_RUNTIME/envs/unirig` | 8.8 GiB |
| yue2 env | `$FORGE_RUNTIME/envs/yue2` | 8.8 GiB (includes 545 MiB of Whisper and Demucs weights) |
| TRELLIS.2 repo | `$FORGE_RUNTIME/repos/TRELLIS.2` | 832 MiB (785 MiB of it git history) |
| UniRig repo | `$FORGE_RUNTIME/repos/UniRig` | 46 MiB fresh (85 MiB here, with the example GLBs fetched by hand) |
| YuE2 repo | `$FORGE_RUNTIME/repos/YuE2` | 41 MiB, plus whatever `outputs/` collects (1.6 GiB here after the test runs) |
| krea weights | Hugging Face cache | 9.4 GB official files + 8.9 GB (Q5_K_M) and/or 7.5 GB (Q4_K_M) GGUF |
| trellis2 weights | Hugging Face cache | 16.6 GB |
| unirig weights | Hugging Face cache | 5.8 GB |
| yue2 weights | Hugging Face cache + yue2 env | 7.8 GB in the cache + 0.57 GB into the env |

While installing, uv and conda also keep a package cache (`uv cache dir`,
`conda info`). Building all four environments from an empty uv cache left
26 GiB in it [measured]. When the cache is on the same filesystem as
`$FORGE_RUNTIME`, uv hard-links instead of copying, and environments plus cache
took 33 GiB together; on a different filesystem, count the 26 GiB on top.
`uv cache clean` frees it once you are done (later rebuilds then download again).

### Software

- Linux x86-64 (tested: Ubuntu 24.04, gcc 13 on the host, which the build does
  not use).
- `git`, `curl`, `tar`, `xz`, `sha256sum`.
- [uv](https://docs.astral.sh/uv/) (tested: 0.11.28). It also downloads the
  exact Python versions the venvs need.
- conda or mamba, e.g. [Miniforge](https://github.com/conda-forge/miniforge)
  (tested: conda 26.5.3, mamba 2.5.0). Used for two environments: trellis2
  (Python plus a CUDA 12.4 compiler toolchain) and yue2 (Python plus ffmpeg).
- `python3` on the host (any 3.10+) for `./forge rig` and `./forge install`.
- Node 22 and npm, for the game (`./forge check`) and for validating rigged GLBs.
- A Hugging Face account and a read token, for the two gated model repos.

## 2. Before you start

```sh
git clone https://github.com/bigerbigernerd/fiat-ludus && cd fiat-ludus
. ./env.sh
echo "$FORGE_RUNTIME"        # default: ~/forge-runtime
```

`env.sh` is sourced by every script. It sets `FORGE_ROOT` (this folder) and
`FORGE_RUNTIME` (where environments, repos and Blender go; default
`~/forge-runtime`, override it before sourcing). It also reads `.env.local` if
present. Put machine-specific settings and secrets there; it is not committed:

```sh
# .env.local
HF_TOKEN=hf_...                  # read token; needed for the gated repos
FORGE_GPU_MAIN=0                 # nvidia-smi index of the GPU the stages use
FORGE_DINOV3_MODEL=...           # only if you use the ungated DINOv3 copy, see 4.3
```

GPU numbers are the ones `nvidia-smi` prints: `env.sh` sets
`CUDA_DEVICE_ORDER=PCI_BUS_ID` so CUDA counts the same way.

**Where the weights go.** The scripts never name a cache path; `huggingface_hub`
decides. It uses `HF_HUB_CACHE`, then the older `HUGGINGFACE_HUB_CACHE`, then
`$HF_HOME/hub`, then `~/.cache/huggingface/hub`. `env.sh` sets `HF_HOME` to
`$FORGE_RUNTIME/hf-cache` only if `HF_HOME` is not set already. On the
reference machine both `HF_HOME` and `HUGGINGFACE_HUB_CACHE` are exported
globally, so the weights live in a shared cache elsewhere, and a lab log notes
that the legacy variable won over `HF_HOME` [log]. To see where your cache
resolves, run either of these:

```sh
setup/download_models.sh --dry-run --offline | head -2
"$FORGE_RUNTIME"/envs/yue2/bin/python -c "from huggingface_hub import constants; print(constants.HF_HUB_CACHE)"
```

## 3. Steps, in order

Each script prints what it is about to do, skips what is already done, and stops
with a message that says what to do next. Run them again after a failure: they
resume. Run against an existing, working installation, they change nothing (they
compare the installed packages with the lock files and exit).

The durations below are for the reference machine: 52 cores, NVMe disk for the
runtime, and a USB Wi-Fi link of about 10 MB/s, shared at times. Downloads
dominate almost every step, so scale them by your bandwidth.

| # | Command | Downloads | Duration on the reference machine |
|---|---|---|---|
| 1 | `setup/install_blender.sh` | 385 MB | 54 s [measured] |
| 2 | `setup/env_krea.sh` | 4.3 GiB of wheels | 6 min 42 s [measured] |
| 3 | `setup/env_trellis2.sh` | about 7 GB cold: 2.5 GB conda toolkit, 4 GB of wheels, 0.8 GB of git [estimate] | 9 min 38 s with most downloads already local [measured]; about 55 min fully cold [log] |
| 4 | `setup/env_unirig.sh` | 4.1 GiB of wheels | 16 min 47 s [measured] |
| 5 | `setup/env_yue2.sh` | 4.0 GiB of wheels, plus about 0.5 GB of conda packages [estimate] | 8 min 02 s, conda packages already local [measured] |
| 6 | `setup/download_models.sh` | about 49 GB (one GGUF) or 57 GB (both) | about 100 s per GB at 10 MB/s, so 1.5 h or more [estimate] |
| 7 | `setup/check.sh` | none | 40 to 60 s [measured] |
| 8 | game and validation tools (4.6) | about 100 MB | a minute or two |

The environment scripts are independent of each other; `download_models.sh`
needs the yue2 environment for its last two files (Demucs and Whisper live
inside it), so run it after step 5, or run `--only krea|trellis2|unirig` earlier.
You can do only the stages you need: each stage needs its own environment and
weight group, and stages 2, 3 and 4 of the pipeline also need Blender.

### 3.1 Blender

```sh
setup/install_blender.sh
```

Downloads the official Blender 5.2.4 LTS Linux tarball from
download.blender.org into `$FORGE_RUNTIME/downloads/`, checks its SHA-256
against the official `blender-5.2.4.sha256` file and against the hash pinned in
`setup/versions.sh` (they must agree), unpacks it to
`$FORGE_RUNTIME/blender/blender-5.2.4-linux-x64/` and points
`$FORGE_RUNTIME/blender/current` at it. `env.sh` then exports `FORGE_BLENDER`.
The pipeline's Blender scripts use the 5.2 Python API (layered actions,
`BLENDER_EEVEE`); older Blender versions will not run them [log].

### 3.2 The four Python environments

```sh
setup/env_krea.sh       # concept images: Krea 2 Turbo
setup/env_trellis2.sh   # image to 3D: TRELLIS.2 (compiles four CUDA extensions)
setup/env_unirig.sh     # rigging: UniRig
setup/env_yue2.sh       # music: YuE2
```

What each one builds:

- **krea**: a uv venv, CPython 3.12.13, the 72 packages in `setup/locks/krea.txt`:
  PyTorch 2.14.1 (CUDA 12.6 build), diffusers at commit `f9316b0d` (the Krea 2
  classes are not in a diffusers release), transformers 5.22.2, gguf 0.19.0.
  This environment had no lab log; it was reconstructed from the installed
  packages and from what `pipeline/gen_image.py` imports.
- **trellis2**: clones `microsoft/TRELLIS.2` at `75fbf018` with its Eigen
  submodule; a conda env from the explicit list `setup/locks/trellis2.conda.txt`
  (Python 3.10.22, CUDA 12.4 compiler and libraries, gcc 12, all from
  conda-forge); the packages in `setup/locks/trellis2.txt` (PyTorch 2.6.0+cu124,
  xformers 0.0.29.post3, transformers 4.57.6, ...) and the flash-attn 2.7.3
  wheel; then writes `envs/trellis2/build-env.sh` and compiles nvdiffrast 0.4.0,
  CuMesh, FlexGEMM and o-voxel at pinned commits against that toolkit.
  Anything you compile into this env later must `source build-env.sh` first.
- **unirig**: clones `VAST-AI-Research/UniRig` at `6793c664`; a uv venv with
  CPython 3.11.14 and the 147 packages in `setup/locks/unirig.txt` (PyTorch
  2.6.0+cu124, bpy 4.2.0, spconv-cu124 2.3.8, transformers 4.51.3, numpy
  1.26.4) plus three prebuilt wheels checked by SHA-256 (flash-attn
  2.7.4.post1, torch-scatter 2.1.2, torch-cluster 1.6.3). `pipeline/rig.py`
  copies its config overrides into the checkout and applies
  `pipeline/unirig/ptv3-sdpa.patch` the first time it runs.
- **yue2**: clones `multimodal-art-projection/YuE` at `1116bea7`; a conda env
  from `setup/locks/yue2.conda.txt` (Python 3.12.16, ffmpeg 9.0.2); the packages
  in `setup/locks/yue2.txt` (PyTorch 2.10.0+cu128, transformers 4.57.6, demucs
  4.0.1, faster-whisper 1.2.1, ...) and `yue2-infer` 0.1.6 from the checkout.

The lock files are complete freezes of the working environments, installed with
`--no-deps`, so nothing is re-resolved and nothing newer slips in. The conda
lists are `conda list --explicit` output, so conda does not run its solver either.

### 3.3 Weights

First look at what will be downloaded and what you already have:

```sh
setup/download_models.sh --dry-run
```

It prints the cache location, then one line per file (size, cached or MISSING),
a total per stage, the space still needed and the free space at the cache. Then:

```sh
setup/download_models.sh                      # everything
setup/download_models.sh --only trellis2      # one stage; repeat --only for more
```

Only the files the stages load are fetched, not whole repos:

| Stage | Repos and files | Size |
|---|---|---|
| krea | `krea/Krea-2-Turbo`: text encoder, VAE, tokenizer, configs (not the 26 GB transformer). `vantagewithai/Krea-2-Turbo-GGUF`: `krea2_turbo-Q5_K_M.gguf` and/or `krea2_turbo-Q4_K_M.gguf` | 9.4 GB + 8.9 / 7.5 GB |
| trellis2 | `microsoft/TRELLIS.2-4B`: `pipeline.json` and the seven checkpoints it names (not the two encoders). `microsoft/TRELLIS-image-large`: the sparse-structure decoder. DINOv3 ViT-L/16. `ZhengPeng7/BiRefNet` | 16.6 GB |
| unirig | `VAST-AI/UniRig`: the skeleton and skin checkpoints (the full repo is 11.5 GB, mostly training data). `facebook/opt-350m`: `config.json` | 5.8 GB |
| yue2 | `m-a-p/YuE2-3B`, `m-a-p/YuE2-Vae`, plus Whisper small and the Demucs `htdemucs` checkpoint into the yue2 env | 8.4 GB |

Which GGUF: `pipeline/gen_image.py` picks Q5_K_M on cards with 12 GiB or more
and Q4_K_M below. `--gguf auto` (the default) fetches whichever the GPUs in this
machine need; `--gguf q4|q5|both` overrides that.

Downloads use plain HTTPS (`HF_HUB_DISABLE_XET=1` is the default in these
scripts) and resume after an interruption: run the same command again. If
`.env.local` sets `HF_HUB_OFFLINE=1`, the script lifts it for its own run.

### 3.4 Check

```sh
setup/check.sh                 # all stages
setup/check.sh --only yue2     # one stage
```

One line per check, `OK`, `WARN` or `FAIL`, and exit status 1 if anything
failed. It checks the host tools, every GPU with its memory and compute
capability, Blender's version, and for each stage: the environment exists, its
packages match the lock file, the key imports work, the repo is at the tested
commit, and every weight file is in the cache. It loads no model and runs no GPU
kernel. On the reference machine it takes 40 to 60 s, nearly all of it Python
imports [measured]. The trellis2 import needs a GPU to be visible (FlexGEMM sets
up Triton at import time); `check.sh` shows it the GPU with the most free memory,
or the one you name with `--gpu N`, and this did not raise that GPU's memory use
[measured].

A good result ends with `All required checks passed.` Then try one real job per
stage on your card (commands in `docs/logs/*-setup.md`, section "Reproduce from
scratch"), for example the music stage:

```sh
./forge music --instrumental --seconds 60 --seed 1101 --out /tmp/field.ogg \
  --prompt "Bright pastoral adventure theme, solo flute over light strings, 88 BPM, D major"
```

## 4. Known traps

Each of these cost someone time while the stages were first set up. The scripts
already handle the ones marked *handled*.

### 4.1 Installing

- **conda picked CUDA 13.5 and gcc 15 without saying so** (*handled*). With
  `-c nvidia/label/cuda-12.4.1`, the NVIDIA channel's index failed to load and
  the solver silently took the newest CUDA from conda-forge [log]. The trellis2
  env is now created from an explicit package list, so no solver runs.
- **Two conda installs at once broke each other.** `conda create` failed with
  `No such file or directory: .../pkgs/python-3.11.18-...conda` (and once for
  `libpython-3.12.16`) because another conda process was writing to the shared
  package cache [log]. Do not run two conda-based scripts (`env_trellis2.sh`,
  `env_yue2.sh`) at the same time. If it happens, rerun.
- **uv rejected wheel files with `%2B` in the name** (*handled*): `curl -O`
  keeps the URL-encoded `+` of the PyG wheel URLs, and uv says `has an invalid
  version` [log]. The scripts save them with a `+`.
- **Eigen comes from gitlab.com** and took 20 minutes once, and failed once with
  `GitLab is currently unable to handle this request due to load` [log]. Rerun,
  or set `FORGE_EIGEN_MIRROR` to an Eigen clone you already have (any clone that
  contains both pinned Eigen commits; the TRELLIS.2 checkout's
  `.git/modules/o-voxel/third_party/eigen` does).
- **o-voxel would rebuild CuMesh and FlexGEMM from their latest commit**
  (*handled*): its `pyproject.toml` lists them as `git+https` dependencies; the
  script builds it with `--no-deps` [log].
- **CUDA headers not found** (*handled*): conda-forge puts them in
  `targets/x86_64-linux/include`; `build-env.sh` adds that to `CPATH` [log].
- **Upstream's `setup.sh` for TRELLIS.2 is not used**: it calls `sudo apt`,
  installs pillow-simd and clones the extensions at their latest commit [log].
- **Compiling for the wrong GPU.** `build-env.sh` sets `TORCH_CUDA_ARCH_LIST`
  from the GPUs visible when it was written (the reference machine's file says
  `7.5;8.6`). If you move the env to a different card, add its compute
  capability and rebuild the extensions.

### 4.2 Downloading

- **The Hugging Face Xet transfer stalled** (*handled*): with four downloads at
  once on a ~10 MB/s link, a 7 GB file sat at 67 MB, then 201 MB, for minutes;
  plain HTTPS (`HF_HUB_DISABLE_XET=1`) finished [log]. The scripts set it; set
  `HF_HUB_DISABLE_XET=0` to use Xet anyway. A stalled attempt can leave an
  `.incomplete` file in the cache's `blobs/` folder; it is harmless.
- **Demucs could not fetch its own weights**: the certificate of
  `dl.fbaipublicfiles.com` expired on 2026-11-11 [log]. `download_models.sh`
  fetches the same file from a Hugging Face mirror and checks its SHA-256
  (`8726e21a...`, which also matches the prefix Demucs puts in the file name).
- **`HF_HUB_OFFLINE=1`** in `.env.local` is a good idea once everything is
  downloaded (no surprise network calls during runs), but it makes any missing
  file an immediate error. `check.sh` tells you which file is missing.

### 4.3 Gated models

Two repos need you to accept terms on the Hugging Face website with the account
whose token you use:

- **`krea/Krea-2-Turbo`**: accept the licence on the model page; access is
  granted at once. Note that the licence is Krea's own (`license: other` on the
  model card); read it before you ship anything.
- **`facebook/dinov3-vitl16-pretrain-lvd1689m`**: access is approved by hand
  and can take hours. Our token first got HTTP 403 and was approved later the
  same morning [log]. Instead, you can use an ungated copy of the same weights:
  `camenduru/dinov3-vitl16-pretrain-lvd1689m`. Its `model.safetensors` was
  checked byte for byte against Meta's (same git object id; details in
  `docs/logs/trellis2-setup.md` section 4). To use it, put
  `FORGE_DINOV3_MODEL=camenduru/dinov3-vitl16-pretrain-lvd1689m` in `.env.local`
  before running `download_models.sh`. The DINOv3 License still applies to the
  copy.

`briaai/RMBG-2.0`, which TRELLIS.2's `pipeline.json` names for background
removal, is gated and non-commercial. The pipeline never loads it; it uses
`ZhengPeng7/BiRefNet` (MIT) instead [log].

### 4.4 Running on a Turing card (RTX 20xx)

These are handled inside the pipeline scripts, not by setup, but they explain
some of the pins:

- FlashAttention 2 has no Turing kernels. It is installed because code imports
  it; attention runs on xformers (TRELLIS.2), PyTorch SDPA (UniRig, with
  config overrides and a patch) or a workaround in `make_bgm.py` (YuE2) [log].
- BF16 runs, but emulated and much slower (YuE2: 7 vs 44 TFLOPS for FP16), so
  the stages switch to FP16 on Turing [log].
- PyTorch 2.6 made `torch.load` default to `weights_only=True`; UniRig's skin
  checkpoint then fails with `Unsupported global: GLOBAL box.box.Box`.
  `rig.py` sets `TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1` for UniRig only [log].
- UniRig's own scripts exit 0 and print `done` even when inference failed;
  `rig.py` checks that each output file exists instead [log].
- `torch.cuda.is_bf16_supported()` returns True on Turing (it counts emulation),
  so do not use it to choose a dtype [log].

### 4.5 Other things worth knowing

- **Weights on a spinning disk are slow to load.** TRELLIS.2: 256 s cold vs
  108 s warm (15 s with our loader) [log]; YuE2 re-hashes its 7.3 GB weights on
  every load, 77 s cold vs 17 s warm (`--skip-hash-check` skips it) [log].
- **First run on a new GPU model is slower.** FlexGEMM and Triton tune their
  kernels on first use (512 generate: 83 s cold vs 35-40 s warm on the 2080 Ti);
  the wrapper keeps those caches in `envs/trellis2/cache/` [log].
- **UniRig's example GLBs** in the checkout are Git LFS pointer files (no
  git-lfs needed for the pipeline itself) [log]. **UniRig's `extract.sh`** runs
  `pip install psutil` on every call; in a uv venv without pip it prints
  `pip: command not found` and carries on [log].
- **The YuE2 instrumental skill's own CLI** refuses to run unless `yue2-infer` is
  exactly 0.1.5; `make_bgm.py` uses the skill's helper modules with 0.1.6 [log].

### 4.6 Game and validation tools (Node)

`./forge check` installs the game's own dependencies (`three` 0.187.0, `vite`,
`playwright-core` 1.63.0) on first use and drives headless Chromium; it uses the
browser build in `~/.cache/ms-playwright` that matches playwright-core
(`npx playwright-core install chromium` fetches it; not tried here).
`pipeline/rig.py` validates rigged GLBs when `FORGE_NODE_TOOLS` points at a
folder with `three` and the Khronos validator:

```sh
mkdir -p "$FORGE_RUNTIME/node-tools" && cd "$FORGE_RUNTIME/node-tools"
npm init -y && npm install three@0.187.0 gltf-validator@2.0.0-dev.3.10
echo 'FORGE_NODE_TOOLS=$FORGE_RUNTIME/node-tools' >> "$FORGE_ROOT/.env.local"
```

(These are the versions installed on the reference machine; the UniRig log says
`three@0.180.0`, which is older than what is there now.)

## 5. Verifying a finished install

1. `setup/check.sh` ends with `All required checks passed.`
2. `setup/download_models.sh --dry-run` ends with `Total still to download: 0 B`.
3. Running any `setup/env_*.sh` again prints `exists and matches ...; nothing to do`.
4. One real job per stage succeeds on your card (see 3.4).

## 6. How this was tested

On 2026-11-12, on the reference machine:

- `bash -n` and ShellCheck 0.10.0 on every script: clean.
- **Idempotence on the working installation.** Every `setup/*.sh` was run
  against the existing runtime. Each detected the existing environment, repo or
  Blender and exited 0. A listing of every file (path, size, mtime) under the
  four environments, the three repos and Blender, plus `uv pip freeze` of each
  environment, was identical before and after.
- **From scratch.** All four environments, the three repos and Blender were
  built into an empty `FORGE_RUNTIME` on the NVMe disk, with an empty uv cache
  of their own, and `setup/check.sh` passed against it. Durations are in the
  table in section 3. What was not cold: the conda packages for trellis2 and
  yue2 came from the machine's existing conda package cache; trellis2's
  PyTorch wheels were already in the scratch uv cache from the unirig build;
  the TRELLIS.2 clone borrowed git objects from the existing checkout
  (`FORGE_GIT_REFERENCE`) and Eigen came from the existing clone
  (`FORGE_EIGEN_MIRROR`); the flash-attn 2.7.3 wheel had been downloaded a few
  minutes earlier to hash it. The four CUDA extensions were compiled from
  source: nvdiffrast 36 s, CuMesh 239 s, FlexGEMM 81 s, o-voxel 77 s.
- **Resume and refusal.** An `env_krea.sh` run killed after 0.3 s resumed on the
  next run; an unrelated venv at the same path was left untouched and the
  script stopped with a message.
- **Weights.** `download_models.sh --dry-run` was run against the full cache.
  Real downloads were not repeated (tens of GB); `download_models.sh` was run
  for all four groups against the scratch runtime, where everything in the
  Hugging Face cache was found and the Demucs checkpoint (84 MB) was actually
  downloaded and its SHA-256 checked. Whisper was copied in from the working
  env rather than downloaded.
- **Not tested here:** a fully cold trellis2 build (network for its toolkit
  and Eigen), downloading the large weight files with these scripts, the gated
  official DINOv3 repo, a machine without the reference machine's caches, other
  Linux distributions, and any generation job on the scratch installation
  (`check.sh` imports code but loads no model).

## 7. Changing a pin

All pins live in `setup/versions.sh` (Blender, Python versions, git commits,
wheel files with their SHA-256), `setup/locks/` (Python and conda packages) and
`setup/models.py` (model repos, files and the commits they were tested at).
The lock files are freezes of working environments: to move a package, build an
environment that works with the new version, then regenerate the lock from it
with `uv pip freeze` (or `conda list --explicit --md5`), and remove the lines of
packages the scripts install separately (they are listed in the script).
