# 다른 세션 · 낮은 모델로 시험하는 절차

**무엇을 재는 시험인가.** "낮은 모델이 글을 잘 쓰나"가 아니다.
**모델을 갈아도 문서의 수치가 흔들리지 않는가**를 잰다.

```
수치 · 표 · 그림 · 절 구성  →  서버(결정론). 모델이 손댈 수 없다.
서술(PROSE 마커 사이)        →  모델. 여기만 모델 차이가 나타나야 한다.
```

그래서 합격은 "좋은 문서가 나왔다"가 아니라 **"통과한 문서의 수치가 카탈로그와 100 % 같다"**
이고, 모델 차이는 **반려 횟수**와 **서술의 질**로만 나타나야 한다.

* * *

## 0. 준비 (한 번만)

### 폴더 배치 — 여기서 먼저 막힌다

`mcp_server/` **만으로는 돌지 않는다.** 서버는 배선이고 알맹이는 `tools/` · `agent/` ·
`registry/` 에 있다. 저장소 전체를 풀어 이렇게 되어야 한다.

```text
C:\Work\_antenna_mcp\
    tools\  agent\  registry\  vendor_srs\  scripts\  mcp_server\   ← 저장소
    sources\
        <프로젝트A>\        ← 원천 1
        <프로젝트B>\        ← 원천 2
```

원천을 저장소 루트에 그냥 두지 않는다. `discover_sources` 를 루트로 겨누면 `tools\` 까지
원천 후보로 훑는다. **원천은 반드시 별도 폴더 아래** 둔다.

저장소를 다른 곳에 두려면 환경변수로 알려 준다.

```bash
set ORCH_REPO=C:\Work\_antenna_mcp     # Windows
export ORCH_REPO=/path/to/repo          # Linux · macOS
```

`ModuleNotFoundError: No module named '_common'` 이 나오면 그것이 이 문제다.
지금은 서버가 어디를 찾아봤고 무엇을 하면 되는지까지 말하고 멈춘다.

### 시험 셋 — 순서대로

```bash
cd C:\Work\_antenna_mcp
pip install mcp                    # 온라인
# 오프라인이면: pip install .\wheels\mcp-*.whl --no-index   (반입 정책 D-31)

python mcp_server/server.py --list        # ① 배선 — 도구 15 · 리소스 5
python mcp_server/api.py                  # ② 알맹이 — 39/39
python mcp_server/selftest_protocol.py    # ③ 프로토콜 — 19/19
```

셋 다 통과하지 않으면 모델 시험으로 넘어가지 않는다. **여기서 실패하면 모델 탓이 아니다.**

★ `--list` 는 도구 이름만 보는 것이 아니다. 인자 이름이 `(*a, **kw)` 로 뭉개져 있으면
클라이언트가 **도구를 보고도 못 부른다** — 실제로 한 번 그랬다. 인자가 이름과 형까지
보이는지 눈으로 확인한다.

### 원장 정본 — 후보가 여럿이면 첫 도구에서 멈춘다

```bash
python tools/dbview.py ledger
set ORCH_LEDGER_DB=C:\Work\_antenna_mcp\ledger-v3.sqlite
```

도구가 정본을 고르지 않는다(A-1). 지정하지 않으면 `LedgerAmbiguous` 로 멈춘다.

### 원천 두 건을 run 으로 만든다

```bash
python agent/graph.py run sources\<프로젝트A> --product <제품> --run-id T-A
python agent/graph.py run sources\<프로젝트B> --product <제품> --run-id T-B
```

한 폴더 안에 안테나가 여럿이면 **각각 run 이 된다.** 나온 `run_id` 를 적어 둔다.
제품을 비워도 되지만 그러면 요구 명세가 광역 기본값이 되고 판정이 대부분
"임계 미지정"이 된다 — 대조 절이 서지 않으므로 시험의 절반이 사라진다.

원천 두 건이 서로 무슨 관계인지도 한 번 본다.

```bash
python -c "import sys;sys.path.insert(0,'mcp_server');import api,json;print(json.dumps(api.relate_entries('sources'),ensure_ascii=False,indent=2))"
```

## 1. 붙이기

### Claude Desktop / Claude Code (stdio — 같은 PC)

`claude_desktop_config.json` (또는 프로젝트의 `.mcp.json`):

```json
{
  "mcpServers": {
    "antenna-orchestrator": {
      "command": "python",
      "args": ["C:\\Work\\_antenna_mcp\\mcp_server\\server.py"],
      "env": {
        "ORCH_REPO": "C:\\Work\\_antenna_mcp",
        "ORCH_DATA_DIR": "C:\\Work\\_antenna_mcp",
        "ORCH_LEDGER_DB": "C:\\Work\\_antenna_mcp\\ledger-v3.sqlite"
      }
    }
  }
}
```

`ORCH_LEDGER_DB` 를 비워도 되지만, 원장 후보가 둘 이상이면 **서버가 첫 도구에서 멈춘다.**

### 다른 PC · 다른 세션 (HTTP)

```bash
python mcp_server/server.py --transport http --host 0.0.0.0 --port 8095
```

★ **원천 폴더는 서버가 도는 PC 에 있어야 한다.** 클라이언트가 보낸 경로는 서버의
파일시스템에서 해석된다. 사내망 밖으로 열지 않는다.

## 2. 붙었는지 확인 (모델에게 시키지 말고 직접)

새 세션에서 그대로 친다.

> `orch_status` 를 불러줘.

기대: `ok: true` · `data_root` · `ledger.chosen` · `n_runs`.
`ledger.ok: false` 면 `ORCH_LEDGER_DB` 를 지정하고 다시 붙는다.

> 리소스 `orch://guide` 를 읽어줘.

