#!/bin/sh
# Build the pinned tile compiler in the repository cache. No global installation.
set -eu
atlas_root=$(CDPATH='' cd "$(dirname "$0")/../.." && pwd)
cache="$atlas_root/.cache/release-tools"
source_dir="$cache/tippecanoe-2.79.0"
binary="$source_dir/tippecanoe"
if [ -x "$binary" ] && [ "$("$binary" --version 2>&1)" = 'tippecanoe v2.79.0' ]; then exit 0; fi
mkdir -p "$cache"
archive="$cache/tippecanoe.tar.gz"
if [ ! -f "$archive" ]; then
  curl -fL --retry 2 --user-agent 'CriticalMineralsAtlas/1.0 (+mailto:raj@satnamenergy.com)' \
    https://codeload.github.com/felt/tippecanoe/tar.gz/refs/tags/2.79.0 -o "$archive"
fi
python3 -c 'import hashlib,pathlib,sys; assert hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest()=="b0fd9df49b6efc988288ea48774822c6de19eb48428017f27ee0b3b01d44f05d", "Tippecanoe archive checksum mismatch"' "$archive"
tar -xzf "$archive" -C "$cache"
if [ "$(uname -s)" = Darwin ]; then
  SDKROOT=$(xcrun --show-sdk-path)
  export SDKROOT
  CPLUS_INCLUDE_PATH="$SDKROOT/usr/include/c++/v1"
  export CPLUS_INCLUDE_PATH
fi
make -j4 -C "$source_dir" tippecanoe
"$binary" --version
