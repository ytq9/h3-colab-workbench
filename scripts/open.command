#!/bin/bash
set -euo pipefail
task_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$task_root"
if [ ! -x .venv/bin/h3-workbench ]; then
  echo "请先运行 scripts/setup.command。"
  exit 1
fi
exec .venv/bin/h3-workbench start
