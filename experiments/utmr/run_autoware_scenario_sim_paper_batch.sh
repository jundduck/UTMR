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
  UTMR_HELPER_LOG_DIR="${HELPER_LOG_DIR:-}" "$STOP_HELPERS" >/dev/null 2>&1 || true
}
trap cleanup_helpers EXIT

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

  if [[ "$ISOLATE_ROS_DOMAIN" == "1" || "$ISOLATE_ROS_DOMAIN" == "true" ]]; then
    domain_span=$((SCENARIO_MAX_ROS_DOMAIN_ID - SCENARIO_BASE_ROS_DOMAIN_ID + 1))
    echo $((SCENARIO_BASE_ROS_DOMAIN_ID + ((run_number - 1) % domain_span)))
  else
    echo "$SCENARIO_BASE_ROS_DOMAIN_ID"
  fi
}

classify_result() {
  local log_file="$1"
  local exit_code="$2"
  if grep -Eq $'\033\\[32mPassed|Passed' "$log_file"; then
    echo "passed"
  elif grep -Eq 'exitFailure|AutowareError|Failed|failure|timeout|timed out' "$log_file"; then
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
    return {
        "variant": variant,
        "episode": episode,
        "attempt": status.get("attempt", ""),
        "result": status["result"],
        "exit_code": status["exit_code"],
        "success": metrics.get("success", ""),
        "collision": metrics.get("collision", ""),
        "timeout": metrics.get("timeout", ""),
        "distance_m": metrics.get("distance_m", ""),
        "mean_speed_kmh": metrics.get("mean_speed_kmh", ""),
        "driving_score": metrics.get("driving_score", ""),
        "step_rows": str(step_count(status)),
    }

def write_tsv(path, fields, rows):
    lines = ["\t".join(fields)]
    for row in rows:
        lines.append("\t".join(str(row.get(field, "")) for field in fields))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

def as_float(value):
    try:
        return float(value)
    except ValueError:
        return None

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
        "mean_attempts": f"{(sum(item for item in attempts if item is not None) / len(rows)):.4f}" if rows else "0.0000",
        "mean_distance_m": f"{(sum(item for item in distances if item is not None) / len(passed)):.4f}" if passed else "0.0000",
        "mean_speed_kmh": f"{(sum(item for item in speeds if item is not None) / len(passed)):.4f}" if passed else "0.0000",
        "mean_driving_score": f"{(sum(item for item in scores if item is not None) / len(passed)):.4f}" if passed else "0.0000",
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

if (( SCENARIO_BASE_ROS_DOMAIN_ID < 0 )); then
  echo "SCENARIO_BASE_ROS_DOMAIN_ID must be >= 0, got $SCENARIO_BASE_ROS_DOMAIN_ID" >&2
  exit 2
fi
if (( SCENARIO_MAX_ROS_DOMAIN_ID > 232 )); then
  echo "SCENARIO_MAX_ROS_DOMAIN_ID must be <= 232 for FastDDS UDP port math, got $SCENARIO_MAX_ROS_DOMAIN_ID" >&2
  exit 2
fi
if (( SCENARIO_BASE_ROS_DOMAIN_ID > SCENARIO_MAX_ROS_DOMAIN_ID )); then
  echo "SCENARIO_BASE_ROS_DOMAIN_ID must be <= SCENARIO_MAX_ROS_DOMAIN_ID, got $SCENARIO_BASE_ROS_DOMAIN_ID > $SCENARIO_MAX_ROS_DOMAIN_ID" >&2
  exit 2
fi

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
      if [[ "$ISOLATE_SCENARIO_PORT" == "1" || "$ISOLATE_SCENARIO_PORT" == "true" ]]; then
        RUN_SCENARIO_PORT=$((SCENARIO_BASE_PORT + run_index - 1))
      fi
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
    if [[ "$START_EMPTY_SIM_INPUTS" == "1" || "$START_EMPTY_SIM_INPUTS" == "true" ]]; then
      publish_perception=1
    fi
    if [[ "$START_EMERGENCY_HEARTBEAT" == "1" || "$START_EMERGENCY_HEARTBEAT" == "true" ]]; then
      publish_emergency=1
    fi
    if [[ "$START_MRM_HEARTBEAT" == "1" || "$START_MRM_HEARTBEAT" == "true" ]]; then
      publish_mrm_state=1
    fi
    if [[ "$publish_perception" == "1" || "$publish_emergency" == "1" || "$publish_mrm_state" == "1" ]]; then
      export AWSIM_EMPTY_PUBLISH_PERCEPTION="$publish_perception"
      export AWSIM_EMPTY_PUBLISH_EMERGENCY="$publish_emergency"
      export AWSIM_EMPTY_PUBLISH_MRM_STATE="$publish_mrm_state"
      export AWSIM_EMPTY_INPUT_PERIOD_S="${AWSIM_EMPTY_INPUT_PERIOD_S:-0.05}"
      start_helper empty_sim_inputs "$HELPER_DIR/empty_sim_inputs.py" \
        "$HELPER_LOG_DIR/empty_sim_inputs.log"
    fi
    start_helper episode_metric_monitor "$HELPER_DIR/episode_metric_monitor.py" \
      "$HELPER_LOG_DIR/episode_metric_monitor.log"
    if [[ "$variant" != "baseline" || "$START_BASELINE_PLANNER" == "1" || "$START_BASELINE_PLANNER" == "true" ]]; then
      start_helper utmr_planner_node "$HELPER_DIR/utmr_planner_node.py" \
        "$HELPER_LOG_DIR/utmr_planner_node.log"
    fi

    ros2 launch scenario_test_runner scenario_test_runner.launch.py \
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
    if [[ "$STABILIZE_CMD_GATE" == "1" || "$STABILIZE_CMD_GATE" == "true" ]]; then
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
        echo "wall-clock timeout: ${elapsed}s > ${wall_limit}s"
        kill -TERM "$scenario_pid" 2>/dev/null || true
        sleep 8
        kill -KILL "$scenario_pid" 2>/dev/null || true
        break
      fi
      draw_bar "$elapsed" "$wall_limit" "$variant/$episode/a$attempt"
      sleep 2
    done
    set +e
    wait "$scenario_pid"
    exit_code="$?"
    set -e
    if [[ "$wall_timed_out" == "1" && "$exit_code" == "0" ]]; then
      exit_code=124
    fi
    draw_bar "$wall_limit" "$wall_limit" "$variant/$episode/a$attempt"
    echo

    cleanup_helpers
    result="$(classify_result "$RUN_LOG" "$exit_code")"
    printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" \
      "$variant" "$episode" "$attempt" "$result" "$exit_code" "$RUN_LOG" "$STEP_LOG" "$EPISODE_ID" >>"$STATUS_TSV"
    echo "result=$result exit=$exit_code attempt=$attempt"
    if [[ "$PRINT_LOG_TAIL" == "1" || "$PRINT_LOG_TAIL" == "true" ]]; then
      rg -n "Passed|exitSuccess|exitFailure|AutowareError|AutowareState|Route set|collision|timeout" "$RUN_LOG" | tail -80 || true
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
find "$ROOT" -xdev -type l -name .codegraph -delete 2>/dev/null || true
echo "symlinks under UTMR: $(find "$ROOT" -xdev -type l | wc -l)"
