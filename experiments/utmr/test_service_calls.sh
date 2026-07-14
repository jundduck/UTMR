#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

FAKE_BIN="$TMP_ROOT/bin"
mkdir -p "$FAKE_BIN"
RESPONSE_DIR="$TMP_ROOT/responses"
WAIT_LOG="$TMP_ROOT/wait.log"
mkdir -p "$RESPONSE_DIR"

cat >"$FAKE_BIN/ros2" <<'BASH'
#!/usr/bin/env bash
set -euo pipefail

if [[ "$1" == "service" && "$2" == "list" ]]; then
  cat <<'SERVICES'
/localization/initialize
/api/routing/clear_route
/planning/clear_route
/api/routing/set_route_points
/system/operation_mode/change_operation_mode
/control/vehicle_cmd_gate/set_stop
SERVICES
  exit 0
fi

if [[ "$1" == "service" && "$2" == "call" ]]; then
  service="$3"
  printf '%s\n' "$service" >>"$UTMR_TEST_CALL_LOG"
  case "$service" in
    /localization/initialize)
      echo "response:"
      if [[ "${UTMR_TEST_LOCALIZATION_SUCCESS:-0}" == "1" ]]; then
        echo "status=ResponseStatus(success=True, code=0, message='')"
      else
        echo "status=ResponseStatus(success=False, code=1, message='The vehicle is not stopped.')"
      fi
      ;;
    /api/routing/set_route_points)
      echo "response:"
      if [[ "${UTMR_TEST_ROUTE_ALREADY_SET:-1}" == "1" ]]; then
        echo "status=ResponseStatus(success=False, code=1, message='The route is already set.')"
      else
        echo "status=ResponseStatus(success=True, code=0, message='')"
      fi
      ;;
    /api/routing/clear_route|/planning/clear_route)
      echo "response:"
      echo "status=ResponseStatus(success=True, code=0, message='')"
      ;;
    /system/operation_mode/change_operation_mode)
      echo "response:"
      if [[ "${UTMR_TEST_OPERATION_SUCCESS:-1}" == "1" ]]; then
        echo "status=ResponseStatus(success=True, code=0, message='')"
      else
        echo "status=ResponseStatus(success=False, code=1, message='operation rejected')"
      fi
      ;;
    /control/vehicle_cmd_gate/set_stop)
      echo "response:"
      echo "status=ResponseStatus(success=True, code=0, message='')"
      ;;
    /planning/set_waypoint_route)
      echo "response:"
      echo "status=ResponseStatus(success=True, code=0, message='')"
      ;;
    *)
      echo "unexpected service $service" >&2
      exit 2
      ;;
  esac
  exit 0
fi

echo "unexpected ros2 args: $*" >&2
exit 2
BASH
chmod +x "$FAKE_BIN/ros2"

FAKE_WAIT="$TMP_ROOT/wait_for_stationary.sh"
cat >"$FAKE_WAIT" <<'BASH'
#!/usr/bin/env bash
set -euo pipefail
printf 'WAIT_STATIONARY\n' >>"$UTMR_TEST_CALL_LOG"
printf 'wait\n' >>"$UTMR_TEST_WAIT_LOG"
exit "${UTMR_TEST_WAIT_STATUS:-0}"
BASH
chmod +x "$FAKE_WAIT"

export PATH="$FAKE_BIN:$PATH"
export UTMR_SERVICE_LIST_TIMEOUT_S=2
export UTMR_SERVICE_CALL_TIMEOUT_S=2
export UTMR_SERVICE_RETRY_COUNT=2
export UTMR_SERVICE_RETRY_SLEEP_S=0
export UTMR_SERVICE_RESPONSE_DIR="$RESPONSE_DIR"
export UTMR_STATIONARY_WAIT_HELPER="$FAKE_WAIT"
export UTMR_TEST_WAIT_LOG="$WAIT_LOG"

source "$ROOT/autoware/utmr_scripts/service_calls.sh"
source "$ROOT/autoware/utmr_scripts/service_readiness.sh"

run_readiness_case() {
  local name="$1"
  local call_log="$TMP_ROOT/${name}.calls.log"
  : >"$call_log"
  export UTMR_TEST_CALL_LOG="$call_log"
  utmr_run_readiness_sequence "{}" "{}" "{}" >"$TMP_ROOT/${name}.out"
  READINESS_CASE_LOG="$call_log"
}

UTMR_TEST_LOCALIZATION_SUCCESS=0 \
UTMR_TEST_ROUTE_ALREADY_SET=1 \
UTMR_TEST_OPERATION_SUCCESS=1 \
  run_readiness_case localization_failure
localization_failure_log="$READINESS_CASE_LOG"

if [[ "$localization_ready" != "0" ]]; then
  echo "expected localization failure to leave localization_ready=0" >&2
  exit 1
fi

