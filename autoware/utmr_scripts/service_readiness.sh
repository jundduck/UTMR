#!/usr/bin/env bash

utmr_run_readiness_sequence() {
  local init_request="$1"
  local route_request="$2"
  local waypoint_route_request="${3:-}"

  localization_ready=0
  if utmr_call_service_with_retry \
    "localization initialize" \
    /localization/initialize \
    autoware_localization_msgs/srv/InitializeLocalization \
    "$init_request" \
    "$UTMR_SERVICE_RETRY_COUNT" \
    "$UTMR_SERVICE_RETRY_SLEEP_S" \
    "success=True"; then
    localization_ready=1
  fi

  route_ready=0
  if utmr_call_service_with_retry \
    "ADAPI route set" \
    /api/routing/set_route_points \
    autoware_adapi_v1_msgs/srv/SetRoutePoints \
    "$route_request" \
    "$UTMR_SERVICE_RETRY_COUNT" \
    "$UTMR_SERVICE_RETRY_SLEEP_S" \
    "success=True|The route is already set"; then
    route_ready=1
  fi

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

utmr_readiness_success() {
  [[ "${localization_ready:-0}" == "1" && "${route_ready:-0}" == "1" && "${operation_ready:-0}" == "1" && "${gate_ready:-0}" == "1" ]]
}
