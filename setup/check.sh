#!/usr/bin/env bash
# Doctor: is everything the pipeline needs in place?
#
#   setup/check.sh [--only krea|trellis2|unirig|yue2|blender]... [--gpu N]
#
# One line per check: OK, WARN (works, but differs from what was tested or is
# optional) or FAIL (a stage will not run). Exit status 1 if anything FAILs.
# For each stage: environment present, packages match setup/locks, key imports
# work, upstream repo at the tested commit, weights in the Hugging Face cache.
# Plus Blender, the GPUs (with memory) and the host tools.
#
# It loads no model and launches no GPU kernel. Imports run with no GPU visible,
# except trellis2's: FlexGEMM sets up Triton at import time and fails with
# "0 active drivers" when no GPU is visible, so that one import sees one GPU
# (--gpu, default: the one with the most free memory). Measured on the
# reference machine: no increase in that GPU's memory use. It does not take the
# tools/gpu_run.sh lock, so it never waits behind a running job.
# Takes 40-60 s on the reference machine, almost all of it Python imports.
set -uo pipefail
STEP_NAME=check
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

only=()
check_gpu=""
while [ $# -gt 0 ]; do
    case "$1" in
    --only) [ $# -ge 2 ] || die "--only needs a stage name"; only+=("$2"); shift 2 ;;
    --gpu) [ $# -ge 2 ] || die "--gpu needs an index"; check_gpu="$2"; shift 2 ;;
    -h | --help) sed -n '2,19p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown argument '$1' (try --help)" ;;
    esac
done
want() { [ ${#only[@]} -eq 0 ] && return 0; local s; for s in "${only[@]}"; do [ "$s" = "$1" ] && return 0; done; return 1; }
# Imports run from an empty directory so nothing local shadows a package.
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

fails=0
line() {  # line LEVEL STAGE MESSAGE
    printf '%-5s %-9s %s\n' "$1" "$2" "$3"
    [ "$1" = FAIL ] && fails=$((fails + 1))
    return 0
}

echo "FORGE_ROOT=$FORGE_ROOT"
echo "FORGE_RUNTIME=$FORGE_RUNTIME"

# --- host --------------------------------------------------------------------
if command -v python3 >/dev/null; then line OK host "python3 $(python3 -c 'import platform; print(platform.python_version())') (runs forge rig/install)"
else line FAIL host "python3 not found; ./forge rig and ./forge install need it"; fi
for t in git curl; do
    if command -v "$t" >/dev/null; then line OK host "$t found"; else line WARN host "$t not found (only needed by setup/*.sh)"; fi
done
if command -v uv >/dev/null; then line OK host "$(uv --version 2>/dev/null)"; else line WARN host "uv not found (only needed by setup/env_*.sh)"; fi
if command -v node >/dev/null; then line OK host "node $(node --version) (forge check, GLB validation)"
else line WARN host "node not found: ./forge check and rig.py's GLB validation need Node 22"; fi
avail="$(df -BG --output=avail "$FORGE_RUNTIME" 2>/dev/null | tail -1 | tr -dc 0-9)"
[ -n "$avail" ] && line OK host "${avail} GB free under $FORGE_RUNTIME"

# --- GPUs --------------------------------------------------------------------
if ! command -v nvidia-smi >/dev/null; then
    line FAIL gpu "nvidia-smi not found: no NVIDIA driver. Every generation stage needs an NVIDIA GPU."
else
    gpus="$(nvidia-smi --query-gpu=index,name,memory.total,memory.free,compute_cap,driver_version --format=csv,noheader,nounits 2>/dev/null)"
    if [ -z "$gpus" ]; then
        line FAIL gpu "nvidia-smi lists no GPU"
    else
        best="" bestfree=-1
        while IFS=', ' read -r idx rest; do
            IFS=',' read -r name total free cc drv <<<"$rest"
            name="$(echo "$name" | sed 's/^ *//;s/ *$//')"; total="${total// /}"; free="${free// /}"; cc="${cc// /}"; drv="${drv// /}"
            level=OK; note=""
            if [ "$idx" = "$FORGE_GPU_MAIN" ]; then note=" <- FORGE_GPU_MAIN"; fi
            if [ "$idx" = "$FORGE_GPU_MAIN" ] && [ "$total" -lt 10900 ]; then level=WARN; note="$note (below the 11 GB the pipeline was tested on)"; fi
            # PyTorch 2.6 cu124 (trellis2, unirig envs) ships kernels up to sm_90.
            if awk -v c="$cc" 'BEGIN{exit !(c+0 >= 10.0)}'; then level=WARN; note="$note (compute capability $cc: the cu124 builds in the trellis2/unirig envs have no kernels for it)"; fi
            if awk -v c="$cc" 'BEGIN{exit !(c+0 < 7.5)}'; then level=WARN; note="$note (compute capability $cc is older than the tested 7.5)"; fi
            line "$level" gpu "GPU $idx $name, $total MiB total, $free MiB free, compute $cc, driver $drv$note"
            if [ "$free" -gt "$bestfree" ]; then best="$idx"; bestfree="$free"; fi
        done <<<"$gpus"
        echo "$gpus" | cut -d, -f1 | tr -d ' ' | grep -qx "$FORGE_GPU_MAIN" ||
            line FAIL gpu "FORGE_GPU_MAIN=$FORGE_GPU_MAIN is not a GPU index nvidia-smi knows (set it in .env.local)"
        check_gpu="${check_gpu:-$best}"
    fi
fi

# --- Blender -----------------------------------------------------------------
if want blender; then
    if [ -z "${FORGE_BLENDER:-}" ] || [ ! -x "$FORGE_BLENDER" ]; then
        line FAIL blender "no Blender at \$FORGE_RUNTIME/blender/current/blender -> setup/install_blender.sh"
    else
        v="$("$FORGE_BLENDER" -b --factory-startup --version 2>/dev/null | head -1)"
        case "$v" in
        "Blender $FORGE_BLENDER_VERSION"*) line OK blender "$v ($FORGE_BLENDER)" ;;
        "Blender 5.2"*) line WARN blender "$v; tested with $FORGE_BLENDER_VERSION" ;;
        "") line FAIL blender "$FORGE_BLENDER does not run; start it by hand to see the error (often missing X11/GL libraries)" ;;
        *) line FAIL blender "$v; the pipeline's scripts need Blender 5.2 -> setup/install_blender.sh" ;;
        esac
    fi
