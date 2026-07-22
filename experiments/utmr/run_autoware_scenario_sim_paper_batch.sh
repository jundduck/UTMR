#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
AUTOWARE_ROOT="$ROOT/autoware"
APT_ROOT="$ROOT/runtime/apt-root"
ACADOS_ROOT="$ROOT/runtime/acados"
MISSING_ROS_PREFIX="$ROOT/runtime/ros-humble-missing/root/opt/ros/humble"
HELPER_DIR="$AUTOWARE_ROOT/utmr_scripts/helpers"
STOP_HELPERS="$AUTOWARE_ROOT/utmr_scripts/stop_demo_helpers.sh"
SCENARIO_PYTHON_PACKAGES="$ROOT/experiments/utmr/runtime/scenario-python-packages"
SCENARIO_RUNTIME_BIN="$ROOT/experiments/utmr/runtime/bin"
AUTOWARE_DATA_PATH="$AUTOWARE_ROOT/autoware_data/ml_models"

OUT_ROOT="${OUT_ROOT:-$ROOT/experiments/utmr/results/autoware_scenario_sim_paper_$(date +%Y%m%d_%H%M%S)}"
SCENARIO_FILE="${SCENARIO_FILE:-$ROOT/experiments/utmr/scenarios/scenario_sim_shinjuku_sample_50.yaml}"
SCENARIO_FRAME_RATE="${SCENARIO_FRAME_RATE:-10.0}"
SCENARIO_GLOBAL_TIMEOUT="${SCENARIO_GLOBAL_TIMEOUT:-240}"
SCENARIO_INITIALIZE_DURATION="${SCENARIO_INITIALIZE_DURATION:-120}"
SCENARIO_BASE_PORT="${SCENARIO_BASE_PORT:-${SCENARIO_PORT:-5555}}"
ISOLATE_SCENARIO_PORT="${ISOLATE_SCENARIO_PORT:-0}"
SCENARIO_BASE_ROS_DOMAIN_ID="${SCENARIO_BASE_ROS_DOMAIN_ID:-${ROS_DOMAIN_ID:-80}}"
SCENARIO_MAX_ROS_DOMAIN_ID="${SCENARIO_MAX_ROS_DOMAIN_ID:-232}"
ISOLATE_ROS_DOMAIN="${ISOLATE_ROS_DOMAIN:-1}"
WALL_GRACE_S="${WALL_GRACE_S:-90}"
RUN_COOLDOWN_S="${RUN_COOLDOWN_S:-15}"
EPISODES="${EPISODES:-1}"
VARIANTS=(${VARIANTS:-baseline utmr})
MAX_ATTEMPTS="${MAX_ATTEMPTS:-3}"
START_EMPTY_SIM_INPUTS="${START_EMPTY_SIM_INPUTS:-0}"
START_EMERGENCY_HEARTBEAT="${START_EMERGENCY_HEARTBEAT:-1}"
START_MRM_HEARTBEAT="${START_MRM_HEARTBEAT:-1}"
STABILIZE_CMD_GATE="${STABILIZE_CMD_GATE:-1}"
START_BASELINE_PLANNER="${START_BASELINE_PLANNER:-1}"
PRINT_LOG_TAIL="${PRINT_LOG_TAIL:-1}"
CURRENT_SCENARIO_PID=""

draw_bar() {
  local current="$1"
  local total="$2"
  local label="$3"
  local width=36
  (( total > 0 )) || total=1
  (( current > total )) && current="$total"
  local pct=$(( current * 100 / total ))
  local filled=$(( current * width / total ))
  local empty=$(( width - filled ))
  local fill blank
  printf -v fill '%*s' "$filled" ''
  printf -v blank '%*s' "$empty" ''
  fill="${fill// /#}"
  blank="${blank// /-}"
  printf "\r[%s%s] %3d%%  %4ds/%-4ds  %s" "$fill" "$blank" "$pct" "$current" "$total" "$label"
}

is_true() {
  local value="${1:-}"
  case "${value,,}" in
    1|true|yes|on)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

require_uint_env() {
  local name="$1"
  local value="${!name}"
  if [[ ! "$value" =~ ^[0-9]+$ ]]; then
    echo "$name must be an unsigned integer, got $value" >&2
    exit 2
  fi
  printf -v "$name" '%d' "$((10#$value))"
}

