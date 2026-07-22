#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUNNER="$ROOT/experiments/utmr/run_autoware_scenario_sim_paper_batch.sh"
TMP_ROOT="$ROOT/experiments/utmr/results/runner_regression_tmp_$$"

cleanup() {
  rm -rf "$TMP_ROOT"
}
trap cleanup EXIT

mkdir -p "$TMP_ROOT"

echo "== runner self-test =="
UTMR_SCENARIO_RUNNER_SELF_TEST=1 "$RUNNER"

echo "== arithmetic env injection rejection =="
marker="$TMP_ROOT/arithmetic_env_marker"
set +e
env \
  "SCENARIO_BASE_ROS_DOMAIN_ID=1+\$(touch $marker)" \
  EPISODES=0 \
  OUT_ROOT="$TMP_ROOT/bad_env" \
  "$RUNNER" >"$TMP_ROOT/bad_env.log" 2>&1
status="$?"
set -e
if [[ "$status" == "0" ]]; then
  echo "expected invalid SCENARIO_BASE_ROS_DOMAIN_ID to fail" >&2
  cat "$TMP_ROOT/bad_env.log" >&2
  exit 1
fi
if [[ -e "$marker" ]]; then
  echo "arithmetic env injection marker was created" >&2
  exit 1
fi
rg -q "SCENARIO_BASE_ROS_DOMAIN_ID must be an unsigned integer" "$TMP_ROOT/bad_env.log"

echo "== fixed port invariant =="
set +e
env \
  ISOLATE_SCENARIO_PORT=1 \
  EPISODES=0 \
  OUT_ROOT="$TMP_ROOT/bad_port" \
  "$RUNNER" >"$TMP_ROOT/bad_port.log" 2>&1
status="$?"
set -e
if [[ "$status" == "0" ]]; then
  echo "expected ISOLATE_SCENARIO_PORT=1 to fail" >&2
  cat "$TMP_ROOT/bad_port.log" >&2
  exit 1
fi
rg -q "port isolation is unsupported" "$TMP_ROOT/bad_port.log"

echo "ok autoware scenario sim runner regression"
