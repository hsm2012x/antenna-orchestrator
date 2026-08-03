#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/revision.py — 판본 비교 · 원인 후보 환산 · 순서 근거 (LLM 0콜)

무엇을 하나
    같은 계열 도안 두 장의 **기하 차이**로 "무엇이 달라졌나"를 말한다.
    `assets.py diff` 는 "값이 다르다"까지만 말한다 — 여기는 **달라진 방식의 종류**를 가른다
    (전체 배율인가 · 폭만인가 · 국부인가 · 아예 다른 배열인가).

출처
    승격  `reference_code/08_antenna_cad_em.ipynb` **셀 35** STEP E —
          `revision_diff` · `interpret_scale` · `parse_claims` · `order_hint`,
          **셀 7** `ms_eeff_z0`(Hammerstad-Jensen 근사).
          원본과 스냅샷은 수정하지 않았다. 셀 36 `scale_dxf` 는 검출기 자체 검증용이라
          운영 경로가 아니다 — 자기 시험 재료로만 쓴다.

★ 승격하면서 고친 것 **여섯** (원본 그대로 옮기면 조용히 틀린다)
    ① **짝짓기** — 원본은 `zip(PA, PB)` 인덱스 짝이다. 주석은 "xc 최근접"이라 적혀 있지만
       구현이 없다. 한쪽에 소자가 하나 끼거나 빠지면 **전부 한 칸씩 밀려** 배율이 통째로
       틀린다. 여기서는 xc 최근접으로 짝짓고, **짝짓기 품질을 판정에 싣는다** —
       애매하면(최근접 거리가 주기의 절반을 넘으면) 판정을 멈춘다(N-3).
    ② **임계 산지** — `tol_um=2.0` · `std<2e-4` · `<3e-3` · `<1e-3` 이 코드에 박혀 있었다.
       `numeric_rules()` 로 모아 산지와 함께 싣는다. 관측 근거가 없으면 없다고 적는다.
    ③ **표본 수 의존** — "배율이 균일한가"를 `std < 2e-4` 로 봤다. 절대 임계라 소자 수·
       치수 크기가 바뀌면 뜻이 달라진다. **상대 피크투피크**(`(max-min)/mean`)로 바꾼다 —
       "전 소자가 같은 배율인가"를 직접 묻는 양이다.
    ④ **폭 기본값** — `interpret_scale(w_patch=12.0, w_line=2.0)` 이 기본값이었다. 다른
       안테나에 그대로 쓰면 `er_equivalent` 가 조용히 오염된다. **추출값을 요구**하고,
       없으면 **계산하지 않는다**(N-3).
    ⑤ **기계 판독 불가** — `order_hint` 가 산문 줄만 돌려줬다. 문서·DB 가 쓸 수 없다.
       `{order, confidence, basis[]}` 로 낸다. 앵커도 ① 하나만 구현돼 있던 것을 넓힌다.
    ⑥ **주파수 부호가 반대** — 승격하며 새로 찾았다. `f ∝ 1/L` 이므로 치수가 커지면 공진은
       **내려가야** 하는데 원본은 양수를 냈다. 주석("치수가 커지면 주파수는 내려간다")과
       값이 어긋난 채였다. 실물 사례가 "시뮬 대비 100 MHz 시프트"라 **방향이 뜻의 전부**다 —
       이대로 문서에 실리면 어느 쪽으로 옮겼는지가 뒤집힌다.

★ 축퇴 — 배율만으로는 원인을 못 가른다
    Dk 2.60→2.57 보정과 50 MHz 시프트 보정은 **배율이 같다**. 그래서 `interpret_scale` 은
    둘 다 환산해 내놓고 **고르지 않는다.** 가르는 정보는 `dk_signature_ppm` (패치와 급전선의
    배율 차이 — Dk 원인이면 다르다)과 **문서 서술**이다.

CLI
    python tools/revision.py compare <run_A> <run_B>
    python tools/revision.py self-test
