#!/usr/bin/env bash
# Shim — the anchor arm is now a campaign spec (campaigns/anchor.json) driven by
# scripts/evals, which prints results as they land, keeps a live table, and
# re-renders the paper tables when it finishes. See campaigns/README.md.
exec "$(dirname "$0")/evals" run anchor "$@"