if [[ "$route_ready" != "0" ]]; then
  echo "expected localization failure to skip route setup" >&2
  exit 1
fi

if [[ "$(sed -n '1p' "$localization_failure_log")" != "WAIT_STATIONARY" ]]; then
  echo "expected stationary wait before localization initialize" >&2
  exit 1
fi

if grep -Eq '/api/routing/set_route_points|/system/operation_mode/change_operation_mode|/control/vehicle_cmd_gate/set_stop' "$localization_failure_log"; then
  echo "downstream service was called despite failed localization" >&2
  exit 1
fi

UTMR_TEST_WAIT_STATUS=1 \
UTMR_TEST_LOCALIZATION_SUCCESS=1 \
UTMR_TEST_ROUTE_ALREADY_SET=0 \
UTMR_TEST_OPERATION_SUCCESS=1 \
  run_readiness_case stationary_failure
stationary_failure_log="$READINESS_CASE_LOG"
unset UTMR_TEST_WAIT_STATUS

if [[ "$stationary_ready" != "0" || "$localization_ready" != "0" || "$route_ready" != "0" ]]; then
  echo "expected stationary failure to skip localization and route setup" >&2
  exit 1
fi

if grep -Eq '/localization/initialize|/api/routing/set_route_points|/system/operation_mode/change_operation_mode|/control/vehicle_cmd_gate/set_stop' "$stationary_failure_log"; then
  echo "service was called despite failed stationary precondition" >&2
  exit 1
fi

UTMR_TEST_LOCALIZATION_SUCCESS=1 \
UTMR_TEST_ROUTE_ALREADY_SET=0 \
UTMR_TEST_OPERATION_SUCCESS=0 \
  run_readiness_case operation_failure
operation_failure_log="$READINESS_CASE_LOG"

if [[ "$localization_ready" != "1" || "$route_ready" != "1" || "$operation_ready" != "0" || "$gate_ready" != "0" ]]; then
  echo "expected operation failure to leave gate_ready=0" >&2
  exit 1
fi

if ! grep -qx '/system/operation_mode/change_operation_mode' "$operation_failure_log"; then
  echo "expected operation service to be called" >&2
  exit 1
fi

if grep -qx '/control/vehicle_cmd_gate/set_stop' "$operation_failure_log"; then
  echo "gate service was called despite failed operation mode" >&2
  exit 1
fi

UTMR_ACCEPT_ROUTE_ALREADY_SET=0 \
UTMR_CLEAR_PLANNING_ROUTE_BEFORE_SET=1 \
UTMR_TEST_LOCALIZATION_SUCCESS=1 \
UTMR_TEST_ROUTE_ALREADY_SET=1 \
UTMR_TEST_OPERATION_SUCCESS=1 \
  run_readiness_case route_already_set_rejected
route_already_set_rejected_log="$READINESS_CASE_LOG"

if [[ "$localization_ready" != "1" || "$route_ready" != "0" || "$operation_ready" != "0" || "$gate_ready" != "0" ]]; then
  echo "expected stale route rejection to block operation and gate readiness" >&2
  exit 1
fi

expected_rejected_calls="$TMP_ROOT/expected_rejected.calls.log"
cat >"$expected_rejected_calls" <<'EOF'
WAIT_STATIONARY
/localization/initialize
/api/routing/clear_route
/planning/clear_route
/api/routing/set_route_points
/api/routing/set_route_points
EOF
if ! diff -u "$expected_rejected_calls" "$route_already_set_rejected_log"; then
  echo "expected stale route rejection to stop after route set failure" >&2
  exit 1
fi

localization_ready=1
route_ready=0
synthetic_route_fallback_active=0
UTMR_START_ROUTE_PUBLISHER=0
UTMR_ALLOW_SYNTHETIC_ROUTE_FALLBACK=1 \
  utmr_apply_synthetic_route_fallback >"$TMP_ROOT/synthetic_fallback.out"
if [[ "$route_ready" != "0" || "$synthetic_route_fallback_active" != "1" || "$UTMR_START_ROUTE_PUBLISHER" != "1" ]]; then
  echo "expected synthetic route fallback to enable planner-only publisher without marking route ready" >&2
  exit 1
fi

localization_ready=0
route_ready=0
synthetic_route_fallback_active=0
UTMR_START_ROUTE_PUBLISHER=0
if UTMR_ALLOW_SYNTHETIC_ROUTE_FALLBACK=1 utmr_apply_synthetic_route_fallback >"$TMP_ROOT/synthetic_fallback_blocked.out"; then
  echo "synthetic route fallback should not run before localization is ready" >&2
  exit 1
fi

python3 - "$ROOT" <<'PY'
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
sys.path.insert(0, str(root))

from experiments.utmr.awsim_supervisor import load_scenario, scenario_env  # noqa: E402


