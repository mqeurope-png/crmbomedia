#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_PYTHON="$ROOT_DIR/backend/.venv/bin/python"
BACKEND_RUFF="$ROOT_DIR/backend/.venv/bin/ruff"
FAILURES=0

if [ ! -x "$BACKEND_PYTHON" ]; then
  BACKEND_PYTHON="python"
fi

if [ ! -x "$BACKEND_RUFF" ]; then
  BACKEND_RUFF="ruff"
fi

section() {
  printf '\n\033[1;34m==> %s\033[0m\n' "$1"
}

run_step() {
  local title="$1"
  shift
  section "$title"
  if "$@"; then
    printf '\033[1;32m✓ %s\033[0m\n' "$title"
  else
    local exit_code=$?
    printf '\033[1;31m✗ %s failed with exit code %s\033[0m\n' "$title" "$exit_code" >&2
    FAILURES=$((FAILURES + 1))
  fi
}

run_step "Backend ruff" "$BACKEND_RUFF" check "$ROOT_DIR/backend"
run_step "Backend compileall" "$BACKEND_PYTHON" -m compileall "$ROOT_DIR/backend/app" "$ROOT_DIR/backend/tests"
run_step "Backend pytest" bash -lc "cd '$ROOT_DIR/backend' && '$BACKEND_PYTHON' -m pytest"
run_step "Frontend build" bash -lc "cd '$ROOT_DIR/frontend' && npm run build"
run_step "Frontend lint" bash -lc "cd '$ROOT_DIR/frontend' && npm run lint"

section "Summary"
if [ "$FAILURES" -gt 0 ]; then
  printf '%s check(s) failed. See output above.\n' "$FAILURES" >&2
  exit 1
fi

printf 'All checks passed.\n'
