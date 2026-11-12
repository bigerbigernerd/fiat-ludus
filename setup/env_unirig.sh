#!/usr/bin/env bash
# Python environment and code for the rigging stage (UniRig), used by
# pipeline/rig.py.
#
#   setup/env_unirig.sh
#
# Clones UniRig into $FORGE_RUNTIME/repos/UniRig at the tested commit and creates
# $FORGE_RUNTIME/envs/unirig: a uv virtualenv with CPython 3.11.14, the packages
# in setup/locks/unirig.txt and three prebuilt wheels (flash-attn, torch-scatter,
# torch-cluster) checked by sha256. No compiler or CUDA toolkit needed.
# rig.py itself copies our config overrides into the checkout and applies
# pipeline/unirig/ptv3-sdpa.patch on first use; this script does not touch them.
# If the environment already exists and matches the lock, nothing is changed.
set -euo pipefail
STEP_NAME=env_unirig
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

need_cmd uv "Install uv: https://docs.astral.sh/uv/getting-started/installation/"
need_cmd git "Install git."
need_cmd curl "Install curl."

ENV="$FORGE_RUNTIME/envs/unirig"
PY="$ENV/bin/python"
LOCK="$LOCKS/unirig.txt"
REPO="$FORGE_RUNTIME/repos/UniRig"
WHEELS="$FORGE_RUNTIME/downloads/wheels"
EXTRAS=("flash-attn==2.7.4.post1" "torch-scatter==2.1.2+pt26cu124" "torch-cluster==1.6.3+pt26cu124")

# 1. code at the tested commit (85 MB; the examples/*.glb are Git LFS pointers,
#    which the pipeline does not need).
clone_at "$UNIRIG_GIT" "$REPO" "$UNIRIG_COMMIT"

# 2. environment
if check_existing_env "$ENV" "$PY" "$LOCK" "${EXTRAS[@]}"; then exit 0; fi

# uv venv, not conda: `conda create` died twice on this machine when another
# install wrote to the shared conda package cache at the same time
# ("No such file or directory: .../pkgs/python-3.11.18-...conda").
mark_started "$ENV"
if [ ! -x "$PY" ]; then
    say "creating $ENV (Python $UNIRIG_PYTHON)"
    uv venv --python "$UNIRIG_PYTHON" "$ENV"
fi

# PyG and flash-attn publish these as plain files, not on an index. They are
# fetched with a '+' in the file name (curl -O keeps '%2B', which uv rejects
# as "invalid version") and checked against the hashes in setup/versions.sh.
wheel_files=()
for entry in "${UNIRIG_WHEELS[@]}"; do
    url="${entry%%|*}"; sha="${entry##*|}"
    f="$WHEELS/$(url_basename "$url")"
    fetch_checked "$url" "$f" "$sha"
    wheel_files+=("$f")
done

# torch 2.6.0+cu124 rather than the README's 2.5.1: PyTorch >= 2.6 needs
# TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1 for the skin checkpoint, which rig.py sets.
# bpy==4.2.0 is the Blender-as-a-module UniRig imports; it is unrelated to the
# Blender 5.2 install and needs Python 3.11 exactly.
# numpy 1.26.4 is what UniRig's README installs last; transformers 4.51.3 is
# its requirements.txt pin.
say "installing $(grep -vc '^#' "$LOCK") pinned packages + 3 wheels (about 4 GB of wheels on a cold cache)"
uv pip install --python "$PY" --no-deps \
    --extra-index-url "$UNIRIG_TORCH_INDEX" --index-strategy unsafe-best-match \
    -r "$LOCK" "${wheel_files[@]}"

env_matches_lock "$PY" "$LOCK" "${EXTRAS[@]}" || die "installed packages differ from $LOCK (see above). Rerun; if it persists, delete $ENV and start again."
mark_done "$ENV"
say "done in $(elapsed): $ENV $(du -sh "$ENV" | cut -f1), $REPO $(du -sh "$REPO" | cut -f1)"
say "next: setup/download_models.sh --only unirig, then setup/check.sh"
