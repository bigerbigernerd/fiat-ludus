# Rigging stage: UniRig + animation retargeting — setup and debugging log

Stage goal: a static, textured humanoid mesh (what an image-to-3D model such as TRELLIS.2 gives
us) becomes one GLB with a skeleton, skin weights and five named clips (`idle`, `walk`, `run`,
`jump`, `attack`) that three.js can play.

Machine: Ubuntu 24.04, no sudo, no system CUDA toolkit. UniRig runs on GPU 1, an
RTX 2080 Ti (11 GB, Turing, sm_75), driver 595.99. Blender 5.2.4 LTS (tarball install).
Date of this log: 2026-11-12.

## 0. What runs where

| step | tool | device | lock |
|---|---|---|---|
| normalise mesh (join, scale to 1.7, feet at origin) | Blender 5.2 headless | CPU | no |
| UniRig mesh extraction | UniRig env (bpy 4.2 pip module) | CPU | no |
| UniRig skeleton prediction | UniRig env, PyTorch | GPU 1 | `tools/gpu_run.sh` |
| UniRig skin prediction | UniRig env, PyTorch | GPU 1 | `tools/gpu_run.sh` |
| UniRig merge onto textured mesh | UniRig env (bpy 4.2) | CPU | no |
| fit library skeleton, retarget clips, export | Blender 5.2 headless | CPU | no |
| renders (stills, contact sheet) | Blender 5.2, Cycles **CPU** | CPU | no |
| validation | Node 22: Khronos glTF validator + three.js GLTFLoader | CPU | no |

## TL;DR

* **Works end to end, unattended:** `pipeline/rig.py --mesh in.glb --out character.glb` takes
  100–165 s per character on the 2080 Ti (two GPU steps of ~40–48 s each, the rest CPU) and writes
  a GLB with a 53-bone skeleton, skin weights, the original textures and five clips. Khronos
  validator: 0 errors on every output; three.js `GLTFLoader` sees the five clips.
* **UniRig needs three fixes on a Turing card (and on PyTorch ≥ 2.6):** FlashAttention-2 is
  switched off in two config files, a 14-line patch makes the point-transformer attention
  memory-efficient (skin step 9.3 GB → 6.5 GB), and the skin checkpoint needs
  `TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1`. Both steps then fit in 7 GB (simulated 8 GB card).
* **Recommended route: "fit".** Use UniRig only for joint positions and skin weights; move the
  animation library's own skeleton onto those joints. Every character then has the same bone
  names (`DEF-hand.R` for the sword), and clips map by name. Motion quality is the same as
  retargeting onto UniRig's own skeleton (we rendered both).