기대: "절대 규칙 넷"이 보인다. **여기까지가 환경 시험이고, 여기부터가 모델 시험이다.**

* * *

## 3. 본 시험 — 낮은 모델에게 이 한 줄만 준다

새 세션(낮은 모델)에 **아래 딱 한 줄만** 붙여 넣는다. 힌트를 더 주면 시험이 무의미해진다.

```text
antenna-orchestrator MCP 서버가 붙어 있다. 먼저 orch://guide 를 읽고,
run_id "T-A-<entry>" 의 통합 문서를 규율대로 작성해서 게이트를 통과시켜라.
반려되면 위반만 고쳐 다시 내라. 최대 5회까지.
```

낮은 모델이 스스로 해야 하는 것 —

1. `orch://guide` 를 읽는다(규칙 파악)
2. `document_brief(run_id)` 로 골격 + 카탈로그를 받는다
3. `<키>` 를 카탈로그의 키로 채운다
4. `PROSE` 마커 사이에 소견을 쓴다
5. `submit_document(run_id, markdown)` 로 낸다
6. `pass: false` 면 `violations` 만 고쳐 다시

### 원천이 둘이라서 할 수 있는 것 — 같은 모델로 두 번

같은 모델에게 **T-A 와 T-B 를 따로** 시킨다(세션을 나눠서, 서로 못 보게).

두 산출물을 나란히 놓고 본다.

| 같아야 한다 | 달라야 한다 |
| --- | --- |
| 절 구성 · 절 제목 · 표 머리글 | 모든 값 |
| 빈 절이 대장으로 모이는 방식 | 대장에 오른 절 목록(원천마다 다르다) |
| 서술 규율 준수 | 서술 내용 |

절 구성이 두 원천에서 다르면 **모델이 골격을 손댄 것**이고 `template_modified` 로
잡혔어야 한다. 잡히지 않고 달라졌다면 알려 달라 — 게이트 결함이다.

### 교차 시험 — 높은 모델과 낮은 모델을 엇갈리게

원천이 둘이면 이렇게도 볼 수 있다.

| 회차 | T-A | T-B |
| --- | --- | --- |
| 1회차 | 낮은 모델 | 높은 모델 |
| 2회차 | 높은 모델 | 낮은 모델 |

네 산출물의 **수치가 원천별로 같아야** 한다. 같은 원천인데 모델에 따라 값이 다르면
그것이 이 설계가 막으려던 바로 그 사고다.

## 4. 채점 — 무엇을 보나

시험이 끝나면 **당신이** 다음을 확인한다. 모델의 자기 보고를 믿지 않는다(B-3).

### ① 통과했는가 · 몇 번 만에

`submit_document` 응답의 `attempt` 가 그대로 원장에 남는다.

```bash
python tools/ledger.py events <run_id> | findstr gate
```

| 지표 | 뜻 |
| --- | --- |
| 1~2회 통과 | 모델이 규칙을 읽고 지켰다 |
| 3~5회 통과 | 위반 메시지를 보고 고칠 줄은 안다 — **이것도 합격이다** |
| 5회 초과 · 미통과 | 규칙이 전달 안 됐다. `orch://guide` 를 읽었는지 먼저 확인 |

### ② ★ 수치가 흔들리지 않았는가 — 이것이 본 시험이다

```bash
python tools/gate.py check <run_id>
```

`pass: true` 면 **통과한 문서의 모든 수치는 카탈로그 값과 글자까지 같다.**
모델이 값을 지어냈다면 애초에 통과할 수 없다 — 문서에 숫자가 없고 참조만 있기 때문이다.

`work\<run_id>\초안.md`(모델이 쓴 것)와 `work\<run_id>\초안_치환.md`(값이 박힌 것)를
나란히 열어 본다. 초안에 숫자가 보이면 그것이 **뚫린 자리**다 — 알려 달라, 게이트 결함이다.

### ③ 기준 보고서와 견준다

같은 파이프라인으로 만든 **기준 산출물**이 `out/기준보고서_치환.md` 에 있다.
다른 원천이므로 값은 다르지만, **모양은 같아야 한다** — 절 구성 · 표 머리글 · 대장의 다섯 칸.

| 같아야 하는 것 | 다를 수 있는 것 |
| --- | --- |
| 절 구성 · 표 · 그림 · **모든 수치** | `PROSE` 마커 사이 서술의 깊이 |
| 키 선택(대부분) | 무엇을 짚어 말하는가 |
| 통과 여부 | 반려 횟수 |

