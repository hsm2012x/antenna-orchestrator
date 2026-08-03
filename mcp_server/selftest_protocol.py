#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mcp_server/selftest_protocol.py — **진짜 MCP 클라이언트로** 서버를 두드린다

`api.py` 의 자기 시험은 알맹이만 본다. 이 시험은 그 위의 **배선**을 본다 —
프로세스를 띄우고 stdio 로 handshake 하고 `tools/list` · `tools/call` · `resources/read` ·
`prompts/get` 을 실제로 주고받는다.

왜 따로 두나
    알맹이가 다 통과해도 배선이 틀리면 클라이언트에서는 아무것도 안 된다.
    실제로 한 번 걸렸다 — 예외 래퍼가 시그니처를 지워 도구 스키마가 `(*a, **kw)` 가 되고,
    도구는 보이는데 **인자 이름을 몰라 못 부르는** 상태가 됐다.

    이 시험이 도는 자리는 **다른 모델이 붙을 자리와 같다.** 여기서 통과하면
    Claude Desktop · Cursor · 다른 LLM 어디서든 같은 것이 보인다.

실행
    python mcp_server/selftest_protocol.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent


async def _run() -> int:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    ok = fail = 0

    def chk(n, cond, d=""):
        nonlocal ok, fail
        if cond:
            ok += 1; print(f"  PASS  {n}")
        else:
            fail += 1; print(f"  FAIL  {n}  {d}")

    params = StdioServerParameters(
        command=sys.executable, args=[str(_HERE / "server.py"), "--transport", "stdio"])

    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            init = await s.initialize()
            chk("handshake — 서버 이름이 온다",
                init.serverInfo.name == "antenna-orchestrator", str(init.serverInfo))
            instr = init.instructions or ""
            chk("서버 지시문이 규칙을 먼저 말한다",
                "LLM 을 부르지 않는다" in instr and "마커" in instr, instr[:80])

            tools = (await s.list_tools()).tools
            names = {t.name for t in tools}
            chk(f"도구 {len(tools)}종이 보인다", len(tools) >= 15, str(sorted(names)))
            chk("문서 이음매 두 도구가 있다",
                {"document_brief", "submit_document"} <= names)
            chk("선언 경로가 있다", {"declare_gaps", "declare_set"} <= names)

            # ★ 스키마 — 인자 이름이 보이지 않으면 도구가 있어도 못 부른다
            byname = {t.name: t for t in tools}
            sub = byname["submit_document"].inputSchema
            chk("도구 스키마에 인자 이름이 실린다",
                set(sub.get("properties", {})) >= {"run_id", "markdown"},
                json.dumps(sub, ensure_ascii=False)[:160])
            chk("필수 인자가 표시된다", set(sub.get("required") or []) >= {"run_id", "markdown"},
                str(sub.get("required")))
            chk("설명이 실린다", "게이트" in (byname["submit_document"].description or ""))

            res = (await s.list_resources()).resources
            uris = {str(x.uri) for x in res}
            chk(f"리소스 {len(res)}종이 보인다", len(res) >= 5, str(sorted(uris)))
            body = (await s.read_resource("orch://guide")).contents[0].text
            chk("사용법을 읽을 수 있다", "절대 규칙" in body, body[:60])

            # 실제 호출
            def _payload(rr):
                return json.loads(rr.content[0].text)

            st = _payload(await s.call_tool("orch_status", {}))
            chk("orch_status 가 돈다", "data_root" in st, str(list(st)[:5]))

            rid = next((x["run_id"] for x in st["runs"] if x.get("골격")), None)
            chk("골격이 있는 run 을 찾았다", bool(rid), str(rid))

            if rid:
                br = _payload(await s.call_tool("document_brief", {"run_id": rid}))
                chk("brief 가 골격·카탈로그를 함께 준다",
                    br["skeleton"].startswith("#") and "값 카탈로그" in br["catalog"])

                bad = _payload(await s.call_tool(
                    "submit_document", {"run_id": rid, "markdown": br["skeleton"]}))
                chk("안 채운 문서를 게이트가 반려한다", bad["pass"] is False,
                    str(bad.get("violation_kinds")))
                chk("무엇을 어겼는지 알려준다", bad["n_violations"] > 0)

                # 골격을 고쳐서 내면 템플릿 위반으로 잡히는가 — **핵심 방어**
                #   실제로 있는 첫 제목을 건드린다. 없는 제목을 바꾸면 아무 일도 안 일어나고
                #   시험이 통과한 척한다(실제로 그랬다).
                head = next(ln for ln in br["skeleton"].splitlines()
                            if ln.startswith("## "))
                tampered = br["skeleton"].replace(head, head + " (내가 바꿈)", 1)
                tv = _payload(await s.call_tool(
                    "submit_document", {"run_id": rid, "markdown": tampered}))
                chk("마커 밖을 고치면 잡는다",
                    "template_modified" in tv.get("violation_kinds", {}),
                    str(tv.get("violation_kinds")))

            # 쓰기 거부가 프로토콜 너머에서도 서는가
            wr = _payload(await s.call_tool("declare_set", {
                "path": "products:example_x_band.hidden.x", "value": "1",
                "product": "example_x_band", "by": "프로토콜시험"}))
            chk("선언 자리 밖 쓰기를 거부한다", wr["ok"] is False, str(wr)[:90])

            prompts = (await s.list_prompts()).prompts
            chk("프롬프트가 있다", any(p.name == "안테나_문서_작성" for p in prompts),
                str([p.name for p in prompts]))
            if rid:
                pm = await s.get_prompt("안테나_문서_작성", {"run_id": rid})
                txt = pm.messages[0].content.text
                chk("프롬프트가 규칙 + 골격 + 카탈로그를 한 덩어리로 준다",
                    "규칙" in txt and "골격" in txt and "값 카탈로그" in txt, txt[:80])

    print(f"\n결과: {ok}/{ok + fail} PASS")
    return 0 if fail == 0 else 1


def main() -> int:
    print("[MCP 프로토콜 자기 시험 — 실제 stdio 클라이언트]")
    try:
        import mcp  # noqa: F401
    except ImportError:
        print("  SKIP  MCP SDK 가 없다 — 반입 필요(EXT-5). 알맹이 시험은 `api.py` 로 돈다")
        return 0
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
