#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/crosscheck.py — 교차검증: 무엇을 무엇과 맞추는가 (LLM 0콜)

출처
    승격  `reference_code/test2_extraction_walkthrough.ipynb` **셀 19**(독립성 표) ·
          **셀 20**(`cross()` + 7건 실행). 원본과 스냅샷은 수정하지 않았다.
          ★ **표가 자산이다.** 코드는 표를 실행하는 껍데기일 뿐이므로, 표를 코드 안에
            데이터(`CHECKS`)로 옮겨 함께 관리한다 — 표와 구현이 갈라지지 않게.

★ 규율 — 원본 셀 19 의 문장이 이 도구의 전부다
    교차검증은 **"값이 그럴듯한가"를 보는 게 아니다.**
    **서로 독립적으로 선언된 두 값이 같은가**를 본다.
    같으면 판독이 옳다는 증거가 되고, 다르면 **어느 쪽도 믿지 않고 `주의`로 남긴다** —
    임의 선택을 하지 않는다.

    그래서 판정은 셋뿐이다.
        일치      두 출처가 같다 → 판독의 증거
        불일치    다르다 → **주의**. 어느 쪽이 옳은지 도구는 정하지 않는다(A-1)
        판정불가  한쪽이 없다 → 결함이 아니다. **무엇이 없어서 못 했는지**를 남긴다

    "독립"의 뜻도 값마다 다르므로 항목마다 **왜 독립인가**를 적는다. 독립이 아닌 두 값을
    맞춰 놓고 "일치했다"고 말하면 아무것도 검증하지 않은 것이다.

★ 6·7 이 이 표의 핵심이다 — **레인이 다르다**
    CST 포트 선언(cst 레인) ↔ DXF 급전선 형상(dxf 레인). 기여 레인을 버렸다면 불가능한
    검증이다. 주도 레인만 남기는 설계였다면 이 두 줄이 통째로 사라진다.

CLI
    python tools/crosscheck.py run <run_id>
    python tools/crosscheck.py table
    python tools/crosscheck.py self-test
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

RESULT_NAME = "교차검증_결과.json"

# 판정 어휘 — 셋뿐이다. 늘리지 않는다.
MATCH, MISMATCH, UNKNOWN = "일치", "불일치", "판정불가"

# 허용 오차 — 판정 규칙 (c). 관측 근거가 없으면 **0**(완전 일치)을 쓴다.
TOL_MM = 0.0005      # 좌표 대조 — 추출 반올림 자리(4자리) 아래만 허용한다
TOL_GHZ = 0.0


def numeric_rules() -> dict:
    return {"tol_mm": TOL_MM, "tol_ghz": TOL_GHZ,
            "산지": ("추출이 좌표를 소수 4자리로 반올림한다(`geom_round_digits=4`). "
                   "그 아래만 허용한다 — 그 위는 실제로 다른 값이다."),
            "규율": ("불일치는 **주의**다. 어느 쪽이 옳은지 도구가 정하지 않는다(A-1). "
                   "판정불가는 결함이 아니라 **무엇이 없는지의 기록**이다.")}


# ── 셀 20 승격 — 대조 한 건 ─────────────────────────────────────────────────

def cross(no, what, a_label, a, b_label, b, tol=0.0, why="", lanes=("", "")) -> dict:
    """두 값을 맞춘다. 출처: 셀 20 `cross()` (판정 어휘·사유 필드를 더함)"""
    if a is None or b is None:
        verdict = UNKNOWN
    elif tol == 0:
        verdict = MATCH if a == b else MISMATCH
    else:
        try:
            verdict = MATCH if abs(float(a) - float(b)) <= tol else MISMATCH
        except (TypeError, ValueError):
            verdict = MATCH if a == b else MISMATCH
    missing = [lbl for lbl, v in ((a_label, a), (b_label, b)) if v is None]
    return {"no": no, "what": what, "verdict": verdict,
            "a": {"label": a_label, "value": a, "lane": lanes[0]},
            "b": {"label": b_label, "value": b, "lane": lanes[1]},
            "독립_근거": why, "tol": tol,
            "why": ("두 출처가 같다 — 판독의 증거" if verdict == MATCH else
                    "다르다 — **어느 쪽도 믿지 않는다.** 어느 쪽이 옳은지는 사람이 정한다(A-1)"
                    if verdict == MISMATCH else
                    f"{' · '.join(missing)} 이(가) 없다 — 결함이 아니라 없음의 기록")}


# ── 독립성 표 (셀 19) — **표가 자산이다** ───────────────────────────────────