require_uint_range() {
  local name="$1"
  local min="$2"
  local max="$3"
  require_uint_env "$name"
  local value="${!name}"
  if (( value < min || value > max )); then
    echo "$name must be in [$min, $max], got $value" >&2
    exit 2
  fi
}

validate_runtime_config() {
  require_uint_range SCENARIO_GLOBAL_TIMEOUT 1 86400
  require_uint_range SCENARIO_INITIALIZE_DURATION 0 86400
  require_uint_range SCENARIO_BASE_PORT 1 65535
  require_uint_range SCENARIO_BASE_ROS_DOMAIN_ID 0 232
  require_uint_range SCENARIO_MAX_ROS_DOMAIN_ID 0 232
  require_uint_range WALL_GRACE_S 0 86400
  require_uint_range RUN_COOLDOWN_S 0 86400
  require_uint_range EPISODES 0 100000
  require_uint_range MAX_ATTEMPTS 1 1000

  if (( SCENARIO_BASE_ROS_DOMAIN_ID > SCENARIO_MAX_ROS_DOMAIN_ID )); then
    echo "SCENARIO_BASE_ROS_DOMAIN_ID must be <= SCENARIO_MAX_ROS_DOMAIN_ID, got $SCENARIO_BASE_ROS_DOMAIN_ID > $SCENARIO_MAX_ROS_DOMAIN_ID" >&2
    exit 2
  fi
  if is_true "$ISOLATE_SCENARIO_PORT"; then
    echo "Scenario Simulator port isolation is unsupported; keep fixed port $SCENARIO_BASE_PORT" >&2
    exit 2
  fi
}

prepend_env_path_if_dir() {
  local name="$1"
  local path="$2"
  if [[ -d "$path" ]]; then
    local current="${!name:-}"
    if [[ -n "$current" ]]; then
      export "$name=$path:$current"
    else
      export "$name=$path"
    fi
  fi
}

prepend_ld_path_if_dir() {
  local path="$1"
  if [[ -d "$path" ]]; then
    export LD_LIBRARY_PATH="$path:${LD_LIBRARY_PATH:-}"
  fi
}

cleanup_helpers() {
  if [[ -n "${HELPER_LOG_DIR:-}" && -s "$HELPER_LOG_DIR/cmd_gate_stabilizer.pid" ]]; then
    local pid
    pid="$(tr -dc '0-9' <"$HELPER_LOG_DIR/cmd_gate_stabilizer.pid")"
    if [[ -n "$pid" ]]; then
      kill -INT "$pid" 2>/dev/null || true
    fi
  fi
  UTMR_HELPER_PID_DIR="${HELPER_LOG_DIR:-}" \
    UTMR_HELPER_LOG_DIR="${HELPER_LOG_DIR:-}" \
    "$STOP_HELPERS" >/dev/null 2>&1 || true
}

stop_scenario_process_group() {
  local pid="${1:-}"
  [[ -n "$pid" ]] || return 0
  kill -0 "$pid" 2>/dev/null || return 0
  kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
  sleep 8
  kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
}

cleanup_all() {
  stop_scenario_process_group "$CURRENT_SCENARIO_PID"
  cleanup_helpers
}
trap cleanup_all EXIT
trap 'trap - EXIT; cleanup_all; exit 130' INT TERM

start_helper() {
  local name="$1"
  local script="$2"
  local log_file="$3"
  PYTHONPATH="$ROOT:$HELPER_DIR:${PYTHONPATH:-}" "$script" >"$log_file" 2>&1 &
  local pid="$!"
  echo "$pid" >"$HELPER_LOG_DIR/${name}.pid"
}

start_cmd_gate_stabilizer() {
  local log_file="$1"
  (
    for attempt in $(seq 1 90); do
      if ros2 param set /control/vehicle_cmd_gate use_emergency_handling false >>"$log_file" 2>&1; then
        echo "set use_emergency_handling=false on attempt $attempt" >>"$log_file"
        exit 0
      fi
      sleep 1
    done
    echo "failed to set use_emergency_handling=false" >>"$log_file"
    exit 1
  ) &
  echo "$!" >"$HELPER_LOG_DIR/cmd_gate_stabilizer.pid"
}

