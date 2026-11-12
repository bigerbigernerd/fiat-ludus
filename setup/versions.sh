# shellcheck shell=bash disable=SC2034  # sourced; the variables are used by setup/*.sh
# Pinned versions for setup/*.sh. Sourced, not run.
#
# Everything here is what the working runtime on the reference machine used on
# 2026-11-12 (read from the live environments, repos and Hugging Face cache), not
# "latest". Python packages are pinned in setup/locks/*.txt; this file holds the
# rest: Blender, Python and toolkit versions, git commits, wheel files that come
# from outside an index, Demucs weights. Model repos and revisions are in
# setup/models.py.

# --- Blender (headless clean-up, previews, retargeting) ----------------------
FORGE_BLENDER_VERSION=5.2.4
# From https://download.blender.org/release/Blender5.2/blender-5.2.4.sha256;
# install_blender.sh checks the download against that file *and* this value.
FORGE_BLENDER_SHA256=2fd14c50987426897ba60237ee996edf2b4151f4b1a72a460ca20be58c1d2d86

# --- Python interpreters ------------------------------------------------------
KREA_PYTHON=3.12.13      # uv-managed CPython
UNIRIG_PYTHON=3.11.14    # uv-managed CPython; bpy 4.2 wheels exist only for 3.11
# trellis2 (3.10.22) and yue2 (3.12.16) get Python from their conda lock files.

# --- PyTorch wheel indexes (the +cuXXX local versions in the locks live there) -
KREA_TORCH_INDEX=https://download.pytorch.org/whl/cu126
TRELLIS2_TORCH_INDEX=https://download.pytorch.org/whl/cu124
UNIRIG_TORCH_INDEX=https://download.pytorch.org/whl/cu124
YUE2_TORCH_INDEX=https://download.pytorch.org/whl/cu128

# --- Upstream code ------------------------------------------------------------
TRELLIS2_GIT=https://github.com/microsoft/TRELLIS.2
TRELLIS2_COMMIT=75fbf0183001ed9876c8dbb35de6b68552ee08bd
UNIRIG_GIT=https://github.com/VAST-AI-Research/UniRig
UNIRIG_COMMIT=6793c6640ff01c8fb389f3993434124bb43d2933
YUE2_GIT=https://github.com/multimodal-art-projection/YuE
YUE2_COMMIT=1116bea76032bee5668e5f097294d082fa0b84f0

# CUDA extensions compiled into the trellis2 env (sources kept in envs/trellis2/src).
NVDIFFRAST_GIT=https://github.com/NVlabs/nvdiffrast.git
NVDIFFRAST_COMMIT=253ac4fcea7de5f396371124af597e6cc957bfae   # tag v0.4.0
CUMESH_GIT=https://github.com/JeffreyXiang/CuMesh.git
CUMESH_COMMIT=12289e1062f0603f2f0d0771b02e1395d247f26f
FLEXGEMM_GIT=https://github.com/JeffreyXiang/FlexGEMM.git
FLEXGEMM_COMMIT=6dd94a859c26ee8246888502eada3dd8ad85532e

# --- Wheels that are not on an index: URL|sha256 ------------------------------
# flash-attn has no Turing kernels but its prebuilt wheels avoid a 30-minute
# source build; the code paths we use import it without running its kernels.
# GitHub shows no digest for these releases. The hashes are of the files we
# installed: the 2.7.3 wheel was downloaded again on 2026-11-12 and its 76 files
# match the RECORD hashes of the installed copy; the other three are the files
# the working unirig env was installed from.
TRELLIS2_FLASH_ATTN_WHEEL="https://github.com/Dao-AILab/flash-attention/releases/download/v2.7.3/flash_attn-2.7.3+cu12torch2.6cxx11abiFALSE-cp310-cp310-linux_x86_64.whl|64ba4ed4a8c4c9b79d8a0959ebe3ffd6f7ea707ae222dbebe6a864237ac0e475"
UNIRIG_WHEELS=(
    "https://github.com/Dao-AILab/flash-attention/releases/download/v2.7.4.post1/flash_attn-2.7.4.post1+cu12torch2.6cxx11abiFALSE-cp311-cp311-linux_x86_64.whl|58853b28a5a926cae14402bfd8d4d93a45ebf8f9e79533f37ab09d0d77a99c05"
    "https://data.pyg.org/whl/torch-2.6.0%2Bcu124/torch_scatter-2.1.2%2Bpt26cu124-cp311-cp311-linux_x86_64.whl|7f58d6015810883c3e84e0174b614a3e43ad310deab7eee37e723a1284762833"
    "https://data.pyg.org/whl/torch-2.6.0%2Bcu124/torch_cluster-1.6.3%2Bpt26cu124-cp311-cp311-linux_x86_64.whl|39a773499aadee859ced042e353d3dd47c36538eeaf0c737e6bbd40306fd88af"
)

# --- Demucs weights for the music stage's vocal check ------------------------
# Meta's CDN certificate (dl.fbaipublicfiles.com) expired 2026-11-11, so the file
# comes from a Hugging Face mirror; the hash is the one Demucs itself expects.
HTDEMUCS_URL=https://huggingface.co/anchor-flux/demucs-htdemucs/resolve/55656a44063d91b8b73fa2ef46398ae477f4da2c/htdemucs.th
HTDEMUCS_FILE=955717e8-8726e21a.th
HTDEMUCS_SHA256=8726e21a993978c7ba086d3872e7608d7d5bfca646ca4aca459ffda844faa8b4