"""
from __future__ import annotations

import math
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

# ── 판정 규칙 (c) — 원장에 실린다. **관측 근거를 함께 적는다** ────────────────
TOL_UM = 2.0                 # 이 이하 변화는 같다고 본다
UNIFORM_SPREAD_MAX = 1e-3    # 상대 피크투피크가 이 이하면 "전 소자 같은 배율"
SCALE_DEADBAND = 1e-4        # 배율이 1 에서 이만큼 안 벗어나면 배율이라 하지 않는다
CLAIM_MATCH_FSHIFT = 3e-3    # 문서의 주파수 시프트 주장과 실측 배율의 허용 차
CLAIM_MATCH_DK = 1e-3        # 문서의 Dk 주장과 실측 배율의 허용 차
PAIR_GAP_RATIO = 0.5         # 최근접 짝의 거리가 주기의 이 배를 넘으면 짝짓기 실패


def numeric_rules() -> dict:
    return {
        "tol_um": TOL_UM,
        "uniform_spread_max": UNIFORM_SPREAD_MAX,
        "scale_deadband": SCALE_DEADBAND,
        "claim_match_fshift": CLAIM_MATCH_FSHIFT,
        "claim_match_dk": CLAIM_MATCH_DK,
        "pair_gap_ratio": PAIR_GAP_RATIO,
        "산지": ("전부 노트북 08 셀 35 에 상수로 박혀 있던 값이다 — **관측 근거가 없다.** "
               "옮겨 적되 여기 모아 두어 재조정할 자리를 만든다. 실물 판본쌍이 쌓이면 "
               "그때 관측으로 정한다."),
        "바뀐 것": ("원본의 `ratio_L.std < 2e-4` 를 **상대 피크투피크**로 바꿨다. 절대 "
                 "표준편차는 소자 수·치수 크기가 달라지면 뜻이 달라진다."),
    }


# ── 셀 7 승격 — 마이크로스트립 유효유전율 ────────────────────────────────────

def ms_eeff_z0(w, h, er):
    """Hammerstad-Jensen 근사 → (eeff, Z0). 출처: 08_antenna_cad_em.ipynb 셀 7 (무수정)"""
    u = max(w, 1e-6) / h
    eeff = ((er + 1) / 2 + (er - 1) / 2 * (1 + 12 / u) ** -0.5
            + (0.04 * (1 - u) ** 2 if u < 1 else 0.0))
    if u <= 1:
        z0 = 60 / math.sqrt(eeff) * math.log(8 / u + u / 4)
    else:
        z0 = 120 * math.pi / (math.sqrt(eeff) * (u + 1.393 + 0.667 * math.log(u + 1.444)))
    return eeff, z0


# ── 도우미 ──────────────────────────────────────────────────────────────────

def _stat(v: list[float]) -> dict:
    n = len(v)
    mean = sum(v) / n
    var = sum((x - mean) ** 2 for x in v) / n
    lo, hi = min(v), max(v)
    return {"mean": mean, "std": math.sqrt(var), "min": lo, "max": hi,
            # 수리 ③ — 균일성 판정은 이 값으로 한다. 표본 수·치수 크기에 덜 휘둘린다.
            "rel_spread": (hi - lo) / abs(mean) if mean else float("inf")}


def _patches(arr: dict) -> list[dict]:
    """추출 산출(`x0`·`x1`·`L_mm`·`W_mm`)을 비교용 모양으로. `xc` 는 여기서 만든다."""
    out = []
    for q in arr.get("patches") or []:
        x0, x1 = q.get("x0"), q.get("x1")
        L = q.get("L_mm", (x1 - x0) if (x0 is not None and x1 is not None) else None)
        if L is None or not q.get("W_mm"):
            continue
        out.append({"xc": (x0 + x1) / 2 if x0 is not None else None,
                    "L": float(L), "W": float(q["W_mm"])})
    return sorted(out, key=lambda p: (p["xc"] is None, p["xc"]))


def pair_by_xc(PA: list[dict], PB: list[dict], pitch_ref: float | None) -> dict:
    """수리 ① — **xc 최근접**으로 짝짓는다. 인덱스 짝이 아니다.

    한쪽에 소자가 하나 끼거나 빠지면 인덱스 짝은 전부 한 칸씩 밀려 배율이 통째로 틀린다.
    최근접 거리가 주기의 `PAIR_GAP_RATIO` 배를 넘으면 **짝짓지 않는다** — 지어내지 않는다.
    """
    if any(p["xc"] is None for p in PA + PB):
        return {"ok": False, "why": "xc 를 모른다 — 좌표 없는 추출본이다", "pairs": []}
    lim = (pitch_ref * PAIR_GAP_RATIO) if pitch_ref else None
    pairs, used, gaps, orphan = [], set(), [], []
    for a in PA:
        cands = [(abs(b["xc"] - a["xc"]), i) for i, b in enumerate(PB) if i not in used]
        if not cands:
            orphan.append(a["xc"]); continue
        d, i = min(cands)
        if lim is not None and d > lim:
            orphan.append(a["xc"]); continue
        used.add(i); gaps.append(d)
        pairs.append((a, PB[i]))
    return {"ok": bool(pairs) and not orphan and len(used) == len(PB),
            "pairs": pairs, "n_paired": len(pairs),
            "max_gap_mm": max(gaps) if gaps else None,
            "orphan_xc": orphan + [b["xc"] for i, b in enumerate(PB) if i not in used],
            "limit_mm": lim,
            "why": ("전 소자가 주기의 절반 안쪽에서 짝지어졌다" if not orphan and pairs else
                    "짝을 못 지은 소자가 있다 — 같은 계열이 아니거나 소자가 늘고 줄었다")}


# ── 판본 diff ───────────────────────────────────────────────────────────────

def revision_diff(arrA: dict, arrB: dict, tol_um: float = TOL_UM) -> dict:
    """도안 A→B 기하 diff. 출처: 셀 35 `revision_diff` (수리 ①③ 반영)"""
    PA, PB = _patches(arrA), _patches(arrB)
    out = {"n_A": len(PA), "n_B": len(PB),
           "topology_changed": len(PA) != len(PB), "pairs": []}
    if not PA or not PB:
        return {**out, "verdict": "no-data",
                "why": "패치를 못 읽었다 — 배열 추출이 없는 도면이다"}
    if out["topology_changed"]:
        return {**out, "verdict": "topology-change",
                "why": f"소자 수가 다르다 {len(PA)} → {len(PB)} — 배율을 논할 수 없다"}

    pitch_ref = None
    pm = arrA.get("pitch_mean_mm") or arrA.get("pitch_mm")
    if isinstance(pm, list) and pm:
        pitch_ref = sum(pm) / len(pm)
    elif isinstance(pm, (int, float)):
        pitch_ref = float(pm)

    pr = pair_by_xc(PA, PB, pitch_ref)
    out["pairing"] = {k: v for k, v in pr.items() if k != "pairs"}
    if not pr["ok"]:
        # 수리 ① — 짝을 못 지으면 **판정하지 않는다**. 밀린 짝으로 낸 배율은 거짓이다.
        return {**out, "verdict": "pairing-ambiguous",
                "why": f"짝짓기 실패 — {pr['why']}. 배율을 내지 않는다(N-3)"}

    rL, rW, dL, dW = [], [], [], []
    for a, b in pr["pairs"]:
        rL.append(b["L"] / a["L"]); rW.append(b["W"] / a["W"])
        dL.append((b["L"] - a["L"]) * 1000); dW.append((b["W"] - a["W"]) * 1000)   # um
        out["pairs"].append({"xc_A": round(a["xc"], 4), "xc_B": round(b["xc"], 4),
                             "dL_um": round(dL[-1], 3), "dW_um": round(dW[-1], 3)})

    out["ratio_L"], out["ratio_W"] = _stat(rL), _stat(rW)
    pa, pb = arrA.get("pitch_mm") or [], arrB.get("pitch_mm") or []
    if len(pa) == len(pb) and pa:
        out["ratio_pitch"] = _stat([b / a for a, b in zip(pa, pb) if a])
    apA, apB = arrA.get("aperture_mm"), arrB.get("aperture_mm")
    if apA and apB:
        out["ratio_aperture"] = apB / apA
    out["max_dL_um"] = max(abs(x) for x in dL)
    out["max_dW_um"] = max(abs(x) for x in dW)

    # ── 분류 — 수리 ③: 균일성은 상대 피크투피크로 본다
    uni_L = out["ratio_L"]["rel_spread"] < UNIFORM_SPREAD_MAX
    changed_L = out["max_dL_um"] > tol_um
    changed_W = out["max_dW_um"] > tol_um
    if not changed_L and not changed_W:
        out["verdict"], out["why"] = "identical", "기하 차이가 허용 오차 안이다"
    elif changed_L and uni_L and abs(out["ratio_L"]["mean"] - 1) > SCALE_DEADBAND:
        out["verdict"] = "uniform-scale"
        out["why"] = "전 소자가 같은 배율로 바뀌었다 — 주파수 재조정 또는 Dk 재보정 계열"
    elif changed_W and not changed_L:
        out["verdict"] = "aperture-taper-tune"
        out["why"] = "폭만 바뀌었다 — SLL·빔폭 튜닝 계열"
    else:
        out["verdict"] = "local-tune"
        out["why"] = "일부 소자만 바뀌었다 — 국부 튜닝"
    out["uniform_L"] = uni_L
    out["numeric_rules"] = numeric_rules()
    return out


# ── 배율 → 원인 후보 ────────────────────────────────────────────────────────

def interpret_scale(ratio: float, f0_ghz: float, h_mm: float, er: float,
                    w_patch_mm: float | None, w_line_mm: float | None) -> dict:
    """배율 → 주파수 재조정 / Dk 재보정을 **각각** 환산한다. 고르지 않는다.

    수리 ④ — `w_patch`·`w_line` 에 기본값을 두지 않는다. 다른 안테나에 12/2 mm 를 그대로
    쓰면 `er_equivalent` 가 조용히 오염된다. 없으면 **계산하지 않는다**(N-3).
    출처: 셀 35 `interpret_scale`
    """
    # ★ 수리 ⑥ (승격하며 새로 발견) — **부호가 반대였다.**
    #   f ∝ 1/L 이므로 치수가 커지면(ratio>1) 공진은 **내려간다**: Δf = f0·(1/ratio − 1).
    #   원본 셀 35 는 `f0·(1 − 1/ratio)` 로 부호가 뒤집혀 있었다. 주석은 "치수가 커지면
    #   주파수는 내려간다"고 적혀 있는데 값은 양수가 나왔다 — 주석과 값이 어긋난 채였다.
    #   이대로 문서에 실리면 **어느 방향으로 옮겼는지가 뒤집힌다.** 실물 사례가
    #   "시뮬 대비 100 MHz 시프트" 라 방향이 뜻의 전부다.
    #   보정량(부호 없는 크기)이 필요한 자리는 `correction_mhz` 로 따로 낸다.
    df_mhz = f0_ghz * 1000 * (1 / ratio - 1)
    out = {"ratio": ratio, "delta_f_mhz": df_mhz, "correction_mhz": abs(df_mhz),
           "부호": "치수가 커지면(ratio>1) 주파수는 내려간다 — 음수",
           "note": "주파수 재조정이면 패치·급전선 배율이 같고, Dk 재보정이면 다르다"}
    if not w_patch_mm or not h_mm or not er:
        return {**out, "er_equivalent": None, "dk_signature_ppm": None,
                "why": ("패치 폭·기판 두께·유전율이 없어 Dk 환산을 하지 않는다 — "
                        "기본값으로 계산하면 등가 유전율이 조용히 오염된다(N-3)")}
    lo, hi = 1.5, 6.0
    for _ in range(80):
        mid = (lo + hi) / 2
        r = math.sqrt(ms_eeff_z0(w_patch_mm, h_mm, er)[0]
                      / ms_eeff_z0(w_patch_mm, h_mm, mid)[0])
        if r < ratio:
            hi = mid
        else:
            lo = mid
    er_eq = (lo + hi) / 2
    out["er_equivalent"] = er_eq
    if not w_line_mm:
        return {**out, "dk_signature_ppm": None,
                "why": "급전선 폭이 없어 Dk 서명을 내지 않는다 — 원인을 가를 근거가 없다"}
    rp = math.sqrt(ms_eeff_z0(w_patch_mm, h_mm, er)[0] / ms_eeff_z0(w_patch_mm, h_mm, er_eq)[0])
    rl = math.sqrt(ms_eeff_z0(w_line_mm, h_mm, er)[0] / ms_eeff_z0(w_line_mm, h_mm, er_eq)[0])
    out["dk_signature_ppm"] = (rp - rl) * 1e6
    out["why"] = "두 원인을 모두 환산했다 — 고르는 것은 dk_signature 와 문서 서술이다"
    return out


# ── 문서 서술에서 단서 뽑기 ─────────────────────────────────────────────────

_CLAIM = [
    (r"([0-9]\.[0-9]+)\s*대신\s*Dk\s*[=:]?\s*([0-9]\.[0-9]+)", "dk_pair"),
    (r"Dk\s*([0-9]\.[0-9]+)\s*대신\s*([0-9]\.[0-9]+)", "dk_pair"),
    (r"(\d+)\s*차\s*설계", "stage"),
    (r"Dk\s*[=:]?\s*([0-9]\.[0-9]+)", "dk"),
    (r"([+\-]?\d+)\s*MHz\s*(시프트|shift|이동)", "fshift"),
]


def parse_claims(text: str) -> list[dict]:
    """보고서 문장에서 판본 단서 추출 — 정규식만(LLM 0). 놓친 것은 사람이 보완한다.
    출처: 셀 35 `parse_claims` (규칙 순서만 조정 — dk_pair 가 dk 보다 먼저 맞아야 한다)"""
    out, spans = [], []
    for pat, kind in _CLAIM:
        for m in re.finditer(pat, text, re.I):
            if any(s <= m.start() < e for s, e in spans):
                continue          # 더 구체적인 규칙이 이미 먹은 자리는 건너뛴다
            spans.append((m.start(), m.end()))
            out.append({"kind": kind, "groups": m.groups(), "text": m.group(0)})
    return out


# ── 순서 판정 — 수리 ⑤: 기계가 읽을 수 있게 낸다 ────────────────────────────

ANCHORS = {
    "doc_stage": (1, "문서의 'N차' 표기 — 사람이 직접 선언한 순서"),
    "measured":  (2, "실측(s2p) 근접 — 어느 판본이 측정과 맞나"),
    "work_time": (3, "작업 시각 — timeline.py 근거 등급(실측 > 선언)"),
}
# ★ mtime 은 앵커가 아니다. 복사·동기화로 뭉개진다(timeline.py 규율). 넣지 않는다.


def order_hint(diff: dict, claims: list[dict], f0_ghz: float | None = None,
               h_mm: float | None = None, er: float | None = None,
               w_patch_mm: float | None = None, w_line_mm: float | None = None,
               times: dict | None = None) -> dict:
    """순서 근거를 **기계 판독 형태로** 낸다. 출처: 셀 35 `order_hint` (수리 ⑤)

    반환 `{order, confidence, basis[], lines[]}` —
      order: `A_first` | `B_first` | `unknown` (순서는 **앵커가 있어야** 정해진다)
    """
    lines, basis = [], []
    v = diff.get("verdict")

    it = None
    if v == "uniform-scale" and f0_ghz:
        it = interpret_scale(diff["ratio_L"]["mean"], f0_ghz, h_mm, er, w_patch_mm, w_line_mm)
        lines.append(f"균일 배율 {it['ratio']:.5f} ({(it['ratio']-1)*100:+.3f}%) — "
                     f"주파수 환산 {it['delta_f_mhz']:+.0f} MHz"
                     + (f" · Dk 환산 등가 er {it['er_equivalent']:.3f}"
                        if it.get("er_equivalent") else f" · Dk 환산 없음({it['why']})"))
        for c in claims:
            if c["kind"] == "fshift":
                # 문서는 "100MHz 시프트"까지만 말하고 **방향을 말하지 않는 일이 많다.**
                # 그래서 크기로 대조하고, 방향은 기하가 말하게 둔다(아래 direction).
                want = f0_ghz * 1000 / (f0_ghz * 1000 - abs(int(c["groups"][0])))
                hit = abs(want - it["ratio"]) < CLAIM_MATCH_FSHIFT
                lines.append(f"  문서 '{c['text']}' → 예상 배율 {want:.5f} · "
                             f"실측 {it['ratio']:.5f} → {'일치' if hit else '불일치'}")
                basis.append({"anchor": "claim_fshift", "grade": 0, "match": hit,
                              "says": c["text"], "expected_ratio": want,
                              "direction": ("내림" if it["delta_f_mhz"] < 0 else "올림"),
                              "why": ("문서는 크기만 말한다 — 방향은 기하가 말한다"
                                      "(치수가 커지면 내림)")})
            if c["kind"] == "dk_pair" and h_mm and er and w_patch_mm:
                a, b = float(c["groups"][0]), float(c["groups"][1])
                want = math.sqrt(ms_eeff_z0(w_patch_mm, h_mm, max(a, b))[0]
                                 / ms_eeff_z0(w_patch_mm, h_mm, min(a, b))[0])
                hit = abs(want - it["ratio"]) < CLAIM_MATCH_DK
                lines.append(f"  문서 '{c['text']}' → 예상 배율 {want:.5f} · "
                             f"실측 {it['ratio']:.5f} → {'일치' if hit else '불일치'}")
                basis.append({"anchor": "claim_dk", "grade": 0, "match": hit,
                              "says": c["text"], "expected_ratio": want})
    elif v in ("aperture-taper-tune", "local-tune"):
        lines.append(f"국부 변경 — 길이 최대 {diff['max_dL_um']:.1f} um · "
                     f"폭 최대 {diff['max_dW_um']:.1f} um → 주파수가 아니라 패턴 튜닝 계열")
    elif v == "topology-change":
        lines.append(f"소자 수 변경 {diff['n_A']} → {diff['n_B']} — 같은 계열이 아닐 수 있다")
    elif v == "pairing-ambiguous":
        lines.append(f"짝짓기 실패 — {diff.get('why', '')}")
    else:
        lines.append("유의미한 기하 차이 없음 — 같은 도면의 사본일 가능성")

    # ── 앵커 — 순서는 여기서만 나온다
    for c in claims:
        if c["kind"] == "stage":
            basis.append({"anchor": "doc_stage", "grade": ANCHORS["doc_stage"][0],
                          "says": c["text"], "order": None,
                          "why": ("문서가 차수를 말한다. 다만 **어느 도면의 차수인지**는 "
                                  "문장만으로 알 수 없다 — 사람이 잇는다")})
    if times:
        a, b = times.get("A"), times.get("B")
        ga, gb = times.get("grade_A"), times.get("grade_B")
        measured = {"solver_log", "dwg_header"}
        if a and b and a != b:
            strong = (ga in measured) and (gb in measured)
            basis.append({"anchor": "work_time", "grade": ANCHORS["work_time"][0],
                          "says": f"A {a} · B {b}", "order": "A_first" if a < b else "B_first",
                          "measured": strong,
                          "why": ("실측 근거끼리의 비교다" if strong else
                                  "한쪽이 선언 근거다 — 약하다")})
    basis.append({"anchor": "measured", "grade": ANCHORS["measured"][0], "order": None,
                  "why": "실측(s2p) 근접 비교 — **미구현**. 원거리장·S 파라미터가 없다(EXT-2)"})

    ordered = [b for b in basis if b.get("order")]
    ordered.sort(key=lambda b: b["grade"])
    order = ordered[0]["order"] if ordered else "unknown"
    conf = 0.0
    if ordered:
        conf = 0.8 if ordered[0].get("measured") else 0.5
        if len({b["order"] for b in ordered}) > 1:
            conf, order = 0.2, "unknown"        # 앵커끼리 어긋나면 정하지 않는다
    return {"order": order, "confidence": conf, "basis": basis, "lines": lines,
            "interpret": it,
            "why": ("순서는 **앵커가 있어야** 정해진다. 기하 차이는 무엇이 달라졌는지만 "
                    "말하고 어느 쪽이 먼저인지는 말하지 못한다.")}


# ── run 두 개 비교 ──────────────────────────────────────────────────────────

def _array_of(work: Path) -> dict | None:
    ex = C.read_json(work / "추출_결과.json")
    for d in (ex.get("geometry", {}).get("dxf") or []):
        if d.get("array"):
            return d["array"]
    return None


DIELECTRIC_ER_MIN = 1.05      # 비유전율 1 은 진공·도체다 — 기판 유전체가 아니다(물리 정의)


def _sub_of(work: Path) -> dict:
    """기판·주파수·폭 — **역할 어휘로** 가져온다. 기본값을 만들지 않는다(수리 ④).

    원천 JSON 구조를 직접 뒤지지 않는다. 원천마다 키 이름이 다르고, 그것을 손으로 맞추기
    시작하면 어휘가 세 곳을 규율한다는 설계(D-23)가 무의미해진다.

    ★ 유전율이 둘 이상이면 **고르지 않는다.** `material_er` 에는 도체(εr=1)와 기판이 함께
      들어온다. 도체는 정의로 걸러낼 수 있지만, 유전체가 둘이면 어느 것이 기판인지 도구는
      모른다 — 사유를 남기고 비운다(N-3).
    """
    import catalog as CAT
    try:
        cat = CAT.load(work.name, work)
    except Exception:
        try:
            cat = CAT.build(work.name, work)
        except Exception:
            cat = {"entries": {}}

    def by_role(role):
        return [e for e in cat.get("entries", {}).values() if e.get("role") == role]

    def one(role):
        v = [e.get("value") for e in by_role(role) if isinstance(e.get("value"), (int, float))]
        return float(v[0]) if len(v) == 1 else None

    ers = [float(e["value"]) for e in by_role("material_er")
           if isinstance(e.get("value"), (int, float))]
    diel = [x for x in ers if x >= DIELECTRIC_ER_MIN]
    er, er_why = (diel[0], "유전체 하나") if len(diel) == 1 else (
        None, f"유전율 후보 {len(diel)}건 — 어느 것이 기판인지 도구가 정하지 않는다(N-3)"
        if diel else "유전율 선언이 없다")

    lo, hi = one("band_lo_ghz"), one("band_hi_ghz")
    arr = _array_of(work) or {}
    ws = arr.get("patch_W_mm") or []
    feed = arr.get("feed_W_mm") or []
    return {"h_mm": one("t_sub_mm"), "er": er, "er_why": er_why,
            "f0_ghz": ((lo + hi) / 2 if lo and hi else None),
            "w_patch_mm": (sum(ws) / len(ws)) if ws else None,
            "w_line_mm": (min(feed) if feed else None),
            "산지": "값 카탈로그의 역할 조회 — 원천 JSON 구조를 직접 뒤지지 않는다(D-23)"}


def compare(run_a: str, run_b: str, report_text: str = "") -> dict:
    wa, wb = C.work_dir(run_a, create=False), C.work_dir(run_b, create=False)
    aa, ab = _array_of(wa), _array_of(wb)
    if not aa or not ab:
        return {"a": run_a, "b": run_b, "verdict": "no-data",
                "why": "한쪽에 배열 추출이 없다 — 판본 비교를 할 근거가 없다"}
    d = revision_diff(aa, ab)
    sub = _sub_of(wa)
    times = {}
    try:
        import timeline as TL
        ta = TL.of_run(run_a); tb = TL.of_run(run_b)
        times = {"A": ta.get("work_start"), "B": tb.get("work_start"),
                 "grade_A": (ta.get("evidence") or "").split("+")[0],
                 "grade_B": (tb.get("evidence") or "").split("+")[0]}
    except Exception:
        times = {}
    oh = order_hint(d, parse_claims(report_text), sub["f0_ghz"], sub["h_mm"], sub["er"],
                    sub["w_patch_mm"], sub["w_line_mm"], times)
    return {"a": run_a, "b": run_b, "diff": d, "order": oh, "substrate": sub,
            "times": times}


# ── 자기 시험 ────────────────────────────────────────────────────────────────

def _synth(patches, pitch=None):
    return {"patches": patches, "pitch_mm": pitch or [],
            "pitch_mean_mm": (sum(pitch) / len(pitch)) if pitch else None}


def _mk(n=10, L=9.0, W=14.0, pitch=18.0, k=1.0, kw=1.0):
    return _synth([{"x0": i * pitch, "x1": i * pitch + L * k, "L_mm": L * k, "W_mm": W * kw}
                   for i in range(n)], [pitch] * (n - 1))


def self_test() -> int:
    ok = fail = 0

    def chk(n, cond, d=""):
        nonlocal ok, fail
        if cond:
            ok += 1; print(f"  PASS  {n}")
        else:
            fail += 1; print(f"  FAIL  {n}  {d}")

    print("[revision.py 자기 시험]")

    # ── 검출기 자체 검증 — 100 MHz 보정 배율을 심고 되찾는가 (셀 36 (a) 승격)
    f0 = 9.4
    k = f0 * 1000 / (f0 * 1000 - 100)
    d = revision_diff(_mk(), _mk(k=k))
    chk("균일 배율을 판정한다", d["verdict"] == "uniform-scale", str(d.get("verdict")))
    chk(f"심은 배율을 되찾는다 (오차 {abs(d['ratio_L']['mean']-k)*1e6:.2f} ppm)",
        abs(d["ratio_L"]["mean"] - k) < 1e-9)
    it = interpret_scale(d["ratio_L"]["mean"], f0, 1.524, 2.6, 14.0, 2.0)
    # 수리 ⑥ 회귀 — 치수를 키운 배율이면 주파수는 **내려간다**(음수)
    chk("주파수 환산이 심은 값을 되돌린다", abs(it["delta_f_mhz"] + 100) < 1.0,
        f"{it['delta_f_mhz']:.2f}")
    chk("치수를 키우면 주파수가 내려간다(부호)", it["delta_f_mhz"] < 0,
        f"{it['delta_f_mhz']:.2f}")
    chk("보정량은 부호 없이 따로 낸다", abs(it["correction_mhz"] - 100) < 1.0,
        f"{it['correction_mhz']:.2f}")
    it_dn = interpret_scale(1 / k, f0, 1.524, 2.6, 14.0, 2.0)
    chk("치수를 줄이면 주파수가 올라간다", it_dn["delta_f_mhz"] > 0,
        f"{it_dn['delta_f_mhz']:.2f}")

    # ── 국부 튜닝과 구별 (셀 36 (b))
    loc = _mk()
    for i in (4, 5, 6):
        loc["patches"][i]["W_mm"] += 0.2
    d2 = revision_diff(_mk(), loc)
    chk("폭만 바뀌면 패턴 튜닝 계열", d2["verdict"] == "aperture-taper-tune", str(d2["verdict"]))
    chk("같으면 identical", revision_diff(_mk(), _mk())["verdict"] == "identical")
    chk("소자 수가 다르면 배율을 논하지 않는다",
        revision_diff(_mk(10), _mk(11))["verdict"] == "topology-change")

    # ── 수리 ① — 인덱스 짝이면 통째로 밀린다. xc 최근접이라야 잡는다
    miss = _mk(10)
    del miss["patches"][3]                       # 가운데 소자 하나가 빠졌다
    d3 = revision_diff(_mk(10), miss)
    chk("소자가 빠지면 topology-change 로 먼저 걸린다",
        d3["verdict"] == "topology-change", str(d3["verdict"]))
    shifted = _synth([{"x0": p["x0"] + 200, "x1": p["x1"] + 200,
                       "L_mm": p["L_mm"], "W_mm": p["W_mm"]} for p in _mk(10)["patches"]],
                     [18.0] * 9)
    d4 = revision_diff(_mk(10), shifted)
    chk("좌표가 통째로 어긋나면 짝짓지 않는다",
        d4["verdict"] == "pairing-ambiguous", str(d4["verdict"]))
    chk("짝짓기 실패를 사유와 함께 남긴다", "짝짓기 실패" in d4.get("why", ""))
    pr = pair_by_xc(_patches(_mk(10)), _patches(_mk(10, k=k)), 18.0)
    chk("정상 짝짓기는 전 소자를 잇는다", pr["ok"] and pr["n_paired"] == 10, str(pr["n_paired"]))

    # ── 수리 ③ — 균일성은 상대 피크투피크로 본다
    chk("상대 피크투피크가 실린다", "rel_spread" in d["ratio_L"])
    chk("균일 배율은 산포가 거의 0", d["ratio_L"]["rel_spread"] < 1e-12,
        str(d["ratio_L"]["rel_spread"]))
    chk("임계에 산지가 붙는다", "관측 근거가 없다" in numeric_rules()["산지"])

    # ── 수리 ④ — 폭이 없으면 Dk 환산을 하지 않는다
    it2 = interpret_scale(k, f0, 1.524, 2.6, None, None)
    chk("폭이 없으면 er 환산을 하지 않는다", it2["er_equivalent"] is None, str(it2))
    chk("하지 않은 이유를 밝힌다", "오염" in it2["why"])
    it3 = interpret_scale(k, f0, 1.524, 2.6, 14.0, None)
    chk("급전선 폭이 없으면 Dk 서명을 내지 않는다", it3["dk_signature_ppm"] is None)
    chk("패치 폭만 있으면 er 환산은 한다", it3["er_equivalent"] is not None)

    # ── 축퇴 — 배율만으로는 원인을 못 가른다
    dk = interpret_scale(k, f0, 1.524, 2.6, 14.0, 2.0)
    chk("Dk 서명이 0 이 아니다(패치·급전선 배율이 다르다)",
        abs(dk["dk_signature_ppm"]) > 1.0, str(dk["dk_signature_ppm"]))
    chk("두 원인을 모두 환산하고 고르지 않는다",
        dk["delta_f_mhz"] is not None and dk["er_equivalent"] is not None
        and "고르는 것은" in dk["why"])

    # ── 문서 단서 (셀 36 (c))
    cl = parse_claims("1차 설계 CST 시뮬레이션 대비 100MHz 시프트.\n"
                      "비고: CST에서 제공된 2.60 대신 Dk = 2.57 사용.")
    kinds = [c["kind"] for c in cl]
    chk("차수·시프트·Dk 쌍을 뽑는다", {"stage", "fshift", "dk_pair"} <= set(kinds), str(kinds))
    chk("dk_pair 가 dk 보다 먼저 맞는다", kinds.count("dk") == 0, str(kinds))

    # ── 수리 ⑤ — 기계 판독
    oh = order_hint(d, cl, f0, 1.524, 2.6, 14.0, 2.0)
    chk("기계 판독 형태로 낸다", {"order", "confidence", "basis"} <= set(oh))
    chk("앵커 없이는 순서를 정하지 않는다", oh["order"] == "unknown", str(oh["order"]))
    chk("문서 주장과 실측 배율을 대조한다",
        any(b["anchor"] == "claim_fshift" and b["match"] for b in oh["basis"]),
        str([b for b in oh["basis"] if b["anchor"].startswith("claim")]))
    chk("실측 앵커가 미구현임을 밝힌다",
        any(b["anchor"] == "measured" and "미구현" in b["why"] for b in oh["basis"]))

    oh2 = order_hint(d, cl, f0, 1.524, 2.6, 14.0, 2.0,
                     times={"A": "2025-05-21 18:00", "B": "2026-02-26 10:50",
                            "grade_A": "solver_log", "grade_B": "dwg_header"})
    chk("작업 시각 앵커가 순서를 정한다", oh2["order"] == "A_first", str(oh2["order"]))
    chk("실측끼리면 신뢰가 높다", oh2["confidence"] >= 0.8, str(oh2["confidence"]))
    oh3 = order_hint(d, cl, f0, 1.524, 2.6, 14.0, 2.0,
                     times={"A": "2026-01-01", "B": "2025-01-01",
                            "grade_A": "declared", "grade_B": "declared"})
    chk("선언끼리면 신뢰가 낮다", oh3["confidence"] <= 0.5, str(oh3["confidence"]))
    chk("mtime 은 앵커가 아니다", "mtime" not in str(ANCHORS))

    # ── 수리 ④ 후속 — 기판은 **역할 어휘로** 가져온다(원천 JSON 구조를 뒤지지 않는다)
    base = C.data_dir() / "work"
    if (base / "L1-test2" / "값_카탈로그.json").exists():
        sub = _sub_of(C.work_dir("L1-test2", create=False))
        chk("기판 두께·유전율·대역을 역할로 얻는다",
            sub["h_mm"] == 0.127 and sub["er"] == 3.07 and sub["f0_ghz"] == 60.0, str(sub))
        chk("도체(εr=1)를 기판으로 고르지 않는다", sub["er"] >= DIELECTRIC_ER_MIN)
        chk("산지를 밝힌다", "역할 조회" in sub["산지"])
    if (base / "L1-Antenna_CAD_ECO" / "값_카탈로그.json").exists():
        sub2 = _sub_of(C.work_dir("L1-Antenna_CAD_ECO", create=False))
        # ★ 이 시험의 뜻이 바뀌었다(08-03-4). 예전에는 "도면만 있으면 기판이 비어야 한다"
        #   였지만, 이제 사람이 `declare.py` 로 재질을 선언할 수 있다. **선언된 값은
        #   지어낸 값이 아니다** — 비어 있어야 한다고 우기면 사람 선언 경로를 막게 된다.
        #   지켜야 하는 것은 "비어 있음"이 아니라 **"산지가 있음"**이다.
        if sub2["er"] is None:
            chk("도면만 있고 선언도 없으면 기판을 비운다(지어내지 않는다)",
                sub2["h_mm"] is None, str(sub2))
            chk("비운 이유를 남긴다",
                "없다" in sub2["er_why"] or "정하지 않는다" in sub2["er_why"])
        else:
            chk("선언으로 들어온 유전율은 유전체 범위 안이다",
                sub2["er"] >= DIELECTRIC_ER_MIN, str(sub2["er"]))
            chk("어디서 왔는지 밝힌다 — 값이 있으면 산지가 있어야 한다",
                "역할 조회" in sub2["산지"], sub2["산지"])
        chk("있는 것(패치 폭)은 가져온다", sub2["w_patch_mm"] is not None)

    # ── 실물 회귀 — 같은 도안을 스스로 비교하면 identical
    w = C.work_dir("L1-Antenna_CAD_ECO", create=False)
    arr = _array_of(w) if (w / "추출_결과.json").exists() else None
    if arr:
        chk("실물: 자기 자신과 비교하면 identical",
            revision_diff(arr, arr)["verdict"] == "identical")
        import copy as _cp
        b = _cp.deepcopy(arr)
        for q in b["patches"]:
            q["L_mm"] *= k; q["x1"] = q["x0"] + q["L_mm"]
        dr = revision_diff(arr, b)
        chk(f"실물 {arr['n_patches']}소자에 심은 배율을 되찾는다",
            dr["verdict"] == "uniform-scale" and abs(dr["ratio_L"]["mean"] - k) < 1e-9,
            str(dr.get("verdict")))

    print(f"\n결과: {ok}/{ok + fail} PASS")
    return 0 if fail == 0 else 1


def main(argv):
    if len(argv) < 2:
        print(__doc__); return 2
    if argv[1] == "self-test":
        return self_test()
    if argv[1] == "compare":
        r = compare(argv[2], argv[3], " ".join(argv[4:]))
        d = r.get("diff") or {}
        print(f"{r['a']}  →  {r['b']}")
        print(f"  판정 {d.get('verdict')} — {d.get('why', '')}")
        if d.get("ratio_L"):
            print(f"  길이 배율 {d['ratio_L']['mean']:.6f} "
                  f"(상대 산포 {d['ratio_L']['rel_spread']:.2e})")
        for l in (r.get("order") or {}).get("lines", []):
            print("  " + l)
        o = r.get("order") or {}
        print(f"  순서 {o.get('order')} (신뢰 {o.get('confidence')}) — {o.get('why', '')}")
        for b in o.get("basis", []):
            print(f"    [{b['anchor']}] {b.get('says', '')} {b.get('why', '')}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
