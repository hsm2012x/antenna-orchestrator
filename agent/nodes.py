#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent/nodes.py — 노드 8종 + 분기 함수 (실행기 비의존)

설계 정본: 노드는 `(state) -> state` **순수 함수**이고 클래스와 1:1이다.
여기에는 langgraph import 가 없다 — 그래야 실행기를 갈아끼울 수 있다(agent/runtime.py 주석).

규율
    · 노드는 **값을 만들지 않는다**. 도구를 부르고 그 산출을 상태에 얹을 뿐이다(N-1).
    · 모든 전이는 원장에 이벤트 1행을 남긴다(B-3). 기록 실패는 삼키지 않는다.
    · 클래스 순서는 고정이다(A-2). 분기 함수는 **문자열 라벨만** 반환한다.
    · 실패는 유형을 나눈다 — 일시(transient)만 재시도, 항구(permanent)는 즉시 HOLD.
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import _common as C          # noqa: E402
import ledger as L           # noqa: E402
import route as ROUTE        # noqa: E402
import extract as EXTRACT    # noqa: E402
import verify_api as VERIFY  # noqa: E402
import render as RENDER      # noqa: E402
import catalog as CAT        # noqa: E402
import gate as GATE          # noqa: E402
import split as SPLIT        # noqa: E402

# 상태 어휘 — orch_state_contract 그대로. 이 밖의 값을 쓰지 않는다.
STATES = ("WAIT", "ROUTE", "EXTRACT", "RETRY", "VERIFY", "COMPOSE", "ASK", "ACT",
          "REVIEW", "HOLD", "DONE")

# 일시 실패로 볼 예외 — 이 밖은 항구 실패다. "모르면 항구"가 안전하다:
# 항구를 일시로 오분류하면 같은 실패를 3번 되풀이하고 그만큼 늦게 사람에게 간다.
TRANSIENT = (TimeoutError, ConnectionError, BlockingIOError, InterruptedError)


def _ledger():
    return L.open_ledger()


def _append(run_id, kind, payload, state, attempt=1):
    """원장 기록. 실패를 삼키지 않는다 — 기록 없는 전이는 없다(B-3)."""
    conn = _ledger()
    try:
        return L.append(conn, run_id, kind, payload, state=state, attempt=attempt,
                        chosen_by="rule")
    finally:
        conn.close()


def _fail(state: dict, node: str, exc: Exception) -> dict:
    kind = "transient" if isinstance(exc, TRANSIENT) else "permanent"
    n = int(state.get("retry_count") or 0)
    payload = {"type": kind, "node": node, "error": f"{type(exc).__name__}: {exc}",
               "retry_count": n, "traceback": traceback.format_exc()[-1500:]}
    nxt = "RETRY" if kind == "transient" else "HOLD"
    payload["final_state"] = nxt
    try:
        _append(state["run_id"], "failure", payload, nxt, state.get("attempt", 1))
    except Exception:
        pass                      # 원장까지 못 쓰는 상황 — 상태는 HOLD 로 간다
    return {"state": nxt, "failure": payload,
            "retry_count": n + (1 if kind == "transient" else 0)}


# ── 노드 ────────────────────────────────────────────────────────────────────

def n_identify(s: dict) -> dict:
    """식별 — 어댑터 확정. LLM 을 넣지 않는다(재현성·게이트 사각지대·A-3)."""
    try:
        src = s["source"]
        r = ROUTE.identify(src["path"], s["run_id"], s.get("product"))
        payload = {k: r.get(k) for k in
                   ("adapter", "adapter_candidates", "contributing_lanes", "source_origin",
                    "lanes", "rejected", "signals", "rule_version", "probe_report", "outcome")}
        payload["candidates"] = payload.pop("adapter_candidates", [])
        payload["contributing"] = payload.pop("contributing_lanes", [])
        payload["numeric_rules"] = C.numeric_rules()
        payload["source_fingerprint"] = {
            "n_files": (r.get("source_fingerprint_before") or {}).get("n_files")}
        ok = r.get("outcome") == "ROUTE_OK" and r.get("adapter")
        st = "EXTRACT" if ok else "HOLD"
        _append(s["run_id"], "routing", payload, st, s.get("attempt", 1))
        return {"state": st, "routing": {
            "adapter": r.get("adapter"), "candidates": r.get("adapter_candidates") or [],
            "contributing": r.get("contributing_lanes") or [],
            "signals": r.get("signals") or [], "rule_version": r.get("rule_version"),
            "outcome": r.get("outcome")}}
    except Exception as exc:
        return _fail(s, "식별", exc)


