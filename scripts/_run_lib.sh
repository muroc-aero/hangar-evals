# shared helpers for the Lane C publication run scripts.
# sourced by run_anchor.sh / run_gemma.sh / run_qwen_chunk*.sh — not run directly.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# the-hangar project env supplies Lane A references (computed in-process);
# override with HANGAR_PROJECT=/path if it is not the sibling default.
: "${HANGAR_PROJECT:=../the-hangar}"
RUN=(uv run --project "$HANGAR_PROJECT" --with-editable ".[anchor]" python -m hangar.evals.run)

LOG_DIR="$REPO_ROOT/results/diagnostics"
mkdir -p "$LOG_DIR"
_ts() { date +%Y-%m-%dT%H:%M:%S; }

check_container_runtime() {
  if ! docker info >/dev/null 2>&1; then
    echo "!! container runtime unreachable — start it first (macOS: 'colima start'; Linux: 'sudo systemctl start docker')." >&2
    return 1
  fi
}

check_anchor_auth() {
  if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
    echo "!! CLAUDE_CODE_OAUTH_TOKEN is unset — the in-container anchor refuses to start." >&2
    echo "   Run 'claude setup-token' once, then export it in this shell." >&2
    return 1
  fi
}

# run_bundle <name> <config.json> [config.json ...]
run_bundle() {
  local name="$1"; shift
  local total=$# i=0 n_ran=0 n_skip=0 n_fail=0
  local log="$LOG_DIR/${name}_$(date +%Y%m%dT%H%M%SZ).log"
  echo "=== bundle '$name': $total cases — started $(_ts) ==="
  echo "    per-case output -> $log"
  for cfg in "$@"; do
    i=$((i+1))
    local case_name; case_name="$(basename "$cfg" .json)"
    if python "$REPO_ROOT/scripts/_done.py" "$cfg" >/dev/null 2>&1; then
      echo "[$i/$total] SKIP  $case_name (already complete)"
      n_skip=$((n_skip+1)); continue
    fi
    echo "[$i/$total] START $case_name — $(_ts)"
    "${RUN[@]}" --config "$cfg" >>"$log" 2>&1
    local rc=$?
    if [ $rc -eq 0 ]; then
      echo "[$i/$total] DONE  $case_name — $(_ts)"
      n_ran=$((n_ran+1))
    else
      echo "[$i/$total] FAIL  $case_name (exit $rc) — tail of $log:"
      tail -n 15 "$log" | sed 's/^/      | /'
      n_fail=$((n_fail+1))
    fi
  done
  echo "=== '$name' done: $n_ran ran, $n_skip skipped, $n_fail failed of $total — $(_ts) ==="
  echo "    aggregate with: python -m hangar.evals.aggregate  (reads results/)"
}