TABLE = [
    (1, "급전 포트 높이 ↔ 도체 두께", "cst", "cst",
     "포트는 형상 위에 그린 여기(勵起) 면, `t_cond` 는 도체 두께 파라미터 — 서로를 참조하지 않는다"),
    (2, "배열 격자 간격 ↔ 파라미터 이름", "cst", "cst",
     "변환은 좌표 명령, 파라미터는 이름표 — 이름과 실측이 맞는지"),
    (3, "λ/2 → 자유공간 공진 ↔ 솔버 선언 대역", "cst", "cst",
     "설계 파라미터와 솔버 설정은 다른 대화상자에서 입력된다"),
    (4, "포트 수 — 세 출처", "cst", "cst",
     "`Model.prj` UID · 이력 정의−삭제 · `Model.dsn` 평문 — 세 곳이 각각 다른 파일·형식"),
    (5, "모니터 서브볼륨 ↔ 임포트 DXF bbox", "cst", "dxf",
     "일치하면 그 좌표는 모델 범위가 아니라 **잔여값**이라는 신호"),
    (6, "포트 x 좌표 ↔ 급전선 시작 x", "cst", "dxf",
     "**레인이 다르다** — 선언(cst) vs 형상(dxf). 기여 레인을 버렸다면 불가능한 검증"),
    (7, "포트 폭 ↔ 급전선 y 폭", "cst", "dxf",
     "6 과 같다 — 선언과 형상이 서로를 참조하지 않는다"),
    (8, "재질 εr 의 측정 주파수 ↔ 타겟 대역", "datasheet", "cst",
     ("유전율은 **주파수 종속**이다. 데이터시트 값은 보통 10 GHz 측정이고, 설계자는 타겟 "
      "주파수로 재계산해 반영한다. 측정 조건과 설계 대역은 서로를 참조하지 않는다 — "
      "그래서 독립이다"),),
]


def table_md() -> str:
    out = ["| # | 대조 | 레인 A | 레인 B | 두 출처가 독립인 이유 |",
           "| --- | --- | --- | --- | --- |"]
    for no, what, la, lb, why in TABLE:
        out.append(f"| {no} | {what} | {la} | {lb} | {why} |")
    return "\n".join(out)


# ── 값 뽑기 ─────────────────────────────────────────────────────────────────

def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _cst(ex: dict) -> dict:
    return ((ex.get("declared") or {}).get("cst") or [{}])[0]


def _dxf_list(ex: dict) -> list:
    return (ex.get("geometry") or {}).get("dxf") or []


def _feed_poly(d: dict) -> dict | None:
    """급전선 후보 — **기판 레이어가 아닌 폴리라인 중 가장 왼쪽**.

    출처: 셀 20 의 판독 규약(`RO3003` 레이어 제외 + 최소 x)을 레이어 이름에 의존하지 않게
    바꿨다. 기판은 **도면 전체를 덮는 가장 큰 도형**이므로 bbox 면적으로 가른다 —
    레이어 이름은 프로젝트마다 다르다.
    """
    polys = d.get("polylines") or []
    if not polys:
        return None
    big = max(polys, key=lambda q: (q["w_mm"] or 0) * (q["h_mm"] or 0))
    cand = [q for q in polys if q is not big]
    return min(cand, key=lambda q: q["x0"]) if cand else None