ros_domain_for_run() {
  local run_number="$1"
  local domain_span

  if is_true "$ISOLATE_ROS_DOMAIN"; then
    domain_span=$((SCENARIO_MAX_ROS_DOMAIN_ID - SCENARIO_BASE_ROS_DOMAIN_ID + 1))
    echo $((SCENARIO_BASE_ROS_DOMAIN_ID + ((run_number - 1) % domain_span)))
  else
    echo "$SCENARIO_BASE_ROS_DOMAIN_ID"
  fi
}

classify_result() {
  local log_file="$1"
  local exit_code="$2"
  if grep -Eq 'AutowareError|exitFailure|wall-clock timeout|TimeoutError' "$log_file"; then
    echo "failed"
  elif [[ "$exit_code" == "124" ]]; then
    echo "failed"
  elif grep -Eq $'\033\\[32mPassed|Passed' "$log_file"; then
    echo "passed"
  elif grep -Eq 'MRM_FAILED|process has died.*exit code -?[1-9][0-9]*' "$log_file"; then
    echo "failed"
  elif [[ "$exit_code" == "0" ]]; then
    echo "completed_no_pass_marker"
  else
    echo "ros_launch_exit_${exit_code}"
  fi
}

write_summary() {
  python3 - "$OUT_ROOT" <<'PY'
import csv
import json
import math
import sys
from pathlib import Path

out = Path(sys.argv[1])
status_path = out / "raw" / "variant_status.tsv"
episode_path = out / "raw" / "scenario_sim_episodes.csv"
status_rows = []
episode_rows = []
if status_path.exists():
    with status_path.open(newline="", encoding="utf-8") as fp:
        status_rows = list(csv.DictReader(fp, delimiter="\t"))
if episode_path.exists():
    with episode_path.open(newline="", encoding="utf-8") as fp:
        episode_rows = list(csv.DictReader(fp))
episodes_by_id = {row.get("episode_id", ""): row for row in episode_rows}

def read_log(status):
    log_path = Path(status.get("log", ""))
    if not log_path.exists():
        return ""
    return log_path.read_text(encoding="utf-8", errors="replace")

def step_count(status):
    variant = status["variant"]
    episode = status["episode"]
    step_log = status.get("step_log", "")
    step_path = Path(step_log) if step_log else out / "raw" / f"{variant}_{episode}_steps.jsonl"
    if not step_path.is_absolute() and not step_path.exists():
        step_path = out / step_path
    if not step_path.exists():
        return 0
    return sum(1 for line in step_path.read_text(encoding="utf-8").splitlines() if line.strip())

def row_for_status(status):
    variant = status["variant"]
    episode = status["episode"]
    episode_id = status.get("episode_id") or f"scenario_sim_{variant}_{episode}"
    metrics = episodes_by_id.get(episode_id, {})
    log_text = read_log(status)
    timeout = metrics.get("timeout", "")
    if (
        status.get("exit_code") == "124"
        or "wall-clock timeout" in log_text
        or "TimeoutError" in log_text
    ):
        timeout = "True"
    elif status.get("result") != "passed" and timeout not in ("True", "False"):
        timeout = ""
    return {
        "variant": variant,
        "episode": episode,
        "attempt": status.get("attempt", ""),
        "result": status["result"],
        "exit_code": status["exit_code"],
        "success": metrics.get("success", ""),
        "collision": metrics.get("collision", ""),
        "timeout": timeout,
        "distance_m": metrics.get("distance_m", ""),
        "mean_speed_kmh": metrics.get("mean_speed_kmh", ""),
        "driving_score": metrics.get("driving_score", ""),
        "failure_reason": failure_reason(status, log_text),
        "step_rows": str(step_count(status)),
    }

def write_tsv(path, fields, rows):
    lines = ["\t".join(fields)]
    for row in rows:
        lines.append("\t".join(str(row.get(field, "")) for field in fields))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

def as_float(value):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(parsed):
        return None
    return parsed

def mean_text(values):
    numbers = [value for value in values if value is not None]
    if not numbers:
        return "0.0000"
    return f"{(sum(numbers) / len(numbers)):.4f}"

def failure_reason(status, log_text):
    if status.get("result") == "passed":
        return ""
    if status.get("exit_code") == "124" or "wall-clock timeout" in log_text:
        return "wall_clock_timeout"
    if "MRM_FAILED" in log_text:
        return "mrm_failed"
    if "AutowareError" in log_text:
        return "autoware_error"
    if "exitFailure" in log_text:
        return "scenario_exit_failure"
    if "TimeoutError" in log_text:
        return "timeout"
    if "process has died" in log_text:
        return "process_died"
    return status.get("result", "")

attempt_fields = [
    "variant",
    "episode",
    "attempt",
    "result",
    "exit_code",
    "success",
    "collision",
    "timeout",
    "distance_m",
    "mean_speed_kmh",
    "driving_score",
    "failure_reason",
    "step_rows",
]

attempt_rows = [row_for_status(status) for status in status_rows]
final_by_key = {}
for row in attempt_rows:
    key = (row["variant"], row["episode"])
    previous = final_by_key.get(key)
    if previous is None or previous["result"] != "passed":
        final_by_key[key] = row
final_rows = list(final_by_key.values())

aggregate_fields = [
    "variant",
    "episodes",
    "passed",
    "success_pct",
    "mean_attempts",
    "mean_distance_m",
    "mean_speed_kmh",
    "mean_driving_score",
    "total_step_rows",
]
aggregate_rows = []
for variant in sorted({row["variant"] for row in final_rows}):
    rows = [row for row in final_rows if row["variant"] == variant]
    passed = [row for row in rows if row["result"] == "passed"]
    attempts = [as_float(row["attempt"]) for row in rows]
    distances = [as_float(row["distance_m"]) for row in passed]
    speeds = [as_float(row["mean_speed_kmh"]) for row in passed]
    scores = [as_float(row["driving_score"]) for row in passed]
    step_rows = [as_float(row["step_rows"]) for row in rows]
    aggregate_rows.append({
        "variant": variant,
        "episodes": len(rows),
        "passed": len(passed),
        "success_pct": f"{(100.0 * len(passed) / len(rows)):.4f}" if rows else "0.0000",
        "mean_attempts": mean_text(attempts),
        "mean_distance_m": mean_text(distances),
        "mean_speed_kmh": mean_text(speeds),
        "mean_driving_score": mean_text(scores),
        "total_step_rows": int(sum(item for item in step_rows if item is not None)),
    })

write_tsv(out / "summary.tsv", attempt_fields, attempt_rows)
write_tsv(out / "summary_final.tsv", attempt_fields, final_rows)
write_tsv(out / "summary_aggregate.tsv", aggregate_fields, aggregate_rows)
(out / "summary.json").write_text(
    json.dumps({"status": status_rows, "episodes": episode_rows, "final": final_rows, "aggregate": aggregate_rows}, indent=2),
    encoding="utf-8",
)
print(out / "summary.tsv")
print((out / "summary.tsv").read_text(encoding="utf-8"))
print(out / "summary_final.tsv")
print((out / "summary_final.tsv").read_text(encoding="utf-8"))
print(out / "summary_aggregate.tsv")
print((out / "summary_aggregate.tsv").read_text(encoding="utf-8"))
PY
}

