# shellcheck shell=bash disable=SC2034  # sourced; LOCKS etc. are used by setup/*.sh
# Shared helpers for setup/*.sh. Sourced, not run.
#
# Sourcing this file sources env.sh (FORGE_ROOT, FORGE_RUNTIME, ...) and
# setup/versions.sh (every pin), and defines small helpers for logging, cloning
# at a fixed commit, downloading with a checksum and checking an environment
# against its lock file.

SETUP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../env.sh
. "$SETUP_DIR/../env.sh"
# shellcheck source=versions.sh
. "$SETUP_DIR/versions.sh"
LOCKS="$SETUP_DIR/locks"

# Plain HTTPS for Hugging Face: the default Xet transfer stalled for an hour on a
# shared ~10 MB/s link while plain HTTPS finished (docs/logs/yue2-setup.md 3.2).
export HF_HUB_DISABLE_XET="${HF_HUB_DISABLE_XET:-1}"

say()  { printf '[%s] %s\n' "${STEP_NAME:-setup}" "$*"; }
warn() { printf '[%s] WARNING: %s\n' "${STEP_NAME:-setup}" "$*" >&2; }
die()  { printf '[%s] ERROR: %s\n' "${STEP_NAME:-setup}" "$*" >&2; exit 1; }

need_cmd() {  # need_cmd <command> <what to do if missing>
    command -v "$1" >/dev/null 2>&1 || die "'$1' not found. $2"
}

# Timer for the summary line each script prints at the end.
SETUP_T0=$(date +%s)
elapsed() { local s=$(( $(date +%s) - SETUP_T0 )); printf '%dm%02ds' $((s / 60)) $((s % 60)); }

# conda front end: mamba if present (faster), else conda. Override with FORGE_CONDA.
find_conda() {
    if [ -n "${FORGE_CONDA:-}" ]; then printf '%s' "$FORGE_CONDA"; return; fi
    local c
    for c in mamba conda "$HOME/miniforge3/bin/mamba" "$HOME/miniforge3/bin/conda"; do
        if command -v "$c" >/dev/null 2>&1; then command -v "$c"; return; fi
    done
    die "neither mamba nor conda found. Install Miniforge (https://github.com/conda-forge/miniforge), or set FORGE_CONDA=/path/to/conda."
}

# clone_at <url> <dir> <commit> [--recursive]
# Clones <url> into <dir> and checks out <commit>. An existing checkout is left
# alone when it is already at <commit>; at any other commit the script stops
# instead of moving it (someone may be working in it).
# FORGE_GIT_REFERENCE=<dir with existing checkouts of the same name> borrows
# objects from them (git clone --reference --dissociate) to save the download.
clone_at() {
    local url="$1" dir="$2" commit="$3" recursive="${4:-}" head ref_args=()
    if [ -d "$dir/.git" ]; then
        head="$(git -C "$dir" rev-parse HEAD)"
        if [ "$head" = "$commit" ]; then
            say "$(basename "$dir") already at ${commit:0:12}, leaving it as it is"
            return 0
        fi
        die "$dir is at ${head:0:12}, expected ${commit:0:12}. Check it out yourself (git -C '$dir' checkout $commit) or move it away and rerun."
    fi
    [ -e "$dir" ] && die "$dir exists but is not a git checkout. Move it away and rerun."
    local ref
    ref="${FORGE_GIT_REFERENCE:-}/$(basename "$dir")"
    if [ -n "${FORGE_GIT_REFERENCE:-}" ] && [ -d "$ref/.git" ]; then
        ref_args=(--reference-if-able "$ref" --dissociate)
        say "borrowing git objects from $ref"
    fi
    say "cloning $url -> $dir"
    mkdir -p "$(dirname "$dir")"
    git clone "${ref_args[@]}" "$url" "$dir"
    git -C "$dir" checkout -q "$commit" || die "commit $commit not found in $url"
    if [ "$recursive" = "--recursive" ]; then
        say "fetching submodules of $(basename "$dir") (Eigen comes from gitlab.com and can be slow)"
        git "${EIGEN_GIT_OPTS[@]}" -C "$dir" submodule update --init --recursive ||
            die "submodule fetch failed (gitlab.com sometimes answers 'unable to handle this request due to load'). Rerun this script; finished parts are kept. Or point FORGE_EIGEN_MIRROR at an Eigen clone you already have."
    fi
}

# TRELLIS.2 (o-voxel) and CuMesh (cubvh) both pull Eigen from gitlab.com, which
# took 20 minutes once and failed once with "GitLab is currently unable to handle
# this request due to load". FORGE_EIGEN_MIRROR=<URL or path of an Eigen clone
# that has both pinned commits, e.g. repos/TRELLIS.2/.git/modules/o-voxel/third_party/eigen>
# makes git fetch Eigen from there instead. git applies the longest matching
# insteadOf, so both spellings of the gitlab URL are redirected.
EIGEN_GIT_OPTS=()
if [ -n "${FORGE_EIGEN_MIRROR:-}" ]; then
    EIGEN_GIT_OPTS=(-c protocol.file.allow=always
        -c "url.${FORGE_EIGEN_MIRROR}.insteadOf=https://gitlab.com/libeigen/eigen.git"
        -c "url.${FORGE_EIGEN_MIRROR}.insteadOf=https://gitlab.com/libeigen/eigen")
