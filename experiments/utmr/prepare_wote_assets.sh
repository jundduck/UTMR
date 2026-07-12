#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UTMR_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
WOTE_ROOT="${WOTE_ROOT:-$UTMR_ROOT/third_party/WoTE}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
PY_SITE="$UTMR_ROOT/runtime/python-packages"

RESNET_URL="https://download.pytorch.org/models/resnet34-b627a593.pth"
WOTE_CKPT_ID="1Gu7W6vp1eAE0f_1DO1X6DwGu8iyt64ht"
PDM_SCORE_256_ID="1STElIeiY7rQ4QboWuyro5IirVUZhHSTm"
ANCHORS_256_ID="1KLTeXGmp4N55k5SutB0a1ShZdIPvFmr9"

download_url() {
  local url="$1"
  local dest="$2"
  if [[ -s "$dest" ]]; then
    printf 'ok      %s\n' "$dest"
    return
  fi
  mkdir -p "$(dirname "$dest")"
  if command -v wget >/dev/null 2>&1; then
    wget -O "$dest.tmp" "$url"
  else
    curl -L -o "$dest.tmp" "$url"
  fi
  mv "$dest.tmp" "$dest"
  printf 'saved   %s\n' "$dest"
}

ensure_gdown() {
  mkdir -p "$PY_SITE"
  PYTHONPATH="$PY_SITE${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1 && return
import gdown
PY
  "$PYTHON_BIN" -m pip install --target "$PY_SITE" gdown
}

download_drive() {
  local file_id="$1"
  local dest="$2"
  if [[ -s "$dest" ]]; then
    printf 'ok      %s\n' "$dest"
    return
  fi
  mkdir -p "$(dirname "$dest")"
  PYTHONPATH="$PY_SITE${PYTHONPATH:+:$PYTHONPATH}" \
    "$PYTHON_BIN" -m gdown "https://drive.google.com/uc?id=$file_id" -O "$dest.tmp"
  mv "$dest.tmp" "$dest"
  printf 'saved   %s\n' "$dest"
}

if [[ ! -d "$WOTE_ROOT/.git" ]]; then
  mkdir -p "$(dirname "$WOTE_ROOT")"
  git clone https://github.com/liyingyanUCAS/WoTE.git "$WOTE_ROOT"
else
  git -C "$WOTE_ROOT" fetch origin --prune
  git -C "$WOTE_ROOT" pull --ff-only
fi

download_url "$RESNET_URL" "$WOTE_ROOT/ckpts/resnet34.pth"
ensure_gdown
download_drive "$WOTE_CKPT_ID" "$WOTE_ROOT/exp/WoTE/default/lightning_logs/version_0/checkpoints/epoch=29-step=19950.ckpt"
download_drive "$PDM_SCORE_256_ID" "$WOTE_ROOT/dataset/extra_data/planning_vb/formatted_pdm_score_256.npy"
download_drive "$ANCHORS_256_ID" "$WOTE_ROOT/dataset/extra_data/planning_vb/trajectory_anchors_256.npy"

"$PYTHON_BIN" "$SCRIPT_DIR/make_wote_64_cache.py" --derive-target-anchors

printf '\nWoTE released assets are ready. Run experiments/utmr/prepare_navsim_data.sh for NAVSIM maps/logs/sensor blobs and metric cache.\n'
