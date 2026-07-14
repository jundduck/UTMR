#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

FAKE_BIN="$TMP_ROOT/bin"
mkdir -p "$FAKE_BIN"
CALL_LOG="$TMP_ROOT/calls.log"
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
      echo "status=ResponseStatus(success=False, code=1, message='The vehicle is not stopped.')"
      ;;
    /api/routing/set_route_points)
      echo "response:"
      echo "status=ResponseStatus(success=False, code=1, message='The route is already set.')"
      ;;
    /system/operation_mode/change_operation_mode|/control/vehicle_cmd_gate/set_stop)
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
export UTMR_TEST_CALL_LOG="$CALL_LOG"
export UTMR_SERVICE_LIST_TIMEOUT_S=2
export UTMR_SERVICE_CALL_TIMEOUT_S=2
export UTMR_SERVICE_RETRY_COUNT=2
export UTMR_SERVICE_RETRY_SLEEP_S=0
export UTMR_SERVICE_RESPONSE_DIR="$RESPONSE_DIR"

source "$ROOT/autoware/utmr_scripts/service_calls.sh"

localization_ready=0
if utmr_call_service_with_retry \
  "localization initialize" \
  /localization/initialize \
  autoware_localization_msgs/srv/InitializeLocalization \
  "{}" \
  2 \
  0 \
  "success=True"; then
  localization_ready=1
fi

route_ready=0
if utmr_call_service_with_retry \
  "ADAPI route set" \
  /api/routing/set_route_points \
  autoware_adapi_v1_msgs/srv/SetRoutePoints \
  "{}" \
  2 \
  0 \
  "success=True|The route is already set"; then
  route_ready=1
fi

if [[ "$localization_ready" == "1" && "$route_ready" == "1" ]]; then
  utmr_call_service_with_retry \
    "operation mode autonomous" \
    /system/operation_mode/change_operation_mode \
    autoware_system_msgs/srv/ChangeOperationMode \
    "{mode: 2}"
  utmr_call_service_with_retry \
    "vehicle command gate unstop" \
    /control/vehicle_cmd_gate/set_stop \
    tier4_control_msgs/srv/SetStop \
    "{stop: false, request_source: utmr}"
fi

if [[ "$localization_ready" != "0" ]]; then
  echo "expected localization failure to leave localization_ready=0" >&2
  exit 1
fi

if [[ "$route_ready" != "1" ]]; then
  echo "expected route already-set response to be accepted" >&2
  exit 1
fi

if grep -Eq '/system/operation_mode/change_operation_mode|/control/vehicle_cmd_gate/set_stop' "$CALL_LOG"; then
  echo "autonomous/gate service was called despite failed localization" >&2
  exit 1
fi

if find "$RESPONSE_DIR" -type f | grep -q .; then
  echo "service response temp files were not cleaned" >&2
  exit 1
fi

echo "service gate test ok"
