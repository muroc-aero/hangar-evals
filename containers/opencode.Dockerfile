# The OpenCode sandbox image (Step 14b): node + the pinned OpenCode CLI and
# nothing else. No python, no hangar code, no ~/.config/opencode state — the
# per-run opencode.json in the mounted workspace is the ONLY config the CLI
# finds, and the local arm needs no auth token at all.
#
# Build:  docker build -t hangar-harness:opencode-1.17.5 \
#           --build-arg OPENCODE_VERSION=1.17.5 -f containers/opencode.Dockerfile containers
# Keep the tag's version suffix == OPENCODE_VERSION == the host brew binary.
FROM node:22-slim

ARG OPENCODE_VERSION=1.17.5
RUN npm install -g opencode-ai@${OPENCODE_VERSION}

# Non-root: workspace files written by the agent shouldn't come back
# root-owned on the virtiofs mount (same discipline as the anchor image).
USER node
ENV HOME=/home/node
WORKDIR /workspace
