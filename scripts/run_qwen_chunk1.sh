#!/usr/bin/env bash
# qwen local arm (qwen3.6:35b-mlx, 5 seeds) — CHUNK 1 of 2, ~4-5h wall, free.
# Split because a 35B model at 5 seeds likely exceeds 8h for the full suite.
source "$(dirname "$0")/_run_lib.sh"
check_container_runtime || exit 1

run_bundle qwen_chunk1 \
  configs/lane_c_qwen/ocp_caravan_full.json \
  configs/lane_c_qwen/paraboloid.json \
  configs/lane_c_qwen/ocp_oas_direct.json \
  configs/lane_c_qwen/pyc_turbojet.json \
  configs/lane_c_qwen/ocp_oas_coupled.json \
  configs/lane_c_qwen/oas_aerostruct_rect.json

