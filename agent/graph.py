#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent/graph.py — 펄스(오케스트레이터) 상태 머신 조립

노드와 엣지 정의는 **한 벌뿐이다**. 실행기만 갈아끼운다.

    langgraph 가 import 되면  → LangGraph StateGraph + SqliteSaver
    아니면                    → agent/runtime.py (stdlib 대역)

둘에 같은 `build_graph()` 결과를 먹인다. 그래프의 뜻은 노드·엣지에 있고 실행기에 없다.
**대역 통과는 langgraph 검증이 아니다** — 본체에서 `python tools/check_env.py --state` 가
따로 확인한다. 어느 실행기로 돌았는지는 원장·상태의 `engine` 에 남는다.

CLI
    python agent/graph.py run <원천경로> [--product P] [--run-id ID]
    python agent/graph.py resume <run_id> --action approve|reject [--by 이름]
    python agent/graph.py show <run_id>
    python agent/graph.py self-test
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "tools"))

import _common as C     # noqa: E402
import discover as DISC  # noqa: E402
import ledger as L      # noqa: E402
import manifest as MF   # noqa: E402
import nodes as N       # noqa: E402
import runtime as RT    # noqa: E402

REVIEW_NODE = "사람검수"
ANSWER_NODE = "분해확정"       # ASK 에서 사람 답을 받는 자리 — interrupt 대상


def engine_name() -> str:
    try:
        import langgraph  # noqa: F401
        from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: F401
        return "langgraph"
    except Exception:
        return "runtime"


# ── 그래프 정의 — 실행기와 무관한 한 벌 ──────────────────────────────────────

NODES = (
    ("식별", N.n_identify), ("추출", N.n_extract), ("해석", N.n_verify),
    ("후보분해", N.n_split), (ANSWER_NODE, N.n_answer),
    ("렌더", N.n_render), ("문서조립", N.n_compose), ("게이트", N.n_gate),
    ("통합문서화", N.n_package), (REVIEW_NODE, N.n_review),
    ("보류", N.n_hold), ("재시도", N.n_retry),
)

# (노드, 분기함수, {라벨: 다음})
BRANCHES = (
    ("식별", N.b_after_identify, {"진행": "추출", "보류": "보류"}),
    ("추출", N.b_after_stage, {"진행": "해석", "재시도": "재시도", "보류": "보류"}),
    ("해석", N.b_after_stage, {"진행": "후보분해", "재시도": "재시도", "보류": "보류"}),
    # 후보 분해 — single 이면 그대로, ambiguous 면 사람에게 묻는다(AMD-2026-07-31-2)
    ("후보분해", N.b_after_split, {"진행": "렌더", "묻는다": ANSWER_NODE,
                                "재시도": "재시도", "보류": "보류"}),
    (ANSWER_NODE, N.b_after_answer, {"진행": "렌더", "보류": "보류"}),
    ("렌더", N.b_after_stage, {"진행": "문서조립", "재시도": "재시도", "보류": "보류"}),
    ("문서조립", N.b_after_stage, {"진행": "게이트", "재시도": "재시도", "보류": "보류"}),
    # 게이트 → 조립 되먹임. 상한은 budgets.max_gate_rejects(정본 3).
    ("게이트", N.b_after_gate, {"통과": "통합문서화", "반려": "문서조립", "보류": "보류"}),
    ("통합문서화", N.b_after_stage, {"진행": REVIEW_NODE, "재시도": "재시도", "보류": "보류"}),
    (REVIEW_NODE, N.b_after_review, {"완료": "__end__", "보류": "보류"}),
    ("재시도", N.b_after_retry, {"재개": "추출", "보류": "보류"}),
)

EDGES = (("보류", "__end__"),)
ENTRY = "식별"


def _wire(g, END):
    for name, fn in NODES:
        g.add_node(name, fn)
    for a, b in EDGES:
        g.add_edge(a, END if b == "__end__" else b)
    for name, router, mapping in BRANCHES:
        g.add_conditional_edges(name, router,
                                {k: (END if v == "__end__" else v) for k, v in mapping.items()})
    return g


