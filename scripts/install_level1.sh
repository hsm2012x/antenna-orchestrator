#!/usr/bin/env bash
# scripts/install_level1.sh — Level 1 4단계 의존 설치 (Linux/macOS · Spark 본체)
# 규율: 1~3단계는 추가 설치가 필요 없다. 이 스크립트는 상태 머신용 최소 설치만 한다.
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"
echo "== Python: $($PY -V) ($PY)"

if [ "${VENV:-1}" = "1" ] && [ ! -d .venv ]; then
  echo "== 가상환경 생성 (.venv) — VENV=0 으로 끌 수 있다"
  "$PY" -m venv .venv
fi
if [ -d .venv ]; then . .venv/bin/activate; PY=python; fi

echo "== 설치"
"$PY" -m pip install --upgrade pip
if [ -n "${WHEELHOUSE:-}" ]; then           # 폐쇄망: 미리 받아 둔 휠 폴더
  "$PY" -m pip install --no-index --find-links "$WHEELHOUSE" -r requirements.txt
else
  "$PY" -m pip install -r requirements.txt
fi

echo "== 점검"
"$PY" tools/check_env.py --state
