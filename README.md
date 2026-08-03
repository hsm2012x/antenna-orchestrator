# antenna-orchestrator

안테나 원천 폴더(CST 프로젝트 · DXF/DWG 도면 · Gerber)에서 값을 뽑아 **통합 문서**를 만들고,
결정론 게이트로 **수치 창작을 구조적으로 막는다.** Claude Code 플러그인이자 MCP 서버다.

## 설치

이 레포 자체가 플러그인 마켓플레이스다.

```
/plugin marketplace add <owner>/<repo>
/plugin install antenna-orchestrator@srs-antenna
```

`<owner>/<repo>` 대신 전체 git URL(`https://…​.git`)도 된다. 설치하면 Claude Code 가 레포를
`~/.claude/plugins/` 아래로 클론해 두고, 세션이 뜰 때 MCP 서버를 자식 프로세스로 띄운다.

설치 뒤 Claude Code 를 다시 띄우고 `orch_status` 를 부르면 붙었는지 알 수 있다.

## 설치 후 반드시 할 것 — 데이터 자리 지정

★ `ORCH_DATA_DIR` 를 **플러그인 밖** 작업 폴더로 지정한다.

지정하지 않으면 작업·산출·원장이 플러그인 캐시 안에 쌓이고, 플러그인을 갱신하는 순간
전부 사라진다. `orch_status()` 가 이 상태를 `ok: false` 로 먼저 알려 준다.

```
setx ORCH_DATA_DIR C:\Work\_antenna            # Windows (새 터미널부터 적용)
export ORCH_DATA_DIR=$HOME/antenna             # Linux · macOS
```

원장 후보가 둘 이상이면 `ORCH_LEDGER_DB` 도 지정한다 — **도구가 정본을 고르지 않는다.**

## 필요한 것

- Python 3.11 이상 · `pip install mcp pyyaml matplotlib`
- 원천 파일은 **서버가 도는 기계**에 있어야 한다

## 핵심 — 서버는 LLM 을 부르지 않는다

이 파이프라인은 LLM 을 **한 자리에서만** 부른다: 문서의 서술 슬롯.
그 경계를 그대로 MCP 경계로 썼다.

```
서버        = 결정론 전부 (값 · 그림 · 골격 · 게이트 · 선언)
호스트 모델  = PROSE 마커 사이만
```

문서는 `{{v:키}}` 참조만 쓰고 숫자를 타이핑하지 않는다. 게이트가 결정론으로 치환하므로
**모델을 갈아도 문서의 수치가 흔들리지 않는다.**

## 들어 있는 것

| 것 | 개수 | 역할 |
| --- | --- | --- |
| MCP 서버 | 1 | 도구 15 · 리소스 5 · 프롬프트 1 |
| 스킬 | 1 | `antenna-doc` — 문서 작성 규율. 쓰기 전에 읽는다 |
| 도구 본체 | `tools/` `agent/` `registry/` `vendor_srs/` | 서버가 부르는 결정론 코드 전부 |

## 환경변수

| 이름 | 뜻 | 기본 |
| --- | --- | --- |
| `ORCH_DATA_DIR` | 작업·산출·원장이 쌓이는 곳 | 플러그인 폴더 (**바꿀 것**) |
| `ORCH_LEDGER_DB` | 원장 정본 | 후보가 하나면 자동, 여럿이면 **필수** |
| `ORCH_REPO` | 도구 본체 위치 | 플러그인이 자동 지정 |
| `ORCH_PRODUCT_REGISTRY` | 제품 레지스트리 | 이 레포의 `registry/products.yaml` |

## 레지스트리는 견본이다

`registry/products.yaml` · `registry/projects.yaml` 에 들어 있는 것은 **양식 견본**이다.
실제 제품·담당·임계는 `$ORCH_DATA_DIR` 쪽에 두거나 `declare_set` 도구로 선언한다 —
그래야 플러그인을 갱신해도 값이 지워지지 않는다.

## 점검

```
python mcp_server/server.py --list          # 배선 — 도구 15 · 리소스 5
python mcp_server/api.py                    # 알맹이
python mcp_server/selftest_protocol.py      # 프로토콜 — 실제 stdio 왕복
```

## 쓰는 법

새 세션에서:

```
orch_status 를 불러줘.
```

`ok: true` 가 나오면 붙은 것이다. 그 다음은 스킬 `antenna-doc` 이 안내한다.
표준 순서는 `discover_sources` → `run_pipeline` → `run_report` → `document_brief` →
`submit_document` → `package_run` 이다.

## 라이선스

MIT