def build_app(engine: str | None = None, checkpoint_path=None):
    """실행 가능한 그래프 + 실제로 쓴 실행기 이름."""
    engine = engine or engine_name()
    if engine == "langgraph":
        import sqlite3
        from langgraph.graph import StateGraph, START, END
        from langgraph.checkpoint.sqlite import SqliteSaver
        from typing import Any, TypedDict

        # orch_state_contract 그대로 (TypedDict, total=False)
        OrchState = TypedDict("OrchState", {
            "run_id": str, "level": int, "source": dict, "routing": dict,
            "extracted": dict, "check": dict, "render": dict, "compose": dict,
            "dossier": dict, "context": dict, "dialog": list, "judgment": dict,
            "action_result": dict, "human": dict, "state": str,
            "attempt": int, "retry_count": int, "gate_rejects": int,
            "budgets": dict, "product": Any, "composer": Any,
            "composer_fault": Any, "no_sleep": bool, "engine": str,
        }, total=False)

        g = StateGraph(OrchState)
        _wire(g, END)
        g.add_edge(START, ENTRY)
        p = Path(checkpoint_path or C.checkpoint_path())
        p.parent.mkdir(parents=True, exist_ok=True)
        C.assert_writable(p)
        conn = sqlite3.connect(str(p), check_same_thread=False)
        try:
            saver = SqliteSaver(conn)
        except TypeError:
            saver = SqliteSaver.from_conn_string(str(p)).__enter__()
        return g.compile(checkpointer=saver,
                         interrupt_before=[ANSWER_NODE, REVIEW_NODE]), engine

    g = RT.StateGraph()
    _wire(g, RT.END)
    g.add_edge(RT.START, ENTRY)
    cp = RT.SqliteCheckpointer(checkpoint_path)
    return g.compile(checkpointer=cp, interrupt_before=[ANSWER_NODE, REVIEW_NODE],
                     savepoints_before=["후보분해", "문서조립"]), engine


# ── 실행 ────────────────────────────────────────────────────────────────────

def start_run(source_path, run_id: str | None = None, product=None, *,
              engine=None, checkpoint_path=None, composer=None, composer_fault=None,
              no_sleep=False, entry: dict | None = None,
              manifest: dict | None = None) -> dict:
    src = Path(source_path).resolve()
    run_id = run_id or C.new_run_id("L1")
    reg = C.load_registry()
    pname, pdef = C.resolve_product(reg, product)
    budgets = dict((pdef or {}).get("budgets") or {})

    conn = L.open_ledger()
    try:
        L.start_run(conn, run_id, str(src), source_kind="folder" if src.is_dir() else "file",
                    source_name=src.name, level=1,
                    rule_version=C.effective_rule_version(),
                    registry_version=reg.get("registry_version"))
    finally:
        conn.close()

    app, eng = build_app(engine, checkpoint_path)
    init = {"run_id": run_id, "level": 1, "state": "WAIT", "attempt": 1,
            "retry_count": 0, "gate_rejects": 0, "budgets": budgets,
            "product": product, "engine": eng, "no_sleep": no_sleep,
            "source": {"path": str(src), "kind": "folder" if src.is_dir() else "file",
                       "name": src.name}}
    if composer:
        init["composer"] = composer
    if composer_fault:
        init["composer_fault"] = composer_fault
    # entry 정보를 work/ 에 남긴다 — 카탈로그가 읽어 문서·자산 DB 로 흘려보낸다.
    # 프로젝트 태그와 파생 근거가 여기서 끊기면 배포 도안과 원본의 연결이 사라진다.
    if entry:
        C.write_json(C.work_dir(run_id) / "entry.json", entry)
        init["entry"] = {k: entry.get(k) for k in
                         ("entry_id", "kind", "project_tag", "derived_from")}

    # 입력 항목 — **선언**이다. 관측과 섞지 않는다(산지 (e) 사람 선언).
    if manifest:
        r = MF.resolve(manifest)
        C.write_json(C.work_dir(run_id) / MF.MANIFEST_NAME, r["manifest"])
        init["manifest"] = r["manifest"]
        init["manifest_filled"] = r["filled"]
        init["manifest_warnings"] = r["warnings"]

    cfg = {"configurable": {"thread_id": run_id}}
    out = app.invoke(init, cfg)
    return {"run_id": run_id, "engine": eng, "state": out.get("state"),
            "gate_rejects": out.get("gate_rejects"),
            "next": _next_of(app, cfg), "values": out}


