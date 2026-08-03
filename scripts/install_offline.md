# 폐쇄망 설치 — Level 1 4단계 의존

> 대상: `langgraph` · `langgraph-checkpoint-sqlite`. **1~3단계는 설치가 필요 없다** —
> 도구·원장·게이트·자산 DB 는 전부 Python 표준 라이브러리로 돈다.
> 목적: LLM 없이 **상태 머신만** 시험하는 것. vLLM 은 Spark 쪽에서 HTTP 로 부르므로
> 이 환경에 `vllm` 패키지를 설치하지 않는다.

## 0. 어디에 설치하나

| 실행 위치 | 무엇을 도나 | 설치 필요 |
| --- | --- | --- |
| 튜너(클라우드·로컬 VM) | 1~3단계 도구·원장·게이트·자산 DB | **없음** |
| 사용자 Windows 본체 | 4단계 상태 머신 · 6단계 end-to-end | langgraph 2종 |
| Spark | vLLM 서빙 (프리즘이 HTTP 로 호출) | 이 문서 대상 아님 |

## 1. 망이 열려 있으면

```bash
# Linux / macOS
bash scripts/install_level1.sh

# Windows
scripts\install_level1.bat
```

둘 다 `.venv` 를 만들고 `requirements.txt` 를 설치한 뒤 `python tools/check_env.py --state`
까지 돌린다. 가상환경을 쓰지 않으려면 `VENV=0` 을 준다.

## 2. 망이 막혀 있으면 — 휠 반입

**망이 되는 기기에서** 휠을 내려받는다. **대상 기기와 같은 OS·Python 판본**이어야 한다.

```bash
# 예: 대상이 Windows + Python 3.11 인 경우
pip download -r requirements.txt \
    -d wheelhouse \
    --platform win_amd64 \
    --python-version 3.11 \
    --only-binary=:all:
```

`wheelhouse/` 를 대상 기기로 옮긴 뒤:

```bash
# Linux / macOS
WHEELHOUSE=/경로/wheelhouse bash scripts/install_level1.sh

# Windows
set WHEELHOUSE=D:\경로\wheelhouse
scripts\install_level1.bat
```

스크립트가 `--no-index --find-links` 로 붙어 외부 망을 타지 않는다.

`--platform` 을 쓰면 `--only-binary=:all:` 이 함께 있어야 한다(pip 요건). 순수 파이썬
패키지만 필요하다면 `--platform` 없이 받아도 대개 동작한다.

## 3. 설치 확인

```bash
python tools/check_env.py            # 설치·원장·자산 DB 점검
python tools/check_env.py --state    # + 상태 머신 왕복 (LLM 0콜)
```

`--state` 는 노드 3개(식별→추출→해석)짜리 최소 그래프를 돌려 **두 가지**를 본다.

- 전이가 순서대로 일어나는가 (`trail == ['식별','추출','해석']`)
- `thread_id = run_id` 로 체크포인트가 왕복하는가 (`get_state` 가 마지막 상태를 돌려주는가)

LLM 을 부르지 않는다. 4단계 착수 전에 **환경만** 확인하는 자리다.

## 4. 알아 둘 것

- **이 경로는 클라우드에서 검증하지 못했다.** 튜너 환경의 패키지 저장소 접근이 막혀 있어
  `langgraph` 를 설치할 수 없었다. 스크립트와 `check_env.py --state` 는 작성만 되어 있고
  **첫 실행이 곧 첫 검증**이다. 실패하면 그 출력을 그대로 가져오면 된다.
- `SqliteSaver` 의 생성 방식이 langgraph 판본에 따라 다르다(`SqliteSaver(conn)` vs
  `SqliteSaver.from_conn_string(...)`). `check_env.py` 는 양쪽을 다 시도한다.
- 설치가 끝나면 `requirements.txt` 에 **실제로 깔린 판본**을 적어 고정한다:
  `pip freeze | findstr langgraph` (Windows) · `pip freeze | grep langgraph` (Linux).
  폐쇄망에서 판본이 흔들리면 재현이 깨진다.
- pip 의존 추가는 사용자 승인 사항이다(PROMPT_IMPL 도구 수리 권한 절). 이 두 개 외에
  무언가 더 필요해지면 먼저 보고한다.
