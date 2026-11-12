#!/usr/bin/env bash
# Python environment for stage 1, concept images with Krea 2 Turbo
# (pipeline/gen_image.py) and for tools/review_models.py.
#
#   setup/env_krea.sh
#
# Creates $FORGE_RUNTIME/envs/krea: a uv virtualenv with CPython 3.12.13 and the
# packages in setup/locks/krea.txt (PyTorch 2.14.1+cu126, diffusers from a fixed
# git commit, transformers 5.22, gguf). No upstream repo: diffusers is the code.
# If the environment already exists and matches the lock, nothing is changed.
set -euo pipefail
STEP_NAME=env_krea
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

need_cmd uv "Install uv: https://docs.astral.sh/uv/getting-started/installation/"
need_cmd git "Install git (uv needs it to fetch diffusers at a fixed commit)."

ENV="$FORGE_RUNTIME/envs/krea"
PY="$ENV/bin/python"
LOCK="$LOCKS/krea.txt"

if check_existing_env "$ENV" "$PY" "$LOCK"; then exit 0; fi

mark_started "$ENV"
if [ ! -x "$PY" ]; then
    say "creating $ENV (Python $KREA_PYTHON)"
    uv venv --python "$KREA_PYTHON" "$ENV"
fi

# Krea2Pipeline / Krea2Transformer2DModel are not in a diffusers release yet, so
# diffusers is pinned to the commit we tested (0.43.0.dev0); `pip install
# diffusers` gives "cannot import name 'Krea2Pipeline'".
# --no-deps: the lock is a complete freeze, so nothing is re-resolved.
# unsafe-best-match: torch/torchvision +cu126 live only on the PyTorch index,
# everything else on PyPI; uv's default would look only at the first index.
say "installing $(grep -vc '^#' "$LOCK") pinned packages (about 3.5 GB of wheels on a cold cache)"
uv pip install --python "$PY" --no-deps \
    --extra-index-url "$KREA_TORCH_INDEX" --index-strategy unsafe-best-match \
    -r "$LOCK"

env_matches_lock "$PY" "$LOCK" || die "installed packages differ from $LOCK (see above). Rerun; if it persists, delete $ENV and start again."
mark_done "$ENV"
say "done in $(elapsed): $ENV, $(du -sh "$ENV" | cut -f1)"
say "next: setup/download_models.sh --only krea, then setup/check.sh"
