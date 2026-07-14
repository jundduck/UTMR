#!/usr/bin/env bash

utmr_service_available() {
  timeout "$UTMR_SERVICE_LIST_TIMEOUT_S" ros2 service list 2>/dev/null | grep -qx "$1"
}

utmr_call_service_with_retry() {
  local label="$1"
  local service="$2"
  local srv_type="$3"
  local request="$4"
  local attempts="${5:-$UTMR_SERVICE_RETRY_COUNT}"
  local retry_sleep_s="${6:-$UTMR_SERVICE_RETRY_SLEEP_S}"
  local accept_pattern="${7:-success=True}"
  local response_dir="${UTMR_SERVICE_RESPONSE_DIR:-${TMPDIR:-/tmp}}"
  local attempt

  for attempt in $(seq 1 "$attempts"); do
    if utmr_service_available "$service"; then
      echo "calling $label ($service), attempt $attempt/$attempts"
      local response_log
      response_log="$(mktemp "$response_dir/utmr-service-call.XXXXXX")"
      if timeout "$UTMR_SERVICE_CALL_TIMEOUT_S" ros2 service call "$service" "$srv_type" "$request" >"$response_log" 2>&1; then
        cat "$response_log"
        if [[ -z "$accept_pattern" ]] || grep -Eq "$accept_pattern" "$response_log"; then
          rm -f "$response_log"
          echo "$label call completed"
          return 0
        fi
        echo "$label response did not match '$accept_pattern', retrying..."
      else
        cat "$response_log" || true
      fi
      rm -f "$response_log"
      echo "$label call failed, retrying..."
    else
      echo "waiting for $label ($service), attempt $attempt/$attempts"
    fi
    if [[ "$attempt" -lt "$attempts" ]]; then
      sleep "$retry_sleep_s"
    fi
  done

  echo "skip $label: service call did not complete"
  return 1
}
