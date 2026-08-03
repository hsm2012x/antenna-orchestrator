#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/dbview.py — sqlite 현황 판독기 (LLM 0콜)

무엇을 하나
    데이터 루트의 **sqlite 파일을 전부 찾아** 스키마 판본 · 행 수 · 기간을 보여준다.
    원장이 여러 개일 때 **어느 것이 정본인지 눈으로 고르게** 하는 것이 목적이다(Q-09).

★ 도구가 정본을 고르지 않는다
    "가장 새것을 쓴다" 같은 규칙을 넣고 싶어진다. 넣지 않는다 —
    정본이 무엇인지는 **조직의 사실**이지 파일 시각의 함수가 아니다(A-1).
    파일이 여럿이면 `ORCH_LEDGER_DB` 를 **요구**하고, 하나뿐일 때만 그것을 쓴다.

    지금까지는 기본 경로가 조용히 구판(`ledger.sqlite`)을 가리켰다. 조용한 것이 문제였다 —
    게이트가 `not_recorded` 로 반려하고 나서야 알 수 있었다(결함 F-11). 이제
    **여럿이면 멈추고 무엇이 있는지 보여준다.**

CLI
    python tools/dbview.py list                     데이터 루트의 sqlite 현황
    python tools/dbview.py ledger                   원장 후보 판정 — 정본이 정해지는가
    python tools/dbview.py peek <파일> [테이블]       표 목록 · 앞 행 몇 개
    python tools/dbview.py self-test
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

PEEK_ROWS = 5

# 원장으로 인정하는 표 — 이 표가 있어야 원장이다. 이름만으로 판정하지 않는다.
LEDGER_MARK = ("runs", "events")


def _tables(conn) -> dict:
    out = {}
    for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        try:
            out[name] = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        except Exception:
            out[name] = None
    return out


def inspect(path) -> dict:
    """파일 하나 — **읽기만 한다.** 스키마를 만들지 않는다(없는 표를 만들면 판독이 오염된다)."""
    p = Path(path)
    rec = {"path": str(p), "name": p.name, "size": p.stat().st_size if p.exists() else 0,
           "exists": p.exists(), "tables": {}, "schema_version": None,
           "kind": None, "span": None, "readable": False, "why": ""}
    if not p.exists():
        rec["why"] = "파일이 없다"
        return rec
    try:
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    except Exception as e:
        rec["why"] = f"열 수 없다: {type(e).__name__}"
        return rec
    try:
        rec["tables"] = _tables(conn)
        rec["readable"] = True
        names = set(rec["tables"])
        if all(t in names for t in LEDGER_MARK):
            rec["kind"] = "ledger"
            try:
                r = conn.execute("SELECT MIN(ts), MAX(ts), COUNT(*) FROM events").fetchone()
                rec["span"] = {"first": r[0], "last": r[1], "n_events": r[2]}
            except Exception:
                pass
        elif "antennas" in names:
            rec["kind"] = "assets"
        elif "checkpoints" in names:
            rec["kind"] = "checkpoints"
        else:
            rec["kind"] = "unknown"
        if "meta" in names:
            try:
                v = conn.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()
                rec["schema_version"] = v[0] if v else None
            except Exception:
                pass
    finally:
        conn.close()
    return rec


def scan(root=None) -> list[dict]:
    root = Path(root) if root else C.data_dir()
    seen = []
    for p in sorted(root.rglob("*.sqlite")):
        if any(s in p.name for s in ("-wal", "-shm", "-journal")):
            continue
        seen.append(inspect(p))
    return seen


def ledger_status(root=None) -> dict:
    """원장 후보가 몇 개인가. **여럿이면 고르지 않는다.**"""
    cands = [r for r in scan(root) if r["kind"] == "ledger"]
    env = os.environ.get("ORCH_LEDGER_DB", "").strip()
    if env:
        return {"n": len(cands), "candidates": cands, "chosen": env,
                "by": "ORCH_LEDGER_DB", "ok": True,
                "why": "환경변수가 정본을 지정했다 — 도구가 고른 것이 아니다"}
    if len(cands) == 1:
        return {"n": 1, "candidates": cands, "chosen": cands[0]["path"], "by": "유일",
                "ok": True, "why": "원장이 하나뿐이라 고를 것이 없다"}
    if not cands:
        return {"n": 0, "candidates": [], "chosen": None, "by": None, "ok": False,
                "why": "원장이 없다 — 아직 run 이 없거나 데이터 루트가 다르다"}
    return {"n": len(cands), "candidates": cands, "chosen": None, "by": None, "ok": False,
            "why": (f"원장 후보가 {len(cands)}개다 — **도구가 고르지 않는다**(A-1). "
                    "`ORCH_LEDGER_DB` 로 정본을 지정한다. 아래 현황을 보고 정한다")}


