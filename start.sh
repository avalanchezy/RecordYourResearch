#!/bin/sh
# RecordYourResearch - macOS / Linux launcher
# First run: creates .venv and installs requirements. If an install failed or requirements.txt changed, it re-installs.
cd "$(dirname "$0")" || exit 1
VPY=.venv/bin/python

if [ ! -x "$VPY" ]; then
  PY=""
  for c in python3.12 python3.13 python3.14 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1 && \
       "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
      PY="$c"
      break
    fi
  done
  if [ -z "$PY" ]; then
    echo "需要 Python 3.10 或更高版本 / Python 3.10+ is required"
    exit 1
  fi
  if ! "$PY" -m venv .venv; then
    echo "创建 .venv 失败（Debian/Ubuntu 请先 sudo apt install python3-venv）/ failed to create .venv (Debian/Ubuntu: sudo apt install python3-venv)"
    rm -rf .venv
    exit 1
  fi
fi

WANT=$("$VPY" -c 'import hashlib, pathlib; print(hashlib.sha256(pathlib.Path("requirements.txt").read_bytes()).hexdigest())') || {
  echo "虚拟环境 .venv 无法运行：删除 .venv 后重新运行 ./start.sh / .venv is broken: delete it and run ./start.sh again"
  exit 1
}
if [ "$(cat .venv/.ryr-deps 2>/dev/null)" != "$WANT" ]; then
  echo "[RecordYourResearch] 安装依赖 / installing requirements ..."
  if ! { "$VPY" -m pip install --upgrade pip >/dev/null && "$VPY" -m pip install -r requirements.txt; }; then
    echo "依赖安装失败：检查网络后重试；仍然不行就删除 .venv 再运行 / pip install failed: check your network and retry; if it keeps failing, delete .venv"
    exit 1
  fi
  printf '%s' "$WANT" > .venv/.ryr-deps
fi

exec "$VPY" run.py --open "$@"
