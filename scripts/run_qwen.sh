#!/usr/bin/env bash
# Shim — the qwen arm is now a campaign spec (campaigns/qwen.json) driven by
# scripts/evals, which prints results as they land, keeps a live table, and
# re-renders the paper tables when it finishes. See campaigns/README.md.
exec "$(dirname "$0")/evals" run qwen "$@"
