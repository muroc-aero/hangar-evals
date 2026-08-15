#!/usr/bin/env bash
# qwen local arm (qwen3.6:35b-mlx, 5 seeds) — CHUNK 2 of 2, ~4-5h wall, free.
source "$(dirname "$0")/_run_lib.sh"
check_container_runtime || exit 1

run_bundle qwen_chunk2 \
  configs/lane_c_qwen/evt_native_sizing.json \
  configs/lane_c_qwen/oas_ocp_combined.json \
  configs/lane_c_qwen/oas_aero_rect.json \
  configs/lane_c_qwen/ocp_caravan_basic.json \
  configs/lane_c_qwen/ocp_hybrid_twin.json