run_self_test() {
  local self_root
  mkdir -p "$ROOT/experiments/utmr/results"
  self_root="$(mktemp -d "$ROOT/experiments/utmr/results/scenario_runner_self_test.XXXXXX")"
  OUT_ROOT="$self_root"
  mkdir -p "$OUT_ROOT/logs" "$OUT_ROOT/raw"

  local original_base="$SCENARIO_BASE_ROS_DOMAIN_ID"
  local original_max="$SCENARIO_MAX_ROS_DOMAIN_ID"
  local original_isolate="$ISOLATE_ROS_DOMAIN"
  SCENARIO_BASE_ROS_DOMAIN_ID=220
  SCENARIO_MAX_ROS_DOMAIN_ID=232
  ISOLATE_ROS_DOMAIN=1
  if [[ "$(ros_domain_for_run 1)" != "220" || "$(ros_domain_for_run 13)" != "232" || "$(ros_domain_for_run 14)" != "220" ]]; then
    echo "self-test failed: ROS domain wrapping" >&2
    exit 1
  fi
  SCENARIO_BASE_ROS_DOMAIN_ID="$original_base"
  SCENARIO_MAX_ROS_DOMAIN_ID="$original_max"
  ISOLATE_ROS_DOMAIN="$original_isolate"

  local fatal_log="$OUT_ROOT/logs/fatal_after_pass.log"
  local teardown_log="$OUT_ROOT/logs/teardown_after_pass.log"
  local timeout_arg_log="$OUT_ROOT/logs/timeout_arg_only.log"
  local pass_log="$OUT_ROOT/logs/pass.log"
  printf "Passed\nAutowareError\n" >"$fatal_log"
  printf "process has died [pid 10, exit code -11, cmd teardown]\nPassed\n" >"$teardown_log"
  printf "global_timeout := 240\n" >"$timeout_arg_log"
  printf "Passed\n" >"$pass_log"
  if [[ "$(classify_result "$fatal_log" 0)" != "failed" ]]; then
    echo "self-test failed: strong fatal marker must override Passed" >&2
    exit 1
  fi
  if [[ "$(classify_result "$teardown_log" 0)" != "passed" ]]; then
    echo "self-test failed: teardown crash marker must not override Passed" >&2
    exit 1
  fi
  if [[ "$(classify_result "$timeout_arg_log" 0)" != "completed_no_pass_marker" ]]; then
    echo "self-test failed: launch timeout parameter must not count as timeout" >&2
    exit 1
  fi
  if [[ "$(classify_result "$pass_log" 0)" != "passed" ]]; then
    echo "self-test failed: Passed marker classification" >&2
    exit 1
  fi

  printf "variant\tepisode\tattempt\tresult\texit_code\tlog\tstep_log\tepisode_id\n" >"$OUT_ROOT/raw/variant_status.tsv"
  printf "baseline\t1\t1\tpassed\t0\t%s\t%s\tepisode_with_metrics\n" \
    "$pass_log" "$OUT_ROOT/raw/baseline_1_steps.jsonl" >>"$OUT_ROOT/raw/variant_status.tsv"
  printf "baseline\t2\t1\tpassed\t0\t%s\t%s\tepisode_without_metrics\n" \
    "$pass_log" "$OUT_ROOT/raw/baseline_2_steps.jsonl" >>"$OUT_ROOT/raw/variant_status.tsv"
  printf "{}\n" >"$OUT_ROOT/raw/baseline_1_steps.jsonl"
  printf "{}\n{}\n" >"$OUT_ROOT/raw/baseline_2_steps.jsonl"
  printf "method,variant,episode_id,collision,success,timeout,distance_m,route_length_m,mean_speed_kmh,driving_score,metric_source,metric_note\n" \
    >"$OUT_ROOT/raw/scenario_sim_episodes.csv"
  printf "baseline,baseline,episode_with_metrics,,True,,100.0,100.0,12.0,80.0,observed,\n" \
    >>"$OUT_ROOT/raw/scenario_sim_episodes.csv"
  write_summary >/dev/null
  if ! awk -F'\t' '$1 == "baseline" { ok = ($6 == "100.0000" && $7 == "12.0000" && $8 == "80.0000") } END { exit ok ? 0 : 1 }' \
    "$OUT_ROOT/summary_aggregate.tsv"; then
    echo "self-test failed: aggregate means must ignore missing metric rows" >&2
    cat "$OUT_ROOT/summary_aggregate.tsv" >&2
    exit 1
  fi

  rm -rf "$self_root"
  echo "ok runner self-test"
}

