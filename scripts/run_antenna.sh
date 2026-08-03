#!/usr/bin/env bash
# scripts/run_antenna.sh — 안테나 오케스트레이터 기동 (SRS AI Studio 규약 준수)
#
# 기준 위치는 studio 와 같다:  cd ~/workspace/AI-Agent-PM/5_SRS_AI_Studio
# 이 스크립트는 그 아래 어디에 두어도 되고, STUDIO_SETUP 으로 setup 경로만 알려주면 된다.
#
#   bash scripts/run_antenna.sh run    <원천경로> [--run-id ID]
#   bash scripts/run_antenna.sh resume <run_id> --action approve|reject --by 이름
#   bash scripts/run_antenna.sh check                      # 환경 점검(+상태 머신 왕복)
#
# 규약 셋 — setup/00_common.sh 와 같은 것을 지킨다
#   ① 프록시 제거: localhost 호출이 SOCKS 로 새는 사고 방지
#   ② env 값은 setup/spark.env 에서 온다. 이름을 여기서 만들지 않는다
#   ③ 이미 export 된 값이 우선
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE"

# ① 프록시 제거 (setup/00_common.sh 와 동일)
for _p in http_proxy https_proxy all_proxy HTTP_PROXY HTTPS_PROXY ALL_PROXY; do unset "$_p"; done

# ② studio setup 의 env 값을 그대로 쓴다 — CHAT_BASE · SERVED_NAME 등
STUDIO_SETUP="${STUDIO_SETUP:-$HERE/../5_SRS_AI_Studio/setup}"
if [ -f "$STUDIO_SETUP/00_common.sh" ]; then
  # shellcheck disable=SC1091
  . "$STUDIO_SETUP/00_common.sh"            # CHAT_BASE · SERVED_NAME · QDRANT_URL …
  [ -f "$STUDIO_SETUP/spark.env" ] && set -a && . "$STUDIO_SETUP/spark.env" && set +a
else
  echo "[!] studio setup 을 못 찾았다: $STUDIO_SETUP" >&2
  echo "    STUDIO_SETUP=<경로> 로 알려주거나, CHAT_BASE 를 직접 export 하라." >&2
  export CHAT_BASE="${CHAT_BASE:-http://localhost:8000/v1}"
fi

# ③ 오케스트레이터 고유 값 — 아직 _system/interfaces.yaml#env_contract 미등재(사람 몫)
export ORCH_COMPOSER="${ORCH_COMPOSER:-prism}"       # prism | skeleton(LLM 0콜 대역)
export PRISM_PROMPT_PATH="${PRISM_PROMPT_PATH:-$HERE/registry/prism_prompt.md}"
export ORCH_DOCUMENT_SPEC="${ORCH_DOCUMENT_SPEC:-$HERE/registry/document_spec.yaml}"
export ORCH_LEDGER_DB="${ORCH_LEDGER_DB:-$HERE/ledger-v3.sqlite}"

cmd="${1:-check}"; shift || true
case "$cmd" in
  check)  python3 tools/check_env.py --state ;;
  run)    echo "[env] chat=$CHAT_BASE model=${SERVED_NAME:-(서버에 물어봄)} composer=$ORCH_COMPOSER"
          python3 agent/graph.py run "$@" ;;
  resume) python3 agent/graph.py resume "$@" ;;
  *)      python3 agent/graph.py "$cmd" "$@" ;;
esac
