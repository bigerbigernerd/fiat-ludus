#!/usr/bin/env bash
# Python environment and code for the music stage (YuE2), used by
# pipeline/make_bgm.py.
#
#   setup/env_yue2.sh
#
# Clones YuE2 into $FORGE_RUNTIME/repos/YuE2 at the tested commit and creates
# $FORGE_RUNTIME/envs/yue2: a conda prefix env (Python 3.12.16 and ffmpeg 9.0.2
# from conda-forge, exact list in setup/locks/yue2.conda.txt), the packages in
# setup/locks/yue2.txt, and yue2-infer 0.1.6 installed from the checkout.
# The Demucs and Whisper weights the vocal check uses are fetched by
# setup/download_models.sh --only yue2, into this env.
# If the environment already exists and matches the lock, nothing is changed.
set -euo pipefail
STEP_NAME=env_yue2
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

need_cmd uv "Install uv: https://docs.astral.sh/uv/getting-started/installation/"
need_cmd git "Install git."
CONDA="$(find_conda)"

ENV="$FORGE_RUNTIME/envs/yue2"
PY="$ENV/bin/python"
LOCK="$LOCKS/yue2.txt"
REPO="$FORGE_RUNTIME/repos/YuE2"
EXTRAS=("yue2-infer==0.1.6")

# 1. code at the tested commit. The instrumental helpers make_bgm.py imports live
#    in skills/yue2-music/instrumental/ of the same checkout.
clone_at "$YUE2_GIT" "$REPO" "$YUE2_COMMIT"

# 2. environment
if check_existing_env "$ENV" "$PY" "$LOCK" "${EXTRAS[@]}"; then exit 0; fi

# conda only for Python + ffmpeg: the machine has no system ffmpeg, and make_bgm.py
# encodes OGG/MP3 with it. conda-forge's ffmpeg pulls in OpenVINO libraries
# (several hundred MB); accepted to avoid a separate static build.
# The explicit list skips the solver. If it fails with "No such file or directory:
# .../pkgs/...conda", another conda process was using the shared package cache
# at the same moment (it happened here twice): wait for it and rerun.
mark_started "$ENV"
if [ ! -x "$PY" ]; then
    say "creating conda env $ENV from $(basename "$LOCKS/yue2.conda.txt")"
    "$CONDA" create -y -p "$ENV" --file "$LOCKS/yue2.conda.txt"
fi

# torch 2.10.0+cu128 is what yue2-infer 0.1.6 pins (pyproject.toml).
# On Turing (sm_75) this wheel reports FlashAttention as available although it
# needs sm80+; make_bgm.py works around that at run time (yue2-setup.md 3.4).
say "installing $(grep -vc '^#' "$LOCK") pinned packages (about 5 GB of wheels on a cold cache)"
uv pip install --python "$PY" --no-deps \
    --extra-index-url "$YUE2_TORCH_INDEX" --index-strategy unsafe-best-match \
    -r "$LOCK"

# yue2-infer from the checkout, not the 0.1.5 wheel the official skill pins:
# make_bgm.py calls the skill's CPU helpers on top of 0.1.6 with one model load.
say "installing yue2-infer from $REPO"
uv pip install --python "$PY" --no-deps "$REPO"

env_matches_lock "$PY" "$LOCK" "${EXTRAS[@]}" || die "installed packages differ from $LOCK (see above). Rerun; if it persists, delete $ENV and start again."
"$ENV/bin/ffmpeg" -hide_banner -version >/dev/null 2>&1 || die "$ENV/bin/ffmpeg does not run; the conda part of the env is broken. Delete $ENV and rerun."
mark_done "$ENV"
say "done in $(elapsed): $ENV $(du -sh "$ENV" | cut -f1), $REPO $(du -sh "$REPO" | cut -f1)"
say "next: setup/download_models.sh --only yue2 (also fetches the Demucs/Whisper files into this env), then setup/check.sh"
