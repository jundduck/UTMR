#!/usr/bin/env bash

utmr_wait_for_stationary_before_init() {
  if [[ "${UTMR_WAIT_STATIONARY_BEFORE_INIT:-1}" == "0" ]]; then
    return 0
  fi

  local readiness_dir
  readiness_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  local helper="${UTMR_STATIONARY_WAIT_HELPER:-$readiness_dir/helpers/wait_for_stationary.py}"
  if [[ ! -x "$helper" ]]; then
    echo "skip stationary wait: helper is not executable: $helper"
    return 0
  fi

  echo "waiting for ego vehicle to become stationary before localization initialize..."
  if "$helper"; then
    echo "stationary wait completed"
    return 0
  else
    echo "stationary wait timed out"
    return 1
  fi
}

utmr_run_localization_and_route() {
  local init_request="$1"
  local route_request="$2"

  stationary_ready=0
  localization_ready=0
  route_ready=0
  operation_ready=0
  gate_ready=0

  if ! utmr_initialize_localization "$init_request"; then
    true
  fi

  if [[ "$localization_ready" != "1" ]]; then
    echo "skip route setup: localization_ready=$localization_ready"
    return 0
  fi

  utmr_clear_route_before_set

  local route_success_pattern="success=True"
  if [[ "${UTMR_ACCEPT_ROUTE_ALREADY_SET:-0}" != "0" ]]; then
    route_success_pattern="success=True|The route is already set"
  fi

  if utmr_call_service_with_retry \
    "ADAPI route set" \
    /api/routing/set_route_points \
    autoware_adapi_v1_msgs/srv/SetRoutePoints \
    "$route_request" \
    "$UTMR_SERVICE_RETRY_COUNT" \
    "$UTMR_SERVICE_RETRY_SLEEP_S" \
    "$route_success_pattern"; then
    route_ready=1
  fi
}

utmr_initialize_localization() {
  local init_request="$1"
  local attempt

  for attempt in $(seq 1 "$UTMR_SERVICE_RETRY_COUNT"); do
    if utmr_wait_for_stationary_before_init; then
      stationary_ready=1
    else
      echo "skip localization initialize: ego vehicle did not satisfy stationary precondition"
      return 1
    fi

    if utmr_call_service_with_retry \
      "localization initialize" \
      /localization/initialize \
      autoware_localization_msgs/srv/InitializeLocalization \
      "$init_request" \
      1 \
      0 \
      "success=True"; then
      localization_ready=1
      return 0
    fi

    if [[ "$attempt" -lt "$UTMR_SERVICE_RETRY_COUNT" ]]; then
      echo "localization initialize retry requires a fresh stationary check"
      sleep "$UTMR_SERVICE_RETRY_SLEEP_S"
    fi
  done

  return 1
}

utmr_clear_route_before_set() {
  if [[ "${UTMR_CLEAR_ROUTE_BEFORE_SET:-1}" == "0" ]]; then
    return 0
  fi

  utmr_call_service_with_retry \
    "ADAPI route clear" \
    /api/routing/clear_route \
    autoware_adapi_v1_msgs/srv/ClearRoute \
    "{}" \
    "${UTMR_CLEAR_ROUTE_RETRY_COUNT:-$UTMR_SERVICE_RETRY_COUNT}" \
    "${UTMR_CLEAR_ROUTE_RETRY_SLEEP_S:-$UTMR_SERVICE_RETRY_SLEEP_S}" \
    "success=True|NO_EFFECT|code=60001|not set|not found" || true

  if [[ "${UTMR_CLEAR_PLANNING_ROUTE_BEFORE_SET:-0}" != "0" ]]; then
    utmr_call_service_with_retry \
      "planning route clear" \
      /planning/clear_route \
      autoware_planning_msgs/srv/ClearRoute \
      "{}" \
      "${UTMR_CLEAR_ROUTE_RETRY_COUNT:-$UTMR_SERVICE_RETRY_COUNT}" \
      "${UTMR_CLEAR_ROUTE_RETRY_SLEEP_S:-$UTMR_SERVICE_RETRY_SLEEP_S}" \
      "success=True|NO_EFFECT|code=60001|not set|not found" || true
  fi
}

utmr_run_operation_and_gate() {
  operation_ready=0
  gate_ready=0
  if [[ "$localization_ready" == "1" && "$route_ready" == "1" ]]; then
    if utmr_call_service_with_retry \
      "operation mode autonomous" \
      /system/operation_mode/change_operation_mode \
      autoware_system_msgs/srv/ChangeOperationMode \
      "{mode: 2}" \
      2 \
      1 \
      "success=True"; then
      operation_ready=1
    fi

    if [[ "$operation_ready" == "1" ]]; then
      if utmr_call_service_with_retry \
        "vehicle command gate unstop" \
        /control/vehicle_cmd_gate/set_stop \
        tier4_control_msgs/srv/SetStop \
        "{stop: false, request_source: utmr}" \
        2 \
        1 \
        "success=True"; then
        gate_ready=1
      fi
    else
      echo "skip vehicle command gate unstop: operation_ready=$operation_ready"
    fi
  else
    echo "skip autonomous/gate services: localization_ready=$localization_ready route_ready=$route_ready"
  fi
}

utmr_apply_synthetic_route_fallback() {
  if [[ "$localization_ready" == "1" && "$route_ready" != "1" && "${UTMR_ALLOW_SYNTHETIC_ROUTE_FALLBACK:-0}" != "0" ]]; then
    echo "route service did not become ready; enabling synthetic route fallback for planner-only debug"
    synthetic_route_fallback_active=1
    UTMR_START_ROUTE_PUBLISHER=1
    return 0
  fi
  synthetic_route_fallback_active=0
  return 1
}

utmr_run_waypoint_route_if_requested() {
  local waypoint_route_request="${1:-}"

  if [[ -n "$waypoint_route_request" ]]; then
    utmr_call_service_with_retry \
      "planning waypoint route set" \
      /planning/set_waypoint_route \
      autoware_planning_msgs/srv/SetWaypointRoute \
      "$waypoint_route_request" \
      1 \
      1 \
      "success=True|The route is already set" || true
  fi
}

utmr_run_readiness_sequence() {
  local init_request="$1"
  local route_request="$2"
  local waypoint_route_request="${3:-}"

  utmr_run_localization_and_route "$init_request" "$route_request"
  utmr_run_operation_and_gate
  utmr_run_waypoint_route_if_requested "$waypoint_route_request"
}

utmr_readiness_success() {
  [[ "${localization_ready:-0}" == "1" && "${route_ready:-0}" == "1" && "${operation_ready:-0}" == "1" && "${gate_ready:-0}" == "1" ]]
}
