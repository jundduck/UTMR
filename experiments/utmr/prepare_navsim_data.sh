#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UTMR_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
WOTE_ROOT="${WOTE_ROOT:-$UTMR_ROOT/third_party/WoTE}"
DATASET_DIR="$WOTE_ROOT/dataset"
DOWNLOAD_DIR="${NAVSIM_DOWNLOAD_DIR:-$DATASET_DIR/.downloads}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
PREPARE_MAPS=1
PREPARE_TEST_METADATA=1
PREPARE_TEST_SENSORS=0
RUN_METRIC_CACHE=0
SENSOR_START=0
SENSOR_END=31
METRIC_SCENE_FILTER=navtest
METRIC_MAX_SCENES=""
METRIC_WORKER=sequential
METRIC_EXTRA_ARGS=()
KEEP_ARCHIVES=0

usage() {
  cat <<USAGE
Usage: $0 [options]

Options:
  --maps                  Download/extract nuPlan maps.
  --no-maps               Skip maps.
  --test-metadata         Download/extract OpenScene test metadata.
  --no-test-metadata      Skip OpenScene test metadata.
  --include-test-sensors  Download/extract OpenScene test camera/lidar shards.
  --sensor-start N        First test sensor shard, default 0.
  --sensor-end N          Last test sensor shard, default 31.
  --metric-cache          Run WoTE metric-cache generation after data prep.
  --metric-cache-filter X Run metric cache with this scene filter, default navtest.
  --metric-cache-worker X Run metric cache with this worker, default sequential.
  --metric-cache-max-scenes N
                          Limit metric-cache generation for smoke testing.
  --metric-cache-override X
                          Pass an extra Hydra override to metric caching.
                          May be repeated, e.g. worker.max_workers=8.
  --keep-archives         Keep downloaded archives after successful extraction.
  --all                   Enable maps, test metadata, test sensors, and metric cache.
  -h, --help              Show this help.
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --maps)
      PREPARE_MAPS=1
      shift
      ;;
    --no-maps)
      PREPARE_MAPS=0
      shift
      ;;
    --test-metadata)
      PREPARE_TEST_METADATA=1
      shift
      ;;
    --no-test-metadata)
      PREPARE_TEST_METADATA=0
      shift
      ;;
    --include-test-sensors)
      PREPARE_TEST_SENSORS=1
      shift
      ;;
    --sensor-start)
      SENSOR_START="$2"
      shift 2
      ;;
    --sensor-end)
      SENSOR_END="$2"
      shift 2
      ;;
    --metric-cache)
      RUN_METRIC_CACHE=1
      shift
      ;;
    --metric-cache-filter)
      METRIC_SCENE_FILTER="$2"
      shift 2
      ;;
    --metric-cache-worker)
      METRIC_WORKER="$2"
      shift 2
      ;;
    --metric-cache-max-scenes)
      METRIC_MAX_SCENES="$2"
      shift 2
      ;;
    --metric-cache-override)
      METRIC_EXTRA_ARGS+=("$2")
      shift 2
      ;;
    --keep-archives)
      KEEP_ARCHIVES=1
      shift
      ;;
    --all)
      PREPARE_MAPS=1
      PREPARE_TEST_METADATA=1
      PREPARE_TEST_SENSORS=1
      RUN_METRIC_CACHE=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

download() {
  local url="$1"
  local dest="$2"
  mkdir -p "$(dirname "$dest")"
  if [[ -s "$dest" ]]; then
    printf 'ok      %s\n' "$dest"
    return
  fi
  local attempt
  local hf_prefix="https://huggingface.co/datasets/OpenDriveLab/OpenScene/resolve/main/"
  for attempt in 1 2 3 4 5; do
    if [[ "${NAVSIM_DIRECT_HTTP_FIRST:-1}" -eq 1 && "$url" == "$hf_prefix"* ]]; then
      if command -v wget >/dev/null 2>&1; then
        if wget -c --tries=10 --waitretry=5 --retry-connrefused --progress=dot:giga -O "$dest.tmp" "$url"; then
          mv "$dest.tmp" "$dest"
          printf 'saved   %s\n' "$dest"
          return
        fi
      elif curl -L --retry 10 --retry-delay 5 --retry-connrefused -C - -o "$dest.tmp" "$url"; then
        mv "$dest.tmp" "$dest"
        printf 'saved   %s\n' "$dest"
        return
      fi
    fi
    if download_hf_openscene "$url" "$dest"; then
      return
    fi
    if command -v wget >/dev/null 2>&1; then
      if wget -c --tries=10 --waitretry=5 --retry-connrefused --progress=dot:giga -O "$dest.tmp" "$url"; then
        mv "$dest.tmp" "$dest"
        printf 'saved   %s\n' "$dest"
        return
      fi
    else
      if curl -L --retry 10 --retry-delay 5 --retry-connrefused -C - -o "$dest.tmp" "$url"; then
        mv "$dest.tmp" "$dest"
        printf 'saved   %s\n' "$dest"
        return
      fi
    fi
    printf 'retry   %s attempt %s/5\n' "$dest" "$attempt" >&2
    sleep $((attempt * 10))
  done
  echo "failed to download: $url" >&2
  return 1
}