fi

# fetch_checked <url> <dest file> <sha256 or empty>
# Resumable download (curl -C -), then sha256 check. A wrong hash deletes the file.
fetch_checked() {
    local url="$1" dest="$2" sha="$3" got
    mkdir -p "$(dirname "$dest")"
    if [ -f "$dest" ] && [ -n "$sha" ] && [ "$(sha256sum "$dest" | cut -d' ' -f1)" = "$sha" ]; then
        say "have $(basename "$dest") (sha256 ok)"
        return 0
    fi
    say "downloading $(basename "$dest")"
    curl -fL --progress-bar --retry 5 --retry-delay 5 -C - -o "$dest.part" "$url" ||
        die "download failed: $url. Rerun to resume."
    got="$(sha256sum "$dest.part" | cut -d' ' -f1)"
    if [ -n "$sha" ] && [ "$got" != "$sha" ]; then
        rm -f "$dest.part"
        die "sha256 mismatch for $(basename "$dest"): got $got, expected $sha. The file was deleted; rerun to download it again."
    fi
    [ -n "$sha" ] || warn "no pinned sha256 for $(basename "$dest"); got $got"
    mv "$dest.part" "$dest"
}

# url_basename <url>: file name of a URL with %2B decoded to '+'.
# uv rejects wheel file names that still contain '%2B' (unirig-setup.md 1.2).
url_basename() { local b="${1##*/}"; printf '%s' "${b//%2B/+}"; }

# env_matches_lock <python> <lock file> [extra name==version ...]
# Exit 0 when every package in the lock (and every extra) is installed at exactly
# that version. Prints the differences otherwise. Reads metadata only.
env_matches_lock() {
    local py="$1" lock="$2"; shift 2
    "$py" -I - "$lock" "$@" <<'PY'
import importlib.metadata as md, re, sys
norm = lambda n: re.sub(r"[-_.]+", "-", n).lower()
have = {norm(d.metadata["Name"]): d.version for d in md.distributions()}
want = {}
for line in open(sys.argv[1]).read().splitlines() + sys.argv[2:]:
    line = line.split("#", 1)[0].strip()
    if not line:
        continue
    if " @ " in line:          # name @ url: the name must be installed, version not pinned here
        want[norm(line.split(" @ ")[0])] = None
    else:
        name, ver = line.split("==")
        want[norm(name)] = ver
bad = []
for name, ver in sorted(want.items()):
    if name not in have:
        bad.append(f"missing {name}" + (f"=={ver}" if ver else ""))
    elif ver and have[name] != ver:
        bad.append(f"{name} is {have[name]}, lock says {ver}")
for b in bad[:10]:
    print("  " + b)
if len(bad) > 10:
    print(f"  ... and {len(bad) - 10} more")
sys.exit(1 if bad else 0)
PY
}

# Marker files next to the env (envs/.<name>.setup-started / .setup-done): a script
# writes "started" before it creates or changes an env and "done" at the end, so a
# rerun can tell "ours, unfinished" (resume) from "built some other way" (leave
# alone). They live outside the env because conda refuses to create an env in a
# directory that already has files in it.
marker()       { printf '%s/.%s.setup-%s' "$(dirname "$1")" "$(basename "$1")" "$2"; }
mark_started() { mkdir -p "$(dirname "$1")"; touch "$(marker "$1" started)"; }
mark_done()    { touch "$(marker "$1" "done")"; }

# check_existing_env <env dir> <python> <lock> [extras...]
# Returns 0 when the caller should stop (env complete), 1 when it should build or
# resume. Dies when the env exists, was not made by these scripts and differs.
check_existing_env() {
    local env="$1" py="$2" lock="$3"; shift 3
    [ -e "$env" ] || return 1
    if [ -x "$py" ] && env_matches_lock "$py" "$lock" "$@" >/dev/null 2>&1; then
        say "environment $env exists and matches $(basename "$lock"); nothing to do"
        return 0
    fi
    if [ -f "$(marker "$env" started)" ]; then
        say "environment $env is unfinished from an earlier run; resuming"
        if [ ! -x "$py" ]; then
            # The interpreter never arrived (env creation was interrupted): start the env over.
            say "no interpreter in it yet; removing the partial $env first"
            rm -rf "${env:?}"
        fi
        return 1
    fi
    if [ -x "$py" ]; then
        say "environment $env exists but differs from $(basename "$lock"):"
        env_matches_lock "$py" "$lock" "$@" || true
    fi
    die "$env was not created by this script and does not match the tested versions. Leaving it untouched. Move it away (or set FORGE_RUNTIME to a fresh directory) and rerun."
}