validate_runtime_config
if is_true "${UTMR_SCENARIO_RUNNER_SELF_TEST:-0}"; then
  run_self_test
  exit 0
fi

mkdir -p "$OUT_ROOT/logs" "$OUT_ROOT/raw"
"$ROOT/experiments/utmr/prepare_awsim_openscenario_runtime.sh" >/dev/null

set +u
source /opt/ros/humble/setup.bash
source "$AUTOWARE_ROOT/utmr_scripts/setup_runtime_overlay.sh"
source "$AUTOWARE_ROOT/install/setup.bash"
set -u

prepend_env_path_if_dir AMENT_PREFIX_PATH "$MISSING_ROS_PREFIX"
prepend_env_path_if_dir CMAKE_PREFIX_PATH "$MISSING_ROS_PREFIX"
prepend_env_path_if_dir PYTHONPATH "$MISSING_ROS_PREFIX/local/lib/python3.10/dist-packages"
prepend_env_path_if_dir PYTHONPATH "$SCENARIO_PYTHON_PACKAGES"
prepend_env_path_if_dir PATH "$SCENARIO_RUNTIME_BIN"
prepend_ld_path_if_dir "$APT_ROOT/usr/lib/x86_64-linux-gnu"
prepend_ld_path_if_dir "$APT_ROOT/opt/ros/humble/lib"
prepend_ld_path_if_dir "$APT_ROOT/opt/ros/humble/lib/x86_64-linux-gnu"
prepend_ld_path_if_dir "$APT_ROOT/opt/ros/humble/opt/zmqpp_vendor/lib"
prepend_ld_path_if_dir "$ACADOS_ROOT/install/lib"
prepend_ld_path_if_dir "$MISSING_ROS_PREFIX/lib"