download_hf_openscene() {
  local url="$1"
  local dest="$2"
  local prefix="https://huggingface.co/datasets/OpenDriveLab/OpenScene/resolve/main/"
  if [[ "$url" != "$prefix"* ]]; then
    return 1
  fi
  local filename="${url#"$prefix"}"
  local hf_dir="$DOWNLOAD_DIR/.hf"
  local downloaded
  if ! downloaded="$(HF_HUB_DISABLE_XET="${HF_HUB_DISABLE_XET:-1}" \
    PYTHONPATH="$UTMR_ROOT/runtime/python-packages${PYTHONPATH:+:$PYTHONPATH}" \
    "$PYTHON_BIN" - "$filename" "$hf_dir" <<'PY'
import sys
from pathlib import Path
from huggingface_hub import hf_hub_download

filename = sys.argv[1]
local_dir = Path(sys.argv[2])
path = hf_hub_download(
    repo_id="OpenDriveLab/OpenScene",
    repo_type="dataset",
    filename=filename,
    local_dir=local_dir,
)
print(path)
PY
  )"; then
    return 1
  fi
  if [[ ! -s "$downloaded" ]]; then
    return 1
  fi
  mv "$downloaded" "$dest.tmp"
  mv "$dest.tmp" "$dest"
  printf 'saved   %s\n' "$dest"
}

require_no_symlinks() {
  local found
  found="$(find "$UTMR_ROOT" -type l -print -quit)"
  if [[ -n "$found" ]]; then
    echo "symlink found under UTMR: $found" >&2
    exit 1
  fi
}

prepare_maps() {
  local target="$DATASET_DIR/maps"
  if [[ -s "$target/nuplan-maps-v1.0.json" ]]; then
    printf 'ok      %s\n' "$target"
    return
  fi
  local archive="$DOWNLOAD_DIR/nuplan-maps-v1.1.zip"
  local tmp
  tmp="$(mktemp -d "$DATASET_DIR/.tmp-maps.XXXXXX")"
  trap 'rm -rf "$tmp"' RETURN
  download "https://motional-nuplan.s3-ap-northeast-1.amazonaws.com/public/nuplan-v1.1/nuplan-maps-v1.1.zip" "$archive"
  unzip -q -o "$archive" -d "$tmp"
  rm -rf "$target"
  mv "$tmp/nuplan-maps-v1.0" "$target"
  if [[ "$KEEP_ARCHIVES" -eq 0 ]]; then
    rm -f "$archive"
  fi
  rm -rf "$tmp"
  trap - RETURN
  printf 'saved   %s\n' "$target"
}

prepare_test_metadata() {
  local target="$DATASET_DIR/navsim_logs/test"
  if [[ -d "$target" ]] && find "$target" -type f -print -quit | grep -q .; then
    printf 'ok      %s\n' "$target"
    return
  fi
  local archive="$DOWNLOAD_DIR/openscene_metadata_test.tgz"
  local tmp
  tmp="$(mktemp -d "$DATASET_DIR/.tmp-metadata.XXXXXX")"
  trap 'rm -rf "$tmp"' RETURN
  download "https://huggingface.co/datasets/OpenDriveLab/OpenScene/resolve/main/openscene-v1.1/openscene_metadata_test.tgz" "$archive"
  tar -xzf "$archive" -C "$tmp"
  mkdir -p "$DATASET_DIR/navsim_logs"
  rm -rf "$target"
  if [[ -d "$tmp/openscene-v1.1/meta_datas/test" ]]; then
    mv "$tmp/openscene-v1.1/meta_datas/test" "$target"
  else
    mv "$tmp/openscene-v1.1/meta_datas" "$target"
  fi
  if [[ "$KEEP_ARCHIVES" -eq 0 ]]; then
    rm -f "$archive"
  fi
  rm -rf "$tmp"
  trap - RETURN
  printf 'saved   %s\n' "$target"
}

