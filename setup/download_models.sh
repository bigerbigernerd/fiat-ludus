#!/usr/bin/env bash
# Download the model weights the pipeline loads, and nothing else.
#
#   setup/download_models.sh [--only krea|trellis2|unirig|yue2]... [--gguf auto|q4|q5|both] [--dry-run]
#
# --dry-run   list every file with its size and whether it is already in the
#             cache; downloads nothing (asks the Hub for sizes; add --offline to
#             skip even that).
# --only      one stage; repeat the flag for several. Default: all four.
# --gguf      which quantized Krea transformer to fetch. auto (default) picks by
#             the GPUs in this machine, as pipeline/gen_image.py does at run time:
#             Q5_K_M (8.9 GB) for cards with 12 GiB or more, Q4_K_M (7.5 GB) below.
#
# The file list lives in setup/models.py. Sizes per group are printed before
# anything is fetched. Files go into the normal Hugging Face cache (wherever
# HF_HUB_CACHE / HF_HOME point; the dry run prints it); an interrupted download
# resumes when you run the script again.
#
# Gated repos (the script stops for that repo and says what to do):
#   krea/Krea-2-Turbo                         accept the licence on the model page; approved at once
#   facebook/dinov3-vitl16-pretrain-lvd1689m  manual approval by Meta; or set
#       FORGE_DINOV3_MODEL=camenduru/dinov3-vitl16-pretrain-lvd1689m (byte-identical copy)
# Both need HF_TOKEN in the environment (or .env.local, or `hf auth login`).
set -euo pipefail
STEP_NAME=models
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

cmd=download
args=()
offline=0
while [ $# -gt 0 ]; do
    case "$1" in
    --dry-run) cmd=plan; shift ;;
    --offline) args+=(--offline); offline=1; shift ;;
    --only) [ $# -ge 2 ] || die "--only needs a stage name"; args+=(--only "$2"); shift 2 ;;
    --only=*) args+=(--only "${1#--only=}"); shift ;;
    --gguf) [ $# -ge 2 ] || die "--gguf needs auto|q4|q5|both"; args+=(--gguf "$2"); shift 2 ;;
    -h | --help) sed -n '2,25p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown argument '$1' (try --help)" ;;
    esac
done

# Any Python with huggingface_hub 0.36 will do. The trellis2/unirig/yue2 envs
# have it; on a machine with none of them yet, uv fetches it into a throwaway env.
hf_python=()
if [ -n "${FORGE_HF_PYTHON:-}" ]; then
    hf_python=("$FORGE_HF_PYTHON")
else
    for e in yue2 trellis2 unirig; do
        p="$FORGE_RUNTIME/envs/$e/bin/python"
        if [ -x "$p" ] && "$p" -I -c "import huggingface_hub" 2>/dev/null; then hf_python=("$p"); break; fi
    done
fi
if [ ${#hf_python[@]} -eq 0 ]; then
    need_cmd uv "Install uv, or set FORGE_HF_PYTHON to a Python that has huggingface_hub installed."
    hf_python=(uv run --quiet --no-project --with "huggingface_hub==0.36.2" python)
fi

export HTDEMUCS_URL HTDEMUCS_FILE HTDEMUCS_SHA256
# Once everything is downloaded people put HF_HUB_OFFLINE=1 in .env.local so runs
# never touch the network; this script has to, so it lifts that for itself only.
if [ "$offline" = 0 ] && [ "${HF_HUB_OFFLINE:-0}" != 0 ]; then
    say "HF_HUB_OFFLINE is set (e.g. in .env.local); ignoring it for this script"
    export HF_HUB_OFFLINE=0
fi
[ "$cmd" = plan ] && say "dry run: nothing will be downloaded"
"${hf_python[@]}" -I -B "$SETUP_DIR/models.py" "$cmd" "${args[@]}"
