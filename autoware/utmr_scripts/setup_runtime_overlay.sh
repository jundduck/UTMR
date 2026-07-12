#!/usr/bin/env bash

# Runtime dependencies restored locally for this copied Autoware workspace.
# Source this after /opt/ros/humble/setup.bash and before autoware/install/setup.bash.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UTMR_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

USER_ROS_OVERLAY="${USER_ROS_OVERLAY:-$UTMR_ROOT/runtime/ros-humble-missing/root/opt/ros/humble}"
USER_USR_OVERLAY="${USER_USR_OVERLAY:-$UTMR_ROOT/runtime/ros-humble-missing/root/usr}"
ACADOS_PREFIX="${ACADOS_PREFIX:-$UTMR_ROOT/runtime/acados/install}"
ACADOS_SOURCE_DIR="${ACADOS_SOURCE_DIR:-$UTMR_ROOT/runtime/acados/src}"
UTMR_PYTHON_PACKAGES="${UTMR_PYTHON_PACKAGES:-$UTMR_ROOT/runtime/python-packages}"
UTMR_RUNTIME_BIN="${UTMR_RUNTIME_BIN:-$UTMR_ROOT/runtime/bin}"

if [ -d "$UTMR_RUNTIME_BIN" ]; then
  export PATH="$UTMR_RUNTIME_BIN:$PATH"
fi

if [ -d "$UTMR_PYTHON_PACKAGES" ]; then
  export PYTHONPATH="$UTMR_PYTHON_PACKAGES:${PYTHONPATH:-}"
fi

if [ -d "$USER_ROS_OVERLAY" ]; then
  export PATH="$USER_ROS_OVERLAY/bin:$PATH"
  export AMENT_PREFIX_PATH="$USER_ROS_OVERLAY:${AMENT_PREFIX_PATH:-}"
  export CMAKE_PREFIX_PATH="$USER_ROS_OVERLAY:${CMAKE_PREFIX_PATH:-}"
  export LD_LIBRARY_PATH="$USER_ROS_OVERLAY/lib:$USER_ROS_OVERLAY/lib/x86_64-linux-gnu:${LD_LIBRARY_PATH:-}"
  export PYTHONPATH="$USER_ROS_OVERLAY/local/lib/python3.10/dist-packages:$USER_ROS_OVERLAY/lib/python3.10/site-packages:${PYTHONPATH:-}"
fi

if [ -d "$USER_USR_OVERLAY" ]; then
  export LD_LIBRARY_PATH="$USER_USR_OVERLAY/lib/x86_64-linux-gnu:${LD_LIBRARY_PATH:-}"
fi

if [ -d "$USER_ROS_OVERLAY/opt/zmqpp_vendor/lib" ]; then
  export LD_LIBRARY_PATH="$USER_ROS_OVERLAY/opt/zmqpp_vendor/lib:${LD_LIBRARY_PATH:-}"
fi

if [ -d "$ACADOS_PREFIX" ]; then
  export CMAKE_PREFIX_PATH="$ACADOS_PREFIX:${CMAKE_PREFIX_PATH:-}"
  export LD_LIBRARY_PATH="$ACADOS_PREFIX/lib:${LD_LIBRARY_PATH:-}"
fi

if [ -d "$ACADOS_SOURCE_DIR" ]; then
  export ACADOS_SOURCE_DIR
fi
