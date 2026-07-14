#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

FAKE_BIN="$TMP_ROOT/bin"
mkdir -p "$FAKE_BIN"
RESPONSE_DIR="$TMP_ROOT/responses"
mkdir -p "$RESPONSE_DIR"

cat >"$FAKE_BIN/ros2" <<'BASH'
#!/usr/bin/env bash
set -euo pipefail

if [[ "$1" == "service" && "$2" == "list" ]]; then
  cat <<'SERVICES'
/localization/initialize
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

export PATH="$FAKE_BIN:$PATH"
export UTMR_SERVICE_LIST_TIMEOUT_S=2
export UTMR_SERVICE_CALL_TIMEOUT_S=2
export UTMR_SERVICE_RETRY_COUNT=2
export UTMR_SERVICE_RETRY_SLEEP_S=0
export UTMR_SERVICE_RESPONSE_DIR="$RESPONSE_DIR"

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

if [[ "$route_ready" != "1" ]]; then
  echo "expected route already-set response to be accepted" >&2
  exit 1
fi

if grep -Eq '/system/operation_mode/change_operation_mode|/control/vehicle_cmd_gate/set_stop' "$localization_failure_log"; then
  echo "autonomous/gate service was called despite failed localization" >&2
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

if find "$RESPONSE_DIR" -type f | grep -q .; then
  echo "service response temp files were not cleaned" >&2
  exit 1
fi

echo "service gate test ok"