def collect(run_id: str, work: Path | None = None) -> list[dict]:
    work = Path(work) if work else C.work_dir(run_id, create=False)
    ex = C.read_json(work / "추출_결과.json")
    cst, dxfs = _cst(ex), _dxf_list(ex)
    par = cst.get("parameters") or {}
    ports_h = cst.get("ports_declared_history") or {}
    items = ports_h.get("items") or []
    p0 = items[0] if items else {}
    lam = _f((par.get("lambda_half") or {}).get("value"))
    rows = []

    # 1 — 포트 높이 ↔ 도체 두께
    zr = [_f(x) for x in (p0.get("zrange") or [])]
    rows.append(cross(1, TABLE[0][1],
                      "포트 Zrange 차", (round(abs(zr[1] - zr[0]), 6)
                                      if len(zr) == 2 and None not in zr else None),
                      "Parameters.json t_cond", _f((par.get("t_cond") or {}).get("value")),
                      TOL_MM, TABLE[0][4], ("cst", "cst")))

    # 2 — 변환 벡터의 x 최소 간격 ↔ lambda_half
    steps = []
    for it in (cst.get("transforms_declared") or {}).get("items") or []:
        ve = it.get("vector_expr")
        if not ve or "translate" not in str(it.get("op", "")):
            continue
        e = str(ve[0]).replace(" ", "")
        if e.endswith("*lambda_half") and lam:
            k = _f(e[: -len("*lambda_half")])
            if k:
                steps.append(abs(k) * lam)
    gaps = sorted({round(b - a, 6) for a, b in zip(sorted(steps), sorted(steps)[1:]) if b > a})
    rows.append(cross(2, TABLE[1][1],
                      "변환 벡터 x 최소 간격", (gaps[0] if gaps else None),
                      "Parameters.json lambda_half", lam,
                      TOL_MM, TABLE[1][4], ("cst", "cst")))

    # 3 — λ/2 자유공간 공진이 선언 대역 안인가
    fr = (cst.get("solver_frequency_range_declared") or [{}])[0]
    lo, hi = _f(fr.get("min")), _f(fr.get("max"))
    f_lh = (C.C_MM_GHZ / (2 * lam)) if lam else None
    rows.append({**cross(3, TABLE[2][1],
                         "c/(2·lambda_half)", (round(f_lh, 4) if f_lh else None),
                         "Solver 선언 대역", (f"{lo}~{hi}" if lo and hi else None),
                         0.0, TABLE[2][4], ("cst", "cst")),
                 # 이 항목은 같은 값을 맞추는 게 아니라 **범위 포함**을 본다
                 "verdict": (UNKNOWN if not (f_lh and lo and hi) else
                             MATCH if lo <= f_lh <= hi else MISMATCH),
                 "why": ("선언 대역 안이다" if (f_lh and lo and hi and lo <= f_lh <= hi) else
                         "대역 밖이다 — 소자 공진과 배열 동작 주파수는 다르다(D-27). "
                         "결함이 아니라 **주의**다" if (f_lh and lo and hi) else
                         "lambda_half 또는 솔버 대역 선언이 없다")})

    # 4 — 포트 수 세 출처
    n_prj = (cst.get("ports_declared") or {}).get("n")
    n_dsn = (cst.get("ports_declared_dsn") or {}).get("n")
    r4 = cross(4, TABLE[3][1], "Model.prj UID port 개수", n_prj,
               "이력 정의−삭제 후 최종", ports_h.get("n_final"),
               0.0, TABLE[3][4], ("cst", "cst"))
    r4["c"] = {"label": "Model.dsn 평문", "value": n_dsn,
               "note": "test2 는 Model.dsn 에 포트 절이 없다 — 없는 것이 정상"}
    rows.append(r4)

    # 5 — 모니터 서브볼륨 ↔ DXF bbox
    sv = cst.get("monitor_subvolume_declared") or {}
    size = (sv.get("size_mm") or [None, None])[:2]
    bb = next((d.get("bbox_size_mm") for d in dxfs if d.get("bbox_size_mm")), None)
    r5 = cross(5, TABLE[4][1],
               "SetSubvolume size (x,y)", (tuple(size) if all(s is not None for s in size) else None),
               "임포트 DXF bbox", (tuple(bb) if bb else None),
               0.0, TABLE[4][4], ("cst", "dxf"))
    r5["applied"] = sv.get("applied")
    r5["경고"] = sv.get("경고")
    if r5["verdict"] == MATCH:
        r5["why"] = ("같다 — 이 좌표는 **모델 범위가 아니라 임포트 도면의 잔여값**이다. "
                     "모델 크기로 읽으면 안 된다")
    rows.append(r5)

    # 6·7 — **레인이 다르다**. CST 선언 ↔ DXF 형상
    feed = next((_feed_poly(d) for d in dxfs if _feed_poly(d)), None)
    xr = [_f(x) for x in (p0.get("xrange") or [])]
    yr = [_f(y) for y in (p0.get("yrange") or [])]
    rows.append(cross(6, TABLE[5][1],
                      "CST 포트 Xrange[0]", (xr[0] if xr else None),
                      "DXF 급전선 최소 x", (feed["x0"] if feed else None),
                      TOL_MM, TABLE[5][4], ("cst", "dxf")))
    rows.append(cross(7, TABLE[6][1],
                      "CST 포트 Yrange 차", (round(yr[1] - yr[0], 6)
                                          if len(yr) == 2 and None not in yr else None),
                      "DXF 급전선 y 폭", (feed["h_mm"] if feed else None),
                      TOL_MM, TABLE[6][4], ("cst", "dxf")))

    # 8 — 재질 εr 의 측정 주파수 ↔ 타겟 대역 (신규 2026-08-03)
    mats = [m for m in (cst.get("materials_declared") or [])
            if _f(m.get("epsilon")) and _f(m["epsilon"]) > 1.05]
    ref = next((m.get("ref_freq_ghz") or m.get("measured_at_ghz") for m in mats), None)
    band = f"{lo}~{hi}" if lo and hi else None
    r8 = cross(8, TABLE[7][1], "재질 선언의 측정 주파수", _f(ref),
               "솔버 선언 대역", band, 0.0, TABLE[7][4], ("datasheet", "cst"))
    r8["재질"] = [{"name": m.get("name"), "epsilon": m.get("epsilon")} for m in mats]
    if r8["verdict"] == UNKNOWN:
        r8["why"] = ("CST 재질 선언에 **측정 주파수가 없다.** 데이터시트 값은 보통 10 GHz "
                     "측정이고 설계자가 타겟 주파수로 재계산해 반영하는데, 그 조건이 "
                     "프로젝트 안에 남지 않는다. → 사람 선언(manifest)으로 받아야 한다. "
                     "쟁점 I-N")
        r8["capability_gap"] = True
    rows.append(r8)
    return rows


