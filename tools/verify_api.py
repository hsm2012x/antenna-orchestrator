#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/verify_api.py — 클래스 「해석」 / py_verify_api. 등가모델 대조. LLM 0콜.

계약(interfaces.yaml py_verify_api):
    verify(geometry: dict, requirements: dict, thresholds: dict) -> list[CheckItem]
    CheckItem = {check, value, threshold: [lo, hi], pass: bool, formula, inputs}

규율:
  · **임계는 인자로만 받는다 — env·하드코딩 금지.** 임계가 null 이면 판정하지 않고
    pass=None · reason="임계 미지정"으로 남긴다(N-3 · I-5). 그럴듯한 기본값을 넣지 않는다.
  · pass 는 판정이 아니라 대조 결과다. 채택 확정은 사람(A-1).
  · 모든 값에 formula 와 inputs 를 남긴다 — 근거 표의 원재료이자 게이트의 대조 집합.

출력: work/<run_id>/해석_결과.json
사용: python tools/verify_api.py --run-id <id> [--product <name>] [--registry <path>]
"""
from __future__ import annotations
import argparse, math, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common
from _common import (AF_DB_FLOOR, AF_POWER_FLOOR, AF_THETA_SAMPLES, C_MM_GHZ, FOUR_PI,
                     GEOM_ROUND_DIGITS, HALF_POWER_DB, HBW_FACTOR_UNIFORM, PARAM_ROUND_DIGITS,
                     load_registry, numeric_rules, read_json, resolve_product, work_dir, write_json)

# 수치 상수는 전부 _common 에서 온다 — 산지(물리 정의값 / 포맷 규약 / 판정 규칙 / 모델 가정)가
# 그곳에 분류되어 있고, 판정 규칙은 rule_version 과 함께 원장에 기록된다.
ND = GEOM_ROUND_DIGITS


def _r(v, n=ND):
    return None if v is None else round(float(v), n)


ER_REF_GAP_MAX = 0.25       # 기준 주파수와 타겟이 이 비율 넘게 벌어지면 그대로 쓰지 않는다


def _er_note(er, er_ref, f0) -> dict:
    """유전율을 **이 주파수에서 써도 되는가**. 판정만 하고 재계산하지 않는다(D-34).

    반환 `{why, skip}` — `skip` 이 문자열이면 그 항목은 **판정하지 않는다**(I-5 · N-3).
    """
    if er is None:
        return {"why": "유전율 미지정", "skip": None}
    if er_ref is None:
        return {"why": ("**기준 주파수 미상** — 이 εr 이 몇 GHz 에서 측정된 값인지 모른다. "
                        "데이터시트 관례는 10 GHz 이지만 관례는 근거가 아니다(Q-15)"),
                "skip": ("유전율의 기준 주파수가 없다 — 주파수 종속 값을 근거 없이 쓰지 않는다"
                         "(ISSUES I-N). registry substrate.er_ref_ghz 확정 필요(사람)")}
    if not f0:
        return {"why": f"기준 {er_ref} GHz — 타겟 주파수가 없어 벌어짐을 재지 못한다",
                "skip": None}
    gap = abs(f0 - er_ref) / er_ref
    if gap > ER_REF_GAP_MAX:
        return {"why": (f"기준 {er_ref} GHz · 타겟 {f0} GHz — **{gap*100:.0f}% 벌어졌다.** "
                        f"이 폭에서는 재계산 없이 쓸 수 없다"),
                "skip": (f"유전율 기준({er_ref} GHz)과 타겟({f0} GHz)이 {gap*100:.0f}% 벌어졌다 "
                         "— 재계산된 εr 이 필요하다. 도구는 재계산하지 않는다(D-34)")}
    return {"why": f"기준 {er_ref} GHz · 타겟 {f0} GHz — {gap*100:.0f}% 차이. 그대로 쓴다",
            "skip": None}


def _item(check, value, threshold, formula, inputs, unit=None, note=None):
    """threshold=None → 판정하지 않는다(pass=None). 이것이 '빈 값은 빈 채'의 코드 표현이다."""
    if threshold is None or value is None:
        ok = None
    else:
        lo, hi = threshold
        ok = ((lo is None or value >= lo) and (hi is None or value <= hi))
    it = {"check": check, "value": value, "unit": unit, "threshold": threshold, "pass": ok,
          "formula": formula, "inputs": inputs}
    if ok is None:
        it["reason"] = note or ("임계 미지정 — registry/products.yaml 확정 필요(사람)"
                                if value is not None else "입력값 없음 — 추출 단계에서 판독되지 않았다")
    elif note:
        it["reason"] = note
    return it


def _f_free_ghz(L_mm):
    """자유공간 반파장 공진: L = λ/2 → f = c/(2L)."""
    return _r(C_MM_GHZ / (2.0 * L_mm)) if L_mm else None


def _f_sub_ghz(L_mm, er):
    """기판 유효 반파장 공진: L = λg/2 = c/(2f√εr) → f = c/(2L√εr)."""
    if not L_mm or not er: return None
    return _r(C_MM_GHZ / (2.0 * L_mm * (float(er) ** 0.5)))


def reference_lambda(arr: dict):
    """빔폭·배열인자가 공유하는 기준 파장. 산지를 한 곳으로 묶어 두 값이 갈라지지 않게 한다.
    f_ref = c/(2·mean(patch_L)) — 자유공간 기준. 기판 기준을 쓰려면 εr 확정이 선행이다."""
    Ls = (arr or {}).get("patch_L_mm") or []
    if not Ls: return None, None
    f_ref = _f_free_ghz(sum(Ls) / len(Ls))
    return f_ref, (_r(C_MM_GHZ / f_ref) if f_ref else None)


# ── 심볼 식 평가 (선언된 파라미터로 CST 좌표를 mm 로 환산) ───────────────────
def eval_expr(expr: str, syms: dict):
    """`3*lambda_half` · `t_sub+t_cond` 같은 선언 식을 파라미터 값으로 계산한다.

    사칙연산·단항부호·거듭제곱만 허용한다(eval 아님 — ast 화이트리스트).
    환산은 **해석의 일**이다: 「추출」은 식과 심볼만 담고 값을 곱하지 않는다.
    """
    import ast
    ok = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name, ast.Load,
          ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.USub, ast.UAdd)
    try:
        tree = ast.parse(str(expr), mode="eval")
    except SyntaxError:
        return None
    for n in ast.walk(tree):
        if not isinstance(n, ok): return None
        if isinstance(n, ast.Name) and n.id not in syms: return None
    try:
        return float(eval(compile(tree, "<expr>", "eval"), {"__builtins__": {}}, dict(syms)))
    except Exception:
        return None


def cst_array_positions(cst: dict) -> dict | None:
    """CST 변환 선언 → 소자 좌표(mm). 원본 1개 + 변환 사본 N개가 배열이다.

    CST 는 원본을 지정한 벡터만큼 옮긴 사본을 만든다(MultipleObjects=True) — 따라서
    좌표 집합은 {원점} ∪ {각 변환 벡터}다. 심볼 값은 Parameters.json 선언에서 온다.
    """
    par = cst.get("parameters") or {}
    syms = {k: _r(v.get("value"), PARAM_ROUND_DIGITS) for k, v in par.items()
            if _r(v.get("value"), PARAM_ROUND_DIGITS) is not None}
    pts, unresolved, labels, rot = [], [], [], []

    # 1순위: 솔리드 이력 재생 결과. 삭제·회전·이름 이어붙이기가 반영된 **최종** 집합이다.
    rp = cst.get("solids_replayed") or {}
    for s in (rp.get("final") or []):
        xyz, ang = [0.0, 0.0, 0.0], None
        bad = False
        for o in s.get("ops") or []:
            if o.get("op") == "translate" and o.get("vector_expr"):
                vv = [eval_expr(e, syms) for e in o["vector_expr"]]
                if any(q is None for q in vv):
                    unresolved.append({"name": s["name"], "vector_expr": o["vector_expr"],
                                       "reason": "심볼 값을 선언에서 찾지 못했다"}); bad = True; break
                xyz = [xyz[k] + vv[k] for k in range(3)]
            elif o.get("op") == "rotate" and o.get("angle_expr"):
                ang = o["angle_expr"]
        if bad: continue
        pts.append(tuple(_r(q) for q in xyz)); labels.append(s["name"]); rot.append(ang)

    # 폴백: 재생 결과가 없으면 변환 선언만으로(원본 1 + 사본 N) 좌표를 만든다.
    if not pts:
        tr = (cst.get("transforms_declared") or {}).get("items") or []
        pts = [(0.0, 0.0, 0.0)]; labels = [None]; rot = [None]
        for t in tr:
            v = t.get("vector_expr")
            if not v or len(v) != 3:
                unresolved.append({"target": t.get("target"), "op": t.get("op"),
                                   "reason": "벡터 선언 없음"})
                continue
            xyz = [eval_expr(e, syms) for e in v]
            if any(q is None for q in xyz):
                unresolved.append({"vector_expr": v, "reason": "심볼 값을 선언에서 찾지 못했다"})
                continue
            pts.append(tuple(_r(q) for q in xyz)); labels.append(None); rot.append(None)
    if len(pts) < 2: return None
    xs = sorted({p[0] for p in pts}); ys = sorted({p[1] for p in pts})
    def steps(a):
        return sorted({_r(a[i + 1] - a[i]) for i in range(len(a) - 1)}) if len(a) > 1 else []
    # y=0 거울 대칭성 — 대칭면 선언이 최종 배치와 정합하는지 판정 가능한 형태로 만든다.
    S = {(p[0], p[1]) for p in pts}
    unpaired = sorted(S - {(x, -y) for x, y in S})
    # 이름에서 칩·채널을 집계한다 — 토큰 관측이며 뜻을 판정하지 않는다(I-6).
    import re as _re
    groups = {}
    for nmz in labels:
        m = _re.search(r"Chip(\d+)_(RX|TX)(\d+)", nmz or "")
        if m: groups[f"Chip{m.group(1)}_{m.group(2)}"] = groups.get(f"Chip{m.group(1)}_{m.group(2)}", 0) + 1
    return {"n_elements": len(pts), "points_mm": [list(p) for p in pts],
            "labels": labels, "rotation_expr": rot[0] if rot else None,
            "name_groups": dict(sorted(groups.items())),
            "산지": ("솔리드 이력 재생(삭제·회전·이름 반영)" if (rp.get("final"))
                    else "변환 선언만(재생 결과 없음)"),
            "y_mirror_symmetric": not unpaired,
            "y_unpaired_count": len(unpaired), "y_unpaired_sample": unpaired[:8],
            "x_unique_mm": xs, "y_unique_mm": ys,
            "x_step_mm": steps(xs), "y_step_mm": steps(ys),
            "aperture_mm": [_r(max(xs) - min(xs)), _r(max(ys) - min(ys))],
            "symbols_used": syms, "unresolved": unresolved,
            "formula": "좌표 = {원점} ∪ {변환 벡터}, 심볼은 Parameters.json 선언값으로 치환",
            "출처": "ModelHistory.json transform 선언 + Model/Parameters.json"}


# ── 배열인자 ────────────────────────────────────────────────────────────────
def array_factor(arr: dict, lam_mm: float | None, n_theta: int = AF_THETA_SAMPLES) -> dict | None:
    """소자 위치만으로 배열인자를 계산한다. 곡선의 산지도 이 함수 하나다 — 렌더는 그리기만 한다.

    가정을 코드에 명시한다: 진폭 균일 · 급전 위상 0 · 소자 패턴 미포함.
    따라서 결과는 **발산 패턴이 아니라 배열인자**다. 문서에 그렇게 쓰여야 한다.
    """
    import math
    pat = (arr or {}).get("patches") or []
    if not pat or not lam_mm: return None
    xs = [(q["x0"] + q["x1"]) / 2.0 for q in pat]
    n, k = len(xs), 2.0 * math.pi / lam_mm
    th = [(-90.0 + 180.0 * i / (n_theta - 1)) for i in range(n_theta)]
    db = []
    for t in th:
        s = math.sin(math.radians(t))
        re = sum(math.cos(k * x * s) for x in xs)
        im = sum(math.sin(k * x * s) for x in xs)
        p = (re * re + im * im) / (n * n)
        db.append(round(10.0 * math.log10(p) if p > AF_POWER_FLOOR else AF_DB_FLOOR, 3))

    i0 = max(range(n_theta), key=lambda i: db[i])           # 주빔 정점
    def cross(step):                                        # 반전력 교차각(선형 보간)
        # 반전력점은 정확히 10·log10(0.5) = −3.0103 dB 다. "−3 dB" 로 반올림해 쓰면 폭이 미세하게 커진다.
        i = i0
        while 0 <= i + step < n_theta and db[i + step] > HALF_POWER_DB: i += step
        j = i + step
        if not (0 <= j < n_theta): return None
        f = (db[i] - HALF_POWER_DB) / (db[i] - db[j]) if db[i] != db[j] else 0.0
        return th[i] + f * (th[j] - th[i])
    lo, hi = cross(-1), cross(1)
    hpbw = round(hi - lo, 4) if (lo is not None and hi is not None) else None

    i = i0                                                  # 첫 널 밖 최대(SLL)
    while i + 1 < n_theta and db[i + 1] < db[i]: i += 1
    right = i
    i = i0
    while i - 1 >= 0 and db[i - 1] < db[i]: i -= 1
    left = i
    out = [(db[j], th[j]) for j in range(n_theta) if j < left or j > right]
    sll, sll_at = (max(out) if out else (None, None))

    # 격자로브: AF 는 sinθ 에서 주기 λ/d 를 가진다. 주빔이 sinθ=0 이면 첫 격자로브는
    # sinθ_g = λ/d 이고, 가시영역에 들어오는 조건은 λ/d ≤ 1(즉 d ≥ λ)이다. ±θ_g 로 대칭.
    d = (arr or {}).get("pitch_mean_mm")
    g = None
    if d:
        v = lam_mm / d
        if v <= 1.0: g = round(math.degrees(math.asin(v)), 4)
    return {"n": n, "lambda_mm": lam_mm, "hpbw_deg": hpbw,
            "hpbw_edges_deg": [round(lo, 4) if lo is not None else None,
                               round(hi, 4) if hi is not None else None],
            "half_power_db": round(HALF_POWER_DB, 4),
            "sll_db": round(sll, 3) if sll is not None else None,
            "sll_angle_deg": round(sll_at, 4) if sll_at is not None else None,
            "grating_deg": g,
            "curve": {"theta_deg": [round(t, 3) for t in th], "af_db": db},
            "가정": ["진폭 균일", "급전 위상 0", "소자 패턴 미포함"],
            "경고": "발산 패턴이 아니다 — 소자 위치만의 배열인자다"}


# ── 실제 대조 ───────────────────────────────────────────────────────────────
def verify(geometry: dict, requirements: dict, thresholds: dict) -> list:
    items = []
    band = requirements.get("band_ghz")
    sub = requirements.get("substrate") or {}
    er = sub.get("er")
    # ★ εr 은 **주파수 종속**이다 — 값만으로는 쓸 수 없다(ISSUES I-N · Q-15).
    #   데이터시트 값은 보통 10 GHz 측정이고, 설계자는 타겟 주파수로 재계산해 CST 에 넣는다.
    #   여기서 **재계산하지는 않는다**(D-34) — 도구가 분산 모델을 고르면 정본이 둘이 된다.
    #   대신 "어느 주파수의 εr 인가"를 값과 함께 싣고, **모르면 계산하지 않는다**.
    er_ref = sub.get("er_ref_ghz")
    f0 = requirements.get("f0_ghz")
    er_note = _er_note(er, er_ref, f0)
    th = thresholds or {}

    # ── DXF 배열 ────────────────────────────────────────────────────────────
    arr = next((d["array"] for d in (geometry.get("dxf") or []) if d.get("array")), None)
    dxf_h = next((abs(min(d["elevations_mm"])) for d in (geometry.get("dxf") or [])
                  if d.get("elevations_mm") and min(d["elevations_mm"]) < 0), None)
    if arr:
        Ls = arr.get("patch_L_mm") or []
        for label, L in (("최장 패치", max(Ls) if Ls else None), ("최단 패치", min(Ls) if Ls else None)):
            items.append(_item(
                f"공진정합 · 자유공간 λ/2 ({label})", _f_free_ghz(L), band,
                "f_GHz = c / (2·L_mm),  c = 299.792458 mm·GHz",
                {"L_mm": L, "출처": "추출: array.patch_L_mm (vendor extract_array)"} , "GHz"))
            items.append(_item(
                f"공진정합 · 기판 λg/2 ({label})", _f_sub_ghz(L, er), band,
                "f_GHz = c / (2·L_mm·√εr)",
                {"L_mm": L, "er": er,
                 "er_출처": "registry/products.yaml substrate.er",
                 "er_기준주파수_GHz": er_ref, "타겟_GHz": f0,
                 "er_주의": er_note["why"]}, "GHz",
                (None if er is None else er_note["skip"]) or
                (None if er else "임계 미지정 — substrate.er 가 null 이다. 유전율을 추정하지 않는다.")))

        pm, pmin, pmax = arr.get("pitch_mean_mm"), arr.get("pitch_min_mm"), arr.get("pitch_max_mm")
        spread = _r((pmax - pmin) / pm) if (pm and pmin is not None and pmax is not None) else None
        items.append(_item(
            "배열주기 · 균일도(산포/평균)", spread,
            [0.0, th["pitch_tol"]] if th.get("pitch_tol") is not None else None,
            "spread = (pitch_max − pitch_min) / pitch_mean",
            {"pitch_min_mm": pmin, "pitch_max_mm": pmax, "pitch_mean_mm": pm,
             "n_pitch": len(arr.get("pitch_mm") or []),
             "출처": "추출: array.pitch_mm"}, "비율",
            "균일 배열 가정의 검사다. 비균일이 설계 의도(테이퍼)인지 여부는 사람 판단(I-6)."))

        f_ref, lam = reference_lambda(arr)
        items.append(_item(
            "배열주기 · 파장 대비(pitch/λ)", _r(pm / lam) if (pm and lam) else None, None,
            "ratio = pitch_mean_mm / λ_mm,  λ_mm = c / f_ref,  f_ref = c/(2·mean(L))",
            {"pitch_mean_mm": pm, "lambda_mm": lam, "f_ref_GHz": f_ref,
             "L_mean_mm": _r(sum(Ls) / len(Ls)) if Ls else None}, "비율",
            "임계 미지정 — 격자로브 허용 기준은 registry 확정 대상(사람)."))

        Lap, Wap = arr.get("aperture_L_mm"), arr.get("aperture_W_mm")
        # 빔폭 계수는 **모델 가정**이다 — 균일 조명이면 0.886, 테이퍼가 있으면 커진다.
        # registry 가 값을 주면 그것을 쓰고, 없으면 코드의 명시된 기본값을 쓰되 어느 쪽인지 기록한다.
        # 우선순위: 조정값(ORCH_OVERRIDE·set_override) > registry > 코드 기본값. 어느 쪽인지 남긴다.
        _ov = _common.overrides().get("hbw_uniform_factor")
        if _ov is not None:
            k_hbw, k_src = _ov["value"], f"조정값 [{_ov['source']}] 사유: {_ov['why']} (by {_ov['by']})"
        elif th.get("hbw_uniform_factor") is not None:
            k_hbw, k_src = th["hbw_uniform_factor"], "registry thresholds.hbw_uniform_factor"
        else:
            k_hbw, k_src = HBW_FACTOR_UNIFORM, "_common.HBW_FACTOR_UNIFORM (균일 조명 가정 기본값)"
        hbw = _r(k_hbw * lam / Lap * math.degrees(1.0)) if (lam and Lap) else None
        items.append(_item(
            "개구빔폭 · 방위면 HBW", hbw, th.get("hbw_target_deg"),
            f"HBW_deg = k · (λ_mm / L_aperture_mm) · 180/π,  k = {k_hbw}",
            {"lambda_mm": lam, "L_aperture_mm": Lap, "f_ref_GHz": f_ref,
             "k_hbw": k_hbw, "k_출처": k_src,
             "출처": "추출: array.aperture_L_mm"}, "deg",
            "k 는 개구 조명 분포 가정이다 — 테이퍼가 있으면 registry 로 교체해야 값이 맞는다."))
        gain = (_r(10.0 * math.log10(FOUR_PI * (Lap * Wap) / (lam ** 2)), 2)
                if (lam and Lap and Wap) else None)
        items.append(_item(
            "이득 추정 · 개구 능률 100 % 상한", gain, None,
            "G_dBi = 10·log10(4π·A / λ²),  A = L_aperture · W_aperture  (4π = _common.FOUR_PI)",
            {"L_aperture_mm": Lap, "W_aperture_mm": Wap, "A_mm2": _r(Lap * Wap) if (Lap and Wap) else None,
             "lambda_mm": lam}, "dBi",
            "개구 능률 100 % 가정의 **상한값**이다 — 실제 이득이 아니다. 실측·시뮬로 확정(사람)."))

        # 배열인자(AF) — 추출된 소자 위치만으로 결정론 계산. 소자 패턴·급전 위상은 넣지 않는다.
        af = array_factor(arr, lam)
        if af:
            items.append(_item(
                "배열인자 · −3 dB 전폭", af["hpbw_deg"], th.get("hbw_target_deg"),
                "AF(θ) = Σ_n exp(j·k·x_n·sinθ), k = 2π/λ · 균일 여자(가중 1, 위상 0) · "
                "−3 dB 전폭 = |AF|²/N² 가 0.5 를 지나는 두 각의 차",
                {"n_elements": af["n"], "lambda_mm": lam, "x_출처": "추출: array.patches 중심좌표",
                 "가정": "소자 패턴 미포함 · 급전 위상 0 · 진폭 균일"}, "deg",
                "**실측·시뮬 발산패턴이 아니다** — 소자 위치만의 배열인자다. 실제 패턴은 시뮬/측정으로 확정(사람)."))
            items.append(_item(
                "배열인자 · 최대 사이드로브 레벨", af["sll_db"], None,
                "SLL_dB = max(|AF|²/N² in dB) — 주빔 첫 널 밖 구간",
                {"n_elements": af["n"], "lambda_mm": lam, "각도_deg": af["sll_angle_deg"]}, "dB",
                "임계 미지정 — 허용 SLL 은 registry 확정 대상(사람)."))
            items.append(_item(
                "배열인자 · 격자로브 발생 각(±)", af["grating_deg"], None,
                "sinθ_g = λ/d_mean → θ_g = asin(λ/d_mean) · λ/d > 1 이면 가시영역에 격자로브 없음",
                {"d_mean_mm": arr.get("pitch_mean_mm"), "lambda_mm": lam,
                 "λ/d": _r(lam / arr["pitch_mean_mm"]) if arr.get("pitch_mean_mm") else None}, "deg",
                "임계 미지정 · ±θ_g 로 대칭 · 비균일 주기에서는 평균 주기 기준의 근사다. "
                "d ≥ λ 이면 격자로브가 존재한다 — 배열 주기 설계의 확인 대상(사람)."))

        bb = next((d.get("bbox_size_mm") for d in (geometry.get("dxf") or []) if d.get("bbox_size_mm")), None)
        items.append(_item(
            "규모 타당성 · 최대 변", max(bb) if bb else None, th.get("scale_bbox_mm"),
            "max(bbox_size_mm)", {"bbox_size_mm": bb, "출처": "추출: geometry.dxf[].bbox_size_mm"}, "mm"))

    items.append(_item(
        "스택업 타당성 · 기판 두께", dxf_h,
        [sub["h_mm"], sub["h_mm"]] if sub.get("h_mm") is not None else None,
        "h_mm = |min(elevations_mm)|  (하부 층 elevation)",
        {"elevations_출처": "추출: geometry.dxf[].elevations_mm",
         "registry_h_mm": sub.get("h_mm")}, "mm",
        None if sub.get("h_mm") is not None else
        "임계 미지정 — registry substrate.h_mm 이 null 이다. 추출값을 정본으로 삼지 않는다."))

    # ── CST 선언값 ──────────────────────────────────────────────────────────
    for c in (geometry.get("cst") or []):
        nm = c.get("name")
        fr = c.get("solver_frequency_range_effective") or {}
        flo = _r(fr.get("min")) if fr.get("min") is not None else None
        fhi = _r(fr.get("max")) if fr.get("max") is not None else None
        for lbl, v in (("하한", flo), ("상한", fhi)):
            items.append(_item(
                f"선언 주파수 대역 {lbl} · CST {nm}", v, band,
                "선언값 그대로 — 계산 아님",
                {"출처": f"ModelHistory.json · Solver.FrequencyRange (선언 "
                         f"{len(c.get('solver_frequency_range_declared') or [])}회, 마지막 채택)",
                 "선언_전체": c.get("solver_frequency_range_declared")}, "GHz"))
        par = c.get("parameters") or {}
        if "lambda_half" in par:
            lh = _r(par["lambda_half"].get("value"))
            items.append(_item(
                f"파라미터 정합 · lambda_half → 자유공간 공진 · CST {nm}", _f_free_ghz(lh), band,
                "f_GHz = c / (2·lambda_half_mm)",
                {"lambda_half_mm": lh, "출처": "Model/Parameters.json"}, "GHz",
                "설계 파라미터가 선언 대역과 맞는지의 교차 검사다."))
        mats = [m for m in (c.get("materials_declared") or []) if m.get("epsilon")]
        for m in mats:
            e = _r(m["epsilon"])
            items.append(_item(
                f"재질 유전율 선언 · {m['name']} · CST {nm}", e,
                [er, er] if er is not None else None, "선언값 그대로 — 계산 아님",
                {"출처": m.get("출처"), "registry_er": er, "tand": m.get("tand")}, "εr",
                None if er is not None else "임계 미지정 — registry substrate.er 확정 필요(사람)."))
        for k in ("t_sub", "t_cond"):
            if k in par:
                items.append(_item(
                    f"스택업 선언 · {k} · CST {nm}", _r(par[k].get("value")),
                    [sub["h_mm"], sub["h_mm"]] if (k == "t_sub" and sub.get("h_mm") is not None) else None,
                    "선언값 그대로 — 계산 아님",
                    {"출처": "Model/Parameters.json", "registry_h_mm": sub.get("h_mm")}, "mm"))
        # 스택업 선언 → 기판 두께·유전율. 선언이 있으면 registry 광역 기본값보다 이것이 가깝다.
        st = c.get("stackup_declared") or []
        sub_layer = next((l for l in st if (l.get("z_mm") or 0) < 0), None)
        h_decl = abs(sub_layer["z_mm"]) if sub_layer else None
        er_decl = next((_r(m["epsilon"]) for m in mats
                        if sub_layer and m.get("name") == sub_layer.get("material")), None)
        if h_decl is not None:
            items.append(_item(
                f"스택업 선언 · 기판 두께(임포트 레이어) · CST {nm}", _r(h_decl),
                [sub["h_mm"], sub["h_mm"]] if sub.get("h_mm") is not None else None,
                "h_mm = |z_mm| (DXF 임포트 AddLayer 선언의 레이어 z)",
                {"layer": sub_layer.get("layer"), "material": sub_layer.get("material"),
                 "출처": sub_layer.get("출처"), "registry_h_mm": sub.get("h_mm")}, "mm"))
        # 공진: 선언 스택업으로 λg/2 를 계산한다 — registry εr 가 비어도 파일 선언으로 채워진다.
        cst_arr = cst_array_positions(c)
        if er_decl and cst_arr and cst_arr["x_step_mm"]:
            dx = min(cst_arr["x_step_mm"])
            items.append(_item(
                f"배열 격자 · x 최소 간격 · CST {nm}", dx, None,
                "min(diff(정렬된 소자 x 좌표))",
                {"n_elements": cst_arr["n_elements"], "x_step_mm": cst_arr["x_step_mm"],
                 "symbols": cst_arr["symbols_used"], "출처": cst_arr["출처"]}, "mm",
                "임계 미지정 — 격자 간격 규격은 registry 확정 대상(사람)."))
            items.append(_item(
                f"배열 격자 · 파장 대비(d/λ, 대역 중심) · CST {nm}",
                (_r(dx / (C_MM_GHZ / ((flo + fhi) / 2))) if (flo and fhi) else None), None,
                "ratio = d_min / λ_center,  λ_center = c / ((f_lo+f_hi)/2)",
                {"d_min_mm": dx, "f_lo_GHz": flo, "f_hi_GHz": fhi,
                 "lambda_center_mm": _r(C_MM_GHZ / ((flo + fhi) / 2)) if (flo and fhi) else None},
                "비율", "0.5 근처면 격자로브 없는 표준 위상배열 격자다 — 판정은 사람."))
        if cst_arr and cst_arr.get("name_groups"):
            items.append(_item(
                f"배열 편성 · 칩×채널 · CST {nm}", len(cst_arr["name_groups"]), None,
                "이름에서 Chip<n>_<RX|TX> 묶음 수 — 토큰 관측(뜻은 판정하지 않는다, I-6)",
                {"묶음별_수": cst_arr["name_groups"], "총_솔리드": cst_arr["n_elements"],
                 "회전": cst_arr.get("rotation_expr"), "산지": cst_arr.get("산지")}, "묶음",
                "각 묶음이 한 칩의 채널 하나에 대응하는지는 설계자 확인 대상이다."))
        if cst_arr:
            items.append(_item(
                f"소자 수 · 최종 솔리드 기준 · CST {nm}", cst_arr["n_elements"], None,
                "N = 솔리드 이력 재생 후 남은 솔리드 수(삭제·회전·이름 반영)",
                {"n_transform_decl": (c.get("transforms_declared") or {}).get("n"),
                 "unresolved": len(cst_arr["unresolved"]),
                 "aperture_mm": cst_arr["aperture_mm"],
                 "출처": cst_arr["출처"]}, "개",
                "이름 토큰 집계(blocks_declared.name_tokens)와 대조하라 — 토큰의 의미는 판정하지 않는다(I-6). "
                "이 수는 '명명된 솔리드 사본 수'이며, 그것이 방사 소자인지는 선언되지 않는다."))
        # 배열인자 — CST 변환 좌표의 φ=0 절단면. y 좌표는 이 절단면에 기여하지 않는다.
        cav_role = "변환 대상 솔리드의 **역할이 선언되지 않는다** — 이름 토큰 'Chip'(RX/TX)은 방사 소자가 아니라 IC 실장 패드를 뜻할 수도 있다(I-6: 판정하지 않는다). 방사 소자가 아니라면 이 곡선은 안테나의 배열인자가 아니다. 임포트 형상 자체가 이미 직렬 급전 배열일 수 있다는 점도 함께 본다."
        if cst_arr and flo and fhi:
            lam_c = _r(C_MM_GHZ / ((flo + fhi) / 2))
            af_c = array_factor({"patches": [{"x0": p[0], "x1": p[0]} for p in cst_arr["points_mm"]],
                                 "pitch_mean_mm": (min(cst_arr["x_step_mm"])
                                                  if cst_arr["x_step_mm"] else None)}, lam_c)
            if af_c:
                items.append(_item(
                    f"배열인자 · −3 dB 전폭(φ=0 절단) · CST {nm}", af_c["hpbw_deg"],
                    th.get("hbw_target_deg"),
                    "AF(θ) = Σ_n exp(j·k·x_n·sinθ) · x_n 은 변환 선언 좌표 · λ = c/f_center · "
                    "균일 여자 · 소자 패턴 미포함",
                    {"n_elements": af_c["n"], "lambda_mm": lam_c, "f_center_GHz": _r((flo + fhi) / 2),
                     "x_출처": cst_arr["출처"]}, "deg",
                    "**발산 패턴이 아니다** — 좌표만의 배열인자다. y 방향 소자는 φ=0 절단에 "
                    "기여하지 않으므로 이 값은 x 축 배열만의 결과다. " + cav_role))
                items.append(_item(
                    f"배열인자 · 최대 사이드로브 레벨(φ=0 절단) · CST {nm}", af_c["sll_db"], None,
                    "SLL_dB = max(|AF|²/N² in dB) — 주빔 첫 널 밖 구간",
                    {"n_elements": af_c["n"], "각도_deg": af_c["sll_angle_deg"],
                     "lambda_mm": lam_c}, "dB", "임계 미지정 — 허용 SLL 은 registry 확정 대상(사람)."))

        sym = c.get("symmetry_declared") or {}
        nsym = sum(1 for k in ("xsymmetry", "ysymmetry", "zsymmetry")
                   if (sym.get(k) or "none") not in ("none", None))
        if sym:
            items.append(_item(
                f"대칭면 선언 수 · CST {nm}", nsym, None,
                "none 이 아닌 Xsymmetry·Ysymmetry·Zsymmetry 의 개수",
                {"symmetry": {k: v for k, v in sym.items() if k != "출처"}, "출처": sym.get("출처")}, "면",
                "대칭면이 있으면 모델은 실물의 일부다 — **소자 수·개구를 자동으로 배가하지 않는다**. "
                "보정 여부는 대칭면이 배열을 가르는지에 달렸고, 그 판단은 사람이다(A-1)."
                if nsym else "대칭면 없음 — 모델이 전체다"))
        # 대칭면 선언이 최종 배치와 정합하는가 — 모호함을 판정 가능한 검사로 바꾼다.
        if sym and cst_arr and (sym.get("ysymmetry") or "none") != "none":
            items.append(_item(
                f"Y 대칭면 ↔ 소자 배치 정합 · CST {nm}", cst_arr["y_unpaired_count"], [0, 0],
                "짝 없는 소자 수 = |{(x,y)} − {(x,−y)}| · 0 이면 y=0 거울 대칭",
                {"ysymmetry_선언": sym.get("ysymmetry"),
                 "y_고유좌표_mm": cst_arr["y_unique_mm"],
                 "짝_없는_소자_예": cst_arr["y_unpaired_sample"],
                 "n_elements": cst_arr["n_elements"],
                 "선언_출처": sym.get("출처"), "좌표_출처": cst_arr["출처"]}, "개",
                "0 이 아니면 선언된 대칭면과 최종 소자 배치가 어긋난다 — **소자 수를 배가하면 틀린 답이 "
                "나올 수 있다**. 대칭 선언이 배열 배치보다 먼저 이루어졌는지(이력 순서) 확인하고, "
                "보정 여부는 사람이 결정한다(A-1)."))
        # 포트: 급전 구조의 선언값. Yrange 폭·Zrange 두께는 선언 좌표의 차이(계산 근거 명시).
        ph = c.get("ports_declared_history") or {}
        items.append(_item(
            f"포트 수 · 최종 · CST {nm}", ph.get("n_final"), None,
            "정의 − 삭제 이력 반영 후 남은 포트 수",
            {"n_events": ph.get("n_events"),
             "kinds": sorted({p.get("kind") for p in (ph.get("items") or [])}),
             "labels": [p.get("label") for p in (ph.get("items") or []) if p.get("label")][:20],
             "ports_prj": (c.get("ports_declared") or {}).get("n"),
             "ports_dsn": (c.get("ports_declared_dsn") or {}).get("n")}, "개",
            "임계 미지정 · Model.prj·Model.dsn 선언과 대조 대상."))
        for p in (ph.get("items") or [])[:1]:
            yr, zr = p.get("yrange"), p.get("zrange")
            if yr and len(yr) == 2:
                items.append(_item(
                    f"급전 포트 폭(Yrange) · 포트 {p.get('number')} · CST {nm}",
                    _r(abs(_r(yr[1]) - _r(yr[0]))) if all(_r(v) is not None for v in yr) else None,
                    None, "width = |Yrange_max − Yrange_min| (포트 선언 좌표)",
                    {"yrange": yr, "출처": p.get("출처")}, "mm", "임계 미지정."))
            if zr and len(zr) == 2:
                z = _r(abs(_r(zr[1]) - _r(zr[0]))) if all(_r(v) is not None for v in zr) else None
                tc = _r((par.get("t_cond") or {}).get("value")) if par.get("t_cond") else None
                items.append(_item(
                    f"급전 포트 높이(Zrange) vs t_cond · 포트 {p.get('number')} · CST {nm}", z,
                    [tc, tc] if tc is not None else None,
                    "height = |Zrange_max − Zrange_min| · 임계 = Parameters.json 의 t_cond",
                    {"zrange": zr, "t_cond_mm": tc, "출처": p.get("출처")}, "mm",
                    None if tc is not None else "t_cond 선언 없음 — 대조 대상 없음"))
        imp = [p.get("impedance_ohm") for p in (ph.get("items") or []) if p.get("impedance_ohm")]
        norm = (c.get("solver_declared") or {}).get("NormingImpedance")
        if imp:
            items.append(_item(
                f"포트 임피던스 vs 솔버 정규화 임피던스 · CST {nm}", _r(imp[0]),
                [_r(norm), _r(norm)] if _r(norm) is not None else None,
                "포트 선언 Impedance vs Solver.NormingImpedance",
                {"port_impedances": sorted(set(imp)), "norming_impedance": norm}, "Ω"))
        # 원거리장 요청 — 결과 파일이 없어도 "무엇을 어느 주파수에서 계산하라 했는가"는 선언에 남는다.
        ff = c.get("farfield_requested") or {}
        fs = [_r(x) for x in (ff.get("frequencies") or []) if _r(x) is not None]
        if fs:
            for lbl, v in (("최저", min(fs)), ("최고", max(fs))):
                items.append(_item(
                    f"원거리장 요청 주파수 {lbl} · CST {nm}", v, band,
                    "선언값 그대로 — 계산 아님",
                    {"n_requested": ff.get("n"), "frequencies": fs, "unit": ff.get("unit"),
                     "선언_대역": [flo, fhi], "출처": ff.get("출처")}, "GHz",
                    "요청 목록이다 — 결과 파일 존재는 '성능 데이터 보유' 항목이 말한다."))
        mb = c.get("monitor_subvolume_declared")
        if mb:
            items.append(_item(
                f"모니터 서브볼륨 · 최대 변 · CST {nm}", max(mb["size_mm"]),
                th.get("scale_bbox_mm") if mb.get("applied") else None,
                "max(size_mm), size = 모니터 SetSubvolume 선언의 축별 (max − min)",
                {"size_mm": mb["size_mm"], "n_declarations": mb["n_declarations"],
                 "n_applied": mb.get("n_applied"), "출처": mb["출처"]}, "mm",
                None if mb.get("applied") else
                "**모델 범위가 아니다** — UseSubvolume=False 로 이 좌표는 적용되지 않는다. "
                "규모 타당성은 소자 좌표 기준 항목으로 판정한다."))
        # 변환 좌표 범위가 임포트 형상의 크기를 넘는지 — 넘으면 배열 대상이 임포트 전체가 아니다.
        imp_bbox = next((g.get("bbox_size_mm") for g in (geometry.get("dxf") or [])
                         if g.get("bbox_size_mm")), None)
        if cst_arr and imp_bbox:
            items.append(_item(
                f"변환 좌표 범위 ÷ 임포트 형상 폭 · CST {nm}",
                _r(cst_arr["aperture_mm"][0] / imp_bbox[0]) if imp_bbox[0] else None, None,
                "ratio = 소자 x 좌표 범위 / 임포트 DXF bbox 폭",
                {"소자_x_범위_mm": cst_arr["aperture_mm"][0], "임포트_bbox_mm": imp_bbox,
                 "출처": "변환 선언 좌표 vs 임포트 DXF 판독 bbox"}, "배",
                "1 을 크게 넘으면 배열 대상이 임포트 형상 **전체가 아니라 그 안의 명명된 솔리드 하나**다. "
                "`import_N` ↔ DXF 폴리라인 대응은 선언되지 않으므로 형상 복제는 추측이 된다 — 하지 않는다(T-1)."))
        if cst_arr:
            items.append(_item(
                f"형상 범위 · 소자 좌표 기준 최대 변 · CST {nm}", max(cst_arr["aperture_mm"]),
                th.get("scale_bbox_mm"),
                "max(aperture_mm), aperture = 배치된 소자 좌표의 축별 (max − min)",
                {"aperture_mm": cst_arr["aperture_mm"], "n_elements": cst_arr["n_elements"],
                 "출처": cst_arr["출처"],
                 "비교_모니터_서브볼륨": (mb or {}).get("size_mm")}, "mm",
                "모니터 서브볼륨과 다르면 그쪽이 임포트 원본 bbox 잔여값일 수 있다 — 실측에서 그랬다."))
        rd = c.get("results_declared") or {}
        items.append(_item(
            f"성능 데이터 보유 · CST {nm}", 0 if not (rd.get("has_sparameter") or rd.get("has_farfield")) else 1,
            None, "Result/Model.res treepath 에 S-파라미터·원거리장 항목이 있는가 (1=있음, 0=없음)",
            {"n_entries": rd.get("n_entries"), "has_sparameter": rd.get("has_sparameter"),
             "has_farfield": rd.get("has_farfield"),
             "farfield_요청_주파수": ff.get("frequencies"), "출처": rd.get("출처")}, "유무",
            "0 이면 성능 절은 비운다 — 채움 주체: 선언된 원거리장 모니터"
            f"({ff.get('n', 0)}건 @ {ff.get('frequencies')} {ff.get('unit')})를 실행하고 "
            "Result/export/ 로 내보내기. 값을 만들어 채우지 않는다(N-3)."))

    return items


def requirements_of(pname: str, pdef: dict) -> dict:
    """제품 정의에서 **해석이 쓰는 요구 묶음**을 뽑는다.

    한 곳에서만 만든다 — 해석이 쓰는 것과 낡음 판정이 대조하는 것이 갈라지면,
    "낡았다/안 낡았다"가 실제와 무관해진다(F-36 과 같은 부류의 사고다).
    """
    return {"product": pname,
            # 사람이 부르는 이름과 **용도** — 문서 요약 절이 이것으로 시작한다.
            # 용도는 원천 어디에도 없다. 사람 선언(산지 e)이고, 없으면 없는 채로 둔다.
            "label": pdef.get("label"), "use": pdef.get("use"),
            "band_ghz": pdef.get("band_ghz"),
            # 타겟 주파수 — **재질·형상 해석의 기준점**이다(Q-16). 없으면 대역 중심도 쓰지
            # 않는다. 대역 중심은 타겟이 아니라 계산 편의값이라 제품이 선언해야 한다.
            "f0_ghz": pdef.get("f0_ghz"),
            "requirements_spec": pdef.get("requirements") or {},
            "substrate": pdef.get("substrate") or {},
            # 사람이 선언해야만 들어오는 값들 — 원천 어디에도 없다(산지 e).
            # 해석은 이 값으로 **판정하지 않는다**. 문서에 싣기 위해 통과시킬 뿐이다.
            "stackup": pdef.get("stackup") or {},
            "reflector": pdef.get("reflector") or {},
            # 그림 취향 — 판정에 쓰지 않는다. 형상 대표를 어느 렌더러로 세울지의 선언이고
            # (D-59), 여기 실려야 figures.py 가 읽는다. 바뀌면 낡음 판정에도 걸린다.
            "figure_preference": pdef.get("figure_preference") or {}}


def stale_requirements(run_id: str, registry_path=None) -> dict:
    """이 run 의 해석이 **지금 레지스트리와 어긋나는가.**

    왜 필요한가 — `declare.py` 로 값을 선언하면 레지스트리는 바뀌지만 이미 돌아 있는
    run 의 `해석_결과.json` 은 그대로다. 그 상태로 문서를 쓰면 **"말하면 채워진다"고
    해놓고 안 채워진다.** 사람은 자기가 말한 값이 왜 없는지 알 수 없다.

    ★ 파일 시각으로 판정하지 않는다(D-28). **내용을 대조한다** — 해석이 저장해 둔
      요구 묶음과 지금 레지스트리에서 다시 뽑은 요구 묶음을 비교한다.
    """
    wd = work_dir(run_id, create=False)
    res = read_json(wd / "해석_결과.json") if (wd / "해석_결과.json").exists() else {}
    if not res:
        return {"stale": False, "why": "해석 결과가 없다 — 낡음을 따질 것이 없다",
                "changed": []}
    was = res.get("requirements") or {}
    reg = load_registry(registry_path)
    pname, pdef = resolve_product(reg, res.get("product"))
    now = requirements_of(pname, pdef)
    changed = [k for k in sorted(set(was) | set(now)) if was.get(k) != now.get(k)]
    return {"stale": bool(changed), "changed": changed,
            "was": {k: was.get(k) for k in changed},
            "now": {k: now.get(k) for k in changed},
            "why": ("선언 이후 레지스트리가 바뀌었다 — 이 run 을 다시 돌려야 "
                    "새 값이 문서에 실린다" if changed else "레지스트리와 일치한다")}


# ── CLI ────────────────────────────────────────────────────────────────────
def interpret(run_id: str, product=None, registry_path=None) -> dict:
    wd = work_dir(run_id)
    ext = read_json(wd / "추출_결과.json")
    reg = load_registry(registry_path)
    pname, pdef = resolve_product(reg, product or (ext.get("source") or {}).get("product"))
    if pdef.get("확정_대기"):
        raise SystemExit(f"레지스트리 항목 '{pname}' 은 확정 대기 상태다 — 사용자 확정 없이 쓰지 않는다")

    geom = dict(ext.get("geometry") or {})
    geom["cst"] = (ext.get("declared") or {}).get("cst") or []
    req = requirements_of(pname, pdef)
    items = verify(geom, req, pdef.get("thresholds") or {})

    # 배열인자 곡선은 여기서 한 번만 계산해 저장한다 — 렌더는 이 곡선을 그리기만 한다(수치 산지 단일화).
    arr = next((d["array"] for d in (geom.get("dxf") or []) if d.get("array")), None)
    af = array_factor(arr, reference_lambda(arr)[1]) if arr else None
    afs = []
    if af:
        afs.append(dict(af, label="DXF 패치 배열(도면 판독 좌표)",
                        label_ascii="DXF patch array (drawing coords)",
                        기준="λ = c / f_ref, f_ref = c/(2·mean(patch_L))"))
    cst_arrays = {}
    for c in (geom.get("cst") or []):
        ca = cst_array_positions(c)
        if not ca: continue
        cst_arrays[c.get("name")] = ca
        fr = c.get("solver_frequency_range_effective") or {}
        lo, hi = _r(fr.get("min")), _r(fr.get("max"))
        if lo and hi:
            lam_c = _r(C_MM_GHZ / ((lo + hi) / 2))
            a2 = array_factor({"patches": [{"x0": p[0], "x1": p[0]} for p in ca["points_mm"]],
                               "pitch_mean_mm": min(ca["x_step_mm"]) if ca["x_step_mm"] else None},
                              lam_c)
            if a2:
                afs.append(dict(a2, label=f"CST {c.get('name')} 소자 배열(변환 선언 좌표 · φ=0 절단)",
                                label_ascii=f"CST {c.get('name')} element array (phi=0 cut)",
                                기준=f"λ = c / f_center, f_center = ({lo}+{hi})/2 GHz"))

    n_undecided = sum(1 for i in items if i["pass"] is None)
    n_fail = sum(1 for i in items if i["pass"] is False)
    verdict = ("OK" if n_fail == 0 and n_undecided == 0 else
               "임계 미지정 포함" if n_fail == 0 else "불일치 있음")
    res = {"run_id": run_id, "product": pname, "registry_version": reg.get("registry_version"),
           "numeric_rules": numeric_rules(),
           "requirements": req, "thresholds": pdef.get("thresholds"),
           "budgets": pdef.get("budgets"),
           "items": items, "array_factor": af, "array_factors": afs,
           "cst_array_positions": cst_arrays,
           "geom_hash": ext.get("geom_hash"), "cached": False,
           "verdict": verdict,
           "reason": f"대조 {len(items)}건 · 불일치 {n_fail} · 임계 미지정 {n_undecided}",
           "규율": "임계 미지정 항목은 판정하지 않는다. pass 는 대조 결과이며 채택 확정은 사람(A-1)."}
    write_json(wd / "해석_결과.json", res)
    return res


def self_test() -> int:
    """유전율의 **기준 주파수** 판정만 본다(Q-15 · ISSUES I-N). 나머지 검산은 실물 run 이 검증한다."""
    ok = fail = 0

    def chk(n, cond, d=""):
        nonlocal ok, fail
        if cond:
            ok += 1; print(f"  PASS  {n}")
        else:
            fail += 1; print(f"  FAIL  {n}  {d}")

    print("[verify_api.py 자기 시험 — εr 기준 주파수]")
    n = _er_note(3.07, None, 9.4)
    chk("기준 주파수가 없으면 판정하지 않는다", bool(n["skip"]), str(n))
    chk("관례를 근거로 삼지 않는다고 밝힌다", "관례는 근거가 아니다" in n["why"])

    n = _er_note(3.07, 10.0, 9.4)
    chk("기준 10 GHz · 타겟 9.4 GHz 는 그대로 쓴다", n["skip"] is None, str(n))
    chk("얼마나 차이 나는지 적는다", "6% 차이" in n["why"], n["why"])

    n = _er_note(3.07, 10.0, 60.0)
    chk("기준과 타겟이 크게 벌어지면 판정하지 않는다", bool(n["skip"]), str(n))
    chk("재계산하지 않는다고 밝힌다(D-34)", "재계산하지 않는다" in n["skip"], n["skip"])
    chk("벌어진 폭을 적는다", "500%" in n["why"], n["why"])

    n = _er_note(3.07, 10.0, None)
    chk("타겟이 없으면 폭을 재지 못한다고 말한다", "재지 못한다" in n["why"], n["why"])
    chk("타겟이 없다고 판정을 막지는 않는다", n["skip"] is None)

    chk("유전율 자체가 없으면 이 검사는 무관", _er_note(None, None, 9.4)["skip"] is None)
    chk("임계에 산지가 있다", ER_REF_GAP_MAX == 0.25)

    # 요구 명세가 레지스트리에서 흘러오는가
    try:
        reg = load_registry()
    except Exception:
        reg = None
    if reg:
        m = (reg.get("products") or {}).get("example_x_band") or {}
        chk("타겟 주파수가 제품에 있다", m.get("f0_ghz") == 9.4, str(m.get("f0_ghz")))
        rq = m.get("requirements") or {}
        chk("요구 명세 5종", len(rq) == 5, str(list(rq)))
        chk("HBW 는 상한만 · VBW 는 하한만 (부등호를 그대로 옮긴다)",
            rq.get("hbw_deg", {}).get("min") is None
            and rq.get("vbw_deg", {}).get("max") is None, str(rq.get("hbw_deg")))
        chk("두 빔폭의 축을 구분한다",
            rq["hbw_deg"]["axis"] != rq["vbw_deg"]["axis"],
            f"{rq['hbw_deg']['axis']} vs {rq['vbw_deg']['axis']}")
        chk("게인은 하한만 — 없는 상한을 지어내지 않는다",
            rq["gain_dbi"]["min"] == 23.0 and rq["gain_dbi"]["max"] is None)

    print(f"\n결과: {ok}/{ok + fail} PASS")
    return 0 if fail == 0 else 1


def main(argv=None):
    if (argv or sys.argv)[1:2] == ["self-test"]:
        return self_test()
    ap = argparse.ArgumentParser(description="해석 — 등가모델 대조(임계는 인자로만)")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--product", default=None)
    ap.add_argument("--registry", default=None)
    a = ap.parse_args(argv)
    r = interpret(a.run_id, a.product, a.registry)
    print(f"해석: product={r['product']} · registry={r['registry_version']} · {r['reason']} → {r['verdict']}")
    for i in r["items"]:
        mark = {True: "○", False: "×", None: "·"}[i["pass"]]
        print(f"  {mark} {i['check']}: {i['value']} {i['unit'] or ''}"
              f"{'  [임계 ' + str(i['threshold']) + ']' if i['threshold'] else ''}")
    print(f"산출: {work_dir(a.run_id, False) / '해석_결과.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
