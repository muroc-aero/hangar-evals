# shared helpers for the Lane C publication run scripts.
# sourced by run_anchor.sh / run_gemma.sh / run_qwen_chunk*.sh — not run directly.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# the-hangar project env supplies Lane A references (computed in-process);
# override with HANGAR_PROJECT=/path if it is not the sibling default.
: "${HANGAR_PROJECT:=../the-hangar}"
UV_PY=(uv run --project "$HANGAR_PROJECT" --with-editable ".[anchor]" python)
RUN=("${UV_PY[@]}" -m hangar.evals.run)

# The model every anchor liveness probe exercises. Keep in step with
# configs/lane_c_anchor/*.json: the probe must hit the SAME model the cases
# will, so a model this account cannot reach fails here and not in hour three.
: "${ANCHOR_MODEL:=claude-opus-5}"

LOG_DIR="$REPO_ROOT/results/diagnostics"
mkdir -p "$LOG_DIR"
_ts() { date +%Y-%m-%dT%H:%M:%S; }

check_container_runtime() {
  if ! docker info >/dev/null 2>&1; then
    echo "!! container runtime unreachable — start it first (macOS: 'colima start'; Linux: 'sudo systemctl start docker')." >&2
    return 1
  fi
}

# Anything printed from a failed probe may carry the echoed Authorization
# header, so it goes through here first (the thorough, fold-aware version lives
# in drivers/claude_cli.py — sed is line-based, but a lone fold tail is inert).
_redact() { sed -E 's/sk-ant-[A-Za-z0-9_-]+/<redacted>/g'; }

# The pinned anchor image, read from the driver so bash can never drift from it.
anchor_image() {
  if [ -z "${_ANCHOR_IMAGE:-}" ]; then
    _ANCHOR_IMAGE="$("${UV_PY[@]}" -c \
      'from hangar.evals.drivers.sandbox import ANCHOR_IMAGE; print(ANCHOR_IMAGE)' \
      2>/dev/null | tail -n 1)"
  fi
  [ -n "${_ANCHOR_IMAGE:-}" ] || return 1
  printf '%s' "$_ANCHOR_IMAGE"
}

# probe_anchor_auth — ONE live turn in the anchor image (seconds, negligible
# usage). Catches the two failures that otherwise convert a whole bundle into
# error rows without ever failing loudly: a stale/rotated token, and an
# exhausted plan window. `run_bundle` calls it between cases too, so a window
# that closes mid-bundle halts the run instead of burning the remaining cases.
probe_anchor_auth() {
  local image out rc
  image="$(anchor_image)" || {
    echo "!! could not resolve the anchor image from the driver." >&2; return 1; }
  local -a limit=()
  if command -v timeout >/dev/null 2>&1; then limit=(timeout 120)
  elif command -v gtimeout >/dev/null 2>&1; then limit=(gtimeout 120); fi
  out="$("${limit[@]}" docker run --rm -e CLAUDE_CODE_OAUTH_TOKEN "$image" \
         claude -p 'reply with OK' --model "$ANCHOR_MODEL" \
         --max-turns 1 --setting-sources "" 2>&1)"
  rc=$?
  # The CLI exits 0 on an API error and reports it in-band, so check both.
  if [ $rc -ne 0 ] || printf '%s' "$out" \
       | grep -qiE 'api error|invalid|expired|usage limit|rate limit'; then
    echo "!! anchor auth probe FAILED (exit $rc):" >&2
    printf '%s\n' "$out" | _redact | tail -n 5 | sed 's/^/     | /' >&2
    return 1
  fi
}

check_anchor_auth() {
  if [ -z "${CLAUDE_CODE_OAUTH_TOKEN:-}" ]; then
    echo "!! CLAUDE_CODE_OAUTH_TOKEN is unset — the in-container anchor refuses to start." >&2
    echo "   Run 'claude setup-token' once, then store it in 1Password and launch with" >&2
    echo "   'op run --env-file=op.env -- bash scripts/run_anchor.sh' (one unlock, whole bundle)." >&2
    return 1
  fi
  # A non-empty token is not a working token — spend seconds proving it here
  # rather than hours discovering it case by case.
  echo "== anchor auth probe ($ANCHOR_MODEL) — $(_ts)"
  probe_anchor_auth || return 1
  echo "   probe OK"
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
    # Between cases: re-prove auth so a plan window that closes mid-bundle
    # halts here, leaving the remaining cases untouched and resumable, instead
    # of silently filling their seeds with error rows. Opt-in per bundle
    # (PRECASE_CHECK) — the local-model arms have no such credential.
    if [ -n "${PRECASE_CHECK:-}" ] && ! "$PRECASE_CHECK"; then
      echo "=== '$name' HALTED before $case_name — $(_ts)"
      echo "    $((total - i + 1)) case(s) not started; nothing was burned."
      echo "    Refresh the token or wait for the usage window, then re-run the"
      echo "    same command — completed cases are skipped automatically."
      echo "    Cases interrupted mid-flight resume with:"
      echo "      ${RUN[*]} --resume results/<case>_<stamp>.jsonl"
      return 1
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