* **Input pose that works:** full body, front view, T-pose or A-pose with a gap between arms and
  torso, legs apart, character facing the camera. A 70-degree A-pose with arms *fused* to the
  torso still rigged acceptably. What breaks: appendages UniRig does not give bones to (a tail
  on UniRig's own example character stretched between the legs).

## 1. Chronological log

### 1.1 Repo and licences (10:46)

```
git clone https://github.com/VAST-AI-Research/UniRig      # commit 6793c6640ff01c8fb389f3993434124bb43d2933 (2026-06-04)
```

* Code: MIT (`LICENSE`, "Copyright (c) 2025 VAST-AI-Research and contributors").
* Checkpoints: Hugging Face model card `VAST-AI/UniRig` says `license: mit`, trained on
  Articulation-XL 2.0 (repo revision `36842e2b5947e9e60f89275b83208c8e74071c63`). Not gated.
* The README now points to a successor, **SkinTokens** (same lab, MIT). Checked, not used: its
  README asks for an NVIDIA GPU with at least 14 GB for inference, plus flash-attn. It does not
  fit the 11 GB target card.

Skimmed before running: `run.py`, `launch/inference/*.sh`, `src/inference/download.py`,
`src/inference/merge.py`, `src/system/{ar,skin}.py`. Network access: only `hf_hub_download` of
the two checkpoints, plus `AutoConfig.from_pretrained("facebook/opt-350m")` (a config file).
One surprise: `launch/inference/extract.sh` runs `pip install psutil` on every call
(harmless here, since our env has no pip; it prints `pip: command not found` and goes on).

### 1.2 Python env (10:47–11:45)

The first try, `conda create -p envs/unirig python=3.11`, died after 7 minutes:

```
[Errno 2] No such file or directory: '/media/.../cache/conda/pkgs/python-3.11.18-h5f976f7_0_cpython.conda'
```

(shared conda package cache being written by other installs at the same time). Switched to
**uv**, which was instant:

```
uv venv --python 3.11 $FORGE_RUNTIME/envs/unirig            # CPython 3.11.14
```

Then PyTorch. The plan was torch 2.5.1+cu124 (the README says ">= 2.3.1"). On the shared ~10 MB/s
link, with three other agents downloading model weights, that download crawled for 40 minutes.
The uv cache on this machine already held **torch 2.6.0+cu124**, so we switched to it (uv still
re-downloaded the 733 MB torch wheel because the cached copy came from another index URL, but
all NVIDIA libraries came from cache). That choice has one consequence, see 1.6.

```
export VIRTUAL_ENV=$FORGE_RUNTIME/envs/unirig
uv pip install torch==2.6.0 torchvision==0.21.0 --index-url https://download.pytorch.org/whl/cu124
# PyG and FlashAttention prebuilt wheels (no nvcc on this machine):
curl -LO https://data.pyg.org/whl/torch-2.6.0%2Bcu124/torch_scatter-2.1.2%2Bpt26cu124-cp311-cp311-linux_x86_64.whl
curl -LO https://data.pyg.org/whl/torch-2.6.0%2Bcu124/torch_cluster-1.6.3%2Bpt26cu124-cp311-cp311-linux_x86_64.whl
curl -LO https://github.com/Dao-AILab/flash-attention/releases/download/v2.7.4.post1/flash_attn-2.7.4.post1%2Bcu12torch2.6cxx11abiFALSE-cp311-cp311-linux_x86_64.whl
for f in *%2B*; do mv "$f" "${f//%2B/+}"; done      # uv rejects '%2B' in wheel file names
uv pip install transformers==4.51.3 python-box einops omegaconf pytorch_lightning lightning addict \
    timm fast-simplification bpy==4.2.0 trimesh open3d==0.20.0 pyrender huggingface_hub wandb psutil \
    spconv-cu124==2.3.8 numpy==1.26.4 ./torch_scatter-*.whl ./torch_cluster-*.whl ./flash_attn-*.whl \
    torch==2.6.0 --extra-index-url https://download.pytorch.org/whl/cu124 --index-strategy unsafe-best-match
```

Error on the way:

```
error: The wheel filename "torch_scatter-2.1.2%2Bpt26cu124-cp311-cp311-linux_x86_64.whl" has an invalid version
```

`curl -O` keeps the URL-encoded `+`; rename the files.

Speed-up that mattered: while torch downloaded, a throwaway venv ran
`uv pip install --no-deps bpy==4.2.0 open3d ...` to fill the uv cache in parallel (bpy 4.2 is
495 MB, open3d 382 MB). The final install then took 75 s.

Result (full lock in `pipeline/unirig/requirements-lock.txt`): torch 2.6.0+cu124, numpy 1.26.4,
bpy 4.2.0, spconv-cu124 2.3.8, flash-attn 2.7.4.post1, transformers 4.51.3, lightning 2.6.6,
open3d 0.20.0, torch-scatter 2.1.2, torch-cluster 1.6.3. **Env size 8.7 GB.**

flash-attn *imports* fine on Turing (the skin model imports `flash_attn.modules.mha.MHA`,
which with its default `use_flash_attn=False` is plain PyTorch). Its CUDA kernels do not run on
sm_75 — see 1.4.

### 1.3 Weights (downloaded by the coordinator, finished 11:50)

`hf download VAST-AI/UniRig` fetches the whole repo: **11.5 GB**, most of it training data
(`data/rigxl/processed.7z` 4.9 GB). Inference needs two files, **5.8 GB**:

```
HF_HUB_DISABLE_XET=1 hf download VAST-AI/UniRig \
    --include "skeleton/articulation-xl_quantization_256/*" "skin/articulation-xl/*"
```

| file | size | content |
|---|---|---|
| `skeleton/articulation-xl_quantization_256/model.ckpt` | 1.44 GB | 360 M params fp32 (OPT-350m-style decoder + Michelangelo shape encoder) |
| `skin/articulation-xl/model.ckpt` | 4.38 GB | 365 M params fp32 **plus Adam optimizer state** (that is why it is 3x the params) |

On this link the default Xet transfer client made no progress while four clients ran at once;
plain HTTPS (`HF_HUB_DISABLE_XET=1`) did. Weights resolve through the machine's normal
`HF_HOME`/`HUGGINGFACE_HUB_CACHE`; `rig.py` sets neither.

### 1.4 The README examples (11:48–11:54)

`examples/*.glb` in the clone are **Git LFS pointer files** (132 bytes; no git-lfs here):

```
Error: Bad glTF: json error: Expecting value: line 1 column 1 (char 0)
ValueError: failed to load examples/tira.glb
```

Fetched from GitHub's media endpoint (sha256 matches the LFS oid):
`curl -L -o examples/tira.glb https://media.githubusercontent.com/media/VAST-AI-Research/UniRig/main/examples/tira.glb`

Unmodified README command, through the GPU wrapper:

```
tools/gpu_run.sh rig readme-skeleton-tira --gpu 1 -- \
  env -C $FORGE_RUNTIME/repos/UniRig PATH=$FORGE_RUNTIME/envs/unirig/bin:$PATH \
  bash launch/inference/generate_skeleton.sh --input examples/tira.glb --output .../tira_skeleton.fbx
```

```
Flash Attention 2.0 only supports torch.float16 and torch.bfloat16 dtypes, but the current dype in OPTForCausalLM is torch.float32. ...
FlashAttention only supports Ampere GPUs or newer.
Predicting DataLoader 0: 100%| 1/1
done
```

**Exit code 0 and no output file**: UniRig catches the exception inside the predict step and
the shell script ends with `echo done`. `rig.py` therefore checks that each step's output file
exists instead of trusting exit codes.

Fix 1, configs only (`pipeline/unirig/`, copied into `configs/` by `rig.py`):

* `forge_unirig_ar_350m.yaml`: `_attn_implementation: sdpa` instead of `flash_attention_2`
  (transformers' OPT supports SDPA);
* `forge_unirig_skin.yaml`: `enable_flash: False` for the PTv3 point encoder;
* two task files that point at those models. `precision: bf16-mixed` stays — Lightning accepts
  bf16 autocast on Turing and it ran fine (PyTorch emulates it).

With those, skeleton prediction on `tira.glb` worked: **47 s wall, peak 6,969 MiB**, a clean
52-bone humanoid (hips, 3 spine, neck, head, clavicle/upper arm/forearm/hand + 5 three-bone
fingers per hand, thigh/shin/foot/toe). It did **not** predict a bone for Tira's tail.

Skin step, unmodified README command:

```
_pickle.UnpicklingError: Weights only load failed. ...
WeightsUnpickler error: Unsupported global: GLOBAL box.box.Box was not an allowed global by default.
```

PyTorch 2.6 changed `torch.load`'s default to `weights_only=True`; the skin checkpoint pickles
a `python-box` object. Fix 2: `TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1` for the UniRig processes
(torch 2.5 would not need it). Only for checkpoints from a source you trust.

Skin then ran: **37.5 s, peak 9,285 MiB** — under 11 GB, but with only ~1.7 GB to spare.
Merge onto the original GLB (`python -m src.inference.merge --source skin.fbx --target tira.glb
--output tira_rigged.glb`, CPU, 22 s) kept all 26 materials and their 4K textures. Posed stills
looked right (see 1.7).

### 1.5 Our own test meshes (in parallel, 10:50–11:40)

CC0 sources (details and URLs in `assets/animations/SOURCES.md`):

* **Quaternius Universal Base Characters** (Standard, CC0): `Superhero_Male_FullBody.gltf`,
  textured, 14 k triangles, T-pose, Unreal-style 65-bone rig. The itch.io page is
  "name your own price"; the free download works without an account through itch's own
  endpoints (`POST /download_url` with the page's CSRF token, then `POST /file/<upload_id>`),
  which is what the browser button does.
* **Quaternius LowPoly Animated Knight** (2018, CC0): 956 triangles, big head, flat colours.

`pipeline/blender/prep_test_mesh.py` makes the static inputs:

| test mesh | how | what it imitates |
|---|---|---|
| `male_static` | skeleton stripped, parts joined, 3 materials | a clean game-art humanoid |
| `male_voxel` | voxel remesh 12 mm → decimate to 40 k tris → smart UV → Cycles bake of the colours into one 1024² texture | TRELLIS-style: one fused watertight surface, one baked texture, no edge flow |
| `male_apose_voxel` | upper arms lowered 70° first, voxel 20 mm | the hard case: arms fused to the torso along the armpits |
| `knight` | stripped | stylised chunky proportions |

Debugging the bake (each one cost a re-run):

1. **Baked texture all black, no error.** The imported materials multiply base colour by the
   vertex colour attribute, and the script had already deleted vertex colours. Delete them
   *after* baking.
2. Linking the bake target texture to the material's Base Color before baking gives
   `Circular dependency for image "baked_basecolor"`. Link it after baking.
3. Missing normal-map files referenced by the source glTF (`T_Eye_Normal_png.png`) make Cycles
   print `Failed to load 2 image files`; harmless for a colour bake but we drop those nodes.
4. The 2018 knight FBX imports with material alpha 0 (fully transparent renders). Forced to 1.

### 1.6 Rendering headless on this machine

`bpy.types.RenderSettings.engine` in Blender 5.2: `BLENDER_EEVEE`, `BLENDER_WORKBENCH`, `CYCLES`
(not `WORKBENCH`). With `-b`, **EEVEE renders fine** (it gets a GPU context through EGL — it
does touch a GPU), Cycles on the CPU too (52 cores: 16 samples at 400x500 in ~0.4 s). All our
stills and contact sheets use **Cycles CPU**, so rendering never competes for the GPU lock.

### 1.7 End to end with rig.py (11:55 onwards)

`rig.py` chains everything (see its docstring). Per character on the 2080 Ti (all numbers
from `cost/ledger.jsonl` and `assets/_smoke/rig/rig_runs.json`):

| mesh | tris | UniRig bones | skeleton step | skin step | whole `rig.py` |
|---|---|---|---|---|---|
| `male_static` (clean, 3 materials) | 14 k | 52 | 47.5 s | 38.4 s | 120 s |
| `male_voxel` (fused, baked) | 40 k | 40 | 44.5 s | 35.4 s | 120 s |
| `male_apose_voxel` (arms fused) | 14 k | 34 | 34–43 s | 36 s | 93–109 s |
| `knight` (stylised) | 1 k | 28 | 38.4 s | 35.4 s | 101 s |
| `tira.glb` (UniRig example, 26 materials, 4K textures) | 76 k verts | 52 | 48.5 s | 39.4 s | 166 s |

Each GPU step is about 15–20 s of process start + checkpoint load and 18–20 s of actual
inference (the skin checkpoint is 4.4 GB to read). Energy per character ≈ 1.0 Wh (skeleton) +
0.5 Wh (skin), GPU only. The CPU steps: extract 5–10 s each, merge 6–22 s, fit 1–5 s, retarget
+ export 3–22 s (most of it writing 4K textures), verify 1–9 s.

UniRig always produced the same 22-bone body (hips, 3 spine, neck, head, clavicle, upper arm,
forearm, hand, thigh, shin, foot, toe); only the finger count varied: 5 three-bone fingers per
hand on clean hands, fewer on blob hands (40, 34 bones), none on the knight's mittens (28).
The topology labeller found all 22 body joints on every run.

### 1.8 VRAM: making both steps fit an 8 GB card (12:07–12:13)

Measured with a wrapper that calls `torch.cuda.set_per_process_memory_fraction` before `run.py`
(simulating a smaller card) and prints `max_memory_allocated` at exit:

| step | before | after the PTv3 patch | under a 7.0 GiB cap (≈ 8 GB card) |
|---|---|---|---|
| skeleton | 6.43 GiB allocated, **6,969 MiB** nvidia-smi peak | (unchanged) | passes |
| skin | 7.70 GiB allocated, **9,285 MiB** peak | 3.95 GiB allocated, **6,541–6,573 MiB** peak | before: OOM; after: passes |

The skin OOM under the cap pointed straight at the cause:

```
File ".../src/model/pointcept/models/PTv3Object.py", line 224, in forward
    attn = self.softmax(attn)
torch.OutOfMemoryError: CUDA out of memory. Tried to allocate 3.00 GiB.
```

With FlashAttention off, PTv3's fallback builds the full `(patches, heads, 1024, 1024)` attention
matrix. `pipeline/unirig/ptv3-sdpa.patch` (applied by `rig.py` with `git apply`) replaces it
with `torch.nn.functional.scaled_dot_product_attention` on fp16 q/k/v — the same fp16 maths the
flash path uses, without the matrix. Check: skin weights from patched vs unpatched code differ
by a mean of 1.5e-4 per weight, with the dominant bone the same on 99.7 % of vertices — exactly
the same spread as two runs of the *unpatched* code against each other (UniRig samples points
randomly), so the difference is noise. Environment variable `FORGE_PTV3_SDPA=0` turns it off.

On the 11 GB 2080 Ti that leaves ≥ 4 GB headroom for both steps. **Smallest card it should
run on: 8 GB** (both steps passed under a 7.0 GiB allocator cap; the extra ~0.35 GB is the CUDA
context). Not tried on a real 8 GB card. A 6 GB card will not run the skeleton step (it failed
under a 5.5 GiB cap; the 15-beam search holds 15 KV caches of the 350 M decoder).

## 2. Quality — what the renders show

All renders in `assets/_smoke/rig/`: `<name>_rest|arms|knee.png` (front + three-quarter view; arms
raised 45° with forearms swung forward; left knee raised 60° and bent 70°, chest twisted 20°)
and `<name>_sheet.png` (rows idle, walk, run, jump, attack; 5 frames each).

* **Clean mesh (`male_clean`):** skin weights are good. Knee and elbow bends keep volume, the
  hip crease folds cleanly, no tearing. Against the character's original hand-made weights the
  difference shows mainly at the shoulders: in the run and attack frames the chest/shoulder area
  dents a little where the artist's rig stays smooth.
* **Fused TRELLIS-like mesh (`male_voxel`):** the same skeleton, slightly softer weights. The
  chest and shoulders crumple visibly in the run cycle (pecs pull with the upper arm). Blender
  bone heat on the same fitted skeleton (`male_voxel_heat`) gave a *cleaner* chest on this
  watertight mesh. Fine at game-camera distance, visible in close-ups.
* **Arms fused to the torso (`male_apose_voxel`):** works better than expected. UniRig still
  put the arm joints inside the arms and separated arm from torso weights; raising the arms
  pulls a web of side-of-chest skin up with them, and the armpit/shorts area smears in deep
  poses (jump take-off). Usable for a game character seen from a distance.
* **Stylised (`knight`, 956 triangles):** big head and mitten hands rig and animate correctly;
  with so few polygons the elbows and knees fold sharply, as the low-poly source would anyway.
* **UniRig's own example (`tira`):** clothes and hair follow well, but **the tail has no bone**
  — its vertices are weighted to the legs and hips and stretch into a grey smear between the
  legs in every clip. Long hair, capes, tails, wings and skirts are the failure class:
  UniRig's humanoid skeleton rarely includes them.

Route comparison on the same UniRig output (`male_voxel` vs `male_voxel_unirigroute`): the
contact sheets are indistinguishable. With the same weights, fitting the library skeleton loses
nothing, and it gains standard bone names.

## 3. Which retargeting route, and why

**Recommended: "fit" (`--route fit`, the default).**

1. Same motion quality as retargeting onto UniRig's own bones (above).
2. Every character ends up with the identical 53-bone `DEF-*` skeleton, so the game can attach
   a sword to `DEF-hand.R`, share one `AnimationMixer` setup, and in principle reuse clips
   between characters with three.js `SkeletonUtils.retargetClip` or by name.
3. The retarget step still runs, but only to correct the rest pose (an A-posed character
   needs the arm rotations re-based); the bone mapping is by name and cannot go wrong.
4. It needs only *joint positions* from UniRig. If a later rigging model (SkinTokens, a newer
   UniRig) outputs a different topology, only the labeller has to cope.

Weights: `--weights unirig` (default) re-assigns UniRig's weights to the library bones;
`--weights heat` uses Blender's bone heat instead. Heat was better on the watertight voxel mesh
and equal elsewhere, but heat is known to fail on meshes with holes and loose parts
(`Bone Heat Weighting: failed to find solution for one or more bones`) — common in AI output —
while UniRig's learned weights never failed here. Keep UniRig as the default, try heat when the
chest/shoulders crumple.

## 4. What input pose to ask the image model for

From the paper/README: nothing prescriptive. UniRig normalises the mesh into [-1, 1] itself
(`normalize_into: [-1.0, 1.0]` in the transform config; scale and position do not matter) and
was trained on Articulation-XL — mostly rest-posed assets. From our runs:

* **Pose:** T-pose and A-pose both worked, *including* a 70° A-pose with arms fused to the torso.
  Prefer arms 30–45° below horizontal with a visible gap between arm and torso and between the
  hands and the hips: the image model is less likely to fuse them, and the shoulder region
  deforms better when the rest pose is between the extremes of the motion. Legs apart a little.
* **Facing:** front view, character facing the camera. TRELLIS output facing +Z (glTF) is what
  we assume; `rig.py` detects a sideways or backwards character from its feet and turns it
  (only in 90° steps). A 3/4 view input is not handled.
* **Hands:** open hands with spread fingers get finger bones (52-bone result); mittens and fists
  get none (the library's finger bones then just follow the hand — fine for a game).
* **Proportions:** chibi/big-head is fine. Avoid tails, capes, long loose hair, wings, long
  skirts, and held objects (a sword in the hand will be weighted to the hand *and* the body —
  generate weapons as separate meshes and attach them to `DEF-hand.R`).
* **Scale/height:** irrelevant to UniRig; `rig.py` normalises to `--height` (1.7 by default).

## 5. Blender 5.2 API notes (checked against the installed `bpy`, not assumed)

* `bpy.types.Action` has **no `fcurves`** attribute any more (`hasattr(action, "fcurves")` is
  `False`). Actions are layered: `action.layers[0].strips[0].channelbag(action.slots[0]).fcurves`.
  To create curves: `action.fcurve_ensure_for_datablock(obj, data_path, index=i, group_name=...)`.
  It creates the slot/layer/strip as needed.
* The glTF importer turns each animation into its own action with one slot (`OBRig` for an
  armature named Rig) and one NLA track per animation; the scene's active action is the first
  clip. When pushing actions onto NLA strips yourself, set `strip.action_slot`.
* Export of separate clips: `export_animation_mode="NLA_TRACKS"` with one muted track per clip,
  named as the clip should be named in the glTF.
* Render engine enum is `BLENDER_EEVEE` / `BLENDER_WORKBENCH` / `CYCLES`.
* glTF-imported objects use `rotation_mode = "QUATERNION"`; setting `rotation_euler` silently
  does nothing — set `matrix_world` instead (this cost one confusing test).

## 6. Debugging log for the retarget/export side

* **Seams tear open under bone-heat weights.** The glTF importer splits vertices along UV
  seams; bone heat treats the halves as separate pieces and weights them differently, and the
  seams (neck, waistband, ankles) open into black slits. Fix: `remove_doubles(1e-5)` before
  `parent_set(type="ARMATURE_AUTO")`. (Welding in the normaliser does not help: the next glTF
  export/import splits them again.)
* **Eyes unweighted after bone heat** (loose parts): filled from the nearest weighted vertex.
* **A stray `Icosphere` mesh** came along from the library GLB (it is the UAL rig's bone
  display shape) and from UniRig's merge output. Not exported: we clear custom shapes and drop
  every mesh without an Armature modifier.
* **Topology labeller failed on raw UniRig output** (`no arm found on side L`): UniRig's
  skeleton FBX is in its normalised frame, centred on the origin (feet at z = -1). Heights are
  now measured from the lowest joint.
* **UniRig's `vroid` class gives wrong names.** Tried `assign_cls: vroid` to get semantic names:
  the output had VRoid names (`J_Bip_L_UpperArm`...) but assigned by sequence order to joints
  in a different order — `J_Bip_L_Hand` sat at the shoulder, `J_Bip_L_UpperLeg` on a finger
  (the released checkpoint is trained on Articulation-XL only; the README marks the mixamo
  class untrained too). The name labeller now checks plausibility (head above hips, knee
  between hip and foot, hand farther out than elbow, left and right on opposite sides) and
  falls back to topology. Keep `articulationxl` and label by topology.
* **Facing test failed first with the turn applied in the wrong direction** (character ended
  up facing away and every limb rotated about the wrong axis). Sign fixed; the facing check
  runs in both `fit_skeleton.py` (so left/right labels are right) and `build_character.py`.
* **Blender exits 0 when a `-P` script raises.** A missing input file "succeeded" and the
  failure only surfaced two steps later inside UniRig. `rig.py` now runs Blender with
  `--python-exit-code 1` and also requires each script's one-line JSON result.
* **Verification poses designed for a T-pose push A-posed arms into the body.** The stills now
  raise the arms instead of lowering them.

## 7. Output format (what the game gets)

* GLB, glTF Y-up, **character faces +Z**, feet on y = 0, height 1.70 (three.js bounding box
  1.699), centred on x/z.
* One skinned mesh (multi-material if the input had several materials; one material for
  TRELLIS-style input), textures embedded.
* Skeleton: 53 bones, `root` + Rigify/UAL `DEF-*` names (`DEF-hips`, `DEF-spine.001..003`,
  `DEF-neck`, `DEF-head`, `DEF-shoulder.L`, `DEF-upper_arm.L`, `DEF-forearm.L`, `DEF-hand.L`,
  fingers, `DEF-thigh.L`, `DEF-shin.L`, `DEF-foot.L`, `DEF-toe.L`, and `.R`).
* Clips (30 fps, sampled, in place): `idle` 2.50 s (loop), `walk` 1.33 s (loop), `run` 0.93 s
  (loop), `jump` 5.17 s (start + airborne loop + land; split it in the game or change
  `assets/animations/clips.json`), `attack` 1.53 s (UAL `Sword_Attack`, empty-handed).
* Validator: 0 errors; warnings `NODE_SKINNED_MESH_NON_ROOT` (Blender parents the mesh to the
  armature node; harmless, three.js ignores the parent transform for skinned meshes and ours is
  identity) and, for inputs with normal maps, `MESH_PRIMITIVE_GENERATED_TANGENT_SPACE`.
* Size: 0.4 MB (knight) to 11 MB (2K textures) — dominated by textures.

## 8. Reproduce from scratch

```
. ./env.sh                       # FORGE_ROOT, FORGE_RUNTIME, FORGE_BLENDER
# 1. code + env
git clone https://github.com/VAST-AI-Research/UniRig $FORGE_RUNTIME/repos/UniRig
git -C $FORGE_RUNTIME/repos/UniRig checkout 6793c6640ff01c8fb389f3993434124bb43d2933
uv venv --python 3.11 $FORGE_RUNTIME/envs/unirig
# then the two `uv pip install` commands of section 1.2 (wheels: PyG torch-2.6.0+cu124,
# flash-attn 2.7.4.post1 cu12torch2.6 cp311; rename %2B -> +), or install from
# pipeline/unirig/requirements-lock.txt
# 2. weights (5.8 GB)
HF_HUB_DISABLE_XET=1 hf download VAST-AI/UniRig \
    --include "skeleton/articulation-xl_quantization_256/*" "skin/articulation-xl/*"
# 3. Blender 5.2.4 LTS tarball at $FORGE_RUNTIME/blender/current/blender (tools/install_blender.sh)
# 4. optional validation tools
mkdir -p $FORGE_RUNTIME/node-tools && cd $FORGE_RUNTIME/node-tools && npm init -y && \
    npm install three@0.180.0 gltf-validator@2.0.0-dev.3.10
export FORGE_NODE_TOOLS=$FORGE_RUNTIME/node-tools
# 5. run (config overrides and the PTv3 patch are installed by rig.py itself)
python3 pipeline/rig.py --mesh character.glb --out character_rigged.glb --gpu 1 \
    --sheet sheet.png --stills stills
```

`rig.py` prints one JSON line: `ok`, per-step `timings_s`, UniRig bone count and labels, final
`bones`, `clips` with durations, weight checks (`verify`), validator counts, `warnings`;
exits 1 with `error` on failure (and keeps the work folder for inspection).

Disk: env 8.7 GB, weights 5.8 GB (1.4 + 4.4), UniRig repo 85 MB (+38 MB example GLBs),
Blender 1.2 GB, animation library 0.4 MB.

## 9. Licences

| thing | licence | where stated |
|---|---|---|
| UniRig code | MIT | `LICENSE` in the repo |
| UniRig checkpoints (`VAST-AI/UniRig`) | MIT | Hugging Face model card metadata (`license: mit`) |
| Quaternius Universal Animation Library (Standard) — shipped as `assets/animations/ual_standard.glb` | CC0 1.0 | `License.txt` in the zip, OpenGameArt page |
| Quaternius Universal Base Characters (Standard) — test mesh only | CC0 1.0 | `License_Standard.txt` in the zip |
| Quaternius LowPoly Animated Knight — test mesh only | CC0 1.0 | OpenGameArt page |
| UniRig `examples/tira.glb` — test only, not shipped | not stated separately (repo is MIT) | — |
| SkinTokens (not used) | MIT; needs ≥ 14 GB VRAM | its README |

## 10. Not verified

* A real TRELLIS.2 mesh: none existed in `assets/_smoke/trellis2/` during this run (only the
  input images). The voxel-remeshed test meshes are a stand-in.
* A real 8 GB card (only simulated with an allocator cap).
* Playback in a browser: three.js parsed the files and evaluated every clip in Node
  (`pipeline/validate_glb.mjs` checks bounding boxes mid-clip), but nothing was rendered in a
  WebGL page.
* Non-humanoid characters (animals, creatures): UniRig rigs them, but the labeller and the
  animation library are humanoid-only; `rig.py` fails with a clear error from the labeller.
* Determinism: UniRig samples (temperature 1.5, 15 beams) and the seed did not make two runs
  bit-identical; the A-pose mesh gave 34 bones on both runs, but skin weights vary by ~1e-4.

## R. The retargeting method (written so you can reimplement it)

Files: `pipeline/blender/retarget.py` (math), `fit_skeleton.py`, `build_character.py`.

### R1. Label the bones

Retargeting needs to know which target bone is the hips, the spine, the neck, the head and the
four limb chains. Two labellers:

* **By name** (`label_by_names`): regexes for the usual schemes — Rigify/UAL `DEF-upper_arm.L`,
  Unreal `upperarm_l` / `calf_l` / `ball_l`, Mixamo `mixamorig:LeftArm`, VRoid
  `J_Bip_L_UpperArm`. Side tokens are stripped first (`.L`, `_l`, `Left`, `J_Bip_L_`).
* **By topology + rest geometry** (`label_by_topology`), for anonymous skeletons such as
  UniRig's `bone_0 ... bone_N`. Assumes Z-up and the character facing -Y (its left is +X):
  1. feet = the lowest leaf bone on each side of x = 0;
  2. hips = the lowest common ancestor of the two feet;
  3. head = the highest leaf that is not in a leg chain; the hips-to-head path is the spine;
  4. hands = the leaf farthest out sideways on each side (above 30 % of the height), excluding
     legs and spine;
  5. an arm chain runs from its branch point on the spine to the hand leaf. The hand is where
     the chain fans out into several children (fingers); without fingers it is the bone that
     covers the last ~9 % of the height. With 3+ bones before the hand, the first is the
     shoulder/clavicle; the elbow is the joint nearest the midpoint between shoulder and wrist;
  6. a leg chain: thigh = first bone after the hips; ankle = first joint whose outgoing bone is
     more horizontal than vertical (or lower than 12 % of the height); knee = the joint nearest
     the hip–ankle midpoint; the bone after the ankle is the toe;
  7. neck = the spine bone just above the higher arm branch, head = the one after it.

  Check: renaming every bone of two named rigs (the Quaternius UE-style base character, 65
  bones, and the UAL rig, 53 bones) to `bone_N` and labelling by topology reproduced the
  name-based labels exactly, 22/22 on both.

### R2. Transfer motion in world space, corrected for different rest poses

Source = the library rig playing a clip. Target = the character's rig. Both armature objects
have identity transforms, so armature space = world space. For each mapped pair (s, t):

```
dir_s, dir_t = rest direction of the bone: from its joint to the next joint in its chain
               (hips->spine->...->neck->head, shoulder->upper arm->forearm->hand,
               thigh->shin->foot->toe); chain ends (hand, head, toe) reuse their parent's
align  = shortest-arc rotation turning dir_s into dir_t     (identity for the hips)
ref_s  = align * Rrest_s          # what the source bone's world rotation would be if the
                                  # source stood in the *target's* rest pose
every frame f:
  D      = Rpose_s(f) * ref_s^-1  # world-space change relative to that common pose
  Rpose_t(f) = D * Rrest_t        # apply the same change to the target bone
```

Because only world-space rotation *changes* are transferred, the two rigs may have different
bone rolls, different local axes, and different rest poses (T-pose library onto an A-pose
character). Twist is preserved because `align` is applied to the full rest rotation, not just
the bone direction.

Hips translation: `pos_t = rest_hips_t + (pos_s(f) - rest_hips_s) * (hip height_t / hip height_s)`;
horizontal drift is removed by default (in-place clips; `root_motion=True` keeps it).

Spine with a different number of bones: target spine bone i (of n) takes source spine bone
`round((i+1)/n * m) - 1` (of m), so the top target bone always follows the top (chest) source
bone. Target bones without a label (fingers, extra spine, hair, tails...) keep their rest
orientation relative to their parent.

Local pose values are then solved down the target hierarchy without the depsgraph:

```
C_t   = P_parent * Rrest_parent^-1 * Rrest_t      # where the bone would be with an identity pose
basis = rot(C_t)^-1 * Rpose_t(f)                  # rotation-only for every bone but the hips
P_t   = C_t * basis
hips: basis = C_t^-1 * (Translation(pos_t) * Rpose_t(f))
```

Keys are written straight into F-curves (`action.fcurve_ensure_for_datablock`, then
`keyframe_points.foreach_set`), with quaternion sign continuity enforced, one key per frame at
30 fps. A 5-clip bake takes about 4 s.

Ground-truth checks (all rendered as contact sheets and inspected):

* UAL clips onto the Quaternius base character's **own** Unreal-style skeleton and weights —
  correct on all five clips, no limb swaps, feet stay planted in idle/walk.
* The same character with its arms lowered 70 degrees and that pose applied as its rest pose
  (A-pose rest) — the contact sheet is indistinguishable from the T-pose one, so the rest-pose
  correction works.
* The rig turned 90 degrees about the vertical axis — detected from the feet (foot->toe
  direction) and turned back automatically. Without that check the result is garbage: the
  hips keep the wrong heading and every limb rotates about the wrong axis.

### R3. Two routes: retarget onto UniRig's skeleton, or fit the library skeleton

**Route "unirig":** keep UniRig's skeleton and skin, label it by topology, retarget.
**Route "fit" (default):** take only *joint positions* (and optionally skin weights) from
UniRig and move the library's own 53-bone skeleton onto them (`fit_skeleton.py`):

* each template bone with a landmark gets its head on the landmark and points at the next
  landmark; its rest matrix is rotated by the shortest arc from the template direction, so
  twist/roll conventions of the library are kept;
* the library has 3 spine bones; their joints are re-sampled at the same fractional lengths
  along the target's hips-to-neck polyline, whatever number of spine joints UniRig made;
* bones without landmarks (fingers, `root`) follow their nearest placed ancestor with the same
  rotation and scale.

Weights for the fitted skeleton, two options:

* `transfer`: UniRig's (or any source rig's) per-vertex weights, re-assigned to the library
  bone with the same label; unlabelled source bones give their weight to the library bone of
  their nearest labelled ancestor (extra spine bones: nearest library spine bone by height);
* `heat`: Blender's automatic weights (bone heat). Needs a welded mesh (see D-log), leaves
  loose parts (eyes) unweighted, which we fill from the nearest weighted vertex.

Which route wins on UniRig output: see section 3.