def peek(path, table=None, n=PEEK_ROWS) -> dict:
    rec = inspect(path)
    if not rec["readable"]:
        return rec
    conn = sqlite3.connect(f"file:{Path(path)}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        if table:
            cur = conn.execute(f'SELECT * FROM "{table}" LIMIT ?', (n,))
            rec["rows"] = [dict(r) for r in cur]
            rec["table"] = table
    finally:
        conn.close()
    return rec


# ── 자기 시험 ────────────────────────────────────────────────────────────────

def self_test() -> int:
    ok = fail = 0

    def chk(nm, cond, d=""):
        nonlocal ok, fail
        if cond:
            ok += 1; print(f"  PASS  {nm}")
        else:
            fail += 1; print(f"  FAIL  {nm}  {d}")

    print("[dbview.py 자기 시험]")
    rs = scan()
    chk(f"데이터 루트에서 sqlite {len(rs)}건을 찾는다", len(rs) >= 2, str(len(rs)))
    kinds = {r["kind"] for r in rs}
    chk("종류를 표로 가른다(이름이 아니라)", "ledger" in kinds, str(sorted(kinds)))
    chk("전부 읽힌다", all(r["readable"] for r in rs),
        str([r["name"] for r in rs if not r["readable"]]))

    led = [r for r in rs if r["kind"] == "ledger"]
    chk(f"원장 후보 {len(led)}개를 드러낸다", len(led) >= 1,
        str([r["name"] for r in led]))
    if len(led) >= 2:
        chk("여러 원장이 실재한다 — 이것이 Q-09 의 실체다", True)

    env_bak = os.environ.pop("ORCH_LEDGER_DB", None)
    try:
        st = ledger_status()
        if len(led) >= 2:
            chk("여럿이면 도구가 고르지 않는다", st["chosen"] is None and not st["ok"], str(st["why"]))
            chk("무엇을 해야 하는지 말한다", "ORCH_LEDGER_DB" in st["why"])
        elif len(led) == 1:
            chk("하나뿐이면 그것을 쓴다", st["ok"] and st["by"] == "유일")
    finally:
        if env_bak is not None:
            os.environ["ORCH_LEDGER_DB"] = env_bak

    st2 = ledger_status()
    if env_bak:
        chk("환경변수가 있으면 그것이 정본", st2["by"] == "ORCH_LEDGER_DB" and st2["ok"])
        chk("도구가 고른 것이 아님을 밝힌다", "도구가 고른 것이 아니다" in st2["why"])

    # 읽기 전용 — 없는 파일에 스키마를 만들지 않는다
    import tempfile
    ghost = Path(tempfile.mkdtemp()) / "없는파일.sqlite"
    r = inspect(ghost)
    chk("없는 파일은 없다고 말한다", not r["exists"] and "없다" in r["why"])
    chk("없는 파일을 만들지 않는다", not ghost.exists())

    if led:
        pk = peek(led[0]["path"], "runs", 3)
        chk("표 내용을 들여다볼 수 있다", "rows" in pk, str(list(pk)[:6]))

    print(f"\n결과: {ok}/{ok + fail} PASS")
    return 0 if fail == 0 else 1


def _fmt(rs) -> str:
    out = []
    for r in rs:
        head = (f"  [{r['kind'] or '?':11}] {r['name']:24} {r['size']/1024:8.1f} KB"
                f"  스키마 {r['schema_version'] or '-'}")
        out.append(head)
        if r.get("span"):
            s = r["span"]
            out.append(f"      사건 {s['n_events']}건 · {s['first']} ~ {s['last']}")
        big = sorted(((n, c) for n, c in r["tables"].items() if c), key=lambda t: -t[1])[:5]
        if big:
            out.append("      " + " · ".join(f"{n} {c}" for n, c in big))
        if not r["readable"]:
            out.append(f"      {r['why']}")
    return "\n".join(out)


def main(argv):
    if len(argv) < 2:
        print(__doc__); return 2
    cmd = argv[1]
    if cmd == "self-test":
        return self_test()
    if cmd == "list":
        rs = scan(argv[2] if len(argv) > 2 else None)
        print(f"{C.data_dir()} — sqlite {len(rs)}건\n")
        print(_fmt(rs))
        return 0
    if cmd == "ledger":
        st = ledger_status()
        print(f"원장 후보 {st['n']}개 → {'정본 ' + str(st['chosen']) if st['ok'] else '정본 미정'}"
              + (f"  ({st['by']})" if st["by"] else ""))
        print(f"  {st['why']}\n")
        print(_fmt(st["candidates"]))
        if not st["ok"] and st["n"] > 1:
            print("\n  고르는 법:  export ORCH_LEDGER_DB=<위 경로 중 하나>")
        return 0 if st["ok"] else 1
    if cmd == "peek":
        r = peek(argv[2], argv[3] if len(argv) > 3 else None)
        print(_fmt([r]))
        for row in (r.get("rows") or []):
            print("   ", {k: (str(v)[:40] if v is not None else None) for k, v in row.items()})
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
