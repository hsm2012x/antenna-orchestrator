#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/relate.py — 안테나 사이의 **관계 제안** · 질문 만들기 (LLM 0콜)

무엇을 하나
    `discover.py` 가 나눈 entry 들 사이의 관계를 **제안**한다. 확정하지 않는다(A-1).
    `ISSUES.md` §2.2 의 경우의 수 A~G 를 코드로 옮긴 것이다.

관계 어휘 — 다섯. 늘리지 않는다
    `derived`  프로젝트에서 뽑은 배포 도안. CST 가 **스스로 선언한** 임포트 경로 일치
               → **자동 확정**(선언이 근거다). `discover.py` 가 이미 정한다
    `variant`  같은 계열의 다른 판본. 판올림 표식 짝 + 기하 유사
    `sibling`  같은 프로젝트에 쓰이지만 **용도가 다른** 안테나
    `foreign`  근원이 다르다. **연결이 아니라 경고다** — 경로를 사람에게 보여준다
    `unknown`  신호가 엇갈린다 → **묻는다**(ASK)

★ B 와 C 를 기계가 가를 수 있나 — **부분적으로만**
    판올림 표식이 있고 기하가 거의 같으면 `variant`, 표식이 없고 기하가 다르면 `sibling`.
    그런데 **표식 없는 판올림**과 **용도가 비슷한 동거**는 신호가 같다 —
    둘 다 "표식 없음 + 기하 유사"다. 그래서 그 칸은 **`unknown` 으로 보내 묻는다.**
    가를 수 없는 것을 가른 척하지 않는 것이 이 도구의 전부다(N-3).

판정 격자 — 두 축뿐이다
                        │ 기하 유사            │ 기하 상이
    ────────────────────┼─────────────────────┼──────────────────────
    같은 컨테이너 · 표식 짝 │ variant (제안)       │ unknown — 표식은 판본인데 형상이 다르다
    같은 컨테이너 · 표식 없음│ **unknown** — B/C 축퇴 │ sibling (제안)
    다른 컨테이너         │ unknown — 연결 근거 필요 │ foreign (보고)

CLI
    python tools/relate.py propose <원천폴더>
    python tools/relate.py self-test
