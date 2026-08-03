#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scripts/ref_compose.py — **기준 보고서**를 만든다 (호스트 LLM 역할을 사람이 대신)

무엇을 하나
    `document_brief` 가 준 골격의 `<키>` 를 카탈로그 키로 채우고 `PROSE` 마커 사이에
    서술을 넣는다. 즉 **낮은 모델이 할 일을 그대로** 한다 — 다만 손으로 정확히 한다.

왜 스크립트로 하나
    이 산출물은 다른 모델과 견줄 **기준선**이다. 기준선이 손 오타로 흔들리면 비교가
    무의미해진다. 키 배정을 표로 두고 기계가 채운다 — 사람이 정하는 것은 **어느 키를
    어느 자리에** 라는 판단뿐이고, 그것이 원래 LLM 의 일이다.

    서술도 여기 문자열로 둔다. 규율은 하나 — **숫자를 쓰지 않는다.** 값은 위 표에 있다.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "mcp_server"))
sys.path.insert(0, str(_REPO / "tools"))

RUN = "REF2-Antenna_CAD_ECO"

# ── 키 배정 — 역할마다 **나타나는 순서대로** 쓴다 ────────────────────────────
# 같은 역할이 여러 번 나오는 자리(두 도면의 레이어 수 등)는 목록 순서가 곧 배정이다.
KEYS: dict[str, list[str]] = {
    # 요약
    "product_label": ["제품.이름"],
    "product_use": ["제품.용도"],
    "f0_target_ghz": ["제품.타겟주파수"],
    "식별.판정": ["식별.판정"],
    # 요약 + 형상 둘 다 — 요약이 reuse 라 두 번 나온다
    "scale_max_edge_mm": ["해석.규모_타당성.최대_변", "해석.규모_타당성.최대_변"],
    # 요약(reuse) + 계산 근거 둘 다에 나온다
    "gain_max_dbi": ["해석.이득_추정.개구_능률_100_상한"] * 2,
    "aperture_hpbw_deg": ["해석.개구빔폭.방위면_HBW"] * 2,
    # 형상
    "array_pitch_over_lambda": ["해석.배열주기.파장_대비pitchλ"],
    "pitch_uniformity": ["해석.배열주기.균일도산포평균"],
    "n_layers": ["추출.형상.Bottom_20260227.dxf.레이어수",
                 "추출.형상.Top_20260227.dxf.레이어수"],
    "n_polyline": ["추출.형상.Bottom_20260227.dxf.엔티티.polyline",
                   "추출.형상.Top_20260227.dxf.엔티티.polyline"],
    "n_circle": ["추출.형상.Bottom_20260227.dxf.엔티티.circle",
                 "추출.형상.Top_20260227.dxf.엔티티.circle"],
    "bbox_x_mm": ["추출.형상.Bottom_20260227.dxf.bbox_x",
                  "추출.형상.Top_20260227.dxf.bbox_x"],
    "bbox_y_mm": ["추출.형상.Bottom_20260227.dxf.bbox_y",
                  "추출.형상.Top_20260227.dxf.bbox_y"],
    "elevation_mm": ["추출.형상.Bottom_20260227.dxf.elevation",
                     "추출.형상.Top_20260227.dxf.elevation"],
    # 스택업 · 재질
    "material_name": ["재질.기판명"],
    "material_er": ["재질.유전율_선언"],
    "material_er_ref_ghz": ["재질.유전율_기준주파수"],
    "substrate_h_declared_mm": ["재질.substrate.h_mm"],
    "substrate_h_mm": ["해석.스택업_타당성.기판_두께"],
    "material_layer_count": ["재질.stackup.layer_count"],
    "copper_weight_oz": ["재질.stackup.copper_oz"],
    "surface_finish": ["재질.stackup.surface_finish"],
    "reflector_material": ["재질.reflector.material"],
    "reflector_thickness_mm": ["재질.reflector.thickness_mm"],
    "reflector_finish": ["재질.reflector.finish"],
    # 계산 근거 · 부록
    "wavelength_mm": ["해석.배열인자.N30.lambda_mm"],
    "resonance_free_min_ghz": ["해석.공진정합.자유공간_λ2_최장_패치"],
    "resonance_free_max_ghz": ["해석.공진정합.자유공간_λ2_최단_패치"],
    "resonance_substrate_min_ghz": ["해석.공진정합.기판_λg2_최장_패치"],
    "resonance_substrate_max_ghz": ["해석.공진정합.기판_λg2_최단_패치"],
    # 그림 — 골격이 자리를 정하고 여기서 키만 고른다
    "figure_3d_iso": ["그림.Top_20260227.3d_iso"],
    "figure_2d_overview": ["그림.Top_20260227.overview"],
    "figure_2d_detail": ["그림.Top_20260227.detail1", "그림.Top_20260227.detail2",
                         "그림.Top_20260227.detail3", "그림.Top_20260227.detail4"],
    "figure_3d_top": ["그림.Top_20260227.3d_top"],
    "figure_dwg_preview": ["그림.antenna reflector.preview",
                           "그림.holes_drill_20260227.preview"],
    "figure_stackup": ["그림.스택업"],
    "figure_array_factor": ["그림.배열인자"],
    "af_hpbw_deg": ["해석.배열인자.-3_dB_전폭"],
    "af_sll_db": ["해석.배열인자.최대_사이드로브_레벨"],
    "af_sll_angle_deg": ["해석.배열인자.N30.sll_angle_deg"],
    "af_grating_deg": ["해석.배열인자.격자로브_발생_각"],
    # 특이 표기 · 실행 메타
    "추출.특이표기수": ["추출.특이표기수"],
    "식별.원천명": ["식별.원천명", "식별.원천명"],   # 제목 줄 + 실행 메타
    "식별.원천종류": ["식별.원천종류"],
    "식별.주도레인": ["식별.주도레인"],
    "식별.기여레인": ["식별.기여레인"],
    "식별.파일수": ["식별.파일수"],
    "식별.판독불가수": ["식별.판독불가수"],
    "run.run_id": ["run.run_id"],
    "run.rule_version": ["run.rule_version"],
    "run.registry_version": ["run.registry_version"],
    "run.product": ["run.product"],
    "추출.geom_hash": ["추출.geom_hash"],
    "verify.n_checks": ["해석.대조건수"],
    "verify.verdict": ["해석.판정"],
}

