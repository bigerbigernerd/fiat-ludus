#!/usr/bin/env bash
# Install the official Blender LTS Linux build used by the pipeline.
#
#   setup/install_blender.sh
#
# Downloads blender-<version>-linux-x64.tar.xz from download.blender.org into
# $FORGE_RUNTIME/downloads (resumable), checks it against the official .sha256
# file and the hash pinned in setup/versions.sh, unpacks it to
# $FORGE_RUNTIME/blender/blender-<version>-linux-x64 and points
# $FORGE_RUNTIME/blender/current at it. env.sh then sets FORGE_BLENDER.
#
# Why a tarball and not the distro package: no sudo needed, and the pipeline's
# scripts are written against the Blender 5.2 Python API (layered actions, the
# BLENDER_EEVEE enum), which distro packages lag behind.
set -euo pipefail
STEP_NAME=blender
# shellcheck source=lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

need_cmd curl "Install curl with your package manager."
need_cmd tar "Install tar."
need_cmd xz "Install xz-utils (tar needs it to unpack .tar.xz)."
need_cmd sha256sum "Install coreutils."

ver="$FORGE_BLENDER_VERSION"
series="${ver%.*}"                                    # 5.2.4 -> 5.2
name="blender-$ver-linux-x64"
base="https://download.blender.org/release/Blender$series"
dest="$FORGE_RUNTIME/blender"
tarball="$FORGE_RUNTIME/downloads/$name.tar.xz"

# Already installed? Then only make sure `current` points at it.
if [ -x "$dest/$name/blender" ] && "$dest/$name/blender" --version 2>/dev/null | grep -q "^Blender $ver"; then
    if [ "$(readlink "$dest/current" 2>/dev/null)" = "$name" ]; then
        say "Blender $ver is installed at $dest/$name and current points at it; nothing to do"
        exit 0
    fi
    say "Blender $ver is installed; pointing $dest/current at it"
    ln -sfn "$name" "$dest/current"
    exit 0
fi

say "installing Blender $ver into $dest"
mkdir -p "$FORGE_RUNTIME/downloads" "$dest"

# Official checksum file (it lists every platform; we need one line).
say "fetching the official checksum file $base/blender-$ver.sha256"
official="$(curl -fsSL --retry 3 "$base/blender-$ver.sha256" | awk -v f="$name.tar.xz" '$2 == f {print $1}')" ||
    die "could not fetch $base/blender-$ver.sha256. Check the network and rerun."
[ -n "$official" ] || die "$name.tar.xz is not listed in blender-$ver.sha256; is FORGE_BLENDER_VERSION=$ver a real release?"
if [ -n "$FORGE_BLENDER_SHA256" ] && [ "$official" != "$FORGE_BLENDER_SHA256" ]; then
    die "the official checksum ($official) differs from the one pinned in setup/versions.sh ($FORGE_BLENDER_SHA256). Do not continue until you know why."
fi

# 385 MB; curl -C - resumes a partial download.
fetch_checked "$base/$name.tar.xz" "$tarball" "$official"

say "unpacking to $dest/$name"
tmp="$(mktemp -d "$dest/.unpack.XXXXXX")"
trap 'rm -rf "$tmp"' EXIT
tar -xJf "$tarball" -C "$tmp"
[ -x "$tmp/$name/blender" ] || die "the tarball did not contain $name/blender"
rm -rf "${dest:?}/$name"
mv "$tmp/$name" "$dest/$name"
ln -sfn "$name" "$dest/current"

"$dest/current/blender" -b --factory-startup --version 2>/dev/null | head -1 | grep -q "^Blender $ver" ||
    die "$dest/current/blender does not run. Run it by hand to see the error (missing system libraries such as libX11, libXi, libXxf86vm, libXfixes, libXrender, libGL, libxkbcommon are the usual cause)."
say "done in $(elapsed): $("$dest/current/blender" --version 2>/dev/null | head -1), $(du -sh "$dest/$name" | cut -f1)"
say "the tarball stays in $tarball; delete it if you need the 385 MB"
