#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mcp_server/server.py — 안테나 오케스트레이터 MCP 서버 (배선만)

이 파일은 **배선만 한다.** 도구의 알맹이는 `api.py` 에 있고 SDK 를 모른다 —
프로토콜이 바뀌면 여기만 고친다.

전송 두 가지
    stdio   같은 PC 에서 호스트가 프로세스를 띄운다. 원천 폴더를 로컬에서 읽으므로
            파일 전송이 없고 런타임 네트워크가 0이다(D-31).
    http    다른 PC·다른 세션에서 붙는다. **원천은 서버 쪽에 있어야 한다** —
            클라이언트가 보낸 경로는 서버의 파일시스템에서 해석된다.

실행
    python mcp_server/server.py                     stdio (기본)
    python mcp_server/server.py --transport http --port 8095
    python mcp_server/server.py --list              도구·리소스 목록만 (배선 점검)
    python mcp_server/server.py --self-test         SDK 없이 알맹이만 시험

환경
    ORCH_REPO              `tools/` · `agent/` · `registry/` 가 있는 저장소 루트.
                           비우면 `mcp_server/` 의 상위 → 현재 폴더 순으로 찾는다.
    ORCH_DATA_DIR          작업·산출 루트
    ORCH_LEDGER_DB         원장 정본(후보가 여럿이면 **필수** — 도구가 고르지 않는다)
    ORCH_PRODUCT_REGISTRY  제품 레지스트리
    ORCH_DOCUMENT_SPEC     문서 양식 정본
"""
from __future__ import annotations

import argparse
import inspect
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

try:
    import api as API  # noqa: E402
except RuntimeError as _e:            # 저장소를 못 찾았다 — 사유를 그대로 보여 준다
    print(f"[{'antenna-orchestrator'}] 시작할 수 없다\n\n{_e}", file=sys.stderr)
    raise SystemExit(2)

SERVER_NAME = "antenna-orchestrator"
INSTRUCTIONS = """\
안테나 원천 파일에서 값을 뽑아 문서를 만드는 서버. **이 서버는 LLM 을 부르지 않는다.**

당신이 하는 일은 하나다 — `document_brief` 가 준 골격의 `<키>` 를 카탈로그의 키로 바꾸고,
`PROSE` 마커 **사이**에 소견을 쓰는 것. 그 밖(표 · 제목 · 역할 · 대장)은 결정론 산출이고
`submit_document` 의 게이트가 변경을 잡아낸다.

시작 전에 리소스 `orch://guide` 를 읽는다. 절대 규칙 넷이 거기 있다 —
숫자를 타이핑하지 않는다 · `|역할` 을 건드리지 않는다 · 마커 밖을 고치지 않는다 ·
행렬의 행을 건드리지 않는다.

표준 순서: orch_status → discover_sources → run_pipeline → run_report →
document_brief → (당신이 씀) → submit_document → package_run.
값이 없으면 `declare_gaps` 로 **물어보면 채워지는 것**을 확인하고, 사용자에게 물어
`declare_set` 에 넣는다 — 들은 값을 문서에 직접 적지 않는다.
"""


def build(mcp=None):
    """FastMCP 인스턴스에 도구·리소스를 등록한다. `mcp` 를 주면 거기에 붙인다."""
    from mcp.server.fastmcp import FastMCP
    mcp = mcp or FastMCP(SERVER_NAME, instructions=INSTRUCTIONS)

    for fn in API.TOOLS:
        # 이름·시그니처·설명을 그대로 물려준다 — 여기서 다시 쓰지 않는다.
        # 설명을 두 곳에 두면 반드시 갈라진다.
        mcp.add_tool(fn, name=fn.__name__, description=inspect.getdoc(fn))

    for uri, (title, mime, getter) in API.RESOURCES.items():
        mcp.resource(uri, name=uri.rsplit("/", 1)[-1],
                     description=title, mime_type=mime)(getter)

    # 프롬프트 — "이 문서를 써라"를 한 번에 꺼내 쓰는 자리.
    # 호스트가 프롬프트를 지원하면 사용자가 슬래시 명령처럼 고를 수 있다.
    @mcp.prompt(name="안테나_문서_작성",
                description="run 하나의 통합 문서를 규율대로 작성한다")
    def compose_prompt(run_id: str) -> str:
        b = API.document_brief(run_id)
        if not b.get("ok"):
            return f"이 run 의 brief 를 못 가져왔다: {b.get('why')}"
        rules = "\n".join(f"- {r}" for r in b["prose_rules"])
        slots = "\n".join(
            f"- `{s['key']}` (최대 {s['max_sentences']}문장)\n    {s['guide']}"
            for s in b["prose_slots"])
        return (
            f"# 안테나 통합 문서 작성 — {run_id}\n\n"
            "## 규칙\n"
            "1. 골격의 `<키>` 를 아래 카탈로그의 키로 바꾼다.\n"
            "2. `|역할` 을 바꾸지 않는다 — 어긋나면 키를 바꾼다.\n"
            "3. `PROSE` 마커 **사이**에만 쓴다. 마커 밖은 한 글자도 고치지 않는다.\n"
            "4. 이미 키가 박힌 행렬의 칸을 건드리지 않는다.\n"
            "5. 본문에 숫자를 타이핑하지 않는다.\n\n"
            f"## 서술 규율\n{rules}\n\n"
            f"## 쓸 자리\n{slots}\n\n"
            f"## 골격\n\n```markdown\n{b['skeleton']}\n```\n\n"
            f"## 값 카탈로그\n\n```\n{b['catalog']}\n```\n\n"
            f"다 쓰면 `submit_document(\"{run_id}\", <전체 마크다운>)` 로 낸다. "
            "반려되면 `violations` 만 고쳐 다시 낸다.\n")

    return mcp


def _list() -> int:
    print(f"{SERVER_NAME} — 도구 {len(API.TOOLS)} · 리소스 {len(API.RESOURCES)}\n")
    print("[도구]")
    for fn in API.TOOLS:
        sig = str(inspect.signature(fn.__wrapped__ if hasattr(fn, "__wrapped__") else fn))
        head = (inspect.getdoc(fn) or "").splitlines()[0]
        print(f"  {fn.__name__}{sig}\n      {head}")
    print("\n[리소스]")
    for uri, (title, mime, _fn) in API.RESOURCES.items():
        print(f"  {uri:<24} {title}  ({mime})")
    print("\n[프롬프트]")
    print("  안테나_문서_작성(run_id)   골격 + 카탈로그 + 규율을 한 덩어리로")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="mcp_server/server.py")
    ap.add_argument("--transport", choices=["stdio", "http", "sse"], default="stdio")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8095)
    ap.add_argument("--list", action="store_true", help="배선 점검 — 서버를 띄우지 않는다")
    ap.add_argument("--self-test", action="store_true", help="SDK 없이 알맹이만 시험")
    a = ap.parse_args(argv)

    if a.self_test:
        return API.self_test()
    if a.list:
        return _list()

    try:
        mcp = build()
    except ImportError:
        print("MCP SDK 가 없다. 반입이 필요하다(EXT-5):\n"
              "    pip install mcp        (온라인)\n"
              "    pip install ./wheels/mcp-*.whl --no-index   (오프라인 반입 — D-31)\n"
              "배선만 확인하려면 `--list`, 알맹이만 시험하려면 `--self-test`.",
              file=sys.stderr)
        return 2

    if a.transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.settings.host, mcp.settings.port = a.host, a.port
        mcp.run(transport="streamable-http" if a.transport == "http" else "sse")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
