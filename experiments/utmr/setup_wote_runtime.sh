#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UTMR_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
PY_SITE="$UTMR_ROOT/runtime/python-packages"

mkdir -p "$PY_SITE"

"$PYTHON_BIN" -m pip install --upgrade --target "$PY_SITE" --no-deps \
  "nuplan-devkit @ git+https://github.com/motional/nuplan-devkit.git@nuplan-devkit-v1.2" \
  "hydra-core==1.3.2" \
  "omegaconf==2.3.0" \
  "antlr4-python3-runtime==4.9.3" \
  "pytorch-lightning==2.2.1" \
  "lightning-utilities==0.15.2" \
  "torchmetrics==1.3.2" \
  "fsspec" \
  "PyYAML" \
  "tqdm" \
  "packaging" \
  "timm" \
  "safetensors" \
  "huggingface_hub" \
  "httpx" \
  "httpcore" \
  "h11" \
  "anyio" \
  "exceptiongroup" \
  "sniffio" \
  "click" \
  "hf-xet" \
  "positional-encodings==6.0.1" \
  "geopandas" \
  "pyogrio" \
  "pyproj" \
  "rtree" \
  "SQLAlchemy==1.4.27" \
  "greenlet" \
  "ujson" \
  "retry" \
  "decorator" \
  "py" \
  "rasterio" \
  "affine" \
  "attrs" \
  "cligj" \
  "click-plugins" \
  "pyparsing"

"$PYTHON_BIN" -m pip install --upgrade --target "$PY_SITE" \
  "aioboto3" \
  "aiofiles"

PYTHONPATH="$PY_SITE:$UTMR_ROOT/third_party/WoTE${PYTHONPATH:+:$PYTHONPATH}" \
NAVSIM_DEVKIT_ROOT="$UTMR_ROOT/third_party/WoTE" \
  "$PYTHON_BIN" - <<'PY'
from navsim.agents.WoTE.WoTE_agent import WoTEAgent
from navsim.agents.WoTE.configs.default import WoTEConfig

cfg = WoTEConfig()
print("wote_runtime_ok")
print("agent", WoTEAgent.__name__)
print("cluster_file_path", cfg.cluster_file_path)
PY