fi

# --- per stage ---------------------------------------------------------------
# What to import per env (no model is loaded; repo code is put on sys.path).
declare -A IMPORTS
IMPORTS[krea]='
import torch, diffusers, transformers, gguf, accelerate, PIL
from diffusers import Krea2Pipeline, Krea2Transformer2DModel, GGUFQuantizationConfig
info = f"torch {torch.__version__}, diffusers {diffusers.__version__}, transformers {transformers.__version__}"'
IMPORTS[trellis2]='
import os, sys
sys.path.insert(0, os.path.join(os.environ["FORGE_RUNTIME"], "repos", "TRELLIS.2"))
import torch, xformers, flash_attn, nvdiffrast.torch, cumesh, flex_gemm, o_voxel, utils3d, trimesh
from transformers import DINOv3ViTModel
from trellis2.pipelines import Trellis2ImageTo3DPipeline
info = f"torch {torch.__version__}, xformers {xformers.__version__}, nvdiffrast/cumesh/flex_gemm/o_voxel import"'
IMPORTS[unirig]='
import os, sys
sys.path.insert(0, os.path.join(os.environ["FORGE_RUNTIME"], "repos", "UniRig"))
import torch, torch_scatter, torch_cluster, spconv.pytorch, flash_attn, lightning, transformers, box, open3d, trimesh
import bpy
from src.inference.download import download
info = f"torch {torch.__version__}, bpy {bpy.app.version_string}, spconv, torch_scatter, flash_attn import"'
IMPORTS[yue2]='
import os, sys
sys.path.insert(0, os.path.join(os.environ["FORGE_RUNTIME"], "repos", "YuE2", "skills", "yue2-music", "instrumental", "scripts"))
import torch, torchaudio, yue2, demucs.pretrained, faster_whisper, pyloudnorm, soundfile, scipy
from yue2 import YuE2Pipeline
import abc_tools, instrumentalize, compile_score
info = f"torch {torch.__version__}, yue2-infer, demucs, faster-whisper, instrumental helpers import"'

declare -A REPO_DIR=([trellis2]=TRELLIS.2 [unirig]=UniRig [yue2]=YuE2)
declare -A REPO_COMMIT=([trellis2]="$TRELLIS2_COMMIT" [unirig]="$UNIRIG_COMMIT" [yue2]="$YUE2_COMMIT")
declare -A EXTRAS=(
    [krea]=""
    [trellis2]="flash-attn==2.7.3 nvdiffrast==0.4.0 cumesh==0.0.1 flex-gemm==1.0.0 o-voxel==0.0.1"
    [unirig]="flash-attn==2.7.4.post1 torch-scatter==2.1.2+pt26cu124 torch-cluster==1.6.3+pt26cu124"
    [yue2]="yue2-infer==0.1.6")

# Python for the weight checks (needs huggingface_hub 0.36).
hf_py=""
for e in yue2 trellis2 unirig; do
    p="$FORGE_RUNTIME/envs/$e/bin/python"
    if [ -x "$p" ] && "$p" -I -c "import huggingface_hub" 2>/dev/null; then hf_py="$p"; break; fi
done
export HTDEMUCS_URL HTDEMUCS_FILE HTDEMUCS_SHA256
if [ -n "$hf_py" ]; then
    echo "Hugging Face cache: $("$hf_py" -I -c 'from huggingface_hub import constants; print(constants.HF_HUB_CACHE)')"