def n_extract(s: dict) -> dict:
    """추출 — 형상·선언·이력 재생. 기여 레인도 함께 돈다(주도 + 기여)."""
    try:
        r = EXTRACT.extract(s["run_id"])
        g = r.get("geometry") or {}
        _append(s["run_id"], "stage",
                {"stage": "추출", "geom_hash": r.get("geom_hash"),
                 "lanes": sorted(g.keys()),
                 "n_geometry": sum(len(v) for v in g.values() if isinstance(v, list)),
                 "n_declared": sum(len(v) for v in (r.get("declared") or {}).values()
                                   if isinstance(v, list)),
                 "n_unreadable": len(r.get("unreadable") or []),
                 "n_annotations": len(r.get("annotations") or [])},
                "VERIFY", s.get("attempt", 1))
        return {"state": "VERIFY", "extracted": {
            "geom_hash": r.get("geom_hash"),
            "lanes": sorted(g.keys()),
            "n_unreadable": len(r.get("unreadable") or [])},
            "retry_count": 0}
    except Exception as exc:
        return _fail(s, "추출", exc)


def n_verify(s: dict) -> dict:
    """해석 — 수치의 **유일한 산지**. 임계 미지정 항목은 판정하지 않는다(I-5·N-3)."""
    try:
        r = VERIFY.interpret(s["run_id"], s.get("product"))
        items = r.get("items") or []
        _append(s["run_id"], "verify",
                {"items": items, "geom_hash": r.get("geom_hash"), "cached": r.get("cached")},
                "VERIFY", s.get("attempt", 1))
        return {"state": "VERIFY", "check": {
            "n_items": len(items), "verdict": r.get("verdict"), "reason": r.get("reason"),
            "n_unjudged": sum(1 for i in items if i.get("pass") is None)}}
    except Exception as exc:
        return _fail(s, "해석", exc)


def n_split(s: dict) -> dict:
    """후보 분해 — 결정론 신호 수집. **확정은 여기서 하지 않는다.**

    verdict=single 이면 그대로 간다(구제 없음 — 성공 경로 불가침, 울타리 ①).
    ambiguous 면 구제 경로가 제안을 만들고 `ASK` 로 사람에게 넘긴다(AMD-2026-07-31-2).
    """
    try:
        import rescue as RES
        run_id = s["run_id"]
        sig = SPLIT.collect(run_id)
        C.write_json(C.work_dir(run_id) / "분해_신호.json", sig)

        if sig["verdict"] == "single" or not RES.enabled():
            _append(run_id, "stage",
                    {"stage": "후보분해", "verdict": sig["verdict"], "why": sig["why"],
                     "rescue": "off" if not RES.enabled() else "불필요",
                     "numeric_rules": SPLIT.numeric_rules()},
                    "COMPOSE", s.get("attempt", 1))
            return {"state": "COMPOSE", "split": {"verdict": sig["verdict"],
                                                  "why": sig["why"], "asked": False}}

        triggers = RES.detect(s, split_result=sig)
        if not triggers:
            return {"state": "COMPOSE", "split": {"verdict": sig["verdict"], "asked": False}}

        trig = triggers[0]
        agent = RES.get_rescue(s.get("rescue_agent"))
        prior = list(s.get("rewind_context") or [])
        prop = agent.propose(trig, trig["evidence"], prior)

        # 구제 호출은 **재량**이다 — chosen_by="llm" + variation 으로 남긴다(울타리 ④).
        conn = _ledger()
        try:
            L.append(conn, run_id, "question",
                     {"question": f"[{trig['code']}] {trig['why']} — 안테나가 몇 개인가?",
                      "options": prop.get("options") or [],
                      "current_assumption": prop.get("proposal"),
                      "reason": prop.get("reason"), "confidence": prop.get("confidence"),
                      "evidence": trig["evidence"], "agent": agent.name,
                      "prior_attempts": prior, "answer": None, "applied": False},
                     state="ASK", attempt=s.get("attempt", 1),
                     chosen_by=("llm" if agent.name == "llm" else "rule"),
                     variation=({"kind": "tool_args", "changed": prop["changed"],
                                 "reason": prop.get("reason", ""),
                                 "prev_attempt": len(prior)}
                                if agent.name == "llm" else None))
        finally:
            conn.close()

        return {"state": "ASK",
                "split": {"verdict": sig["verdict"], "why": sig["why"], "asked": True,
                          "trigger": trig["code"], "proposal": prop.get("proposal"),
                          "options": prop.get("options"), "reason": prop.get("reason"),
                          "confidence": prop.get("confidence"), "agent": agent.name}}
    except Exception as exc:
        return _fail(s, "후보분해", exc)