export HOME="$AUTOWARE_ROOT"
export ROS_DOMAIN_ID="$SCENARIO_BASE_ROS_DOMAIN_ID"
export RMW_IMPLEMENTATION="${RMW_IMPLEMENTATION:-rmw_fastrtps_cpp}"
export FASTDDS_BUILTIN_TRANSPORTS="${FASTDDS_BUILTIN_TRANSPORTS:-UDPv4}"

MAP_PATH="$(ros2 pkg prefix --share shinjuku_map)/map"
STATUS_TSV="$OUT_ROOT/raw/variant_status.tsv"
printf "variant\tepisode\tattempt\tresult\texit_code\tlog\tstep_log\tepisode_id\n" >"$STATUS_TSV"

echo "Output root: $OUT_ROOT"
echo "Scenario:    $SCENARIO_FILE"
echo "Variants:    ${VARIANTS[*]}"
echo "Episodes:    $EPISODES"
echo "Max attempts: $MAX_ATTEMPTS"
echo "Frame rate:  $SCENARIO_FRAME_RATE Hz"
echo "Timeout:     $SCENARIO_GLOBAL_TIMEOUT s"
echo "Cooldown:    $RUN_COOLDOWN_S s"
echo "Base ROS domain: $SCENARIO_BASE_ROS_DOMAIN_ID"
echo "Max ROS domain:  $SCENARIO_MAX_ROS_DOMAIN_ID"
echo "Base port:   $SCENARIO_BASE_PORT"
echo "Isolate ROS domain: $ISOLATE_ROS_DOMAIN"
echo "Isolate port: $ISOLATE_SCENARIO_PORT"
echo "Empty inputs: $START_EMPTY_SIM_INPUTS"
echo "Emergency heartbeat: $START_EMERGENCY_HEARTBEAT"
echo "MRM heartbeat: $START_MRM_HEARTBEAT"
echo "Command-gate stabilization: $STABILIZE_CMD_GATE"
echo "Baseline planner helper: $START_BASELINE_PLANNER"
echo "Print log tail: $PRINT_LOG_TAIL"
echo

