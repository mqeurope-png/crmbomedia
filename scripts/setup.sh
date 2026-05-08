#!/usr/bin/env bash
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_VENV="$ROOT_DIR/backend/.venv"
PYTHON_BIN="${PYTHON:-python}"
FAILURES=0

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

section "Repository"
printf 'Using project root: %s\n' "$ROOT_DIR"

if [ ! -d "$BACKEND_VENV" ]; then
  run_step "Create backend virtualenv" "$PYTHON_BIN" -m venv "$BACKEND_VENV"
else
  section "Create backend virtualenv"
  printf 'Backend virtualenv already exists: %s\n' "$BACKEND_VENV"
fi

if [ -x "$BACKEND_VENV/bin/python" ]; then
  run_step "Upgrade backend pip" "$BACKEND_VENV/bin/python" -m pip install --upgrade pip
  run_step "Install backend dev dependencies" \
    "$BACKEND_VENV/bin/python" -m pip install -r "$ROOT_DIR/backend/requirements-dev.txt"
else
  printf 'Backend virtualenv python is missing; skipping backend dependency install.\n' >&2
  FAILURES=$((FAILURES + 1))
fi

run_step "Install frontend npm dependencies" bash -lc "cd '$ROOT_DIR/frontend' && npm install"

section "Next steps"
printf '1. Run ./scripts/dev-check.sh\n'
printf '2. Run docker compose up --build\n'
printf '3. Open http://localhost:3000 and http://localhost:8000/docs\n'

if [ "$FAILURES" -gt 0 ]; then
  printf '\nSetup completed with %s failure(s). See output above.\n' "$FAILURES" >&2
  exit 1
fi

printf '\nSetup completed successfully.\n'