def run_source(source_path, *, prefix=None, product=None, engine=None,
               only: str | None = None, no_sleep=False, composer=None) -> dict:
    """원천 폴더 하나 → **entry 수만큼** run.

    설계 흐름도의 첫 상자(「원천 폴더 큐 투입 · run_id 발급」)다. 클래스 순서는 건드리지
    않는다 — entry 마다 같은 파이프라인을 처음부터 돌린다(A-2).
    """
    d = DISC.scan(source_path)
    picked = [e for e in d["entries"] if not only or e["entry_id"] == only]
    if only and not picked:
        raise ValueError(f"entry 없음: {only} — 있는 것 {[e['entry_id'] for e in d['entries']]}")
    runs = []
    for e in picked:
        rid = f"{prefix}-{e['entry_id']}" if prefix else C.new_run_id(f"L1-{e['entry_id']}")
        try:
            r = start_run(e["path"], rid, product, engine=engine, no_sleep=no_sleep,
                          composer=composer, entry=e)
            runs.append({"entry_id": e["entry_id"], "kind": e["kind"], "run_id": r["run_id"],
                         "state": r["state"], "next": r["next"],
                         "gate_rejects": r["gate_rejects"],
                         "derived_from": e.get("derived_from"),
                         "link_candidates": e.get("link_candidates") or []})
        except Exception as exc:                 # 한 entry 의 실패가 나머지를 막지 않는다
            runs.append({"entry_id": e["entry_id"], "kind": e["kind"], "run_id": rid,
                         "state": "HOLD", "error": f"{type(exc).__name__}: {exc}"})
    return {"source": d["source"], "n_entries": d["n_entries"], "runs": runs,
            "unassigned": d["unassigned"]}


