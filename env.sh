#!/usr/bin/env bash
# Source this file before running any pipeline step:  . ./env.sh
#
# Everything heavy (model weights, Python environments, third-party repos, Blender)
# lives under FORGE_RUNTIME, outside the project tree, so the repo stays small.
# Put it on an SSD: model load time is dominated by disk read speed.

FORGE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export FORGE_ROOT
export FORGE_RUNTIME="${FORGE_RUNTIME:-$HOME/forge-runtime}"

# Hugging Face downloads go here instead of ~/.cache.
export HF_HOME="${HF_HOME:-$FORGE_RUNTIME/hf-cache}"

# Make CUDA device numbers match the numbers nvidia-smi prints.
export CUDA_DEVICE_ORDER=PCI_BUS_ID

# GPU used for the heavy stages (image, 3D, music) and for the light one (rigging).
export FORGE_GPU_MAIN="${FORGE_GPU_MAIN:-0}"
export FORGE_GPU_AUX="${FORGE_GPU_AUX:-$FORGE_GPU_MAIN}"

# Blender binary; set by setup/install_blender.sh.
if [ -z "${FORGE_BLENDER:-}" ] && [ -x "$FORGE_RUNTIME/blender/current/blender" ]; then
    export FORGE_BLENDER="$FORGE_RUNTIME/blender/current/blender"
fi

# Machine-local settings and secrets (never committed). Expected keys:
#   HF_TOKEN          - Hugging Face token; needed for gated weights
#   OPENCODE_API_KEY  - only for the cheap-model end-to-end test
if [ -r "$FORGE_ROOT/.env.local" ]; then
    set -a; . "$FORGE_ROOT/.env.local"; set +a
fi