# ── 서술 — **숫자를 쓰지 않는다.** 값은 표가 말한다 ──────────────────────────
PROSE: dict[str, str] = {
    "요약.한줄_요약": (
        "선박 항해용 X 대역 레이더에 쓰는 안테나의 도면 묶음이다. "
        "앞면과 뒷면 도면이 한 쌍으로 들어와 있고, 앞면 도면에 패치가 한 줄로 늘어선 "
        "선형 배열 형상이 담겨 있다. "
        "반사판은 별도 도면으로 있으나 본문이 압축되어 형상을 읽지 못했다."),
    "요약.지금_상태": (
        "형상과 재질 선언은 서 있고, 성능은 아직 하나도 없다. "
        "시뮬레이션 결과와 측정 성적이 둘 다 반입되지 않아 요구 대조가 성립하지 않는다. "
        "무엇을 하면 채워지는지는 마지막 대장에 절별로 적혀 있다."),
    "요구명세.요구의_출처": (
        "전부 제품 담당이 선언한 값이고 원천 파일에서 나온 것이 아니다. "
        "이득과 수직 빔폭은 하한만, 반사 손실·부엽·수평 빔폭은 상한만 걸려 있다 — "
        "한쪽만 걸린 요구이지 나머지가 미정인 것이 아니다. "
        "적용 축이 비어 있는 항목은 방향과 무관한 요구라는 뜻이다."),
    "형상.배열_구성": (
        "앞면 도면의 폴리라인이 한 줄로 늘어선 선형 배열이고, 뒷면은 외곽선과 구멍만 담겨 있다. "
        "배열 주기가 파장에 견주어 큰 편이라 격자로브가 계산에 나타난다. "
        "주기 산포가 작지 않은데, 이것이 의도한 테이퍼인지 도면 판독의 흔들림인지는 "
        "여기서 판정하지 않는다 — 설계자가 확인할 일이다."),
    "시제품.실물_소견": (
        "시제품 사진은 반입되지 않았다. 아래 그림은 전부 도면에서 그린 것이고 "
        "실물을 찍은 것이 아니므로, 만들어진 물건이 도면과 같은지는 이 문서로 확인할 수 없다."),
    "시제품.도면_소견": (
        "상세 시트가 여러 장인 것은 전체를 원척으로 담을 수 없어 가로로 나눠 그렸기 "
        "때문이다 — 시트마다 겹치는 구간이 있어 이어 볼 수 있다. "
        "반사판과 구멍 도면은 프리뷰만 실렸다. 본문이 압축된 형식이라 형상을 읽지 못했고, "
        "변환기가 반입되면 이 둘도 잴 수 있는 도면이 된다."),
    "계산근거.계산의_한계": (
        "이 절의 값은 형상만으로 나온 것이다 — 소자마다 같은 세기로 여자된다고 보고, "
        "소자 하나의 방사 패턴은 곱하지 않았으며, 개구 능률은 최대로 잡았다. "
        "그래서 여기 이득과 빔폭은 **달성값이 아니라 상한과 근사**다. "
        "시뮬레이션 결과나 측정값과 같은 것으로 읽으면 안 된다 — 배열인자는 빔패턴이 아니다."),
    "형상.이_형상이_뜻하는_것": (
        "앞면 도면이 폴리라인 다수를 담고 뒷면은 외곽선과 구멍 하나만 담는다 — "
        "앞면이 방사 소자를 그린 신호층이고 뒷면이 접지면이라는 뜻으로 읽힌다. "
        "두 도면의 가로 폭이 비슷하고 뒷면이 조금 더 큰 것은 접지면이 소자 배열을 "
        "감싸는 흔한 배치와 맞는다. 뒷면 도면의 층 높이가 음수로 들어와 있는데, "
        "이것이 기판 두께만큼 아래에 놓였다는 뜻이라면 그 값이 곧 기판 두께다 — "
        "다만 도면이 그렇게 말하지는 않으므로 스택업 절의 선언값과 함께 읽어야 한다. "
        "가로가 세로보다 훨씬 긴 형상이므로 빔은 가로 방향으로 좁고 세로 방향으로 넓다."),
    "형상.대칭면_소견": (
        "이 원천은 도면 레인만 있어 대칭면 선언이 없고, 따라서 짝 맞춤을 대조할 상대가 없다. "
        "대칭 여부는 형상만 보고 단정하지 않는다."),
    "스택업.스택업_소견": (
        "기판 두께가 두 갈래로 들어와 있다 — 하나는 제품 선언이고 하나는 뒷면 도면의 "
        "층 높이에서 나온 값이다. 둘이 어긋나므로 어느 쪽이 실물인지는 사람이 확정해야 한다. "
        "도체는 앞면과 뒷면 두 층이고 외층 동박은 선언으로 들어왔다."),
    "스택업.재질이_뜻하는_것": (
        "유전율이 낮은 기판은 매질 안의 파장을 덜 줄인다 — 같은 주파수에서 소자가 "
        "더 커지지만 방사 효율과 대역폭에는 유리한 쪽이다. 이 기판은 흔히 쓰는 유리섬유 "
        "계열보다 유전율이 낮은 축이고, 그 선택은 레이더 대역에서 손실을 줄이려는 방향과 "
        "맞는다. 기판이 두꺼울수록 대역폭은 넓어지지만 표면파 손실이 커지는 맞바꿈이 "
        "있으며, 어느 쪽을 택했는지는 두께 선언이 말해 준다. "
        "표면 처리는 무전해 니켈 위에 얇은 금을 입히는 공정으로, 평탄도와 보관성을 "
        "얻는 대신 니켈 층의 손실을 감수하는 선택이다 — 다만 그 선택의 근거가 "
        "기록에 남아 있지는 않다."),
    "스택업.유전율_기준": (
        "유전율에 측정 기준 주파수가 함께 선언되어 있고, 타겟 주파수와 크게 벌어지지 않아 "
        "그대로 쓴다. 도구는 이 값을 타겟 주파수로 다시 계산하지 않는다 — "
        "설계자가 이미 한 계산과 어긋나면 정본이 둘이 되기 때문이다. "
        "재계산한 값을 쓰려면 그 값이 제품 선언으로 들어와야 한다."),
    "스택업.반사판_소견": (
        "반사판은 안테나 기판과 다른 물건이다 — 금속판이고 별도 도면으로 관리된다. "
        "표면 처리는 시제품 단계라 아직 없고 양산 시 계획만 기록되어 있다. "
        "해수 환경에서의 장기 내식성은 확인되지 않았다."),
    "계산근거.무엇을_말해주나": (
        "개구 크기에서 낸 빔폭과 배열인자에서 낸 빔폭이 서로 가깝다 — 두 계산이 같은 형상을 "
        "다른 길로 본 것이므로, 가깝다는 사실 자체가 도면 판독이 크게 어긋나지 않았다는 "
        "약한 근거가 된다. 격자로브 각이 나왔다는 것은 배열 주기가 파장에 견주어 "
        "충분히 작지 않다는 뜻이다 — 주기가 파장에 가까워질수록 격자로브는 정면 쪽으로 "
        "들어오고, 멀어질수록 시야 밖으로 밀려난다. 부엽 레벨이 요구보다 훨씬 높게 나오는 "
        "것은 균일 여자를 가정했기 때문이며, 실제 설계는 여기에 테이퍼를 준다."),
    "계산근거.요구와의_거리": (
        "이득은 개구 능률을 최대로 잡은 상한이므로 요구를 넘어 보이는 것이 당연하고, "
        "실제 이득은 반드시 이보다 낮다 — 만족한다고 읽으면 안 된다. "
        "수평 빔폭은 요구와 같은 방향(좁다)이지만 이 값도 소자 패턴을 곱하지 않은 것이다. "
        "진짜 판정은 시뮬레이션 결과나 측정 성적이 들어와 요구 대조 절이 설 때 나온다."),
    "부록계산.참고값_읽는_법": (
        "이 절의 값은 동작 주파수가 아니다 — 패치 길이 하나에서 역산한 반파장 공진이고 "
        "요구 대조에 쓰지 않는다. 기판 기준과 자유공간 기준이 갈리는 것은 유전율이 "
        "매질 안의 파장을 줄이기 때문이며, 같은 길이라도 기판 위에서는 더 낮은 주파수에 "
        "공진한다. 어느 쪽이 옳다고 정하지 않는다 — 실제 패치는 기판 위에 있으나 "
        "가장자리 효과와 급전 구조가 빠져 있어 둘 다 근사다."),
    "특이표기.표기_소견": (
        "이 도면에서는 특이 표기가 관측되지 않았다. "
        "표기가 있었더라도 의미는 도구가 판정하지 않고 도면 담당이 확정한다."),
    "업데이트필요.다음_할_일": (
        "먼저 닫아야 하는 것은 시뮬레이션 결과다 — 그것이 들어와야 요구 대조 절이 서고, "
        "대조가 서야 이 문서가 판정을 담은 보고서가 된다. "
        "시험 결과는 그 다음이고, 둘 중 하나만으로도 대조는 시작된다. "
        "반사판 도면은 변환기가 들어오면 형상까지 읽혀 시제품 절이 함께 채워진다. "
        "이번 대장에 말로 채울 수 있는 항목은 남아 있지 않다 — 재질과 요구는 이미 선언되었다."),
}


