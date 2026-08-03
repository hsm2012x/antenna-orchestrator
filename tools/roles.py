#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/roles.py — 물리량 역할 어휘 (LLM 0콜)

하나의 어휘가 세 곳을 동시에 규율한다.

    role  =  자산 DB 컬럼  =  문서 골격 슬롯  =  게이트 타입

왜 이것으로 키 오배치가 닫히나
    격자로브 각도(deg)를 반전력 빔폭(deg) 자리에 넣는 것은 **단위로는** 구별되지 않는다.
    구별되는 것은 **역할**이다. 안테나는 3차원 형상이고 그 형상에서 나오는 양의 종류는
    유한하다 — 실물 3종 전 run 관측 결과 (단위, 의미) 조합은 **41종**이었다.
    41은 손으로 셀 수 있는 수다. 그래서 역할을 어휘로 못박고 타입 검사로 만든다.

    게이트가 검사하는 것은 "선언된 역할 == 카탈로그의 역할"이다. 이것이 실효를 가지려면
    **역할 선언이 키와 독립된 출처에서 와야 한다** — 그래서 역할은 결정론으로 만든
    문서 골격의 슬롯에서 오고, 키는 프리즘이 고른다. 두 진술이 어긋나면 오배치다.

    닫히지 않는 나머지 — 골격 밖 자유 서술. 거기서는 역할도 프리즘이 쓰므로 두 진술이
    독립이 아니다. 그래서 수치는 되도록 골격 안에 두게 한다(문서 골격 생성기 참조).

미매핑 규율
    어휘에 없는 항목은 role=None + role_unmapped=True 로 남긴다. 그럴듯한 역할을
    지어내지 않는다 — "임계 미지정"과 같은 규율이다(I-5 · N-3). 사람이 등재한다.

CLI
    python tools/roles.py list                역할 어휘 전체
    python tools/roles.py scan                work/ 전 run 을 훑어 미매핑 항목 보고
    python tools/roles.py self-test