def n_answer(s: dict) -> dict:
    """사람의 답을 받는다. 답이 없으면 진행하지 않는다 — 확정은 사람뿐이다(A-1)."""
    a = s.get("answer") or {}
    if not a.get("choice"):
        return {"state": "HOLD",
                "failure": {"type": "rule", "node": "분해확정",
                            "error": "answer.choice 없음 — 승인 없이 경계를 쓰지 않는다(A-1)",
                            "final_state": "HOLD"}}
    _append(s["run_id"], "human",
            {"action": "answer", "fields": a, "decided_by": a.get("decided_by"),
             "decided_at": a.get("decided_at"),
             "applied_to": (s.get("split") or {}).get("trigger")},
            "COMPOSE", s.get("attempt", 1))
    return {"state": "COMPOSE",
            "split": {**(s.get("split") or {}), "answer": a.get("choice"),
                      "confirmed_by": a.get("decided_by")}}


def n_render(s: dict) -> dict:
    """렌더 — 그리기만. 값을 만들지 않는다.

    두 갈래를 **한 단계에서** 돌린다.
      render.py   레인 산출(2D SVG · 3D HTML · 배열인자 곡선 · DWG 프리뷰)
      figures.py  그 산출을 **문서가 참조할 수 있는 항목**으로(그림도 참조다 — D-33)

    figures 를 따로 돌리게 두면 사람이 잊고, 잊으면 문서에 그림이 **한 장도 안 붙는다.**
    붙지 않아도 게이트는 통과하므로 아무도 모른다 — 그래서 파이프라인에 넣는다.
    """
    try:
        r = RENDER.render(s["run_id"])
        arts = sorted((r or {}).keys())
        fig = {"n_figures": 0, "n_failed": 0}
        try:
            import figures as FIGURES
            fr = FIGURES.build(s["run_id"])
            fig = {"n_figures": fr["n_figures"], "n_failed": fr["n_failed"]}
        except Exception as fe:        # 그림 실패가 파이프라인을 멈추지 않는다(F-27)
            fig = {"n_figures": 0, "n_failed": -1,
                   "why": f"{type(fe).__name__}: {fe}"[:200]}
        _append(s["run_id"], "stage",
                {"stage": "렌더", "artifacts": arts, "figures": fig},
                "COMPOSE", s.get("attempt", 1))
        return {"state": "COMPOSE", "render": {"artifacts": arts, "figures": fig}}
    except Exception as exc:
        return _fail(s, "렌더", exc)


def n_compose(s: dict) -> dict:
    """문서 조립 — Level 1 의 유일한 LLM 자리. 카탈로그·골격을 만들어 조립기에 넘긴다."""
    try:
        from composer_bridge import composer_for   # 지연 import — 순환 회피
        run_id = s["run_id"]
        work = C.work_dir(run_id)
        cat = CAT.build(run_id, work)
        C.write_json(work / CAT.CATALOG_NAME, cat)
        sk = CAT.skeleton(cat)
        (work / "골격.md").write_text(sk, encoding="utf-8")

        comp = composer_for(s)
        rejects = int(s.get("gate_rejects") or 0)
        draft = comp.compose(run_id, cat, sk,
                             violations=(s.get("dossier") or {}).get("violations"),
                             attempt=rejects + 1)
        (work / GATE.DRAFT_NAME).write_text(draft, encoding="utf-8")
        _append(run_id, "stage",
                {"stage": "문서조립", "composer": comp.name, "n_catalog": cat["n_entries"],
                 "n_slots": sk.count("{{"), "reject_round": rejects,
                 "unmapped_keys": cat.get("unmapped_keys") or []},
                "COMPOSE", s.get("attempt", 1))
        return {"state": "COMPOSE", "compose": {
            "composer": comp.name, "n_catalog": cat["n_entries"],
            "draft": str(work / GATE.DRAFT_NAME)}}
    except Exception as exc:
        return _fail(s, "문서조립", exc)


def n_gate(s: dict) -> dict:
    """참조 무결성 게이트 — 결정론. 문서를 고치지 않는다. 원장 미기록이면 통과가 아니다."""
    try:
        v = GATE.check_run(s["run_id"], attempt=s.get("attempt", 1))
        rejects = int(s.get("gate_rejects") or 0) + (0 if v["pass"] else 1)
        return {"state": "REVIEW" if v["pass"] else "COMPOSE",
                "gate_rejects": rejects,
                "dossier": {"gate": {"pass": v["pass"], "violations": v["violations"]},
                            "violations": v["violations"],
                            "doc_path": v.get("substituted_path"),
                            "n_refs": v.get("n_refs"),
                            "recorded": v.get("recorded")}}
    except Exception as exc:
        return _fail(s, "게이트", exc)


