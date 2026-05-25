#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -x "$ROOT_DIR/.venv312/bin/python" ]]; then
  exec "$ROOT_DIR/.venv312/bin/python" "$@"
fi

if [[ -x "/opt/homebrew/bin/python3.12" ]]; then
  exec "/opt/homebrew/bin/python3.12" "$@"
fi

exec python3 "$@"
