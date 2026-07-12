# UTMR AWSIM + Autoware Runbook

## Folder Structure

```text
/home/yax/UTMR/
  AWSIM-Demo/
  autoware/
    autoware_data/
    utmr_scripts/
```

`autoware_data` is stored only inside `autoware`.

## Run

Terminal 1:

```bash
cd /home/yax/UTMR
./autoware/utmr_scripts/run_awsim.sh
```

Terminal 2:

```bash
cd /home/yax/UTMR
./autoware/utmr_scripts/launch_autoware_e2e.sh
```

The default launch is the quiet AWSIM straight-drive demo mode. It disables
RViz, perception, and Autoware planning modules because this copied workspace
does not have a healthy TensorRT/CUDA perception stack.

To run the full stack anyway:

```bash
cd /home/yax/UTMR
RVIZ=true PERCEPTION=true PLANNING=true ./autoware/utmr_scripts/launch_autoware_e2e.sh
```

Terminal 3, after Autoware topics appear:

```bash
cd /home/yax/UTMR
./autoware/utmr_scripts/run_straight_demo.sh
```

## Paths Used By Scripts

```text
AWSIM_DIR=/home/yax/UTMR/AWSIM-Demo
AUTOWARE_DIR=/home/yax/UTMR/autoware
AUTOWARE_DATA_DIR=/home/yax/UTMR/autoware/autoware_data
MAP_PATH=/home/yax/UTMR/AWSIM-Demo/Shinjuku-Map/map
DATA_PATH=/home/yax/UTMR/autoware/autoware_data/ml_models
```

The scripts still allow overrides by exporting those variables before running them.
