#!/usr/bin/env bash
# Can a cheap model drive the whole pipeline?
#
#   tests/agent/run.sh [model] [workspace]
#
# Copies the repository (without generated working files) into a workspace, gives a
# coding agent the task in tests/agent/task.md through the opencode CLI, lets it run
# with every permission auto-approved, and collects what it did and what it cost:
#   <workspace>/agent-run-N.jsonl   raw opencode events of each turn
#   <workspace>/agent-summary.json  tokens, API cost, wall time, GPU cost, final check
#
# The agent runs shell commands on this machine as you. Use a throw-away workspace,
# and do not put secrets it does not need into its environment (HF_TOKEN is removed
# here; the weights must already be downloaded).
set -euo pipefail
model="${1:-opencode-go/deepseek-v4.1-flash}"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
work="${2:-$HOME/forge-agent-test}"
turns="${FORGE_AGENT_TURNS:-6}"

session_of() {   # first session id in an event file (no pipe into head: that trips pipefail)
    python3 -c 'import re, sys; m = re.search(r"\"sessionID\":\"([^\"]+)\"", open(sys.argv[1]).read()); print(m.group(1) if m else "")' "$1"
}

first=1
session=""
if [ -e "$work" ]; then
    # FORGE_AGENT_RESUME=1 continues an interrupted test in the same workspace and session.
    if [ "${FORGE_AGENT_RESUME:-0}" != 1 ] || [ ! -f "$work/agent-run-1.jsonl" ]; then
        echo "workspace $work already exists; remove it, pass another path, or set FORGE_AGENT_RESUME=1" >&2
        exit 2
    fi
    cd "$work"
    first=$(($(ls agent-run-*.jsonl | wc -l) + 1))
    session="$(session_of agent-run-1.jsonl)"
    started="$(stat -c %Y agent-run-1.err)"
else
    mkdir -p "$work"
    rsync -a --exclude .git --exclude-from="$root/.gitignore" "$root/" "$work/"
    [ -f "$root/.env.local" ] && cp "$root/.env.local" "$work/.env.local"
    [ -d "$root/game/node_modules" ] && cp -a "$root/game/node_modules" "$work/game/node_modules"
    mkdir -p "$work/cost" "$work/logs"
    : > "$work/cost/ledger.jsonl"          # the test keeps its own books
    cd "$work"
    started="$(date +%s)"
fi

for ((n = first; n < first + turns; n++)); do
    if [ -z "$session" ]; then
        prompt="$(cat tests/agent/task.md)"
        args=()
    else
        # A model that ends its turn to "wait for a job" is simply asked to go on.
        prompt="Continue the task in tests/agent/task.md from where you stopped. Do not end your turn while a job is running: poll it with ./forge wait until it has finished, then carry on. You are done only when REPORT.md exists and ./forge check exits with status 0."
        args=(-s "$session")
    fi
    # stdin must be closed, or opencode waits for it.
    env -u HF_TOKEN HF_HUB_OFFLINE=1 opencode run --auto --format json -m "$model" "${args[@]}" "$prompt" \
        < /dev/null > "agent-run-$n.jsonl" 2> "agent-run-$n.err" || true
    [ -n "$session" ] || session="$(session_of "agent-run-$n.jsonl")"
    [ -f REPORT.md ] && break
    [ -n "$session" ] || break
done
ended="$(date +%s)"

set +e
./forge check > logs/final-check.log 2>&1
check=$?
set -e
python3 - "$model" "$started" "$ended" "$check" <<'PY'
import glob, json, subprocess, sys
model, started, ended, check = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
tokens = {"input": 0, "output": 0, "reasoning": 0, "cache_read": 0, "cache_write": 0}
cost, steps, tools, turns = 0.0, 0, {}, 0
for path in sorted(glob.glob("agent-run-*.jsonl")):
    turns += 1
    for line in open(path):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        part = event.get("part") or {}
        if event.get("type") == "step_finish":
            steps += 1
            t = part.get("tokens") or {}
            tokens["input"] += t.get("input", 0)
            tokens["output"] += t.get("output", 0)
            tokens["reasoning"] += t.get("reasoning", 0)
            tokens["cache_read"] += (t.get("cache") or {}).get("read", 0)
            tokens["cache_write"] += (t.get("cache") or {}).get("write", 0)
            cost += part.get("cost") or 0.0
        elif event.get("type") == "tool_use":
            name = part.get("tool", "?")
            tools[name] = tools.get(name, 0) + 1
gpu = subprocess.run(["./forge", "cost"], capture_output=True, text=True).stdout.strip().splitlines()
summary = {"model": model, "turns": turns, "steps": steps, "tool_calls": tools, "tokens": tokens,
           "api_cost_usd": round(cost, 4), "wall_seconds": ended - started,
           "gpu": json.loads(gpu[-1]) if gpu else None, "check_exit": check,
           "report_written": __import__("os").path.exists("REPORT.md")}
json.dump(summary, open("agent-summary.json", "w"), indent=1)
print(json.dumps(summary))
PY
