#!/usr/bin/env bash
# Build the two sandbox images (Step 14a/14b) with one command, identically on
# macOS/colima and native-Linux Docker — `docker build` is runtime-agnostic, so
# there is no OS branch here. Run it per-host: the image is built for the host's
# arch (arm64 on the Mac, amd64 on spitfire) from the SAME Dockerfile.
#
# The version defaults MUST match the pinned tags in
# src/hangar/evals/drivers/sandbox.py (ANCHOR_IMAGE / OPENCODE_IMAGE) and the
# host CLI versions. Override to bump:  ANCHOR_VERSION=2.1.213 ./containers/build.sh
#
# Usage:
#   ./containers/build.sh            # build both
#   ./containers/build.sh anchor     # anchor only  (needs no local model)
#   ./containers/build.sh opencode   # opencode only (local-LLM arm)
set -euo pipefail

ANCHOR_VERSION="${ANCHOR_VERSION:-2.1.212}"
OPENCODE_VERSION="${OPENCODE_VERSION:-1.17.5}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CTX="$REPO_ROOT/containers"

build_anchor() {
  echo ">> building hangar-harness:anchor-${ANCHOR_VERSION}"
  docker build -t "hangar-harness:anchor-${ANCHOR_VERSION}" \
    --build-arg "CLAUDE_CODE_VERSION=${ANCHOR_VERSION}" \
    -f "$CTX/anchor.Dockerfile" "$CTX"
}

build_opencode() {
  echo ">> building hangar-harness:opencode-${OPENCODE_VERSION}"
  docker build -t "hangar-harness:opencode-${OPENCODE_VERSION}" \
    --build-arg "OPENCODE_VERSION=${OPENCODE_VERSION}" \
    -f "$CTX/opencode.Dockerfile" "$CTX"
}

case "${1:-both}" in
  anchor)   build_anchor ;;
  opencode) build_opencode ;;
  both)     build_anchor; build_opencode ;;
  *) echo "usage: $0 [anchor|opencode|both]" >&2; exit 2 ;;
esac
echo ">> done."
