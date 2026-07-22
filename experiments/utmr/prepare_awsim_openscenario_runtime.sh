#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APT_LIB_DIR="$ROOT/runtime/apt-root/usr/lib/x86_64-linux-gnu"
TARGET_BARE_LIB="$APT_LIB_DIR/libzmq"
TARGET_LIB="$APT_LIB_DIR/libzmq.so"
TARGET_VERSIONED_LIB="$APT_LIB_DIR/libzmq.so.5.2.4"

mkdir -p "$APT_LIB_DIR"

if [[ ! -f "$TARGET_LIB" ]]; then
  source_lib=""
  for candidate in \
    /usr/lib/x86_64-linux-gnu/libzmq.so.5.2.4 \
    /usr/lib/x86_64-linux-gnu/libzmq.so.5 \
    /lib/x86_64-linux-gnu/libzmq.so.5.2.4 \
    /lib/x86_64-linux-gnu/libzmq.so.5; do
    if [[ -f "$candidate" ]]; then
      source_lib="$candidate"
      break
    fi
  done

  if [[ -z "$source_lib" ]]; then
    echo "missing libzmq runtime. Install/copy libzmq, then rerun this script."
    exit 6
  fi

  cp -f "$source_lib" "$TARGET_BARE_LIB"
  cp -f "$source_lib" "$TARGET_LIB"
  cp -f "$source_lib" "$TARGET_VERSIONED_LIB"
elif [[ ! -f "$TARGET_BARE_LIB" ]]; then
  cp -f "$TARGET_LIB" "$TARGET_BARE_LIB"
elif [[ ! -f "$TARGET_VERSIONED_LIB" ]]; then
  cp -f "$TARGET_LIB" "$TARGET_VERSIONED_LIB"
fi

for target in "$TARGET_BARE_LIB" "$TARGET_LIB" "$TARGET_VERSIONED_LIB"; do
  if [[ -L "$target" ]]; then
    echo "unexpected symlink: $target"
    exit 7
  fi
done

LD_LIBRARY_PATH="$APT_LIB_DIR:${LD_LIBRARY_PATH:-}" python3 - <<'PY'
import ctypes

ctypes.CDLL("libzmq")
print("ok libzmq loads via ctypes.CDLL('libzmq')")
PY

echo "ok AWSIM OpenSCENARIO runtime libs: $APT_LIB_DIR"