def answer(run_id: str, choice: str, by: str, *, engine=None, checkpoint_path=None,
           note: str | None = None) -> dict:
    """ASK 에 대한 사람의 답. **이것 없이는 경계가 쓰이지 않는다**(A-1)."""
    from datetime import datetime, timezone
    app, eng = build_app(engine, checkpoint_path)
    cfg = {"configurable": {"thread_id": run_id}}
    app.update_state(cfg, {"answer": {
        "choice": choice, "note": note, "decided_by": by,
        "decided_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}})
    out = app.invoke(None, cfg)
    return {"run_id": run_id, "engine": eng, "state": out.get("state"),
            "gate_rejects": out.get("gate_rejects"),
            "next": _next_of(app, cfg), "values": out}


def rewind(run_id: str, *, label=None, seq=None, context=None, engine=None,
           checkpoint_path=None) -> dict:
    """savepoint 로 되감아 **새 분기**를 연다. 옛 시도는 지우지 않는다.

    되감기 자체를 원장에 남긴다 — 되돌린 것도 사실이다(B-3).
    """
    app, eng = build_app(engine, checkpoint_path)
    if eng != "runtime":
        raise NotImplementedError(
            "되감기는 대역 실행기에만 있다. LangGraph 는 자체 checkpoint 분기를 쓴다 — "
            "본체에서 붙일 때 이 자리를 langgraph API 로 옮긴다.")
    r = app.cp.rewind(run_id, label=label, seq=seq, context=context)
    conn = L.open_ledger()
    try:
        L.append(conn, run_id, "action",
                 {"branch": "rewind", "from_branch": r["from_branch"],
                  "from_seq": r["from_seq"], "from_label": r["from_label"],
                  "to_branch": r["branch_id"], "context": context},
                 state="ASK", attempt=1, chosen_by="human",
                 variation={"kind": "tool_args",
                            "changed": ["boundaries"],
                            "reason": context or "사람이 되감았다",
                            "prev_attempt": r["from_seq"]})
    finally:
        conn.close()
    return r


def resume(run_id: str, human: dict, *, engine=None, checkpoint_path=None) -> dict:
    """사람 개입 재개 — `{action, fields}` 주입. 순서는 바꾸지 않는다(A-2)."""
    app, eng = build_app(engine, checkpoint_path)
    cfg = {"configurable": {"thread_id": run_id}}
    app.update_state(cfg, {"human": human})
    out = app.invoke(None, cfg)
    return {"run_id": run_id, "engine": eng, "state": out.get("state"),
            "gate_rejects": out.get("gate_rejects"),
            "next": _next_of(app, cfg), "values": out}


def _next_of(app, cfg):
    try:
        snap = app.get_state(cfg)
        return list(snap.next) if snap and snap.next else []
    except Exception:
        return []


# ── 자기 시험 — 실물 원천, LLM 0콜 ───────────────────────────────────────────

def _experiment(name: str) -> Path | None:
    base = C.data_dir() / "handoff" / "04_experiment_data"
    for p in (base / "cst_projects" / name, base / name):
        if p.exists():
            return p
    return None


def self_test(engine: str | None = None) -> int:
    import tempfile
    ok = fail = 0

    def chk(n, cond, d=""):
        nonlocal ok, fail
        if cond:
            ok += 1
            print(f"  PASS  {n}")
        else:
            fail += 1
            print(f"  FAIL  {n}  {d}")

    eng = engine or engine_name()
    print(f"[graph.py 자기 시험 — 실행기 {eng} · LLM 0콜]")
    if eng != "langgraph":
        print("  ※ langgraph 미설치 — stdlib 대역으로 돈다. **이 통과는 langgraph 검증이 아니다.**")

    def _ev(rid):
        c = L.open_ledger()
        try:
            return L.events(c, rid)
        except Exception:
            return []
        finally:
            c.close()

    src = _experiment("test2")
    if not src:
        print("  건너뜀 — 실물 원천 없음")
        return 2
    cp = Path(tempfile.mkdtemp()) / "checkpoints.sqlite"
    import os
    os.environ["ORCH_CHECKPOINT_DB"] = str(cp)

    def _run_to_review(rid, **kw):
        """ASK 가 뜨면 답하고 검수까지 간다 — 구제 경로가 생겨 한 단계 늘었다."""
        r = start_run(src, run_id=rid, engine=eng, no_sleep=True, **kw)
        if r["state"] == "ASK":
            r = answer(rid, "안테나 하나다 — 틈은 칩·블록 경계다", "self-test", engine=eng)
        return r

    # 1) 정상 경로 — 식별→…→게이트→통합문서화→(검수 앞에서 멈춤)
    r = _run_to_review("g1-test2")
    chk("검수 앞에서 멈춘다(interrupt)", r["next"] == ["사람검수"], str(r["next"]))
    chk("게이트 통과 · 반려 0회", r["gate_rejects"] == 0 and
        (r["values"].get("dossier") or {}).get("gate", {}).get("pass") is True,
        json.dumps(r["values"].get("dossier"), ensure_ascii=False)[:200])
    chk("통합문서화 산출", Path((r["values"].get("dossier") or {}).get("doc_path", "x")).exists())
    chk("상태 어휘 준수", r["state"] in N.STATES, str(r["state"]))

    # 2) 사람 승인 재개
    r2 = resume("g1-test2", {"action": "approve", "decided_by": "self-test"}, engine=eng)
    chk("승인 → DONE", r2["state"] == "DONE", str(r2["state"]))
    chk("종료 후 다음 노드 없음", r2["next"] == [], str(r2["next"]))

    # 3) 사람 반려 → HOLD
    _run_to_review("g2-test2")
    r3 = resume("g2-test2", {"action": "reject", "fields": {"why": "시험"},
                             "decided_by": "self-test"}, engine=eng)
    chk("반려 → HOLD", r3["state"] == "HOLD", str(r3["state"]))

    # 4) 게이트 반려 고리 — 결함을 주입하고 되먹임을 실물로 돈다
    conn0 = L.open_ledger()
    for fault in ("bare_number", "undefined_key", "role_mismatch",
                  "template_modified", "prose_unwritten"):
        rid = f"g3-{fault}"
        # 원장은 append-only 다 — 앞선 시험 실행의 이벤트가 같은 run_id 에 남아 있다.
        # 이번 실행의 이벤트만 보려면 시작 시점의 event_id 를 기준으로 잘라야 한다.
        since = max([e["event_id"] for e in L.events(conn0, rid)] or [0])
        r4 = _run_to_review(rid, composer_fault=fault)
        chk(f"반려 고리 1회 후 통과 ({fault})",
            r4["gate_rejects"] == 1 and r4["next"] == ["사람검수"],
            f"rejects={r4['gate_rejects']} next={r4['next']}")
        # 첫 게이트 이벤트가 **주입한 결함 그대로** 반려했는지 — 우연한 반려가 아님을 확인
        first = next((e for e in L.events(conn0, rid)
                      if e["kind"] == "gate" and e["event_id"] > since), None)
        got = set((first or {}).get("payload", {}).get("violation_kinds") or [])
        chk(f"반려 사유가 주입한 결함과 일치 ({fault})", got == {fault}, str(got))
    conn0.close()

    # 4b) 반려 상한 — 계속 실패하면 HOLD 로 간다 (budgets.max_gate_rejects)
    # 주의: 상태는 체크포인트로 **JSON 직렬화**된다. 살아 있는 객체를 상태에 넣으면
    # 재개 시 문자열로 되살아나 조용히 무시된다 — 그래서 결함은 문자열로 지정한다.
    r5 = _run_to_review("g4-limit", composer_fault="always_bad")
    limit = int((r5["values"].get("budgets") or {}).get("max_gate_rejects") or 3)
    rej = (r5["values"].get("gate_rejects") or r5.get("gate_rejects") or 0)
    chk(f"반려 상한 {limit} 도달 → HOLD",
        r5["state"] == "HOLD" and rej == limit,
        f"state={r5['state']} rejects={rej}")

    # 4c) 식별 실패 → HOLD (판독 대상이 없는 폴더)
    empty = Path(tempfile.mkdtemp()) / "빈원천"
    empty.mkdir()
    r6 = start_run(empty, run_id="g5-empty", engine=eng, no_sleep=True)
    chk("판독 대상 없음 → HOLD", r6["state"] == "HOLD", str(r6["state"]))

    # 4d) 구제 경로 — ASK · 되감기 · 답 (AMD-2026-07-31-2)
    import rescue as RES
    rid = "g6-rescue"
    since = max([e["event_id"] for e in _ev(rid)] or [0])
    r7 = start_run(src, run_id=rid, engine=eng, no_sleep=True)
    chk("경계 신호가 엇갈리면 ASK 로 멈춘다",
        r7["state"] == "ASK" and r7["next"] == [ANSWER_NODE],
        f"{r7['state']} {r7['next']}")
    sp = (r7["values"].get("split") or {})
    chk("결정론이 확정하지 않았다", sp.get("verdict") == "ambiguous", str(sp))
    chk("구제 제안에 선택지가 있다", bool(sp.get("options")), str(sp.get("options")))
    q = [e for e in _ev(rid) if e["kind"] == "question" and e["event_id"] > since]
    chk("질문이 원장에 남는다", len(q) == 1, str(len(q)))
    chk("가정·증거가 함께 남는다",
        {"current_assumption", "evidence", "options"} <= set(q[0]["payload"]),
        str(list(q[0]["payload"])))

    # 답 없이 진행하면 HOLD — 승인 없이 경계를 쓰지 않는다(A-1)
    app0, _ = build_app(eng)
    cfg0 = {"configurable": {"thread_id": rid}}
    out0 = app0.invoke(None, cfg0)
    chk("답이 없으면 HOLD (A-1)", out0.get("state") == "HOLD", str(out0.get("state")))

    # 되감기 → 새 분기, 옛 시도 보존
    rid2 = "g7-rewind"
    start_run(src, run_id=rid2, engine=eng, no_sleep=True)
    rw = rewind(rid2, label="후보분해_전", context="반려: 칩 경계를 안테나 경계로 읽지 마라")
    chk("되감기가 새 분기를 연다", rw["branch_id"] != rw["from_branch"], str(rw))
    app1, _ = build_app(eng)
    chk("옛 분기가 보존된다", len(app1.cp.history(rid2, "main")) >= 5,
        str(len(app1.cp.history(rid2, "main"))))
    chk("되감기가 원장에 남는다",
        any(e["kind"] == "action" and (e["payload"] or {}).get("branch") == "rewind"
            for e in _ev(rid2)))
    out1 = app1.invoke(None, {"configurable": {"thread_id": rid2}})
    chk("분기에서 재개해 다시 묻는다", out1.get("state") == "ASK", str(out1.get("state")))
    ctx = out1.get("rewind_context") or []
    chk("반려 사유가 다음 시도에 실린다", any("칩 경계" in c for c in ctx), str(ctx))

    # 답하면 이어간다
    r8 = answer(rid2, "안테나 하나다 — 틈은 칩·블록 경계다", "self-test", engine=eng)
    chk("답하면 검수까지 간다", r8["next"] == [REVIEW_NODE], f"{r8['state']} {r8['next']}")
    chk("답이 원장에 남는다",
        any(e["kind"] == "human" and (e["payload"] or {}).get("action") == "answer"
            for e in _ev(rid2)))

    # 구제를 끄면 개정 전 동작
    import os as _os
    _os.environ["ORCH_RESCUE"] = "off"
    r9 = start_run(src, run_id="g8-off", engine=eng, no_sleep=True)
    _os.environ.pop("ORCH_RESCUE", None)
    chk("ORCH_RESCUE=off → 묻지 않고 진행", r9["next"] == [REVIEW_NODE],
        f"{r9['state']} {r9['next']}")

    # 울타리 — 허용 밖 변경은 거부
    for bad in (["게이트완화"], []):
        try:
            RES._validate({"changed": bad})
            chk(f"울타리: {bad or '빈 changed'} 거부", False, "통과해 버렸다")
        except ValueError:
            chk(f"울타리: {bad or '빈 changed'} 거부", True)

    # 5) 원장 — 모든 전이가 남았나
    conn = L.open_ledger()
    ev = L.events(conn, "g1-test2")
    kinds = [e["kind"] for e in ev]
    chk("원장에 전이 전수 기록", {"routing", "stage", "verify", "gate", "human"} <= set(kinds),
        str(kinds))
    chk("원장 현재 상태 = DONE", L.current_state(conn, "g1-test2") == "DONE",
        str(L.current_state(conn, "g1-test2")))
    ev4 = [e for e in L.events(conn, "g3-bare_number") if e["kind"] == "gate"]
    chk("게이트 반려·통과가 둘 다 남는다", len(ev4) >= 2, str(len(ev4)))
    conn.close()

    # 6) 체크포인트 왕복 (thread_id = run_id)
    app, _ = build_app(eng)
    snap = app.get_state({"configurable": {"thread_id": "g1-test2"}})
    chk("체크포인트에서 상태 복원", snap is not None and snap.values.get("state") == "DONE",
        str(snap))
    chk("thread_id = run_id", snap.values.get("run_id") == "g1-test2")

    # 7) 그래프 정의 무결성
    chk("노드 12종", len(NODES) == 12, str(len(NODES)))
    defined = {n for n, _ in NODES} | {"__end__"}
    dangling = sorted({v for _, _, m in BRANCHES for v in m.values()} - defined)
    chk("분기가 정의된 노드만 가리킨다", not dangling, str(dangling))
    unreachable = defined - {"__end__", ENTRY} - {v for _, _, m in BRANCHES for v in m.values()} \
        - {b for _, b in EDGES}
    chk("도달 불가 노드 없음", not unreachable, str(sorted(unreachable)))
    labels = {lbl for _, _, m in BRANCHES for lbl in m}
    chk("분기 라벨 어휘",
        labels <= {"진행", "보류", "재시도", "반려", "통과", "완료", "재개", "묻는다"},
        str(labels))

    # 8) 순서 고정 — 클래스 순서를 바꾸는 경로가 없다(A-2)
    order = ["식별", "추출", "해석", "후보분해", "렌더", "문서조립", "게이트",
             "통합문서화", REVIEW_NODE]
    forward = {n: m.get("진행") or m.get("통과") for n, _, m in BRANCHES}
    chain = [order[0]]
    while forward.get(chain[-1]) and forward[chain[-1]] in order:
        chain.append(forward[chain[-1]])
    chk("정상 경로가 설계 순서와 같다", chain == order, str(chain))

    os.environ.pop("ORCH_CHECKPOINT_DB", None)
    print(f"\n결과: {ok}/{ok + fail} PASS")
    return 0 if fail == 0 else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="graph.py")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run"); r.add_argument("source"); r.add_argument("--product")
    r.add_argument("--run-id"); r.add_argument("--engine"); r.add_argument("--composer")
    r.add_argument("--entry", help="이 entry 하나만 돌린다")
    r.add_argument("--project", help="입력 항목 자동 채움 원천(registry/projects.yaml)")
    r.add_argument("--kind", help="자산 종류(registry/asset_kinds.yaml)")
    r.add_argument("--band", help="주파수 대역(registry/bands.yaml)")
    r.add_argument("--asset", help="계보 자산 id (antenna_v1 · antenna_v2 …)")
    r.add_argument("--manifest", help="입력 항목 JSON 파일")
    r.add_argument("--no-split", action="store_true",
                   help="분해하지 않고 폴더를 통째로 한 run 으로 (구동작)")
    d = sub.add_parser("discover"); d.add_argument("source")
    rb = sub.add_parser("resume-branch"); rb.add_argument("run_id"); rb.add_argument("--engine")
    v = sub.add_parser("resume"); v.add_argument("run_id")
    v.add_argument("--action", required=True, choices=["approve", "reject"])
    v.add_argument("--by", default="cli"); v.add_argument("--engine")
    an = sub.add_parser("answer"); an.add_argument("run_id")
    an.add_argument("--choice", required=True); an.add_argument("--by", default="cli")
    an.add_argument("--note"); an.add_argument("--engine")
    rw = sub.add_parser("rewind"); rw.add_argument("run_id")
    rw.add_argument("--label"); rw.add_argument("--seq", type=int)
    rw.add_argument("--context", required=True)
    br = sub.add_parser("branches"); br.add_argument("run_id")
    s = sub.add_parser("show"); s.add_argument("run_id")
    t = sub.add_parser("self-test"); t.add_argument("--engine")
    a = ap.parse_args(argv)

    if a.cmd == "self-test":
        return self_test(a.engine)
    if a.cmd == "discover":
        import discover as _D
        return _D.main(["discover.py", "scan", a.source])
    if a.cmd == "run":
        mf = None
        if getattr(a, "manifest", None):
            mf = C.read_json(a.manifest)
        elif any(getattr(a, k, None) for k in ("project", "kind", "band", "asset")):
            mf = {"project": a.project, "asset_kind": a.kind, "band": a.band,
                  "asset_id": a.asset}
        if mf:
            rr = MF.resolve(mf)
            print(f"[입력 항목] 자동 채움 {len(rr['filled'])}건 "
                  f"· 대역 {MF.operating_range(rr['manifest'])} GHz")
            for w in rr["warnings"]:
                print(f"  [주의] {w['code']}: {w['why']}")
        if a.no_split:
            out = start_run(a.source, a.run_id, a.product, engine=a.engine,
                            composer=a.composer, manifest=mf)
            print(f"run_id {out['run_id']} · 실행기 {out['engine']} · 상태 {out['state']} "
                  f"· 게이트 반려 {out['gate_rejects']}회 · 다음 {out['next']}")
            if out["next"]:
                print(f"  검수: python agent/graph.py resume {out['run_id']} --action approve")
            return 0
        out = run_source(a.source, prefix=a.run_id, product=a.product, engine=a.engine,
                         only=a.entry, composer=a.composer)
        print(f"{out['source']}  → entry {out['n_entries']}개")
        for r in out["runs"]:
            line = (f"  [{r['kind']}] {r['entry_id']:<32} run {r['run_id']:<28} "
                    f"{r['state']}")
            if r.get("gate_rejects"):
                line += f" · 반려 {r['gate_rejects']}회"
            print(line)
            if r.get("error"):
                print(f"      실패: {r['error']}")
            if r.get("derived_from"):
                print(f"      ← 확정 연결: {r['derived_from']}")
            for c in r.get("link_candidates") or []:
                print(f"      ~ 후보 연결: {c['project_tag']} (신뢰 {c['confidence']}) [확정: 사람]")
        wait = [r["run_id"] for r in out["runs"] if r.get("next")]
        if wait:
            print(f"\n  검수 대기 {len(wait)}건:")
            for w in wait:
                print(f"    python agent/graph.py resume {w} --action approve --by <이름>")
        if out["unassigned"]:
            print(f"\n  미분류 {len(out['unassigned'])}건 — 규칙을 늘려야 하는지 사람이 본다")
        return 0
    if a.cmd == "resume":
        from datetime import datetime, timezone
        out = resume(a.run_id, {"action": a.action, "decided_by": a.by,
                                "decided_at": datetime.now(timezone.utc).isoformat()},
                     engine=a.engine)
        print(f"run_id {out['run_id']} · 상태 {out['state']}")
        return 0
    if a.cmd == "resume-branch":
        app, eng = build_app(a.engine)
        cfg = {"configurable": {"thread_id": a.run_id}}
        out = app.invoke(None, cfg)
        print(f"run_id {a.run_id} · 분기 {app.cp.current_branch(a.run_id)} "
              f"· 상태 {out.get('state')} · 다음 {_next_of(app, cfg)}")
        return 0
    if a.cmd == "answer":
        out = answer(a.run_id, a.choice, a.by, engine=a.engine, note=a.note)
        print(f"run_id {out['run_id']} · 상태 {out['state']} · 다음 {out['next']}")
        if out["next"]:
            print(f"  검수: python agent/graph.py resume {out['run_id']} --action approve")
        return 0
    if a.cmd == "rewind":
        r = rewind(a.run_id, label=a.label, seq=a.seq, context=a.context)
        print(f"되감기: {r['from_branch']}#{r['from_seq']}({r['from_label']}) "
              f"→ 분기 {r['branch_id']} · 다음 {r['next_node']}")
        print(f"  주입: {r['context']}")
        print(f"  이어가기: python agent/graph.py resume-branch {a.run_id}")
        return 0
    if a.cmd == "branches":
        app, eng = build_app()
        print(f"실행기 {eng}")
        for b in app.cp.branches(a.run_id):
            print(f"  {b['branch_id']:<6} 체크포인트 {b['n']:<3} parent_seq={b['parent_seq']} "
                  f"· {b['context'] or ''}")
        print("\n  savepoints:")
        for sp in app.cp.savepoints(a.run_id):
            print(f"    {sp['branch_id']:<6} #{sp['seq']:<3} {sp['label']}")
        return 0
    if a.cmd == "show":
        app, eng = build_app()
        snap = app.get_state({"configurable": {"thread_id": a.run_id}})
        conn = L.open_ledger()
        print(f"실행기 {eng} · 체크포인트 상태 {snap.values.get('state') if snap else None} "
              f"· 다음 {list(snap.next) if snap else []}")
        print(f"원장 현재 상태 {L.current_state(conn, a.run_id)}")
        for e in L.events(conn, a.run_id):
            print(f"  #{e['event_id']:<4} {e['kind']:<8} {e['state']}")
        conn.close()
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
