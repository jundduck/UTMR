#!/usr/bin/env bash
set -e

export FASTDDS_BUILTIN_TRANSPORTS="${FASTDDS_BUILTIN_TRANSPORTS:-UDPv4}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UTMR_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
AWSIM_DIR="${AWSIM_DIR:-$UTMR_ROOT/AWSIM-Demo}"

source /opt/ros/humble/setup.bash
cd "$AWSIM_DIR"
./AWSIM-Demo.x86_64