def _fill_all(doc: str) -> tuple[str, list[str], list[str]]:
    """골격의 `<키>` 를 채운다.

    ★ 한 줄에 참조가 셋이다 — `{{l:}}` 이름 · `{{v:}}` 값 · `{{s:}}` 출처. **셋이 같은 키**를
      가리켜야 한다. 참조마다 다음 키를 꺼내면 이름·값·출처가 서로 다른 항목을 가리키게
      되고, 역할은 전부 맞으므로 게이트가 잡지 못한다 — 행렬의 행 짝 문제와 같은 부류다.
      그래서 **줄이 시작될 때만**(`l:`) 새 키를 꺼내고 나머지는 그것을 쓴다.
    """
    pool = {k: list(v) for k, v in KEYS.items()}
    cur: dict[str, str] = {}
    missing: list[str] = []

    def take(role):
        q = pool.get(role)
        if not q:
            missing.append(role)
            return None
        cur[role] = q.pop(0)
        return cur[role]

    def sub(m):
        sig, role = m.group(1), m.group(2)
        # `l` 은 표의 줄머리, `g` 는 그림 한 장 — 둘 다 **새 항목이 시작되는 자리**다.
        # `g` 를 빼먹으면 그림 네 장이 전부 같은 파일을 가리킨다(실제로 그랬다).
        key = take(role) if (sig in ("l", "g") or role not in cur) else cur[role]
        return m.group(0) if key is None else "{{%s:%s|%s}}" % (sig, key, role)

    out = re.sub(r"\{\{([a-z]):<키>\|([^}|]+)\}\}", sub, doc)
    extra = [f"{r}×{len(q)}" for r, q in pool.items() if q]
    return out, missing, extra