prepare_test_sensors() {
  local target="$DATASET_DIR/sensor_blobs/test"
  local marker_dir="$DATASET_DIR/sensor_blobs/.prepared_shards"
  mkdir -p "$target" "$marker_dir"
  local complete=1
  for shard in $(seq "$SENSOR_START" "$SENSOR_END"); do
    if [[ ! -f "$marker_dir/camera_${shard}.done" ]] || [[ ! -f "$marker_dir/lidar_${shard}.done" ]]; then
      complete=0
      break
    fi
  done
  if [[ "$complete" -eq 1 ]] && find "$target" -type f -print -quit | grep -q .; then
    printf 'ok      %s\n' "$target"
    return
  fi

  extract_sensor_archive() {
    local archive="$1"
    local marker="$2"
    if [[ -f "$marker" ]]; then
      printf 'ok      %s\n' "$marker"
      return
    fi
    if tar -tzf "$archive" | grep -q '^openscene-v1\.1/sensor_blobs/test/'; then
      tar -xzf "$archive" -C "$target" --strip-components=3 'openscene-v1.1/sensor_blobs/test'
    elif tar -tzf "$archive" | grep -q '^sensor_blobs/test/'; then
      tar -xzf "$archive" -C "$target" --strip-components=2 'sensor_blobs/test'
    else
      local tmp
      tmp="$(mktemp -d "$DATASET_DIR/.tmp-sensors.XXXXXX")"
      tar -xzf "$archive" -C "$tmp"
      if [[ -d "$tmp/openscene-v1.1/sensor_blobs/test" ]]; then
        cp -a "$tmp/openscene-v1.1/sensor_blobs/test/." "$target/"
      elif [[ -d "$tmp/sensor_blobs/test" ]]; then
        cp -a "$tmp/sensor_blobs/test/." "$target/"
      else
        echo "unknown sensor archive layout: $archive" >&2
        rm -rf "$tmp"
        exit 1
      fi
      rm -rf "$tmp"
    fi
    date -u +"%Y-%m-%dT%H:%M:%SZ" > "$marker"
    if [[ "$KEEP_ARCHIVES" -eq 0 ]]; then
      rm -f "$archive"
    fi
    printf 'saved   %s\n' "$marker"
  }

  for shard in $(seq "$SENSOR_START" "$SENSOR_END"); do
    local camera_archive="$DOWNLOAD_DIR/openscene_sensor_test_camera_${shard}.tgz"
    local camera_marker="$marker_dir/camera_${shard}.done"
    if [[ -f "$camera_marker" ]]; then
      printf 'ok      %s\n' "$camera_marker"
    else
      download "https://huggingface.co/datasets/OpenDriveLab/OpenScene/resolve/main/openscene-v1.1/openscene_sensor_test_camera/openscene_sensor_test_camera_${shard}.tgz" "$camera_archive"
      extract_sensor_archive "$camera_archive" "$camera_marker"
    fi
    local lidar_archive="$DOWNLOAD_DIR/openscene_sensor_test_lidar_${shard}.tgz"
    local lidar_marker="$marker_dir/lidar_${shard}.done"
    if [[ -f "$lidar_marker" ]]; then
      printf 'ok      %s\n' "$lidar_marker"
    else
      download "https://huggingface.co/datasets/OpenDriveLab/OpenScene/resolve/main/openscene-v1.1/openscene_sensor_test_lidar/openscene_sensor_test_lidar_${shard}.tgz" "$lidar_archive"
      extract_sensor_archive "$lidar_archive" "$lidar_marker"
    fi
  done
  printf 'saved   %s\n' "$target"
}

run_metric_cache() {
  source "$SCRIPT_DIR/source_wote_runtime.sh"
  local cache_path="${METRIC_CACHE_PATH:-$NAVSIM_EXP_ROOT/metric_cache}"
  local metadata_rows=0
  local csv
  if [[ -d "$cache_path/metadata" ]]; then
    while IFS= read -r csv; do
      metadata_rows=$((metadata_rows + $(awk 'END { print (NR > 0 ? NR - 1 : 0) }' "$csv")))
    done < <(find "$cache_path/metadata" -maxdepth 1 -type f -name '*.csv')
  fi
  if [[ "$metadata_rows" -gt 0 ]]; then
    printf 'ok      %s (%s metadata rows)\n' "$cache_path" "$metadata_rows"
    return
  fi
  metric_args=("scene_filter=$METRIC_SCENE_FILTER" "worker=$METRIC_WORKER")
  if [[ -n "$METRIC_MAX_SCENES" ]]; then
    metric_args+=("scene_filter.max_scenes=$METRIC_MAX_SCENES")
  fi
  metric_args+=("${METRIC_EXTRA_ARGS[@]}")
  SPLIT=test "$WOTE_ROOT/scripts/evaluation/run_metric_caching.sh" "${metric_args[@]}"
}

mkdir -p "$DATASET_DIR" "$DOWNLOAD_DIR"
if [[ "$PREPARE_MAPS" -eq 1 ]]; then
  prepare_maps
fi
if [[ "$PREPARE_TEST_METADATA" -eq 1 ]]; then
  prepare_test_metadata
fi
if [[ "$PREPARE_TEST_SENSORS" -eq 1 ]]; then
  prepare_test_sensors
fi
if [[ "$RUN_METRIC_CACHE" -eq 1 ]]; then
  run_metric_cache
fi
require_no_symlinks