run_index=0
for episode in $(seq 1 "$EPISODES"); do
  for variant in "${VARIANTS[@]}"; do
    attempt=0
    variant_passed=0
    while (( attempt < MAX_ATTEMPTS )); do
      attempt=$((attempt + 1))
      run_index=$((run_index + 1))
      RUN_ROS_DOMAIN_ID="$(ros_domain_for_run "$run_index")"
      RUN_SCENARIO_PORT="$SCENARIO_BASE_PORT"
      export ROS_DOMAIN_ID="$RUN_ROS_DOMAIN_ID"

      EPISODE_ID="scenario_sim_${variant}_${episode}_a${attempt}"
      HELPER_LOG_DIR="$OUT_ROOT/logs/${variant}_${episode}_a${attempt}_helpers"
      mkdir -p "$HELPER_LOG_DIR"
      STEP_LOG="$OUT_ROOT/raw/${variant}_${episode}_a${attempt}_steps.jsonl"
      RUN_LOG="$OUT_ROOT/logs/${variant}_${episode}_a${attempt}.log"
      EPISODE_CSV="$OUT_ROOT/raw/scenario_sim_episodes.csv"
      rm -f "$STEP_LOG" "$RUN_LOG"

      echo "================================================================================"
      echo "scenario-sim variant=$variant episode=$episode attempt=$attempt/$MAX_ATTEMPTS"
      echo "ros_domain_id: $RUN_ROS_DOMAIN_ID"
      echo "scenario_port: $RUN_SCENARIO_PORT"
      echo "log:  $RUN_LOG"
      echo "step: $STEP_LOG"
      echo "================================================================================"

      cleanup_helpers
      export UTMR_EPISODE_CSV="$EPISODE_CSV"
      export UTMR_METHOD="$variant"
      export UTMR_VARIANT="$variant"
      export UTMR_EPISODE_ID="$EPISODE_ID"
      export UTMR_STEP_LOG="$STEP_LOG"
      PLANNER_MODE="$variant"
      if [[ "$variant" == "baseline" ]]; then
        PLANNER_MODE="coarse"
      fi
      export UTMR_MODE="$PLANNER_MODE"
      export UTMR_KINEMATIC_TOPIC="${UTMR_KINEMATIC_TOPIC:-/localization/kinematic_state}"
      export UTMR_KINEMATIC_MSG_TYPE="${UTMR_KINEMATIC_MSG_TYPE:-Odometry}"
      export UTMR_ROUTE_STATE_TOPIC="/api/routing/state"
      export UTMR_GOAL_X="${UTMR_GOAL_X:-81673.27596165462}"
      export UTMR_GOAL_Y="${UTMR_GOAL_Y:-50042.52556753614}"
      export UTMR_GOAL_Z="${UTMR_GOAL_Z:-41.347661394530725}"
      export UTMR_ROUTE_LENGTH_M="${UTMR_ROUTE_LENGTH_M:-77.42}"
      export UTMR_OBJECTS_TOPIC="${UTMR_OBJECTS_TOPIC:-/perception/object_recognition/objects}"
      export UTMR_OBJECTS_MSG_TYPE="${UTMR_OBJECTS_MSG_TYPE:-PredictedObjects}"
      export UTMR_K="${UTMR_K:-64}"
      export UTMR_TOP_N="${UTMR_TOP_N:-8}"
      export UTMR_BETA="${UTMR_BETA:-0.25}"
      export UTMR_GAMMA_H="${UTMR_GAMMA_H:-0.30}"
      export UTMR_GAMMA_M="${UTMR_GAMMA_M:-0.20}"
      export UTMR_PLANNER_START_DELAY_S="${UTMR_PLANNER_START_DELAY_S:-55.0}"
      export UTMR_ENABLE_ROUTE_GUIDANCE="${UTMR_ENABLE_ROUTE_GUIDANCE:-1}"
      export AWSIM_EMPTY_OBJECTS_TOPIC="${AWSIM_EMPTY_OBJECTS_TOPIC:-/utmr/empty/objects}"
      export AWSIM_EMPTY_GRID_TOPIC="${AWSIM_EMPTY_GRID_TOPIC:-/utmr/empty/occupancy_grid}"
      export AWSIM_EMPTY_POINTCLOUD_TOPIC="${AWSIM_EMPTY_POINTCLOUD_TOPIC:-/utmr/empty/pointcloud}"

      publish_perception=0
      publish_emergency=0
      publish_mrm_state=0
      if is_true "$START_EMPTY_SIM_INPUTS"; then
        publish_perception=1
      fi
      if is_true "$START_EMERGENCY_HEARTBEAT"; then
        publish_emergency=1
      fi
      if is_true "$START_MRM_HEARTBEAT"; then
        publish_mrm_state=1
      fi
      if [[ "$publish_perception" == "1" || "$publish_emergency" == "1" || "$publish_mrm_state" == "1" ]]; then
        export AWSIM_EMPTY_SIMULATION_GUARD=1
        export AWSIM_EMPTY_PUBLISH_PERCEPTION="$publish_perception"
        export AWSIM_EMPTY_PUBLISH_EMERGENCY="$publish_emergency"
        export AWSIM_EMPTY_PUBLISH_MRM_STATE="$publish_mrm_state"
        export AWSIM_EMPTY_INPUT_PERIOD_S="${AWSIM_EMPTY_INPUT_PERIOD_S:-0.05}"
        start_helper empty_sim_inputs "$HELPER_DIR/empty_sim_inputs.py" \
          "$HELPER_LOG_DIR/empty_sim_inputs.log"
      fi
      start_helper episode_metric_monitor "$HELPER_DIR/episode_metric_monitor.py" \
        "$HELPER_LOG_DIR/episode_metric_monitor.log"
      if [[ "$variant" != "baseline" ]] || is_true "$START_BASELINE_PLANNER"; then
        start_helper utmr_planner_node "$HELPER_DIR/utmr_planner_node.py" \
          "$HELPER_LOG_DIR/utmr_planner_node.log"
      fi

      setsid ros2 launch scenario_test_runner scenario_test_runner.launch.py \
        architecture_type:=awf/universe/20250130 \
        record:=false \
        scenario:="$SCENARIO_FILE" \
        sensor_model:=sample_sensor_kit \
        vehicle_model:=sample_vehicle \
        launch_simple_sensor_simulator:=true \
        simulate_localization:=true \
        global_frame_rate:="$SCENARIO_FRAME_RATE" \
        global_timeout:="$SCENARIO_GLOBAL_TIMEOUT" \
        initialize_duration:="$SCENARIO_INITIALIZE_DURATION" \
        publish_empty_context:=true \
        autoware_launch_file:=planning_simulator.launch.xml \
        launch_visualization:=false \
        launch_rviz:=false \
        autoware.rviz:=false \
        autoware.scenario_simulation:=true \
        autoware.map_path:="$MAP_PATH" \
        autoware.data_path:="$AUTOWARE_DATA_PATH" \
        port:="$RUN_SCENARIO_PORT" \
        >"$RUN_LOG" 2>&1 &
      scenario_pid="$!"
      CURRENT_SCENARIO_PID="$scenario_pid"
      if is_true "$STABILIZE_CMD_GATE"; then
        start_cmd_gate_stabilizer "$HELPER_LOG_DIR/cmd_gate_stabilizer.log"
      fi
      started="$(date +%s)"
      wall_limit=$((SCENARIO_GLOBAL_TIMEOUT + WALL_GRACE_S))
      wall_timed_out=0
      while kill -0 "$scenario_pid" 2>/dev/null; do
        now="$(date +%s)"
        elapsed=$((now - started))
        if (( elapsed > wall_limit )); then
          wall_timed_out=1
          echo
          echo "wall-clock timeout: ${elapsed}s > ${wall_limit}s" | tee -a "$RUN_LOG"
          stop_scenario_process_group "$scenario_pid"
          break
        fi
        draw_bar "$elapsed" "$wall_limit" "$variant/$episode/a$attempt"
        sleep 2
      done
      set +e
      wait "$scenario_pid"
      exit_code="$?"
      set -e
      CURRENT_SCENARIO_PID=""
      if [[ "$wall_timed_out" == "1" ]]; then
        exit_code=124
      fi
      draw_bar "$wall_limit" "$wall_limit" "$variant/$episode/a$attempt"
      echo

      cleanup_helpers
      result="$(classify_result "$RUN_LOG" "$exit_code")"
      printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" \
        "$variant" "$episode" "$attempt" "$result" "$exit_code" "$RUN_LOG" "$STEP_LOG" "$EPISODE_ID" >>"$STATUS_TSV"
      echo "result=$result exit=$exit_code attempt=$attempt"
      if is_true "$PRINT_LOG_TAIL"; then
        rg -n "Passed|exitSuccess|exitFailure|AutowareError|AutowareState|Route set|collision|wall-clock timeout|timed out|TimeoutError|MRM_FAILED" "$RUN_LOG" | tail -80 || true
      fi
      echo
      if [[ "$result" == "passed" ]]; then
        variant_passed=1
        break
      fi
      if (( attempt < MAX_ATTEMPTS )); then
        echo "retrying variant=$variant episode=$episode after failed attempt=$attempt"
      fi
      if (( RUN_COOLDOWN_S > 0 )); then
        sleep "$RUN_COOLDOWN_S"
      fi
    done
    if [[ "$variant_passed" != "1" ]]; then
      echo "variant=$variant episode=$episode exhausted $MAX_ATTEMPTS attempts"
    fi
  done
done

write_summary
cleanup_helpers
unlink "$ROOT/.codegraph" 2>/dev/null || true
echo "symlinks under UTMR: $(find "$ROOT" -xdev -type l | wc -l)"