class Args:
    scenario_file = root / "experiments/utmr/scenarios/awsim_shinjuku_turn_sample.json"
    scenario_id = None
    scenario_index = 0


base_scenario = load_scenario(Args)
env = scenario_env(base_scenario)
route_points = json.loads(env["UTMR_ROUTE_POINTS_JSON"])
assert route_points[0]["x"] == 81383.82
assert "81383.82" in env["UTMR_ROUTE_WAYPOINTS_YAML"]
assert env["UTMR_ACCEPT_ROUTE_ALREADY_SET"] == "0"
assert env["UTMR_ALLOW_SYNTHETIC_ROUTE_FALLBACK"] == "1"
assert "UTMR_START_ROUTE_PUBLISHER" not in env
assert "UTMR_CLEAR_PLANNING_ROUTE_BEFORE_SET" not in env

bad_bool_file = root / "experiments/utmr/results/bad_bool_scenario.json"
bad_bool_file.parent.mkdir(parents=True, exist_ok=True)
bad_bool = {"scenarios": [{**base_scenario, "allow_synthetic_route_fallback": "false"}]}
bad_bool_file.write_text(json.dumps(bad_bool), encoding="utf-8")

class BadBoolArgs:
    scenario_file = bad_bool_file
    scenario_id = None
    scenario_index = 0


try:
    scenario_env(load_scenario(BadBoolArgs))
except ValueError as exc:
    assert "allow_synthetic_route_fallback" in str(exc)
else:
    raise AssertionError("string fallback boolean must be rejected")

bad_nan_file = root / "experiments/utmr/results/bad_nan_scenario.json"
bad_nan = {
    "scenarios": [
        {
            **base_scenario,
            "route_waypoints": [{"x": 81383.82, "y": float("nan"), "z": 41.3}],
        }
    ]
}
bad_nan_file.write_text(json.dumps(bad_nan), encoding="utf-8")

class BadNanArgs:
    scenario_file = bad_nan_file
    scenario_id = None
    scenario_index = 0


try:
    scenario_env(load_scenario(BadNanArgs))
except ValueError as exc:
    assert "finite" in str(exc)
else:
    raise AssertionError("non-finite route values must be rejected")

bad_goal_file = root / "experiments/utmr/results/bad_goal_scenario.json"
bad_goal = {
    "scenarios": [
        {
            **base_scenario,
            "goal_pose": {**base_scenario["goal_pose"], "x": float("inf")},
        }
    ]
}
bad_goal_file.write_text(json.dumps(bad_goal), encoding="utf-8")

class BadGoalArgs:
    scenario_file = bad_goal_file
    scenario_id = None
    scenario_index = 0


try:
    scenario_env(load_scenario(BadGoalArgs))
except ValueError as exc:
    assert "UTMR_GOAL_x" in str(exc)
else:
    raise AssertionError("non-finite goal values must be rejected")

bad_obstacle_file = root / "experiments/utmr/results/bad_obstacle_scenario.json"
bad_obstacle = {
    "scenarios": [
        {
            **base_scenario,
            "obstacles": [{"x_m": 0.0, "y_m": 0.0, "radius_m": float("nan")}],
        }
    ]
}
bad_obstacle_file.write_text(json.dumps(bad_obstacle), encoding="utf-8")

class BadObstacleArgs:
    scenario_file = bad_obstacle_file
    scenario_id = None
    scenario_index = 0


try:
    scenario_env(load_scenario(BadObstacleArgs))
except ValueError as exc:
    assert "obstacles[0].radius" in str(exc)
else:
    raise AssertionError("non-finite obstacle values must be rejected")
PY

UTMR_TEST_LOCALIZATION_SUCCESS=1 \
UTMR_TEST_ROUTE_ALREADY_SET=0 \
UTMR_TEST_OPERATION_SUCCESS=1 \
  run_readiness_case success
success_log="$READINESS_CASE_LOG"

if ! utmr_readiness_success; then
  echo "expected full readiness success" >&2
  exit 1
fi

if ! grep -qx '/control/vehicle_cmd_gate/set_stop' "$success_log"; then
  echo "expected gate service to be called after operation success" >&2
  exit 1
fi

expected_success_calls="$TMP_ROOT/expected_success.calls.log"
cat >"$expected_success_calls" <<'EOF'
WAIT_STATIONARY
/localization/initialize
/api/routing/clear_route
/api/routing/set_route_points
/system/operation_mode/change_operation_mode
/control/vehicle_cmd_gate/set_stop
EOF
if ! diff -u "$expected_success_calls" "$success_log"; then
  echo "expected successful readiness sequence to clear stale routes before route set" >&2
  exit 1
fi

if find "$RESPONSE_DIR" -type f | grep -q .; then
  echo "service response temp files were not cleaned" >&2
  exit 1
fi

echo "service gate test ok"