"""
from __future__ import annotations

import sys
from itertools import combinations
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402
import split as SP  # noqa: E402

DERIVED, VARIANT, SIBLING, FOREIGN, UNKNOWN = (
    "derived", "variant", "sibling", "foreign", "unknown")
RELATIONS = (DERIVED, VARIANT, SIBLING, FOREIGN, UNKNOWN)

# 확정 주체 — 선언이 근거인 `derived` 만 자동이다. 나머지는 전부 사람(A-1).
AUTO = (DERIVED,)

# 기하 유사로 보는 판정 — revision.revision_diff 의 verdict 를 그대로 쓴다
GEOM_SIMILAR = ("identical", "uniform-scale", "aperture-taper-tune", "local-tune")
GEOM_DIFFERENT = ("topology-change",)


def numeric_rules() -> dict:
    return {"auto_confirm": list(AUTO),
            "geom_similar": list(GEOM_SIMILAR),
            "geom_different": list(GEOM_DIFFERENT),
            "규율": ("`derived` 만 자동 확정한다 — 근거가 **선언**이기 때문이다. 나머지는 "
                   "제안이고 확정은 사람이다(A-1). 가를 수 없는 칸은 `unknown` 으로 "
                   "보내 묻는다 — 가른 척하지 않는다(N-3)."),
            "산지": "ISSUES.md §2.2 경우의 수 A~G (사용자 확정 2026-07-31)"}


# ── 신호 ────────────────────────────────────────────────────────────────────

def version_pair(a_name: str, b_name: str) -> dict:
    """판올림 표식 **짝**인가. 표식만으로는 부족하다 — 나머지 이름이 같아야 한다.

    `Top_20260227` / `Bottom_20260227` 은 같은 날짜 표식이지만 판본이 아니라 **면**이다.
    그래서 표식이 **서로 달라야** 판본 짝으로 본다(결함 F-22 의 교훈).
    """
    sa, ma = SP._strip_mark(a_name)
    sb, mb = SP._strip_mark(b_name)
    same_stem = bool(sa) and sa == sb
    return {"pair": same_stem and bool(ma) and bool(mb) and ma != mb,
            "stem": sa if same_stem else None, "marks": [ma, mb],
            "why": ("같은 이름 + 서로 다른 판올림 표식" if (same_stem and ma and mb and ma != mb)
                    else "표식이 같다 — 판본이 아니라 같은 판의 다른 면일 수 있다"
                    if (same_stem and ma and ma == mb) else "판올림 짝이 아니다")}


def geom_relation(arr_a: dict | None, arr_b: dict | None) -> dict:
    """기하가 닮았나. `revision.py` 의 판정을 그대로 쓴다 — 판단 규칙을 두 벌 만들지 않는다."""
    if not arr_a or not arr_b:
        return {"class": "unknown", "verdict": None,
                "why": "한쪽에 배열 추출이 없다 — 기하로 판단할 수 없다"}
    import revision as RV
    d = RV.revision_diff(arr_a, arr_b)
    v = d.get("verdict")
    cls = ("similar" if v in GEOM_SIMILAR else
           "different" if v in GEOM_DIFFERENT else "unknown")
    return {"class": cls, "verdict": v, "why": d.get("why", ""),
            "n_A": d.get("n_A"), "n_B": d.get("n_B")}


# ── 관계 판정 ───────────────────────────────────────────────────────────────

def classify(a: dict, b: dict, arr_a=None, arr_b=None) -> dict:
    """entry 두 개 → 관계 **제안**. 격자는 모듈 머리말의 표 그대로다."""
    basis = []
    # ① 선언이 있으면 그것이 이긴다 — derived 는 자동 확정
    if b.get("derived_from") == a.get("entry_id") or a.get("derived_from") == b.get("entry_id"):
        return {"relation": DERIVED, "confirmed_by": "선언",
                "confidence": 1.0,
                "basis": [{"signal": "declared_import",
                           "says": "CST 가 스스로 선언한 임포트 경로가 일치한다"}],
                "why": "선언이 근거다 — 추정이 아니다"}

    same_container = (a.get("project_tag") and a.get("project_tag") == b.get("project_tag"))
    basis.append({"signal": "container",
                  "says": "같은 컨테이너" if same_container else "다른 컨테이너",
                  "a": a.get("project_tag"), "b": b.get("project_tag")})

    vp = version_pair(a.get("entry_id", ""), b.get("entry_id", ""))
    basis.append({"signal": "version_mark", "says": vp["why"], "pair": vp["pair"]})

    g = geom_relation(arr_a, arr_b)
    basis.append({"signal": "geometry", "says": g["why"], "class": g["class"],
                  "verdict": g["verdict"]})

    if not same_container:
        if g["class"] == "different" or g["class"] == "unknown":
            return {"relation": FOREIGN, "confirmed_by": None, "confidence": 0.0,
                    "basis": basis,
                    "why": ("컨테이너가 다르고 연결 근거가 없다. **연결이 아니라 보고**다 — "
                            "사람이 실수로 무관한 안테나를 한 폴더에 넣었을 수 있다. "
                            "두 경로를 나란히 보여준다"),
                    "paths": [a.get("rel"), b.get("rel")]}
        return {"relation": UNKNOWN, "confirmed_by": None, "confidence": 0.0, "basis": basis,
                "why": "컨테이너는 다른데 기하가 닮았다 — 연결 근거가 필요하다. 묻는다"}

    if vp["pair"]:
        if g["class"] == "similar":
            return {"relation": VARIANT, "confirmed_by": None, "confidence": 0.7, "basis": basis,
                    "why": "판올림 표식 짝 + 기하 유사 — 같은 계열의 다른 판본으로 **제안**한다"}
        return {"relation": UNKNOWN, "confirmed_by": None, "confidence": 0.0, "basis": basis,
                "why": ("표식은 판본이라는데 형상이 다르다 — 신호가 엇갈린다. "
                        "표식을 재사용했거나 실제로 다른 안테나다. 묻는다")}

    if g["class"] == "different":
        return {"relation": SIBLING, "confirmed_by": None, "confidence": 0.6, "basis": basis,
                "why": "같은 컨테이너 + 표식 없음 + 기하 상이 — 용도가 다른 동거로 **제안**한다"}

    # ★ 가를 수 없는 칸 — 표식 없는 판올림과 용도 비슷한 동거는 신호가 같다
    return {"relation": UNKNOWN, "confirmed_by": None, "confidence": 0.0, "basis": basis,
            "why": ("표식이 없고 기하가 닮았다 — **표식 없는 판올림**과 **용도가 비슷한 "
                    "동거**가 같은 신호를 낸다. 기계가 가를 수 없는 자리다(ISSUES §2.2)")}


# ── 질문 만들기 — 빈손으로 묻지 않는다 ──────────────────────────────────────

def question_for(a: dict, b: dict, rel: dict) -> dict | None:
    """`unknown` 이면 질문을 만든다. **가정을 들고** 묻는다(`current_assumption`).

    계약(`orch_ledger_contract` `kind=question`)의 페이로드 모양을 그대로 쓴다 —
    새 모양을 만들지 않는다.
    """
    if rel["relation"] != UNKNOWN:
        return None
    ai, bi = a.get("entry_id"), b.get("entry_id")
    return {
        "question": f"`{ai}` 와 `{bi}` 는 어떤 관계인가?",
        "options": [
            {"value": VARIANT, "label": "같은 안테나의 다른 판본 — 각각 처리하고 판본으로 잇는다"},
            {"value": SIBLING, "label": "같은 프로젝트의 다른 용도 안테나 — 각각 처리하고 태그만 공유"},
            {"value": FOREIGN, "label": "근원이 다르다 — 잇지 않는다"},
            {"value": "hold", "label": "모르겠다 — 보류(HOLD)"},
        ],
        "current_assumption": UNKNOWN,
        "why": rel["why"],
        "basis": rel["basis"],
        "paths": [a.get("rel"), b.get("rel")],
        "규율": ("제안을 들고 묻는다 — 빈손으로 묻지 않는다. 답하지 않으면 진행하지 않는다"
               "(관계는 확정이 필요한 값이다). '모르겠다' 는 HOLD 다"),
    }


# ── 원천 하나 훑기 ──────────────────────────────────────────────────────────

def _array_of(entry: dict) -> dict | None:
    """entry 의 배열 기하. 추출 산출이 있으면 거기서, 없으면 원천에서 직접 뽑지 않는다(N-1)."""
    import glob
    import json
    for p in glob.glob(str(C.data_dir() / "work" / "*" / "추출_결과.json")):
        try:
            ex = json.loads(Path(p).read_text(encoding="utf-8"))
        except Exception:
            continue
        src = str((ex.get("source") or {}).get("path") or "")
        if not src or Path(src).name != Path(entry.get("path", "")).name:
            continue
        for d in (ex.get("geometry") or {}).get("dxf") or []:
            if d.get("array"):
                return d["array"]
    return None


def propose(source_path) -> dict:
    import discover as DS
    sc = DS.scan(source_path)
    entries = sc["entries"]
    arrs = {e["entry_id"]: _array_of(e) for e in entries}

    pairs, questions = [], []
    for a, b in combinations(entries, 2):
        rel = classify(a, b, arrs.get(a["entry_id"]), arrs.get(b["entry_id"]))
        rec = {"from": a["entry_id"], "to": b["entry_id"], **rel}
        pairs.append(rec)
        q = question_for(a, b, rel)
        if q:
            questions.append({"pair": [a["entry_id"], b["entry_id"]], **q})

    n = {r: sum(1 for p in pairs if p["relation"] == r) for r in RELATIONS}
    return {"source": str(source_path), "n_entries": len(entries), "n_pairs": len(pairs),
            "counts": n, "pairs": pairs, "questions": questions,
            "numeric_rules": numeric_rules(),
            "state": "ASK" if questions else "OK",
            "규율": ("관계는 **제안**이다. `derived` 만 선언이 근거라 자동 확정된다. "
                   "나머지는 사람이 확정한다(A-1). `foreign` 은 연결이 아니라 **보고**다")}


# ── 자기 시험 ────────────────────────────────────────────────────────────────

def self_test() -> int:
    ok = fail = 0

    def chk(n, cond, d=""):
        nonlocal ok, fail
        if cond:
            ok += 1; print(f"  PASS  {n}")
        else:
            fail += 1; print(f"  FAIL  {n}  {d}")

    print("[relate.py 자기 시험]")

    E = lambda i, tag=None, der=None, rel=None: {
        "entry_id": i, "project_tag": tag or i, "derived_from": der, "rel": rel or i}
    A = lambda n=10, k=1.0: {"patches": [
        {"x0": j * 18.0, "x1": j * 18.0 + 9.0 * k, "L_mm": 9.0 * k, "W_mm": 14.0}
        for j in range(n)], "pitch_mm": [18.0] * (n - 1), "pitch_mean_mm": 18.0}

    # ── D — 선언이 근거면 자동 확정
    r = classify(E("test2"), E("배포_도안", der="test2"))
    chk("D 선언 임포트 → derived 자동 확정",
        r["relation"] == DERIVED and r["confirmed_by"] == "선언", str(r["relation"]))
    chk("자동 확정은 derived 뿐", AUTO == (DERIVED,))

    # ── B — 판올림 표식 짝 + 기하 유사
    r = classify(E("ant_v1", "P"), E("ant_v2", "P"), A(), A(k=1.01))
    chk("B 표식 짝 + 기하 유사 → variant 제안", r["relation"] == VARIANT, str(r))
    chk("variant 는 확정이 아니다", r["confirmed_by"] is None)

    # ── C — 표식 없음 + 기하 상이
    r = classify(E("rx_array", "P"), E("tx_horn", "P"), A(10), A(4))
    chk("C 표식 없음 + 기하 상이 → sibling 제안", r["relation"] == SIBLING, str(r))

    # ── ★ 가를 수 없는 칸 — 표식 없는 판올림 vs 용도 비슷한 동거
    r = classify(E("ant_a", "P"), E("ant_b", "P"), A(), A(k=1.01))
    chk("표식 없음 + 기하 유사 → unknown (가른 척하지 않는다)", r["relation"] == UNKNOWN, str(r))
    chk("가를 수 없는 이유를 밝힌다", "같은 신호를 낸다" in r["why"], r["why"])

    # ── 표식은 판본인데 형상이 다르다 → 묻는다
    r = classify(E("ant_v1", "P"), E("ant_v2", "P"), A(10), A(4))
    chk("표식 짝 + 기하 상이 → unknown", r["relation"] == UNKNOWN, str(r["relation"]))
    chk("신호가 엇갈린다고 말한다", "엇갈린다" in r["why"])

    # ── E — 컨테이너가 다르고 근거가 없다 → foreign(보고)
    r = classify(E("eco", "eco", rel="Antenna_CAD_ECO"), E("t2", "t2", rel="cst/test2"),
                 A(10), A(4))
    chk("E 다른 컨테이너 + 기하 상이 → foreign", r["relation"] == FOREIGN, str(r["relation"]))
    chk("foreign 은 연결이 아니라 보고", "보고" in r["why"])
    chk("두 경로를 나란히 보여준다", r.get("paths") == ["Antenna_CAD_ECO", "cst/test2"],
        str(r.get("paths")))

    # ── 결함 F-22 의 교훈 — 표식이 같으면 판본 짝이 아니다
    vp = version_pair("Top_20260227", "Bottom_20260227")
    chk("같은 날짜 표식은 판본 짝이 아니다", not vp["pair"], str(vp))
    vp2 = version_pair("ant_v1", "ant_v2")
    chk("서로 다른 표식 + 같은 이름이라야 짝", vp2["pair"], str(vp2))

    # ── F·G — 질문은 가정을 들고 묻는다
    r = classify(E("ant_a", "P"), E("ant_b", "P"), A(), A(k=1.01))
    q = question_for(E("ant_a", "P"), E("ant_b", "P"), r)
    chk("unknown 이면 질문을 만든다", q is not None)
    chk("계약 페이로드 모양", {"question", "options", "current_assumption"} <= set(q))
    chk("선택지 넷 — 관계 셋 + 모르겠다", len(q["options"]) == 4
        and q["options"][-1]["value"] == "hold", str([o["value"] for o in q["options"]]))
    chk("가정을 들고 묻는다", q["current_assumption"] == UNKNOWN)
    chk("근거를 함께 보여준다", len(q["basis"]) == 3,
        str([b["signal"] for b in q["basis"]]))
    chk("unknown 이 아니면 묻지 않는다",
        question_for(E("a"), E("b", der="a"),
                     classify(E("a"), E("b", der="a"))) is None)

    # ── 기하를 모르면 판단하지 않는다
    g = geom_relation(None, A())
    chk("한쪽 기하가 없으면 unknown", g["class"] == "unknown", str(g))

    # ── 실물
    base = C.data_dir() / "handoff" / "04_experiment_data"
    if base.exists():
        r = propose(base)
        chk(f"실물 entry {r['n_entries']} · 쌍 {r['n_pairs']}", r["n_pairs"] == 3, str(r["n_pairs"]))
        # ECO 와 test2 는 서로 다른 안테나다 — 연결되지 않는 것이 정답(I-I)
        pr = {(p["from"], p["to"]): p for p in r["pairs"]}
        eco_t2 = pr.get(("test2", "Antenna_CAD_ECO")) or pr.get(("Antenna_CAD_ECO", "test2"))
        chk("실물: 서로 다른 원천은 잇지 않는다",
            eco_t2 and eco_t2["relation"] in (FOREIGN, UNKNOWN), str(eco_t2 and eco_t2["relation"]))
        chk("확정된 관계가 없다(derived 없음)",
            all(p["confirmed_by"] is None for p in r["pairs"]))
        chk("관계 어휘 밖의 값이 없다", all(p["relation"] in RELATIONS for p in r["pairs"]))

    print(f"\n결과: {ok}/{ok + fail} PASS")
    return 0 if fail == 0 else 1


def main(argv):
    if len(argv) < 2:
        print(__doc__); return 2
    if argv[1] == "self-test":
        return self_test()
    r = propose(argv[2])
    print(f"{r['source']} — entry {r['n_entries']} · 쌍 {r['n_pairs']} → {r['state']}")
    for p in r["pairs"]:
        print(f"\n  {p['from']}  ↔  {p['to']}   [{p['relation']}] "
              f"신뢰 {p['confidence']}"
              + (f" · 확정 {p['confirmed_by']}" if p.get("confirmed_by") else " · 확정 대기"))
        print(f"      {p['why']}")
        for b in p["basis"]:
            print(f"        - {b['signal']}: {b['says']}")
    for q in r["questions"]:
        print(f"\n  [질문] {q['question']}")
        for o in q["options"]:
            print(f"      · {o['value']:8} {o['label']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
