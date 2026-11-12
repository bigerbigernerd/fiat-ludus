#!/usr/bin/env bash
# Run one GPU job under a per-GPU lock and record what it cost.
#
#   tools/gpu_run.sh <stage> <label> [--gpu N] -- <command> [args...]
#
# - Takes an exclusive lock on the GPU, so jobs started by different people or
#   agents queue up instead of fighting over memory.
# - Samples GPU power, memory and utilisation once a second while the job runs.
# - Appends one JSON line to cost/ledger.jsonl: wall time, time spent waiting
#   for the lock, average and peak power, peak memory, energy in watt-hours.
#
# Power is what nvidia-smi reports for the card only. CPU, disk and the rest of
# the machine are not included; cost/README.md says how the totals are derived.

set -uo pipefail

usage() { sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }

[ $# -ge 4 ] || usage
STAGE="$1"; LABEL="$2"; shift 2

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
RUNTIME="${FORGE_RUNTIME:-$HOME/forge-runtime}"
GPU="${FORGE_GPU_MAIN:-0}"

if [ "${1:-}" = "--gpu" ]; then GPU="$2"; shift 2; fi
[ "${1:-}" = "--" ] || usage
shift
[ $# -ge 1 ] || usage

LEDGER="${FORGE_LEDGER:-$ROOT/cost/ledger.jsonl}"
mkdir -p "$(dirname "$LEDGER")" "$RUNTIME/locks"
LOCK="$RUNTIME/locks/gpu$GPU.lock"
SAMPLES="$(mktemp "${TMPDIR:-/tmp}/gpu_run.XXXXXX")"
SAMPLER_PID=""
cleanup() {
    [ -z "$SAMPLER_PID" ] || kill "$SAMPLER_PID" 2>/dev/null
    rm -f "$SAMPLES"
}
trap cleanup EXIT

QUEUED_AT="$(date +%s.%N)"
exec 9>"$LOCK"
flock 9
STARTED_AT="$(date +%s.%N)"

nvidia-smi -i "$GPU" --query-gpu=power.draw,memory.used,utilization.gpu \
    --format=csv,noheader,nounits -l 1 >"$SAMPLES" 2>/dev/null &
SAMPLER_PID=$!

# The lock is held by this wrapper's file descriptor 9; close it in the child so
# a background process left behind by the job cannot keep the GPU locked.
CUDA_DEVICE_ORDER=PCI_BUS_ID CUDA_VISIBLE_DEVICES="$GPU" "$@" 9>&-
STATUS=$?

ENDED_AT="$(date +%s.%N)"
kill "$SAMPLER_PID" 2>/dev/null; wait "$SAMPLER_PID" 2>/dev/null; SAMPLER_PID=""

GPU_NAME="$(nvidia-smi -i "$GPU" --query-gpu=name --format=csv,noheader 2>/dev/null | head -1)"

python3 - "$SAMPLES" "$LEDGER" "$STAGE" "$LABEL" "$GPU" "$GPU_NAME" \
    "$QUEUED_AT" "$STARTED_AT" "$ENDED_AT" "$STATUS" "$@" <<'PY'
import json, sys, datetime

samples, ledger, stage, label, gpu, gpu_name, queued, started, ended, status = sys.argv[1:11]
cmd = sys.argv[11:]
queued, started, ended = float(queued), float(started), float(ended)

power, mem, util = [], [], []
with open(samples) as f:
    for line in f:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) != 3:
            continue
        try:
            power.append(float(parts[0])); mem.append(float(parts[1])); util.append(float(parts[2]))
        except ValueError:
            continue

def portable(text):
    """Keep machine-specific paths out of the ledger: write the variable, not its value."""
    import os
    for name in ("FORGE_RUNTIME", "FORGE_ROOT", "HOME"):
        value = os.environ.get(name)
        if value and len(value) > 1:
            text = text.replace(value, "$" + name)
    return text


seconds = ended - started
avg_w = sum(power) / len(power) if power else None
row = {
    "time": datetime.datetime.fromtimestamp(started).astimezone().isoformat(timespec="seconds"),
    "stage": stage,
    "label": label,
    "gpu": int(gpu),
    "gpu_name": gpu_name,
    "seconds": round(seconds, 1),
    "wait_seconds": round(started - queued, 1),
    "avg_watts": round(avg_w, 1) if avg_w is not None else None,
    "peak_watts": round(max(power), 1) if power else None,
    "peak_mem_mib": int(max(mem)) if mem else None,
    "avg_util_pct": round(sum(util) / len(util), 1) if util else None,
    "watt_hours": round(avg_w * seconds / 3600, 3) if avg_w is not None else None,
    "exit": int(status),
    "cmd": portable(" ".join(cmd))[:400],
}
with open(ledger, "a") as f:
    f.write(json.dumps(row, ensure_ascii=False) + "\n")

wh = row["watt_hours"]
print(f"[gpu_run] {stage}/{label}: {row['seconds']}s on GPU {gpu}, "
      f"peak {row['peak_mem_mib']} MiB, {wh if wh is not None else '?'} Wh, exit {status}",
      file=sys.stderr)
PY

exit "$STATUS"