def main() -> int:
    import api as A
    b = A.document_brief(RUN)
    if not b.get("ok"):
        print(f"brief 실패: {b.get('why')}"); return 1
    if b["stale"]["stale"]:
        print(f"낡은 run — 다시 돌린다: {b['stale']['changed']}"); return 1

    doc, missing, extra = _fill_all(b["skeleton"])
    if missing:
        print(f"키 배정이 없는 역할: {sorted(set(missing))}"); return 1
    if extra:
        print(f"쓰이지 않은 배정(자리보다 많다): {extra}"); return 1

    # ② 서술 채우기 — 마커 **사이만** 바꾼다
    for slot, text in PROSE.items():
        pat = re.compile(
            r"(<!--\s*PROSE:" + re.escape(slot) + r"[^>]*-->\n)(.*?)(\n<!-- /PROSE:"
            + re.escape(slot) + r" -->)", re.S)
        if not pat.search(doc):
            print(f"슬롯이 골격에 없다: {slot}"); return 1
        doc = pat.sub(lambda m: m.group(1) + text + m.group(3), doc)

    left = re.findall(r"<!--\s*PROSE:([^\s]+)[^>]*-->\n\(여기서부터 작성\)", doc)
    if left:
        print(f"안 채운 슬롯: {left}"); return 1

    r = A.submit_document(RUN, doc)
    print(f"게이트 {'통과' if r['pass'] else '반려'} · 시도 {r['attempt']} · "
          f"참조 {r['n_refs']} · 위반 {r['n_violations']}")
    for v in r["violations"][:12]:
        print(f"  {v['kind']:20} {v.get('location', '')} {str(v.get('value') or v.get('key') or v.get('got') or '')[:60]}")
    if r["pass"]:
        out = _REPO / "out" / "기준보고서_치환.md"
        out.write_text(r["substituted"], encoding="utf-8")
        print(f"치환본: {out}")
    return 0 if r["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