"""
from __future__ import annotations

import glob
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# ── 물리량(quantity) — 단위에서 결정론으로 나온다. (b) 외부 포맷 규약 성격 ──────
UNIT_QUANTITY = {
    "mm": "length", "GHz": "frequency", "deg": "angle", "dB": "level_db",
    "dBi": "gain_dbi", "εr": "permittivity", "Ω": "impedance",
    "비율": "ratio", "배": "ratio", "개": "count", "면": "count", "묶음": "count",
    "유무": "presence", "": "unitless", None: "unitless",
}

# ── 역할(role) — (정규식, role, 설명) ────────────────────────────────────────
# 순서가 뜻을 가진다: 위에서부터 먼저 맞는 것을 쓴다(구체 → 일반).
# 정규식은 `check` 문자열에서 **원천명 조각을 뗀 core** 에 맞춘다.
ROLE_RULES: tuple[tuple[str, str, str], ...] = (
    # 수리(결함 23): 패치 길이의 최단/최장을 **주파수** min/max 로 그대로 매핑해 축이 뒤집혔다.
    #   f = c / (2·L) 이므로 **짧은 패치가 높은 주파수**다. 자산 DB 에서 min(16.83) > max(15.68)
    #   이 나와 드러났다 — 한 안테나만 보면 둘 다 그럴듯한 숫자라 보이지 않는다.
    (r"공진정합.*기판.*최단",        "resonance_substrate_max_ghz", "기판 λg/2 공진 상한 — 최단 패치(짧을수록 높다)"),
    (r"공진정합.*기판.*최장",        "resonance_substrate_min_ghz", "기판 λg/2 공진 하한 — 최장 패치"),
    (r"공진정합.*자유공간.*최단",     "resonance_free_max_ghz",      "자유공간 λ/2 공진 상한 — 최단 패치(짧을수록 높다)"),
    (r"공진정합.*자유공간.*최장",     "resonance_free_min_ghz",      "자유공간 λ/2 공진 하한 — 최장 패치"),
    (r"선언 ?주파수 ?대역 ?하한",     "band_lo_ghz",                 "선언 동작 대역 하한"),
    (r"선언 ?주파수 ?대역 ?상한",     "band_hi_ghz",                 "선언 동작 대역 상한"),
    (r"원거리장.*최저",             "farfield_freq_min_ghz",       "원거리장 요청 주파수 최저"),
    (r"원거리장.*최고",             "farfield_freq_max_ghz",       "원거리장 요청 주파수 최고"),
    (r"파라미터 ?정합.*공진",        "param_resonance_ghz",         "설계 파라미터에서 역산한 공진"),

    (r"배열인자.*사이드로브",         "af_sll_db",                   "배열인자 최대 사이드로브 레벨"),
    (r"배열인자.*전폭",             "af_hpbw_deg",                 "배열인자 −3 dB 전폭"),
    (r"배열인자.*격자로브",          "af_grating_deg",              "격자로브 발생 각"),
    (r"개구 ?빔폭",                "aperture_hpbw_deg",           "개구 크기 기반 반전력 빔폭"),
    (r"이득 ?추정",                "gain_max_dbi",                "개구 능률 100 % 이득 상한"),

    (r"급전 ?포트 ?폭",             "port_width_mm",               "급전 포트 폭(Yrange)"),
    (r"급전 ?포트 ?높이",           "port_height_mm",              "급전 포트 높이(Zrange)"),
    (r"포트 ?임피던스",             "port_impedance_ohm",          "포트 임피던스"),
    (r"포트 ?수",                  "n_ports",                     "포트 수(이력 재생 후)"),

    (r"스택업 ?선언.*t_cond",      "t_cond_mm",                   "도체 두께 선언값"),
    (r"스택업 ?선언.*t_sub",       "t_sub_mm",                    "기판 두께 선언값"),
    (r"스택업 ?선언.*기판 ?두께",    "substrate_h_declared_mm",     "임포트 레이어 기준 기판 두께"),
    (r"스택업 ?타당성",             "substrate_h_mm",              "기판 두께(스택업 타당성)"),
    (r"재질 ?유전율",              "material_er",                 "재질 유전율 선언값"),

    (r"배열 ?격자.*파장 ?대비",      "array_pitch_over_lambda",     "배열 주기 ÷ 파장"),
    (r"배열주기.*파장 ?대비",        "array_pitch_over_lambda",     "배열 주기 ÷ 파장"),
    (r"배열 ?격자.*간격",           "array_pitch_mm",              "배열 최소 격자 간격"),
    (r"배열주기.*균일도",           "pitch_uniformity",            "배열 주기 산포 ÷ 평균"),
    (r"배열 ?편성",                "array_grouping",              "배열 편성(칩×채널)"),
    (r"소자 ?수.*변환",            "n_elements_transform",        "소자 수 — 변환 선언 기준"),
    (r"소자 ?수.*최종",            "n_elements_solid",            "소자 수 — 최종 솔리드 기준"),
    (r"소자 ?수",                  "n_elements",                  "소자 수"),

    (r"대칭면 ?선언 ?수",           "n_symmetry_planes",           "선언된 대칭면 수"),
    (r"대칭면.*정합",              "symmetry_unpaired_count",     "대칭면 기준 짝 없는 소자 수"),

    (r"규모 ?타당성",              "scale_max_edge_mm",           "규모 타당성 — 최대 변"),
    (r"형상 ?범위",                "geometry_max_edge_mm",        "형상 범위 — 최대 변"),
    (r"모니터 ?서브볼륨",           "monitor_subvolume_max_mm",    "모니터 서브볼륨 최대 변"),
    (r"모델 ?상자",                "model_box_max_mm",            "모델 상자 최대 변"),
    (r"변환 ?좌표 ?범위",           "transform_span_ratio",        "변환 좌표 범위 ÷ 임포트 형상 폭"),

    (r"성능 ?데이터 ?보유",         "performance_data_present",    "s2p·원거리장 등 성능 데이터 유무"),
)

# ── 규칙 없이 **도구가 명시 지정**하는 역할 ──────────────────────────────────
# `check` 문자열에서 파생되지 않는 값들이 있다(해석의 array_factors[] 처럼 구조화된 산출).
# 그 값에도 역할이 있어야 하고, 역할은 어휘의 일부여야 한다 — 어휘 밖 이름을 도구가 쓰면
# 문서 양식(docspec)이 그 이름을 검증할 수 없다. 수리: catalog 가 쓰던 두 이름이 어휘에
# 없어 document_spec.yaml 검증이 걸렸다.
EXTRA_ROLES: dict[str, str] = {
    "af_sll_angle_deg": "최대 사이드로브가 나타나는 각",
    "wavelength_mm":    "계산 기준 파장(대역 중심)",
    "verify.n_checks":  "대조 항목 수 — 실행 메타",
    "verify.verdict":   "해석 종합 판정 — 실행 메타",
    # 원천 분해(discover.py)가 정하는 자산 단위와 연결
    "entry.id":             "자산 entry 식별자",
    "entry.kind":           "entry 종류(cst_project · cad_group)",
    "entry.project_tag":    "프로젝트 태그 — 배포 도안과 원본을 잇는다",
    "entry.derived_from":   "파생 원본 프로젝트(선언 근거로 확정)",
    "entry.link_candidate": "연결 후보 — 확정은 사람(A-1)",
    # 도면 레인의 기하 지문 — **자산 간 연결의 재료**다.
    # 수리: 이 값들이 역할 없이 파일명 기반 키로만 실려, 배포 도안과 원본 프로젝트를
    # 값으로 대조할 수 없었다(같은 도면인데 키가 달라 매칭이 0건). 어휘에 올린다.
    "n_layers":        "도면 레이어 수",
    "n_polyline":      "폴리라인 수",
    "n_circle":        "원 엔티티 수",
    "bbox_x_mm":       "도면 bbox 가로",
    "bbox_y_mm":       "도면 bbox 세로",
    "elevation_mm":    "도면 elevation(층 높이)",
    # 시각 근거(figures.py) — 그림도 카탈로그 항목이므로 역할이 있어야 한다.
    # 역할이 종류를 가르고, 종류가 **치수 정본 여부**를 정한다(캡션의 근거).
    "figure_2d_overview": "2D 전체도 — 치수 정본",
    "figure_2d_detail":   "2D 상세도 — 치수 정본",
    "figure_3d_iso":      "3D 사시 preview — 치수 정본 아님",
    "figure_3d_top":      "3D 평면 preview — 치수 정본 아님",
    "figure_dwg_preview": "DWG 내장 프리뷰 — 형상 판독 불가 파일의 유일한 시각 근거",
    # 아직 산출이 없는 그림도 어휘에 올린다 — 어휘에 없으면 문서 골격이 그 자리를
    # 만들지 못하고, 자리가 없으면 **없다는 사실이 문서에 나타나지 않는다**(I-5).
    "figure_photo_bare":     "실물 사진 — 베어 보드(사람 반입 · T-3)",
    "figure_photo_assembly": "실물 사진 — 반사판 포함 조립체(사람 반입 · T-3)",
    "figure_photo_test":     "실물 사진 — 측정 장면(사람 반입 · T-3)",
    "figure_pattern_polar":  "방사 패턴 — 절단면(2D). 자세한 분석용",
    "figure_pattern_3d":     "방사 패턴 — 3D. 빔폭 판독용",
    # ★ 배열인자는 **빔패턴이 아니다**(I-L). 역할 이름을 갈라 두어야 그림 캡션과 문서
    #   배치에서 둘이 섞이지 않는다 — 이름을 바꿔 없는 것을 있는 것처럼 만들지 않는다.
    "figure_array_factor":   "배열인자 곡선 — 소자 패턴 미포함. 빔패턴이 아니다",
    "figure_stackup":        "스택업 단면도 — 층 구성과 재질을 그림으로",

    # ── 요구 명세 — registry/products.yaml requirements 에서 온다 ─────────────
    # 산지 (e) 사람 선언. **하한과 상한은 다른 값이다** — 한 역할로 뭉치면
    # "> 23 dBi" 와 "< -10 dB" 가 같은 칸에 들어가 방향을 잃는다.
    "req_min":   "요구 하한 — 이 값 이상이어야 한다",
    "req_max":   "요구 상한 — 이 값 이하여야 한다",
    "req_axis":  "요구가 걸리는 축(수평 방위 · 수직 고각 등)",
    "req_basis": "요구의 근거 — 누가 언제 정했나",

    # ── 성능 측점 — (레인, 주파수)마다 한 행 ────────────────────────────────
    # 레인(시뮬/시험)은 **절이 정한다** — 역할을 레인마다 쪼개면 어휘가 두 배가 되고,
    # 같은 물리량이 두 이름을 갖는다(자산 DB 에서 나란히 세울 수 없게 된다).
    "freq_point_ghz": "성능 측점 주파수(GHz)",
    "freq_point_mhz": "성능 측점 주파수(MHz) — 환산하지 않는다(N-1)",
    "perf_gain_dbi":        "측점 이득",
    "perf_return_loss_db":  "측점 반사 손실",
    "perf_sll_db":          "측점 부엽 레벨",
    "perf_hbw_deg":         "측점 수평(방위) 빔폭",
    "perf_vbw_deg":         "측점 수직(고각) 빔폭",

    # ── 요구 대조 — 요구·시뮬·시험을 한 줄에 세운다(보고서의 척추) ───────────
    "cmp_required": "대조 — 요구값",
    "cmp_sim":      "대조 — 시뮬레이션값",
    "cmp_test":     "대조 — 시험 측정값",
    "cmp_verdict":  "대조 판정 — 임계나 값이 없으면 판정하지 않는다(I-5)",

    # ── 재질 상세 ───────────────────────────────────────────────────────────
    "material_name":         "기판 재질 품명(제조사 표기 그대로)",
    "material_er_ref_ghz":   "유전율이 측정된 기준 주파수 — 없으면 판정하지 않는다(D-34)",
    "material_layer_count":  "도체 층 수",
    "copper_weight_oz":      "외층 동박 두께(oz)",
    "surface_finish":        "표면 처리",
    "reflector_material":    "반사판 재질",
    "reflector_thickness_mm": "반사판 두께",
    "reflector_finish":      "반사판 표면 처리",

    # ── 공급사 출하 성적 — 항목마다 규격 대 실측 ────────────────────────────
    # 판정은 **공급사가 한 것**을 옮길 뿐이다. 우리가 다시 판정하지 않는다(A-1).
    "qc_spec_min":      "성적 항목 규격 하한",
    "qc_measured_mean": "성적 항목 실측 평균",
    "qc_measured_range": "성적 항목 실측 범위",
    "qc_standard":      "적용 규격 문서(IPC 등)",
    "qc_verdict":       "성적 항목 판정 — 공급사 선언",
    "qc_lot_verdict":   "출하 최종 판정 — 공급사 선언",

    # ── 판본 비교 — tools/revision.py 산출 ──────────────────────────────────
    "rev_verdict":        "판본 관계 판정(identical · uniform-scale · …)",
    "rev_scale_ratio":    "치수 배율(패치 길이 기준)",
    "rev_freq_shift_mhz": "배율에서 나오는 주파수 시프트",
    "rev_dk_signature_ppm": "급전선 폭 대비 패치 길이 변화 — Dk 변경 서명",
    "rev_claim":          "판본 사이 사람 기록(비고)에서 뽑은 주장",
    "rev_claim_match":    "주장과 기하가 맞는가",

    # ── 요약 — 문서를 열자마자 읽는 것 ──────────────────────────────────────
    "product_label":  "제품 이름 — 사람이 부르는 이름",
    "product_use":    "용도 — 이 안테나가 무엇에 쓰이나",
    # 타겟 주파수는 대역과 다르다 — 재질·형상 해석의 **기준점**이다(Q-16).
    # 요약이 대역만 싣고 이것을 빠뜨리면 "무슨 대역이냐"는 답해도 "무엇에 맞춰 설계했냐"는 못 답한다.
    "f0_target_ghz":  "타겟 주파수 — 설계 기준점",

    # ── 미검증 대장 — 보고서가 스스로 밝히는 공백 ───────────────────────────
    "open_item":  "미검증·미확정 항목",
    "open_owner": "그 항목의 담당",

    # ── 업데이트 필요 대장 — 빈 절을 절마다 찍지 않고 여기 모은다 ───────────
    # 사람이 아는 값과 파일이 들어와야 하는 값은 **다른 종류의 공백**이다.
    # 가르지 않으면 "말하면 되는 것"이 "기다려야 하는 것"에 묻힌다.
    "gap_section": "공백이 난 절",
    "gap_what":    "무엇이 없나",
    "gap_kind":    "공백의 종류 — 선언 · 반입 · 도구",
    "gap_slot":    "적을 자리 — 어디에 넣으면 채워지나",
}

_COMPILED = tuple((re.compile(p), r, d) for p, r, d in ROLE_RULES)
ROLES = tuple(dict.fromkeys([r for _, r, _ in ROLE_RULES] + list(EXTRA_ROLES)))
ROLE_DESC = {**{r: d for _, r, d in ROLE_RULES}, **EXTRA_ROLES}

# 원천명 조각을 떼기 위한 표식 — 원천 이름은 role 이 아니다
_SOURCE_HINT = re.compile(r"(CST|DXF|DWG|Gerber)\s", re.I)


def core_of(check: str) -> str:
    """`check` 에서 원천명 조각을 떼어 의미 부분만 남긴다."""
    parts = [p.strip() for p in str(check).split("·")]
    if len(parts) > 1 and _SOURCE_HINT.search(parts[-1] + " "):
        parts = parts[:-1]
    return " · ".join(parts)


def quantity_of(unit) -> str:
    return UNIT_QUANTITY.get(unit, "unitless")


def role_of(check: str) -> tuple[str | None, bool]:
    """(role, unmapped) — 어휘에 없으면 (None, True). 지어내지 않는다."""
    core = core_of(check)
    for rx, role, _ in _COMPILED:
        if rx.search(core):
            return role, False
    return None, True


def classify(check: str, unit=None) -> dict:
    role, unmapped = role_of(check)
    return {"role": role, "role_unmapped": unmapped,
            "role_desc": ROLE_DESC.get(role, ""), "quantity": quantity_of(unit)}


# ── work/ 전수 점검 ─────────────────────────────────────────────────────────

def scan(pattern: str = "work/*/해석_결과.json") -> dict:
    from collections import Counter
    mapped, unmapped = Counter(), Counter()
    for f in sorted(glob.glob(pattern)):
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        for it in d.get("items", []):
            role, um = role_of(it.get("check", ""))
            (unmapped if um else mapped)[core_of(it.get("check", "")) if um else role] += 1
    return {"n_roles_used": len(mapped), "mapped": dict(mapped),
            "n_unmapped": len(unmapped), "unmapped": dict(unmapped)}


def self_test() -> int:
    ok = fail = 0

    def chk(name, cond, detail=""):
        nonlocal ok, fail
        if cond:
            ok += 1
            print(f"  PASS  {name}")
        else:
            fail += 1
            print(f"  FAIL  {name}  {detail}")

    print("[roles.py 자기 시험]")

    # 오배치의 핵심 — 같은 단위, 다른 역할
    hp, _ = role_of("배열인자 · −3 dB 전폭(φ=0 절단) · CST test2")
    gr, _ = role_of("배열인자 · 격자로브 발생 각(±) · DXF Antenna_CAD_ECO")
    ap, _ = role_of("개구빔폭 · 방위면 HBW · DXF Antenna_CAD_ECO")
    chk("빔폭 ≠ 격자로브 (같은 deg)", hp != gr and None not in (hp, gr), f"{hp} / {gr}")
    chk("배열인자 빔폭 ≠ 개구 빔폭", hp != ap and ap is not None, f"{hp} / {ap}")
    chk("셋 다 quantity 는 angle", all(quantity_of("deg") == "angle" for _ in (1,)))

    # 공진 min/max — 축이 뒤집히지 않았나(결함 23)
    rmin, _ = role_of("공진정합 · 자유공간 λ/2 (최장 패치)")
    rmax, _ = role_of("공진정합 · 자유공간 λ/2 (최단 패치)")
    chk("짧은 패치가 공진 상한", rmax == "resonance_free_max_ghz", str(rmax))
    chk("긴 패치가 공진 하한", rmin == "resonance_free_min_ghz", str(rmin))

    # 대역 상/하한 구분
    lo, _ = role_of("선언 주파수 대역 하한 · CST test2")
    hi, _ = role_of("선언 주파수 대역 상한 · CST test2")
    chk("대역 하한 ≠ 상한", lo == "band_lo_ghz" and hi == "band_hi_ghz", f"{lo}/{hi}")

    # 두께 3종 구분
    ts, _ = role_of("스택업 선언 · t_sub · CST test2")
    tc, _ = role_of("스택업 선언 · t_cond · CST test2")
    chk("t_sub ≠ t_cond", ts == "t_sub_mm" and tc == "t_cond_mm", f"{ts}/{tc}")

    # 원천명 제거
    chk("원천명 제거", core_of("소자 수 · 변환 선언 기준 · CST test2") == "소자 수 · 변환 선언 기준",
        core_of("소자 수 · 변환 선언 기준 · CST test2"))

    # 미매핑은 지어내지 않는다
    r, um = role_of("존재하지 않는 이상한 항목")
    chk("미매핑은 None + 표시", r is None and um is True)

    # 실물 전수 — 미매핑 0 이어야 한다
    s = scan()
    chk(f"실물 전 run 미매핑 0건 (사용 role {s['n_roles_used']}종)", s["n_unmapped"] == 0,
        json.dumps(s["unmapped"], ensure_ascii=False)[:300])

    chk("role 어휘에 중복 없음", len(ROLES) == len(set(ROLES)))
    chk("모든 단위가 quantity 를 갖는다",
        all(quantity_of(u) != "unitless" for u in ("mm", "GHz", "deg", "dB", "dBi", "εr", "Ω", "개")))

    print(f"\n결과: {ok}/{ok + fail} PASS")
    return 0 if fail == 0 else 1


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    if argv[1] == "self-test":
        return self_test()
    if argv[1] == "list":
        print(f"역할 어휘 {len(ROLES)}종 · 물리량 {len(set(UNIT_QUANTITY.values()))}종\n")
        for r in ROLES:
            print(f"  {r:<30} {ROLE_DESC[r]}")
        return 0
    if argv[1] == "scan":
        s = scan(*argv[2:3])
        print(f"사용 role {s['n_roles_used']}종 · 미매핑 {s['n_unmapped']}종")
        for k, v in sorted(s["mapped"].items()):
            print(f"  {k:<30} x{v}")
        for k, v in sorted(s["unmapped"].items()):
            print(f"  [미매핑] {k}  x{v}")
        return 0
    print(f"알 수 없는 명령: {argv[1]}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
