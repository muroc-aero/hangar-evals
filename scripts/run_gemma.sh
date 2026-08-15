#!/usr/bin/env bash
# gemma local arm (gemma4:26b-mlx, 5 seeds) — ~7h wall, free (on-device).
# One script (under the 8h split threshold, but near it: budget an overnight).
source "$(dirname "$0")/_run_lib.sh"
check_container_runtime || exit 1

run_bundle gemma \
  configs/lane_c_gemma/ocp_oas_coupled.json \
  configs/lane_c_gemma/pyc_turbojet.json \
  configs/lane_c_gemma/oas_aerostruct_rect.json \
  configs/lane_c_gemma/ocp_hybrid_twin.json \
  configs/lane_c_gemma/ocp_caravan_basic.json \
  configs/lane_c_gemma/oas_aero_rect.json \
  configs/lane_c_gemma/oas_ocp_combined.json \
  configs/lane_c_gemma/ocp_oas_direct.json \
  configs/lane_c_gemma/paraboloid.json \
  configs/lane_c_gemma/evt_native_sizing.json \
  configs/lane_c_gemma/ocp_caravan_full.json

