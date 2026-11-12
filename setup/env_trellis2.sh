#!/usr/bin/env bash
# Python environment and code for the image-to-3D stage (TRELLIS.2), used by
# pipeline/image_to_3d.py.
#
#   setup/env_trellis2.sh
#
# 1. clones TRELLIS.2 (with its Eigen submodule) into $FORGE_RUNTIME/repos/TRELLIS.2
#    at the tested commit;
# 2. creates the conda prefix env $FORGE_RUNTIME/envs/trellis2 from
#    setup/locks/trellis2.conda.txt: Python 3.10.22 plus a CUDA 12.4 toolkit and
#    gcc 12 *inside the env* (no system CUDA or sudo needed);
# 3. installs setup/locks/trellis2.txt (PyTorch 2.6.0+cu124, xformers, ...) and the
#    flash-attn 2.7.3 wheel;
# 4. writes envs/trellis2/build-env.sh and compiles nvdiffrast, CuMesh, FlexGEMM
#    and o-voxel against that toolkit.
# Upstream's setup.sh is not used: it calls sudo apt, installs pillow-simd, and
# clones the extensions at whatever their HEAD is.
# Every step is skipped when already done; an existing env that matches the lock
# is not changed.
set -euo pipefail
STEP_NAME=env_trellis2
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

need_cmd uv "Install uv: https://docs.astral.sh/uv/getting-started/installation/"
need_cmd git "Install git."
need_cmd curl "Install curl."
CONDA="$(find_conda)"

ENV="$FORGE_RUNTIME/envs/trellis2"
PY="$ENV/bin/python"
LOCK="$LOCKS/trellis2.txt"
REPO="$FORGE_RUNTIME/repos/TRELLIS.2"
WHEELS="$FORGE_RUNTIME/downloads/wheels"
EXTRAS=("flash-attn==2.7.3" "nvdiffrast==0.4.0" "cumesh==0.0.1" "flex-gemm==1.0.0" "o-voxel==0.0.1")

# 1. code (o-voxel is built from it in step 4)
clone_at "$TRELLIS2_GIT" "$REPO" "$TRELLIS2_COMMIT" --recursive
[ -f "$REPO/o-voxel/third_party/eigen/Eigen/Core" ] ||
    die "$REPO/o-voxel/third_party/eigen is empty: the Eigen submodule did not arrive. Run: git -C '$REPO' submodule update --init --recursive (or set FORGE_EIGEN_MIRROR, see setup/lib.sh) and rerun."

if check_existing_env "$ENV" "$PY" "$LOCK" "${EXTRAS[@]}"; then exit 0; fi

# 2. conda env with the CUDA toolkit.
# The explicit list pins cuda-version 12.4 and gcc 12. A first, unpinned attempt
# here silently got CUDA 13.5 + gcc 15 from conda-forge after the NVIDIA channel's
# index failed to load; PyTorch 2.6 wheels are cu124, so the extensions must be too.
mark_started "$ENV"
if [ ! -x "$PY" ]; then
    say "creating conda env $ENV from $(basename "$LOCKS/trellis2.conda.txt") (about 2.5 GB, mostly the CUDA toolkit)"
    "$CONDA" create -y -p "$ENV" --file "$LOCKS/trellis2.conda.txt"
fi

# 3. Python packages.
# xformers: flash-attn has no kernels for Turing (RTX 20xx), so attention runs on
# xformers there. transformers 4.57 (>= 4.56) is needed for DINOv3ViTModel.
# flash-attn is the official prebuilt wheel (a source build takes ~30 min).
url="${TRELLIS2_FLASH_ATTN_WHEEL%%|*}"; sha="${TRELLIS2_FLASH_ATTN_WHEEL##*|}"
fa="$WHEELS/$(url_basename "$url")"
fetch_checked "$url" "$fa" "$sha"
say "installing $(grep -vc '^#' "$LOCK") pinned packages + flash-attn (about 4 GB of wheels on a cold cache)"
uv pip install --python "$PY" --no-deps \
    --extra-index-url "$TRELLIS2_TORCH_INDEX" --index-strategy unsafe-best-match \
    -r "$LOCK" "$fa"

