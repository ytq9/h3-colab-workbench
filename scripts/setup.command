#!/bin/bash
set -euo pipefail
task_root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$task_root"
if ! command -v python3.12 >/dev/null 2>&1; then
  echo "请先安装 Python 3.12，再重新运行本文件。"
  exit 1
fi
if [ ! -x .venv/bin/python ]; then
  python3.12 -m venv .venv
fi
.venv/bin/python -m pip install -r requirements/colab-cli.txt
.venv/bin/python -m pip install --no-deps .
if ! .venv/bin/python -c 'from h3_workbench.config import config_path; raise SystemExit(not config_path().is_file())'; then
  read -r -p "有 Colab 额度的 Google 邮箱: " task_email
  read -r -p "保存目录（留空使用 ~/Movies/H3Workbench）: " task_project
  .venv/bin/h3-workbench init --email "$task_email" --project-dir "${task_project:-$HOME/Movies/H3Workbench}"
fi
.venv/bin/h3-workbench install
.venv/bin/h3-workbench start