def n_package(s: dict) -> dict:
    """통합 문서화 — 게이트 통과본만 산출 영역(out/)에 닿는다. 판단이 없다(템플릿)."""
    try:
        import json as _json
        import package as PKG
        run_id = s["run_id"]
        work = C.work_dir(run_id)
        name = (s.get("source") or {}).get("name") or run_id
        out = C.out_dir(name)
        doc = work / GATE.SUBSTITUTED_NAME
        if not doc.exists():
            raise FileNotFoundError(f"치환본이 없다: {doc} — 게이트를 통과한 문서만 포장한다")
        r = PKG.build(run_id, work)      # md + dossier.json + index.html
        _append(run_id, "stage",
                {"stage": "통합문서화", "out_dir": r["out_dir"], "doc_path": r["doc_path"],
                 "dossier_path": r["dossier_path"], "html_path": r["html_path"],
                 "badges_active": r["badges_active"]},
                "REVIEW", s.get("attempt", 1))
        return {"state": "REVIEW", "dossier": {**(s.get("dossier") or {}), **r}}
    except Exception as exc:
        return _fail(s, "통합문서화", exc)


def n_review(s: dict) -> dict:
    """사람 검수 — 확정 권한은 사람뿐이다(A-1). interrupt 로 멈춘 뒤 주입된 값을 읽는다."""
    h = s.get("human") or {}
    action = h.get("action")
    if action not in ("approve", "reject"):
        # 실행기가 interrupt_before 로 멈추므로 여기 오면 주입이 없었다는 뜻이다.
        return {"state": "HOLD",
                "failure": {"type": "rule", "node": "사람검수",
                            "error": "human.action 없음 — 승인·반려는 사람만 넣는다(A-1)",
                            "final_state": "HOLD"}}
    st = "DONE" if action == "approve" else "HOLD"
    _append(s["run_id"], "human",
            {"action": action, "fields": h.get("fields"),
             "decided_by": h.get("decided_by"), "decided_at": h.get("decided_at")},
            st, s.get("attempt", 1))
    return {"state": st}


def n_hold(s: dict) -> dict:
    """보류 — 사람 트리거 재투입까지 멈춘다. 같은 run_id 로 이어간다(재투입 정책)."""
    f = s.get("failure") or {}
    _append(s["run_id"], "failure",
            {"type": f.get("type", "rule"), "node": f.get("node"),
             "error": f.get("error"), "retry_count": s.get("retry_count", 0),
             "final_state": "HOLD"},
            "HOLD", s.get("attempt", 1))
    return {"state": "HOLD"}


def n_retry(s: dict) -> dict:
    """재시도 — **같은 방법**을 그대로 다시. 지수 백오프. 상한은 예산이 정한다(B-1)."""
    n = int(s.get("retry_count") or 0)
    limit = int((s.get("budgets") or {}).get("max_retries") or 3)
    if n >= limit:
        return {"state": "HOLD",
                "failure": {**(s.get("failure") or {}), "type": "transient",
                            "error": f"재시도 상한 {limit} 초과", "final_state": "HOLD"}}
    delay = 2 ** n
    _append(s["run_id"], "action",
            {"branch": "retry", "attempt_of_node": (s.get("failure") or {}).get("node"),
             "retry_count": n, "backoff_s": delay},
            "RETRY", s.get("attempt", 1))
    if not s.get("no_sleep"):
        import time
        time.sleep(min(delay, 8))
    return {"state": "EXTRACT"}


# ── 분기 함수 — 문자열 라벨만 반환한다 ────────────────────────────────────────

def b_after_identify(s) -> str:
    return "진행" if s.get("state") == "EXTRACT" else "보류"


def b_after_stage(s) -> str:
    """추출·해석·렌더 공통. 일시 실패는 재시도, 항구 실패는 보류."""
    st = s.get("state")
    if st == "RETRY":
        return "재시도"
    if st == "HOLD":
        return "보류"
    return "진행"


def b_after_split(s) -> str:
    st = s.get("state")
    if st == "ASK":
        return "묻는다"
    if st == "RETRY":
        return "재시도"
    return "보류" if st == "HOLD" else "진행"


def b_after_answer(s) -> str:
    return "보류" if s.get("state") == "HOLD" else "진행"


def b_after_gate(s) -> str:
    if s.get("state") == "HOLD":
        return "보류"
    g = (s.get("dossier") or {}).get("gate") or {}
    if g.get("pass"):
        return "통과"
    limit = int((s.get("budgets") or {}).get("max_gate_rejects") or 3)
    return "반려" if int(s.get("gate_rejects") or 0) < limit else "보류"


def b_after_retry(s) -> str:
    return "보류" if s.get("state") == "HOLD" else "재개"


def b_after_review(s) -> str:
    return "완료" if s.get("state") == "DONE" else "보류"
