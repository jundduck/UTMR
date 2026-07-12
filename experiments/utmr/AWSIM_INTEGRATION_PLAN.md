# AWSIM integration plan

The runnable implementation now has a local high-speed closed-loop backend:

- K=64 cubic-spline candidate generation
- coarse scoring with `Tc=2.0`, `dtc=0.20`
- fine scoring schedules for UTMR, Uniform Fine, and ablations
- coarse collision/TTC feasibility filtering
- step JSONL and episode CSV logs
- paper reducers for all reported figures/tables
- ROS2 UTMR planner node that publishes `/planning/trajectory`
- AWSIM/Autoware process supervisor wrapper
- scenario-file driven AWSIM route/localization setup
- AWSIM batch runner for baseline, UTMR, Uniform Fine, and ablations
- `/utmr/collision` bridge from ego/object proximity when a simulator collision topic is not available

To attach the same experiment to AWSIM/Autoware, keep the current log schema and replace only the backend that advances the world.

## Adapter boundary

The AWSIM adapter should emit the same files as `closed_loop_runner.py`:

- `raw/closed_loop_steps.jsonl`
- `raw/closed_loop_episodes.csv`

Each planning step should include:

- ego speed
- candidate speeds and coarse scores
- coarse feasibility mask or collision/TTC arrays
- fine scores for full/fine-dt/short-horizon schedules when available
- selected index, baseline index, trigger flag
- wall-clock planning latency

## ROS2 planner node

`autoware/utmr_scripts/helpers/utmr_planner_node.py` wraps `utmr_core.py` and already:

1. Subscribes to ego state from `/localization/kinematic_state`.
2. Optionally subscribes to object topics with `PredictedObjects`, `TrackedObjects`, or `DetectedObjects`.
3. Converts object poses into ego-frame obstacles for scoring, including map-frame and ego-frame obstacle inputs.
4. Generates K=64 candidates each planning tick.
5. Scores coarse and fine schedules through `utmr_core.py`.
6. Publishes the selected trajectory as `autoware_planning_msgs/Trajectory` on `/planning/trajectory`.
7. Appends one JSONL step row per tick when `UTMR_STEP_LOG` is set.

The remaining planner-node work is choosing the best live object topic for the active AWSIM/Autoware launch and validating obstacle frame assumptions.

## Episode supervisor

`experiments/utmr/awsim_supervisor.py` now:

1. Starts AWSIM with `autoware/utmr_scripts/run_awsim.sh`.
2. Starts Autoware with `autoware/utmr_scripts/launch_autoware_e2e.sh`.
3. Starts the UTMR helper stack with one variant mode.
4. Loads optional scenario JSON and exports localization pose, goal pose, route length, and static obstacles.
5. Starts `episode_metric_monitor.py`.
6. Writes process logs, step logs, and episode metrics.

The metric monitor already subscribes to `/localization/kinematic_state`, `/api/routing/state`, and a configurable `std_msgs/Bool` collision topic. By default the helper stack publishes `/utmr/collision` from object proximity; set `UTMR_COLLISION_TOPIC` to a real AWSIM collision topic if one is available.

## Scenarios

Use `experiments/utmr/scenarios/high_speed_sample.json` for local closed-loop scenarios and `experiments/utmr/scenarios/awsim_shinjuku_sample.json` for AWSIM route setup. AWSIM scenarios can include:

- localization pose
- goal pose
- expected route length
- timeout
- optional static obstacles in `ego` or `map` frame
- scenario-specific obstacle/traffic setup if AWSIM exposes it later

Run the live batch with:

```bash
experiments/utmr/run_awsim_batch.sh \
  --out-dir experiments/utmr/results/awsim_batch \
  --scenario-file experiments/utmr/scenarios/awsim_shinjuku_sample.json \
  --variants baseline utmr uniform_fine fine_dt_only short_horizon_only \
  --episodes 5 \
  --timeout-s 120
```

## Next implementation step

Identify the active AWSIM object topic in a running session, then set `UTMR_OBJECTS_TOPIC` and `UTMR_OBJECTS_MSG_TYPE` for the batch. If AWSIM exposes a better collision signal, set `UTMR_COLLISION_TOPIC`; otherwise keep the `/utmr/collision` bridge.
