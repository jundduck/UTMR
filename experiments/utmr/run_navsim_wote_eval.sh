#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UTMR_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
WOTE_ROOT="$UTMR_ROOT/third_party/WoTE"
PY_SITE="$UTMR_ROOT/runtime/python-packages"

MODE="${MODE:-utmr}"
CONFIG_NAME="${CONFIG_NAME:-default}"
NUM_TRAJ_ANCHOR="${NUM_TRAJ_ANCHOR:-256}"
CHECKPOINT_PATH="${WOTE_CHECKPOINT_PATH:-$WOTE_ROOT/exp/WoTE/$CONFIG_NAME/lightning_logs/version_0/checkpoints/epoch=29-step=19950.ckpt}"

if [[ ! -f "$CHECKPOINT_PATH" ]]; then
  echo "missing checkpoint: $CHECKPOINT_PATH" >&2
  echo "Set WOTE_CHECKPOINT_PATH or place the checkpoint inside $WOTE_ROOT/exp/WoTE/$CONFIG_NAME/..." >&2
  exit 2
fi

common_args=(
  "experiment_name=eval/WoTE/${CONFIG_NAME}_${MODE}"
  "+agent.config.num_traj_anchor=$NUM_TRAJ_ANCHOR"
  "+agent.config.cluster_file_path=$WOTE_ROOT/dataset/extra_data/planning_vb/trajectory_anchors_${NUM_TRAJ_ANCHOR}.npy"
  "+agent.config.sim_reward_dict_path=$WOTE_ROOT/dataset/extra_data/planning_vb/formatted_pdm_score_${NUM_TRAJ_ANCHOR}.npy"
)

case "$MODE" in
  baseline)
    mode_args=("+agent.config.use_utmr=false")
    ;;
  utmr)
    mode_args=(
      "+agent.config.use_utmr=true"
      "+agent.config.utmr_beta=${UTMR_BETA:-1.0}"
      "+agent.config.utmr_gamma_h=${UTMR_GAMMA_H:-0.75}"
      "+agent.config.utmr_gamma_m=${UTMR_GAMMA_M:-0.05}"
      "+agent.config.utmr_top_n=${UTMR_TOP_N:-8}"
      "+agent.config.utmr_min_ttc_score=${UTMR_MIN_TTC_SCORE:-0.5}"
      "+agent.config.utmr_min_nc=${UTMR_MIN_NC:-0.5}"
      "+agent.config.utmr_fine_im_weight=${UTMR_FINE_IM_WEIGHT:-0.0}"
      "+agent.config.utmr_fine_nc_weight=${UTMR_FINE_NC_WEIGHT:-1.0}"
      "+agent.config.utmr_fine_dac_weight=${UTMR_FINE_DAC_WEIGHT:-1.0}"
      "+agent.config.utmr_fine_ep_weight=${UTMR_FINE_EP_WEIGHT:-0.5}"
      "+agent.config.utmr_fine_ttc_weight=${UTMR_FINE_TTC_WEIGHT:-1.0}"
      "+agent.config.utmr_fine_comfort_weight=${UTMR_FINE_COMFORT_WEIGHT:-0.5}"
      "+agent.config.utmr_fine_margin_min=${UTMR_FINE_MARGIN_MIN:-0.0}"
      "+agent.config.utmr_max_coarse_drop=${UTMR_MAX_COARSE_DROP:-1000000000.0}"
    )
    ;;
  *)
    echo "MODE must be baseline or utmr, got: $MODE" >&2
    exit 2
    ;;
esac

export WOTE_CHECKPOINT_PATH="$CHECKPOINT_PATH"
export PYTHONPATH="$PY_SITE:${PYTHONPATH:-}"
export UTMR_WOTE_METHOD="${UTMR_WOTE_METHOD:-$MODE}"

"$WOTE_ROOT/scripts/evaluation/eval_wote.sh" "${common_args[@]}" "${mode_args[@]}" "$@"
