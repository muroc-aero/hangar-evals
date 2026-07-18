# The Claude-anchor sandbox image (Step 14a): node + the pinned Claude Code
# CLI and nothing else. No python, no hangar code, no ~/.claude state — the
# container starts clean (threat (d) closes structurally) and auth arrives at
# runtime as the CLAUDE_CODE_OAUTH_TOKEN env var (minted by `claude setup-token`).
#
# Build:  docker build -t hangar-harness:anchor-2.1.212 \
#           --build-arg CLAUDE_CODE_VERSION=2.1.212 -f containers/anchor.Dockerfile containers
# Keep the tag's version suffix == CLAUDE_CODE_VERSION == the host CLI version.
FROM node:22-slim

ARG CLAUDE_CODE_VERSION=2.1.212
RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}

# Non-root: Claude Code refuses permission-bypass as root, and workspace files
# written by the agent shouldn't come back root-owned on the virtiofs mount.
USER node
ENV HOME=/home/node
WORKDIR /workspace