fi

for stage in krea trellis2 unirig yue2; do
    want "$stage" || continue
    env="$FORGE_RUNTIME/envs/$stage"; py="$env/bin/python"

    # environment and packages
    if [ ! -x "$py" ]; then
        line FAIL "$stage" "no environment at $env -> setup/env_$stage.sh"
    else
        # shellcheck disable=SC2086  # EXTRAS is a space-separated list on purpose
        if diff="$(env_matches_lock "$py" "$LOCKS/$stage.txt" ${EXTRAS[$stage]} 2>&1)"; then
            line OK "$stage" "env $env ($("$py" -c 'import platform; print(platform.python_version())'), packages match locks/$stage.txt)"
        else
            n="$(printf '%s\n' "$diff" | grep -c .)"
            line WARN "$stage" "env $env differs from locks/$stage.txt in $n package(s): $(printf '%s' "$diff" | head -3 | sed 's/^ *//' | paste -sd';')"
        fi
        # imports
        code="${IMPORTS[$stage]}"
        if [ "$stage" = trellis2 ]; then
            if [ -z "$check_gpu" ]; then
                line FAIL "$stage" "import check skipped: it needs a visible GPU and none was found"; code=""
            else
                vis="$check_gpu"
            fi
        else
            vis=""
        fi
        if [ -n "$code" ]; then
            t0=$(date +%s)
            if out="$(cd "$work" && CUDA_VISIBLE_DEVICES="$vis" FORGE_RUNTIME="$FORGE_RUNTIME" \
                "$py" -I -B -c "$code
print('INFO', info)" 2>&1)"; then
                line OK "$stage" "imports: $(printf '%s\n' "$out" | sed -n 's/^INFO //p') ($(( $(date +%s) - t0 )) s${vis:+, GPU $vis visible})"
            else
                line FAIL "$stage" "imports failed: $(printf '%s\n' "$out" | grep -E 'Error|error' | tail -1 | cut -c1-200)"
            fi
        fi
    fi

    # upstream repo
    if [ -n "${REPO_DIR[$stage]:-}" ]; then
        r="$FORGE_RUNTIME/repos/${REPO_DIR[$stage]}"
        if [ ! -d "$r/.git" ]; then
            line FAIL "$stage" "no checkout at $r -> setup/env_$stage.sh"
        else
            head="$(git -C "$r" rev-parse HEAD 2>/dev/null)"
            if [ "$head" = "${REPO_COMMIT[$stage]}" ]; then
                extra=""
                if [ "$stage" = unirig ]; then
                    if grep -q "ai-game-forge patch" "$r/src/model/pointcept/models/PTv3Object.py" 2>/dev/null; then
                        extra=", PTv3 SDPA patch applied"
                    else
                        extra=", PTv3 patch not applied yet (rig.py applies it on first run)"
                    fi
                fi
                line OK "$stage" "repo $r at ${head:0:12}$extra"
            else
                line FAIL "$stage" "repo $r at ${head:0:12}, tested ${REPO_COMMIT[$stage]:0:12}: git -C '$r' checkout ${REPO_COMMIT[$stage]}"
            fi
        fi
    fi
    if [ "$stage" = trellis2 ] && [ -d "$env" ]; then
        if [ -f "$env/build-env.sh" ]; then
            line OK "$stage" "build-env.sh present (source it before compiling extensions)"
        else
            line WARN "$stage" "no $env/build-env.sh (only needed to rebuild the CUDA extensions; setup/env_trellis2.sh writes it)"
        fi
    fi
    if [ "$stage" = yue2 ] && [ -x "$env/bin/ffmpeg" ]; then
        line OK "$stage" "ffmpeg $("$env/bin/ffmpeg" -hide_banner -version 2>/dev/null | head -1 | awk '{print $3}') in the env"
    elif [ "$stage" = yue2 ] && [ -d "$env" ]; then
        line FAIL "$stage" "no ffmpeg in $env/bin; make_bgm.py needs it to encode OGG/MP3"
    fi

    # weights
    if [ -z "$hf_py" ]; then
        line FAIL "$stage" "weights not checked: no env with huggingface_hub yet"
    else
        while IFS= read -r l; do
            lvl="${l%% *}"; rest="${l#"$lvl"}"; rest="${rest#"${rest%%[! ]*}"}"; rest="${rest#"$stage"}"; rest="${rest#"${rest%%[! ]*}"}"
            case "$lvl" in
            OK | WARN | FAIL) line "$lvl" "$stage" "$rest" ;;
            *) line FAIL "$stage" "weight check error: $l" ;;
            esac
        done < <(HF_HUB_OFFLINE=1 "$hf_py" -I -B "$SETUP_DIR/models.py" check --only "$stage" 2>&1)
    fi
done

echo
if [ "$fails" -gt 0 ]; then
    echo "$fails check(s) failed. Each FAIL line says what to run."
    exit 1
fi
echo "All required checks passed."