### ④ 낮은 모델이 흔히 걸리는 것 — 걸리는 게 정상이다

| 위반 | 무슨 짓을 한 것인가 | 게이트가 잡는가 |
| --- | --- | --- |
| `bare_number` | 서술에 숫자를 타이핑 | ✅ |
| `role_mismatch` | 역할은 그대로 두고 엉뚱한 키를 넣음 | ✅ |
| `template_modified` | 표를 예쁘게 고치거나 제목 번호를 바꿈 | ✅ |
| `undefined_key` | 있을 법한 키를 **지어냄** | ✅ |
| `unsubstituted_ref` | `<키>` 를 몇 개 안 채움 | ✅ |
| `prose_unwritten` | 마커 사이를 비워 둠 | ✅ |

**전부 잡히면 시험 성공이다.** 낮은 모델이 실수를 많이 할수록 게이트가 잘 도는지 더 잘 보인다.

### ⑤ 한 줄에 이름 · 값 · 출처가 **같은 키**를 가리키는가

표 한 줄에는 참조가 셋이다 — 이름 · 값 · 출처. 셋이 서로 다른 항목을 가리켜도
**역할은 전부 맞으므로 게이트가 잡지 못한다.** 행렬의 행 짝 문제와 같은 부류다.

치환본에서 한두 줄만 눈으로 확인한다 — 이름과 값이 서로 맞는 항목인가.
어긋나 있으면 알려 달라.

## 5. 곁들여 볼 것 — 선언 경로

낮은 모델이 **물어볼 줄 아는가**도 같이 본다.

```text
declare_gaps 를 불러서, 내가 알려주면 채울 수 있는 것이 무엇인지 물어봐.
```

기대 — 모델이 `declare_gaps(run_id)` 를 부르고 목록을 보여 주며 **당신에게 묻는다.**
당신이 "기판은 Rogers AD255C" 라고 답하면 모델은 `declare_set` 을 부른다.

**틀린 행동**: 모델이 그 값을 문서에 직접 타이핑하는 것. 그러면 `bare_number` 나
`undefined_key` 로 반려된다 — 그것도 게이트가 잡는다는 증거다.

### 선언 뒤에는 다시 돌려야 한다

`declare_set` 은 레지스트리에 값을 얹을 뿐이다. **이미 돌아 있는 run 은 그대로다.**
다시 돌리지 않고 `document_brief` 를 부르면 낡은 카탈로그를 받는다.

서버가 이것을 먼저 말한다 — `document_brief` 와 `run_report` 의 `stale` 에
무엇이 바뀌었는지가 실린다. 모델이 그 경고를 읽고 `run_pipeline` 을 다시 부르는지 본다.
부르지 않고 문서를 다 쓰면 그 작업은 통째로 버려진다.

## 6. 기록 — 비교할 수 있게 남긴다

시험 한 번마다 아래를 적어 둔다. 안 적으면 다음 모델과 비교할 수 없다.

```text
모델        : <이름 · 판본>
원천        : T-A / T-B
run_id      : <id>
통과        : 예 / 아니오
반려 횟수    : <n>
위반 종류    : {bare_number: 3, role_mismatch: 1, ...}
수치 일치    : gate check pass = true / false
낡음 대응    : stale 경고를 읽고 다시 돌렸나 (예 / 아니오 / 해당 없음)
선언 질문    : 물어봤나 / 직접 타이핑했나
서술 소견    : (사람이 읽은 인상 한두 줄)
```

`out\<원천명>\` 아래 산출물과 `초안_치환.md` 를 함께 보관한다.
원천이 둘이므로 **표 한 장에 네 줄**(모델 2 × 원천 2)이 쌓이면 비교가 선명해진다.

## 부록 — 잘 안 될 때

| 증상 | 원인 | 조치 |
| --- | --- | --- |
| `No module named '_common'` | `mcp_server/` 만 풀었다 | 저장소 전체를 풀거나 `ORCH_REPO` 지정 |
| 도구가 아예 안 보인다 | 서버가 안 떴다 | `python mcp_server/server.py --list` 로 배선부터 |
| 도구는 보이는데 인자를 모른다 | 스키마가 안 실렸다 | `selftest_protocol.py` 의 "도구 스키마" 항목 확인 |
| `LedgerAmbiguous` | 원장 후보가 여럿 | `python tools/dbview.py ledger` → `ORCH_LEDGER_DB` 지정 |
| `not_found` | run 이 없다 | `orch_status()` 의 `runs` 목록에서 고른다 |
| `discover_sources` 가 이상한 것을 잡는다 | 저장소 루트를 겨눴다 | `sources\` 아래를 겨눈다 |
| 모델이 규칙을 무시한다 | guide 를 안 읽었다 | 지시문에 "먼저 orch://guide 를 읽고"를 넣었는지 확인 |
| 계속 `template_modified` | 모델이 표를 손본다 | 정상이다. 5회 안에 못 고치면 그 모델의 한계로 기록 |
| 선언한 값이 문서에 없다 | 선언 뒤 다시 안 돌렸다 | `stale` 을 확인하고 `run_pipeline` 재실행 |