def run(run_id: str, work: Path | None = None) -> dict:
    work = Path(work) if work else C.work_dir(run_id, create=False)
    rows = collect(run_id, work)
    n = {v: sum(1 for r in rows if r["verdict"] == v) for v in (MATCH, MISMATCH, UNKNOWN)}
    res = {"run_id": run_id, "n_checks": len(rows), "counts": n, "checks": rows,
           "numeric_rules": numeric_rules(),
           "표": table_md(),
           "규율": ("서로 **독립적으로 선언된** 두 값이 같은가를 본다. 값이 그럴듯한가를 "
                  "보는 게 아니다. 다르면 어느 쪽도 믿지 않는다."),
           "verdict": ("주의" if n[MISMATCH] else "일치" if n[MATCH] else "판정불가")}
    C.write_json(work / RESULT_NAME, res)
    return res


# ── 자기 시험 ────────────────────────────────────────────────────────────────

def self_test() -> int:
    ok = fail = 0

    def chk(n, cond, d=""):
        nonlocal ok, fail
        if cond:
            ok += 1; print(f"  PASS  {n}")
        else:
            fail += 1; print(f"  FAIL  {n}  {d}")

    print("[crosscheck.py 자기 시험]")

    # ── 판정 어휘는 셋뿐이고, 없는 것은 결함이 아니다
    chk("같으면 일치", cross(0, "t", "a", 1.0, "b", 1.0)["verdict"] == MATCH)
    chk("다르면 불일치", cross(0, "t", "a", 1.0, "b", 2.0)["verdict"] == MISMATCH)
    chk("한쪽이 없으면 판정불가", cross(0, "t", "a", None, "b", 2.0)["verdict"] == UNKNOWN)
    chk("판정불가는 결함이 아니라고 밝힌다",
        "결함이 아니라" in cross(0, "t", "a", None, "b", 2.0)["why"])
    chk("불일치는 어느 쪽도 믿지 않는다고 밝힌다",
        "어느 쪽도 믿지 않는다" in cross(0, "t", "a", 1.0, "b", 2.0)["why"])
    chk("허용 오차 안쪽은 일치", cross(0, "t", "a", 1.0, "b", 1.0004, 0.0005)["verdict"] == MATCH)
    chk("허용 오차 밖은 불일치", cross(0, "t", "a", 1.0, "b", 1.002, 0.0005)["verdict"] == MISMATCH)

    # ── 표가 자산이다 — 항목마다 독립 근거가 있어야 한다
    chk("표 8건", len(TABLE) == 8, str(len(TABLE)))
    chk("전 항목에 독립 근거가 있다", all(len(t[4]) > 20 for t in TABLE))
    chk("레인이 다른 항목이 표시된다",
        sum(1 for t in TABLE if t[2] != t[3]) >= 3,
        str([t[0] for t in TABLE if t[2] != t[3]]))
    chk("표를 마크다운으로 낸다", table_md().count("|") > 40)

    # ── 급전선 판독은 레이어 이름에 의존하지 않는다
    d = {"polylines": [
        {"layer": "SUB", "x0": -29.5, "x1": 29.5, "y0": -3, "y1": 3, "w_mm": 59.0, "h_mm": 6.0},
        {"layer": "CU", "x0": -29.5, "x1": -28.5, "y0": -0.15, "y1": 0.15,
         "w_mm": 1.0, "h_mm": 0.3},
        {"layer": "CU", "x0": -28.5, "x1": 26.9, "y0": -0.95, "y1": 0.95,
         "w_mm": 55.4, "h_mm": 1.9}]}
    f = _feed_poly(d)
    chk("가장 큰 도형(기판)을 빼고 가장 왼쪽을 고른다",
        f and f["x0"] == -29.5 and f["h_mm"] == 0.3, str(f))
    chk("폴리라인이 없으면 None", _feed_poly({"polylines": []}) is None)

    # ── 실물 — test2
    base = C.data_dir() / "work" / "L1-test2"
    if not (base / "추출_결과.json").exists():
        print("  건너뜀 — 실물 없음"); print(f"\n결과: {ok}/{ok + fail} PASS")
        return 0 if fail == 0 else 1

    r = run("L1-test2")
    by = {x["no"]: x for x in r["checks"]}
    chk(f"8건 전부 실행 (일치 {r['counts'][MATCH]} · 불일치 {r['counts'][MISMATCH]} · "
        f"판정불가 {r['counts'][UNKNOWN]})", r["n_checks"] == 8, str(r["n_checks"]))
    chk("① 포트 높이 = 도체 두께", by[1]["verdict"] == MATCH,
        f"{by[1]['a']['value']} vs {by[1]['b']['value']}")
    chk("② 격자 간격 = lambda_half", by[2]["verdict"] == MATCH,
        f"{by[2]['a']['value']} vs {by[2]['b']['value']}")
    chk("④ 포트 수 두 출처 일치", by[4]["verdict"] == MATCH,
        f"{by[4]['a']['value']} vs {by[4]['b']['value']}")
    chk("⑤ 서브볼륨 = DXF bbox → 잔여값 신호", by[5]["verdict"] == MATCH
        and "잔여값" in by[5]["why"], f"{by[5]['a']['value']} vs {by[5]['b']['value']}")
    chk("⑤ 적용되지 않은 좌표임을 함께 남긴다", by[5]["applied"] is False)

    # ★ 레인이 다른 대조가 실제로 맞는다 — 이 표의 핵심
    chk("⑥ CST 포트 x = DXF 급전선 x (레인이 다르다)", by[6]["verdict"] == MATCH,
        f"{by[6]['a']['value']} vs {by[6]['b']['value']}")
    chk("⑦ CST 포트 폭 = DXF 급전선 폭 (레인이 다르다)", by[7]["verdict"] == MATCH,
        f"{by[7]['a']['value']} vs {by[7]['b']['value']}")
    chk("⑥⑦ 이 서로 다른 레인임을 기록한다",
        by[6]["a"]["lane"] == "cst" and by[6]["b"]["lane"] == "dxf")

    # ③ — 대역 밖이어도 결함이 아니다(D-27)
    chk("③ 대역 포함 여부를 본다", by[3]["verdict"] in (MATCH, MISMATCH, UNKNOWN))
    if by[3]["verdict"] == MISMATCH:
        chk("③ 대역 밖을 결함이라 하지 않는다", "결함이 아니라" in by[3]["why"], by[3]["why"])

    # ⑧ — 재질 측정 주파수는 선언에 없다
    chk("⑧ 재질 측정 주파수가 없어 판정불가", by[8]["verdict"] == UNKNOWN, str(by[8]))
    chk("⑧ 무엇이 없어서 못 했는지 밝힌다", "측정 주파수가 없다" in by[8]["why"])
    chk("⑧ 능력 공백으로 표시", by[8].get("capability_gap") is True)
    chk("⑧ 유전체 재질을 함께 싣는다", any(m["name"] for m in by[8]["재질"]), str(by[8]["재질"]))

    chk("불일치가 있으면 종합이 주의",
        (r["verdict"] == "주의") == (r["counts"][MISMATCH] > 0), r["verdict"])

    print(f"\n결과: {ok}/{ok + fail} PASS")
    return 0 if fail == 0 else 1


def main(argv):
    if len(argv) < 2:
        print(__doc__); return 2
    if argv[1] == "self-test":
        return self_test()
    if argv[1] == "table":
        print(table_md()); return 0
    r = run(argv[2])
    print(f"{r['run_id']} — 대조 {r['n_checks']}건 · "
          f"일치 {r['counts'][MATCH]} · 불일치 {r['counts'][MISMATCH]} · "
          f"판정불가 {r['counts'][UNKNOWN]}  → {r['verdict']}")
    for x in r["checks"]:
        print(f"\n[{x['no']}] {x['what']}   → {x['verdict']}")
        print(f"     A ({x['a']['lane']:9}) {x['a']['label']:34} = {x['a']['value']}")
        print(f"     B ({x['b']['lane']:9}) {x['b']['label']:34} = {x['b']['value']}")
        if x.get("c"):
            print(f"     C {' ':11}{x['c']['label']:34} = {x['c']['value']}")
        print(f"     {x['why']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
