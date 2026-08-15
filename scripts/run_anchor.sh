#!/usr/bin/env bash
# Anchor arm (Claude Opus, 3 seeds) — ~2.4h wall, draws Max-plan usage.
# One bundle (under the ~3h target). hybrid_twin runs to the cap, kept last.
# For tighter usage pacing, run single cases:
#   uv run --project ../the-hangar --with-editable .[anchor] \
#     python -m hangar.evals.run --config configs/lane_c_anchor/<case>.json
source "$(dirname "$0")/_run_lib.sh"
check_container_runtime || exit 1
check_anchor_auth || exit 1

run_bundle anchor \
  configs/lane_c_anchor/ocp_caravan_basic.json \
  configs/lane_c_anchor/evt_native_sizing.json \
  configs/lane_c_anchor/ocp_oas_coupled.json \
  configs/lane_c_anchor/ocp_caravan_full.json \
  configs/lane_c_anchor/ocp_oas_direct.json \
  configs/lane_c_anchor/paraboloid.json \
  configs/lane_c_anchor/oas_aero_rect.json \
  configs/lane_c_anchor/pyc_turbojet.json \
  configs/lane_c_anchor/oas_ocp_combined.json \
  configs/lane_c_anchor/oas_aerostruct_rect.json \
  configs/lane_c_anchor/ocp_hybrid_twin.json

