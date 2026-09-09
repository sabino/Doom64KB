#!/usr/bin/env bash
set -euo pipefail
# Inputs are read-only. All copies, generated sources and objects stay in DEST.
source_root=${1:?usage: build-emulator.sh NGDEVKIT_SOURCE DEST NGDEVKIT_PREFIX}
dest=${2:?new private build directory required}
prefix=${3:?installed ngdevkit prefix required for headers and BIOS/data assets}
script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
test ! -e "$dest"
mkdir -p "$dest"
dest=$(cd "$dest" && pwd)
cp -a "$source_root/gngeo" "$source_root/emudbg" "$dest/"
patch -d "$dest" -p1 < "$script_dir/emulator-perf.patch"
mkdir "$dest/build-emudbg" "$dest/build-gngeo"
cd "$dest/build-emudbg"
"$dest/emudbg/configure" --prefix="$dest/install"
make -j"${JOBS:-4}"
cd "$dest/build-gngeo"
PKG_CONFIG_PATH="$dest/build-emudbg:$prefix/lib/pkgconfig" \
    "$dest/gngeo/configure" --prefix="$prefix" \
    "CPPFLAGS=-Wno-implicit-function-declaration -I$dest/emudbg/src -I$prefix/include" \
    'CFLAGS=-O2 -g -Wno-implicit-function-declaration' \
    "LDFLAGS=-L$dest/build-emudbg/src -L$prefix/lib"
make -j"${JOBS:-4}"
printf '\nPrivate emulator: %s/build-gngeo/src/gngeo\n' "$dest"