# 4. CUDA extensions.
# conda-forge puts the CUDA headers in targets/x86_64-linux/include, not include/,
# so builds fail with "cuda_runtime.h: No such file" unless CPATH points there;
# build-env.sh sets that, the compilers and the GPU architectures.
BUILD_ENV="$ENV/build-env.sh"
if [ ! -f "$BUILD_ENV" ]; then
    archs="$(nvidia-smi --query-gpu=compute_cap --format=csv,noheader 2>/dev/null | sort -u | paste -sd';' || true)"
    archs="${archs:-7.5;8.6}"
    say "writing $BUILD_ENV (TORCH_CUDA_ARCH_LIST=$archs, from the GPUs visible now)"
    cat >"$BUILD_ENV" <<SH
# Source this before building CUDA extensions into the trellis2 env.
# CUDA 12.4 toolkit + gcc 12 come from conda-forge inside the env itself.
E="\$(cd "\$(dirname "\${BASH_SOURCE[0]}")" && pwd)"
export PATH="\$E/bin:\$PATH"
export CUDA_HOME="\$E"
# conda-forge puts CUDA headers/libs under targets/, not include/ and lib/
export CPATH="\$E/targets/x86_64-linux/include\${CPATH:+:\$CPATH}"
export LIBRARY_PATH="\$E/targets/x86_64-linux/lib:\$E/lib\${LIBRARY_PATH:+:\$LIBRARY_PATH}"
export CC="\$E/bin/x86_64-conda-linux-gnu-gcc"
export CXX="\$E/bin/x86_64-conda-linux-gnu-g++"
export CUDAHOSTCXX="\$CXX"
# GPU architectures to compile for (7.5 = RTX 20xx, 8.6 = RTX 30xx, 8.9 = RTX 40xx).
# Add yours and rebuild if you move the env to another card.
export TORCH_CUDA_ARCH_LIST="\${TORCH_CUDA_ARCH_LIST:-$archs}"
export MAX_JOBS="\${MAX_JOBS:-16}"
SH
else
    say "using existing $BUILD_ENV"
fi

clone_at "$NVDIFFRAST_GIT" "$ENV/src/nvdiffrast" "$NVDIFFRAST_COMMIT"
clone_at "$CUMESH_GIT"     "$ENV/src/CuMesh"     "$CUMESH_COMMIT" --recursive
clone_at "$FLEXGEMM_GIT"   "$ENV/src/FlexGEMM"   "$FLEXGEMM_COMMIT"

installed() { "$PY" -I -c "import importlib.metadata as m, sys; m.version(sys.argv[1])" "$1" >/dev/null 2>&1; }
build() {  # build <dist name> <source dir>
    if installed "$1"; then say "$1 already built and installed"; return 0; fi
    say "compiling $1 from $2"
    local t0; t0=$(date +%s)
    # Subshell so build-env.sh's PATH/CC changes do not leak; run from a scratch
    # directory so the source tree is not shadowed on sys.path.
    # --no-build-isolation: build against the torch already installed.
    # --no-deps: o-voxel's pyproject lists CuMesh and FlexGEMM as git+https
    # dependencies and would otherwise rebuild them from their unpinned HEAD.
    ( # shellcheck source=/dev/null
      . "$BUILD_ENV"
      tmpd="$(mktemp -d)"; trap 'rm -rf "$tmpd"' EXIT
      cd "$tmpd"
      "$PY" -m pip install --no-build-isolation --no-deps "$2" ) ||
        die "building $1 failed (see the compiler output above). Typical causes: a GPU whose architecture CUDA 12.4 does not know (RTX 50xx), or too little RAM for MAX_JOBS parallel nvcc jobs (try MAX_JOBS=4). Rerun after fixing; finished extensions are kept."
    say "$1 built in $(( $(date +%s) - t0 )) s"
}
build nvdiffrast "$ENV/src/nvdiffrast"
build cumesh     "$ENV/src/CuMesh"
build flex_gemm  "$ENV/src/FlexGEMM"
build o_voxel    "$REPO/o-voxel"

env_matches_lock "$PY" "$LOCK" "${EXTRAS[@]}" || die "installed packages differ from $LOCK (see above). Rerun; if it persists, delete $ENV and start again."
mark_done "$ENV"
say "done in $(elapsed): $ENV $(du -sh "$ENV" | cut -f1), $REPO $(du -sh "$REPO" | cut -f1)"
say "next: setup/download_models.sh --only trellis2, then setup/check.sh (its import test needs a visible GPU)"
