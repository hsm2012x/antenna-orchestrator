#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/check_env.py — 설치·환경 점검 (노트북 00 셀의 실체)

무엇을 보나
    1) Python 판본 · 실행 위치
    2) 1~3단계 도구가 **추가 설치 없이** 도는가 (import + 자기 시험 진입점)
    3) 4단계 의존(langgraph · langgraph-checkpoint-sqlite)이 있는가 — 없으면 설치 명령을 알려준다
    4) 원장 경로가 현재 스키마인가 (구버전을 가리키면 여기서 걸린다)
    5) sqlite 저널 모드 협상 결과 (FUSE 마운트면 WAL 이 아니다)

돌리는 법
    python tools/check_env.py            점검만
    python tools/check_env.py --state    langgraph 가 있으면 **LLM 0콜 상태 머신** 왕복 시험
"""
from __future__ import annotations

import importlib
import platform
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

OK, WARN, BAD = "○", "△", "×"


def _line(mark, name, detail=""):
    print(f"  {mark}  {name:<38} {detail}")


def check(state_test: bool = False) -> int:
    bad = warn = 0
    print("[환경 점검 — 안테나 오케스트레이터 Level 1]\n")

    print("1) 실행 환경")
    _line(OK, "Python", f"{platform.python_version()} · {sys.executable}")
    _line(OK, "플랫폼", platform.platform())
    if sys.version_info < (3, 10):
        _line(BAD, "판본 요건", "3.10+ 필요 (코드가 `X | None` 표기를 쓴다)")
        bad += 1

    print("\n2) 1~3단계 도구 — 추가 설치 없이 도는가")
    for m in ("_common", "route", "extract", "verify_api", "render",
              "ledger", "roles", "catalog", "gate", "assets"):
        try:
            mod = importlib.import_module(m)
            has = "self_test" if hasattr(mod, "self_test") else ""
            _line(OK, f"tools/{m}.py", has)
        except Exception as exc:
            _line(BAD, f"tools/{m}.py", f"{type(exc).__name__}: {exc}")
            bad += 1

    print("\n3) 4단계 의존 — 상태 머신")
    missing = []
    for pkg, mod in (("langgraph", "langgraph"),
                     ("langgraph-checkpoint-sqlite", "langgraph.checkpoint.sqlite")):
        try:
            importlib.import_module(mod)
            _line(OK, pkg, "설치됨")
        except Exception:
            _line(WARN, pkg, "없음 — 4단계 전까지는 없어도 된다")
            missing.append(pkg)
            warn += 1
    if missing:
        print("\n     설치:  pip install " + " ".join(missing))
        print("     폐쇄망:  scripts/install_offline.md 참조")

    print("\n4) 원장")
    try:
        import _common as C
        import ledger as L
        p = C.ledger_path()
        _line(OK, "경로", str(p))
        conn = L.open_ledger()
        _line(OK, "저널 모드", L.journal_mode(conn) + "  (FUSE 마운트면 WAL 이 아닌 것이 정상)")
        _line(OK, "등재된 run", str(len(L.runs(conn))))
        conn.close()
    except Exception as exc:
        _line(BAD, "원장 열기", f"{type(exc).__name__}: {exc}")
        print("     구버전 스키마이면:  python tools/ledger.py migrate --from <old> --to <new>")
        print("     그 뒤:              ORCH_LEDGER_DB=<new> 로 가리킨다")
        bad += 1

    print("\n5) 자산 DB")
    try:
        import assets as A
        conn = A.open_db()
        st = A.status(conn)
        _line(OK, "경로", str(A.asset_db_path()))
        _line(OK, "등재 안테나", str(len(st)) + (f"  {[s['asset_id'] for s in st]}" if st else ""))
        conn.close()
    except Exception as exc:
        _line(WARN, "자산 DB", f"{type(exc).__name__}: {exc}")
        warn += 1

    if state_test:
        print("\n6) 상태 머신 왕복 — LLM 0콜")
        bad += _state_roundtrip()

    print(f"\n결과: 실패 {bad}건 · 주의 {warn}건")
    return 1 if bad else 0


def _state_roundtrip() -> int:
    """langgraph 가 있으면 노드 3개짜리 최소 그래프를 돌려 체크포인트 왕복을 확인한다.

    LLM 을 부르지 않는다 — 상태·전이·체크포인트만 본다. 4단계 착수 전 환경 확인용이다.
    """
    try:
        from typing import TypedDict
        from langgraph.graph import StateGraph, START, END
        from langgraph.checkpoint.sqlite import SqliteSaver
    except Exception as exc:
        _line(WARN, "건너뜀", f"langgraph 없음 ({exc})")
        return 0
    import sqlite3
    import tempfile

    class S(TypedDict, total=False):
        run_id: str
        state: str
        trail: list

    def mk(name, nxt):
        def node(s: S) -> S:
            return {"state": nxt, "trail": (s.get("trail") or []) + [name]}
        return node

    g = StateGraph(S)
    g.add_node("식별", mk("식별", "EXTRACT"))
    g.add_node("추출", mk("추출", "VERIFY"))
    g.add_node("해석", mk("해석", "COMPOSE"))
    g.add_edge(START, "식별")
    g.add_edge("식별", "추출")
    g.add_edge("추출", "해석")
    g.add_edge("해석", END)

    db = Path(tempfile.mkdtemp()) / "cp.sqlite"
    conn = sqlite3.connect(str(db), check_same_thread=False)
    # SqliteSaver 의 생성 방식은 langgraph 판본에 따라 다르다. 어느 쪽이든 받는다 —
    # 이 코드는 **폐쇄망에서 설치 후 처음 도는 자리**라 판본을 미리 알 수 없다.
    try:
        saver = SqliteSaver(conn)
    except TypeError:
        try:
            saver = SqliteSaver.from_conn_string(str(db)).__enter__()
        except Exception as exc:
            _line(BAD, "SqliteSaver 생성", f"{type(exc).__name__}: {exc} — 판본 확인 필요")
            conn.close()
            return 1
    app = g.compile(checkpointer=saver)
    cfg = {"configurable": {"thread_id": "env-check"}}
    out = app.invoke({"run_id": "env-check", "state": "ROUTE", "trail": []}, cfg)
    ok = out.get("trail") == ["식별", "추출", "해석"] and out.get("state") == "COMPOSE"
    _line(OK if ok else BAD, "노드 3개 전이", str(out.get("trail")))
    snap = app.get_state(cfg)
    ok2 = bool(snap and snap.values.get("state") == "COMPOSE")
    _line(OK if ok2 else BAD, "체크포인트 왕복 (thread_id=run_id)",
          f"{db.name} · state={snap.values.get('state') if snap else None}")
    conn.close()
    return 0 if (ok and ok2) else 1


if __name__ == "__main__":
    raise SystemExit(check("--state" in sys.argv))
