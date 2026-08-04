#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/catalog.py — 값 카탈로그 생성기 (LLM 0콜)

목적
    문서 조립(프리즘)이 **숫자를 쓰지 않게** 만든다. 프리즘은 카탈로그의 키를 참조로만
    인용하고, 치환은 게이트가 결정론으로 수행한다. 대조할 숫자가 없으면 창작할 숫자도 없다.

산지
    카탈로그는 값을 **만들지 않는다**. 식별·추출·해석 JSON에 이미 있는 값을 평탄화해
    키를 붙일 뿐이다(N-1). 단위 환산·반올림·재계산을 하지 않는다.

렌더 규칙 (판정 규칙 (c) — numeric_rules 에 등재)
    canonical_decimal_no_trailing_zeros — JSON 값의 십진 표기에서 소수부 후행 0과
    끝의 소수점만 제거한다. 50.0 → "50" · 1.3742 → "1.3742" · -9.055 → "-9.055".
    자리수를 줄이지 않으므로 **무손실**이다. 반올림·유효자리 정책이 필요 없어진다.

CLI
    python tools/catalog.py build <run_id>      work/<run_id>/값_카탈로그.json 생성
    python tools/catalog.py show <run_id> [필터]  키 목록을 사람이 읽는 표로
    python tools/catalog.py prompt <run_id>     프리즘 프롬프트에 실을 블록으로
    python tools/catalog.py self-test
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402
import roles as R    # noqa: E402

CATALOG_NAME = "값_카탈로그.json"

# 참조 구문 — 게이트와 공유하는 단일 출처. 여기를 고치면 gate.py 가 따라온다.
REF_SIGILS = {
    "v": "값 + 단위 (기본형)",
    "n": "값만 — 단위가 표 머리글에 이미 있을 때",
    "u": "단위만",
    "f": "계산식",
    "s": "출처",
    # 항목 이름에도 숫자가 들어간다(ro3003 · −3 dB 전폭 · N=28). 이름을 손으로 쓰면
    # 게이트가 맨 숫자로 잡고, 예외로 빼면 값 주장이 숨을 자리가 생긴다.
    # 이름도 카탈로그에서 오게 하는 것이 두 문제를 한 번에 없앤다.
    "l": "항목 이름(레이블)",
    "r": "빈 값의 사유·담당 — I-5",
    # 값만 나열하면 문서가 **DB 덤프**가 된다. 읽는 사람이 알고 싶은 것은 값이 아니라
    # "이 값이 요구를 만족하나"다. 판정은 해석이 이미 냈다 — 문서에 세우기만 하면 된다.
    # ★ 판정을 프리즘이 쓰게 두지 않는다. "부합"을 지어내는 순간 게이트의 존재 이유가 사라진다.
    "p": "판정 — 부합 · 불일치 · 임계 미지정(해석이 낸 것을 옮긴다)",
    # 그림도 참조다 — 문서가 파일 경로를 직접 쓰면 참조 바인딩이 깨진다(D-22).
    # 게이트가 이미지 + **결정론 캡션**으로 치환한다. 캡션에는 축척·단위 산지·
    # **치수 정본 여부**가 들어가는데, 이것을 프리즘이 쓰게 두면 "3D 그림 밑에 치수를
    # 적는" 사고가 난다.
    "g": "그림 — 이미지 + 결정론 캡션",
}
# {{시길:키}} 또는 {{시길:키|역할}} — 역할은 **골격**이 넣는다(키는 프리즘이 고른다).
# 두 진술의 출처가 달라야 키 오배치가 대조로 드러난다.
REF_RE = re.compile(r"\{\{\s*([a-z])\s*:\s*([^{}|]+?)\s*(?:\|\s*([^{}|]+?)\s*)?\}\}")
# 형태는 참조인데 시길이 목록에 없거나 키가 빈 것 — 조용히 지나가면 안 된다.
REF_LOOSE_RE = re.compile(r"\{\{(.*?)\}\}", re.S)

RENDER_RULE = "canonical_decimal_no_trailing_zeros"


# ── 키 만들기 ────────────────────────────────────────────────────────────────

_KEEP = re.compile(r"[^0-9A-Za-z가-힣Ͱ-Ͽ_.\-]")


def slug(part: str) -> str:
    s = unicodedata.normalize("NFKC", str(part))
    s = s.replace("−", "-").replace("–", "-")
    s = re.sub(r"\s+", "_", s.strip())
    s = _KEEP.sub("", s)
    s = re.sub(r"_+", "_", s)
    return s.strip("._")          # '-' 는 남긴다 — "−3 dB" 의 부호는 뜻이다


def make_key(ns: str, label: str) -> str:
    parts = [p for p in (slug(x) for x in str(label).split("·")) if p]
    return ".".join([ns] + (parts or ["x"]))


# ── 값 렌더 ──────────────────────────────────────────────────────────────────

def render_value(v) -> str:
    """무손실 십진 표기. 반올림하지 않는다."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "예" if v else "아니오"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        s = repr(v)
        if "e" in s or "E" in s:
            return s
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return s or "0"
    if isinstance(v, (list, tuple)):
        return " ~ ".join(render_value(x) for x in v)
    return str(v)


def _entry(key, label, value, unit=None, formula=None, source=None, extra=None,
           role=None, quantity=None, group=None) -> dict:
    cls = R.classify(label, unit)
    e = {
        "key": key,
        "label": label,
        "value": value,
        "unit": unit or "",
        "render": render_value(value),
        "formula": formula or "",
        "source": source or "",
        "role": role if role is not None else cls["role"],
        "role_unmapped": cls["role_unmapped"] if role is None else False,
        "quantity": quantity or cls["quantity"],
        # 행렬 절의 **행 묶음**. 같은 group 의 항목들이 한 줄이 된다.
        # 이것이 있어야 행을 결정론으로 편다 — 프리즘이 행을 고르면 9300 행에
        # 9400 의 값이 들어가도 역할은 맞아 게이트가 못 잡는다.
        "group": group,
    }
    if unit:
        e["render_with_unit"] = (e["render"] + " " + unit).strip() if e["render"] else ""
    else:
        e["render_with_unit"] = e["render"]
    if extra:
        e.update(extra)
    return e


_SRC_KEY = re.compile(r"(출처|source)$")


def _src_of(inputs) -> str:
    """산지 문자열을 만든다.

    수리: 앞선 구현은 `출처`·`source` 두 키만 보아 `x_출처`·`선언_출처` 처럼 접두가 붙은
    출처를 놓쳤고, 파생값(배열인자·파장 대비 등)은 출처가 **빈 채로** 카탈로그에 실렸다.
    게이트가 이를 "출처 없는 숫자"로 반려해 드러났다.
    파생값의 산지는 원천 출처 + **계산 입력**이다 — 둘 다 적는다.
    """
    if not isinstance(inputs, dict):
        return ""
    named, keys = [], []
    for k, v in inputs.items():
        if _SRC_KEY.search(str(k)):
            if isinstance(v, str) and v.strip():
                named.append(v.strip())
        else:
            keys.append(str(k))
    parts = []
    if named:
        parts.append(" · ".join(dict.fromkeys(named)))
    if keys:
        parts.append("입력: " + "·".join(keys))
    return " / ".join(parts)


# ── 요구 명세 → 행렬 행 ─────────────────────────────────────────────────────
# 레지스트리 키는 영문이고 문서는 한국어다. 이름을 **어디선가** 정해야 하는데,
# 프리즘이 정하게 두면 같은 요구가 run 마다 다른 이름으로 실린다. 판정 규칙 (c) 로
# 못박고 rule_version 에 묶는다. 없는 키는 키 그대로 싣는다 — 이름을 지어내지 않는다.
REQ_LABELS = {
    "band": "동작 주파수 범위",
    "gain_dbi": "이득",
    "return_loss_db": "반사 손실",
    "sidelobe_level_db": "부엽 레벨",
    "hbw_deg": "수평 빔폭(방위)",
    "vbw_deg": "수직 빔폭(고각)",
}
REQ_UNITS = {
    "gain_dbi": "dBi", "return_loss_db": "dB", "sidelobe_level_db": "dB",
    "hbw_deg": "deg", "vbw_deg": "deg",
}


def _requirement_rows(verify: dict) -> list[dict]:
    """`registry/products.yaml` 의 requirements 를 행렬 한 줄씩으로.

    ★ 하한과 상한을 한 칸에 뭉치지 않는다. "> 23 dBi" 를 문자열로 실으면 그 문자열은
      값이 아니라 문장이 되고, 대조 절이 그것을 다시 파싱해야 한다. 파싱은 두 번째
      해석이고 두 번째 해석은 두 번째 정본이다.
    """
    req = (verify or {}).get("requirements") or {}
    spec = req.get("requirements_spec") or {}
    band = req.get("band_ghz")
    src = "registry/products.yaml requirements — 사람 선언(산지 e)"
    rows: list[dict] = []

    def row(rid, label, lo, hi, unit, axis, note):
        g = f"요구.{rid}"
        rows.append(_entry(f"{g}.항목", f"요구 항목 {label}", label, source=src,
                           role="open_item", group=g))
        rows.append(_entry(f"{g}.하한", f"{label} 요구 하한", lo, unit=unit, source=src,
                           role="req_min", group=g))
        rows.append(_entry(f"{g}.상한", f"{label} 요구 상한", hi, unit=unit, source=src,
                           role="req_max", group=g))
        rows.append(_entry(f"{g}.축", f"{label} 적용 축", axis or "", source=src,
                           role="req_axis", group=g))
        rows.append(_entry(f"{g}.근거", f"{label} 요구 근거", note or "", source=src,
                           role="req_basis", group=g))

    if isinstance(band, (list, tuple)) and len(band) >= 2:
        row("band", REQ_LABELS["band"], band[0], band[1], "GHz", "",
            f"제품 {req.get('product') or ''} 대역 선언".strip())
    for rid, d in spec.items():
        if not isinstance(d, dict):
            continue
        row(rid, REQ_LABELS.get(rid, rid), d.get("min"), d.get("max"),
            REQ_UNITS.get(rid, ""), d.get("axis"), d.get("note"))
    return rows


# ── 카탈로그 조립 ────────────────────────────────────────────────────────────

def build(run_id: str, work: Path | None = None) -> dict:
    work = Path(work) if work else C.work_dir(run_id, create=False)
    entry = _load(work / "entry.json")
    ident = _load(work / "식별_결과.json")
    extract = _load(work / "추출_결과.json")
    verify = _load(work / "해석_결과.json")
    if not (ident or extract or verify):
        raise FileNotFoundError(f"{work} 에 식별·추출·해석 산출이 없다 — 카탈로그를 만들 근거가 없다")

    entries: dict[str, dict] = {}

    def put(e: dict):
        k = e["key"]
        if k in entries:                      # 충돌은 조용히 덮지 않는다
            i = 2
            while f"{k}#{i}" in entries:
                i += 1
            e["key"] = f"{k}#{i}"
            e["collision_of"] = k
        entries[e["key"]] = e

    # run 메타 — 날짜·버전·해시도 참조로 쓸 수 있어야 맨 숫자가 남지 않는다
    meta_src = verify or ident or extract
    put(_entry("run.run_id", "실행 식별자", meta_src.get("run_id"), source="원장 runs.run_id"))
    put(_entry("run.rule_version", "규칙 판본",
               ident.get("rule_version") or C.effective_rule_version(), source="tools/_common.py"))
    if verify.get("registry_version"):
        put(_entry("run.registry_version", "레지스트리 판본", verify["registry_version"],
                   source="registry/products.yaml"))
    if verify.get("product"):
        put(_entry("run.product", "제품군 배정", verify["product"], source="registry/products.yaml"))

    # entry — 원천 분해가 정한 자산 단위와 프로젝트 연결
    if entry:
        put(_entry("entry.id", "자산 entry", entry.get("entry_id"),
                   source="분해: discover.py", role="entry.id"))
        put(_entry("entry.kind", "entry 종류", entry.get("kind"),
                   formula=entry.get("anchor", ""), source="분해: discover.py",
                   role="entry.kind"))
        put(_entry("entry.project_tag", "프로젝트 태그", entry.get("project_tag"),
                   source="분해: discover.py", role="entry.project_tag"))
        if entry.get("derived_from"):
            put(_entry("entry.derived_from", "파생 원본 프로젝트", entry["derived_from"],
                       formula="; ".join(entry.get("derived_basis") or []),
                       source="분해: CST 선언 임포트와 파일명 일치", role="entry.derived_from"))
        if entry.get("link_candidates"):
            top = entry["link_candidates"][0]
            put(_entry("entry.link_candidate", "연결 후보(확정: 사람)",
                       f"{top['project_tag']}",
                       formula="; ".join(top.get("basis") or []),
                       source="분해: 이름·형제 정황 — 확정이 아니다",
                       role="entry.link_candidate"))

    # 식별
    if ident:
        src = ident.get("source", {})
        put(_entry("식별.원천명", "원천 이름", src.get("name"), source="식별: source.name"))
        put(_entry("식별.원천종류", "원천 종류", src.get("kind"), source="식별: source.kind"))
        put(_entry("식별.주도레인", "주도 어댑터", ident.get("adapter"),
                   formula=ident.get("adapter_rule", ""), source="식별: adapter"))
        lanes = ident.get("contributing_lanes") or []
        put(_entry("식별.기여레인", "기여 레인", ", ".join(lanes) if lanes else "없음",
                   source="식별: contributing_lanes"))
        put(_entry("식별.파일수", "원천 파일 수", len(ident.get("files") or []), unit="개",
                   source="식별: files[]"))
        put(_entry("식별.판독불가수", "판독 불가 파일 수", len(ident.get("unreadable") or []), unit="개",
                   source="식별: unreadable[]"))
        put(_entry("식별.판정", "식별 결과", ident.get("outcome"), source="식별: outcome"))
        for f in ident.get("files") or []:
            rel = f.get("rel") or f.get("name") or ""
            if not rel:
                continue
            put(_entry(make_key("식별.파일", rel + "·판독수준"), f"{rel} 판독 수준",
                       f.get("readability"), source=f"식별: files[] {rel}"))

    # 추출
    if extract:
        put(_entry("추출.geom_hash", "기하 지문", extract.get("geom_hash"), source="추출: geom_hash"))
        for g in extract.get("geometry", {}).get("dxf", []) or []:
            rel = g.get("rel", "dxf")
            base = make_key("추출.형상", rel)
            put(_entry(base + ".판독수준", f"{rel} 판독 수준", g.get("readability"),
                       source=f"추출: geometry.dxf {rel}"))
            # ↓ 역할을 준다 — 파일명이 달라도 **같은 도면**을 값으로 알아볼 수 있어야 한다
            put(_entry(base + ".레이어수", f"{rel} 레이어 수", len(g.get("layers") or []),
                       unit="개", source=f"추출: geometry.dxf {rel}.layers",
                       role="n_layers"))
            ents = g.get("entities")
            if isinstance(ents, dict):
                for ek, ev in ents.items():
                    if isinstance(ev, (int, float)):
                        put(_entry(base + ".엔티티." + slug(ek), f"{rel} {ek} 수", ev, unit="개",
                                   source=f"추출: geometry.dxf {rel}.entities",
                                   role={"polyline": "n_polyline",
                                         "circle": "n_circle"}.get(ek)))
            size = g.get("bbox_size_mm")
            if isinstance(size, (list, tuple)) and len(size) >= 2:
                for i, (nm, role) in enumerate((("가로", "bbox_x_mm"), ("세로", "bbox_y_mm"))):
                    put(_entry(f"{base}.bbox_{role[5]}", f"{rel} bbox {nm}",
                               size[i], unit="mm",
                               source=f"추출: geometry.dxf {rel}.bbox_size_mm", role=role))
            elev = g.get("elevations_mm")
            if isinstance(elev, (list, tuple)) and elev:
                put(_entry(base + ".elevation", f"{rel} elevation", elev[0], unit="mm",
                           source=f"추출: geometry.dxf {rel}.elevations_mm",
                           role="elevation_mm"))
            bbox = g.get("bbox") or g.get("bbox_mm")
            if isinstance(bbox, dict):
                for bk, bv in bbox.items():
                    put(_entry(base + ".bbox." + slug(bk), f"{rel} bbox {bk}", bv, unit="mm",
                               source=f"추출: geometry.dxf {rel}.bbox"))
        put(_entry("추출.특이표기수", "특이 표기 건수", len(extract.get("annotations") or []), unit="건",
                   source="추출: annotations[]"))

    # 해석 — CheckItem 이 카탈로그의 척추다
    for it in verify.get("items") or []:
        label = it.get("check", "")
        key = make_key("해석", label)
        thr = it.get("threshold")
        put(_entry(
            key, label, it.get("value"), unit=it.get("unit"),
            formula=it.get("formula"), source=_src_of(it.get("inputs")),
            extra={
                "threshold": thr,
                "threshold_render": render_value(thr) if thr else "임계 미지정",
                "pass": it.get("pass"),
                "판정": ("대조 없음" if it.get("pass") is None else ("부합" if it["pass"] else "불일치")),
                "reason": it.get("reason", ""),
            },
        ))

    # 배열인자 곡선 요약(해석이 계산한 값 — 여기서 재계산하지 않는다)
    for af in verify.get("array_factors") or []:
        n = af.get("n")
        base = f"해석.배열인자.N{n}"
        for k, unit, role in (("hpbw_deg", "deg", "af_hpbw_deg"),
                              ("sll_db", "dB", "af_sll_db"),
                              ("sll_angle_deg", "deg", "af_sll_angle_deg"),
                              ("grating_deg", "deg", "af_grating_deg"),
                              ("lambda_mm", "mm", "wavelength_mm")):
            if af.get(k) is None:
                continue
            # 수리: items[] 가 이미 실은 값을 array_factors[] 가 같은 역할·같은 값으로
            # 다시 실었다(해석이 두 경로로 내보낸다). 자산 DB 에서 안테나별로 나란히
            # 세우자 같은 역할에 키가 둘인 것이 드러났다. 중복은 넣지 않는다 —
            # 어느 쪽을 인용해도 같은 값이면 문서가 두 이름을 갖게 될 뿐이다.
            if any(x["role"] == role and x["value"] == af[k] for x in entries.values()):
                continue
            put(_entry(f"{base}.{k}", f"배열인자 N={n} {k}", af[k], unit=unit,
                       source="해석: array_factors[]", role=role))

    # 요구 명세 — 제품 레지스트리의 requirements 를 **행렬 행**으로 편다.
    # 이 절은 오늘 채울 수 있는 유일한 성능 계열 절이다. 시뮬·시험은 데이터가 없다.
    for e in _requirement_rows(verify):
        put(e)

    # 요약 절의 첫 두 줄 — 제품 이름과 용도. 원천 어디에도 없는 값이다(사람 선언).
    _rq = (verify.get("requirements") or {})
    if _rq.get("label"):
        put(_entry("제품.이름", "제품 이름", _rq["label"],
                   source="registry/products.yaml label", role="product_label"))
    if _rq.get("use"):
        put(_entry("제품.용도", "용도", _rq["use"],
                   source="registry/products.yaml use — 사람 선언(산지 e)",
                   role="product_use"))
    if _rq.get("f0_ghz") is not None:
        put(_entry("제품.타겟주파수", "타겟 주파수", _rq["f0_ghz"], unit="GHz",
                   formula="제품이 선언한 설계 기준점 — 대역 중심과 같을 수도 다를 수도 있다",
                   source="registry/products.yaml f0_ghz", role="f0_target_ghz"))

    # 기판 선언 — 이름과 **유전율의 기준 주파수**. 유전율 자체는 해석 items 가 이미 싣는다.
    # 기준 주파수는 어느 대조 항목도 값으로 내지 않는데, 없으면 유전율로 판정할 수 없다(D-34).
    # 값이 아니라 **판정 가능 여부**를 정하는 값이라 카탈로그에 따로 올린다.
    _sub = ((verify.get("requirements") or {}).get("substrate")) or {}
    if _sub.get("name"):
        put(_entry("재질.기판명", "기판 재질 품명", _sub["name"],
                   source="registry/products.yaml substrate.name", role="material_name"))
    if _sub.get("er_ref_ghz") is not None:
        put(_entry("재질.유전율_기준주파수", "유전율 측정 기준 주파수", _sub["er_ref_ghz"],
                   unit="GHz", formula="데이터시트 측정 주파수 — 재계산하지 않는다(D-37)",
                   source="registry/products.yaml substrate.er_ref_ghz",
                   role="material_er_ref_ghz"))

    # 스택업·반사판 — **사람 선언으로만 들어오는 값들**(산지 e). 원천 어디에도 없다.
    # `tools/declare.py` 로 넣으면 `registry/declared/` 에 쌓이고 여기로 흘러든다.
    _src_dec = "사람 선언 — registry/products.yaml + registry/declared/"
    # 유전율은 CST 선언이 있으면 그쪽이 산지다 — 같은 역할에 키가 둘이 되면
    # 문서가 한 값을 두 이름으로 싣는다(I-D). 원천에 없을 때만 선언값을 올린다.
    if _sub.get("er") is not None and not any(
            e.get("role") == "material_er" for e in entries.values()):
        put(_entry("재질.유전율_선언", "재질 유전율(선언)", _sub["er"], unit="εr",
                   formula="원천에 재질 선언이 없어 제품 선언을 쓴다 — 기준 주파수와 함께 읽는다",
                   source=f"{_src_dec}substrate.er", role="material_er"))
    for node, key, label, unit, role in (
        ("substrate", "h_mm", "기판 두께(선언)", "mm", "substrate_h_declared_mm"),
        ("stackup", "layer_count", "도체 층 수", "개", "material_layer_count"),
        ("stackup", "copper_oz", "외층 동박", "oz", "copper_weight_oz"),
        ("stackup", "surface_finish", "표면 처리", None, "surface_finish"),
        ("reflector", "material", "반사판 재질", None, "reflector_material"),
        ("reflector", "thickness_mm", "반사판 두께", "mm", "reflector_thickness_mm"),
        ("reflector", "finish", "반사판 표면 처리", None, "reflector_finish"),
    ):
        v = ((_rq.get(node) or {}) if node != "substrate" else _sub).get(key)
        if v is None:
            continue
        put(_entry(f"재질.{node}.{key}", label, v, unit=unit,
                   source=f"{_src_dec}{node}.{key}", role=role))

    # 시각 근거 — figures.py 가 만든 그림 항목을 그대로 싣는다(값을 만들지 않는다).
    # 그림이 카탈로그에 있어야 문서가 `{{g:키}}` 로 참조할 수 있고, 게이트의 기존
    # 무결성 검사를 그대로 물려받는다.
    figs = _load(work / "그림_결과.json")
    for fe in (figs.get("entries") or []):
        put(dict(fe, role_unmapped=fe.get("role") not in R.ROLES))
    # 그리지 **못한** 사유도 카탈로그가 나른다(F-37). 골격이 대장을 세울 때 이걸 읽는다.
    # ★ 카탈로그에 실어야 하는 이유 — 게이트가 골격을 **다시 만들어** 대조하기 때문이다.
    #   사유를 골격 인자로 넘기면 게이트 쪽 골격에는 없어서 template_modified 가 난다.
    #   같은 파일에서 읽으면 양쪽이 반드시 같아진다.
    figure_gaps = figs.get("skipped") or []

    if verify:
        # 아래 둘은 물리량이 아니라 실행 메타다 — 역할을 명시해 어휘 미매핑과 구분한다.
        put(_entry("해석.대조건수", "대조 항목 수", len(verify.get("items") or []), unit="건",
                   source="해석: items[]", role="verify.n_checks"))
        put(_entry("해석.판정", "해석 종합", verify.get("verdict"),
                   formula=verify.get("reason", ""), source="해석: verdict",
                   role="verify.verdict"))

    # 메타·식별·추출은 물리량이 아니다 — 키가 곧 역할이고, 어휘 미매핑이 아니다.
    for k, e in entries.items():
        if e["role"] is None and not k.startswith("해석."):
            e["role"], e["role_unmapped"] = k, False

    unmapped = sorted(k for k, e in entries.items() if e["role_unmapped"])
    cat = {
        "run_id": run_id,
        "rule_version": C.effective_rule_version(),
        "render_rule": RENDER_RULE,
        "role_vocab_size": len(R.ROLES),
        "unmapped_keys": unmapped,        # 역할을 지어내지 않는다 — 사람이 등재한다
        "ref_syntax": {f"{{{{{k}:<키>}}}}": v for k, v in REF_SIGILS.items()},
        "규율": "카탈로그는 값을 만들지 않는다 — 식별·추출·해석 JSON의 값을 평탄화해 키를 붙일 뿐이다(N-1).",
        "n_entries": len(entries),
        "figure_gaps": figure_gaps,       # 그림을 왜 못 그렸나 — 대장이 읽는다
        "entries": entries,
    }
    return cat


def _load(p: Path) -> dict:
    try:
        return C.read_json(p)
    except Exception:
        return {}


def write(run_id: str, work: Path | None = None) -> Path:
    work = Path(work) if work else C.work_dir(run_id)
    cat = build(run_id, work)
    return C.write_json(work / CATALOG_NAME, cat)


def load(run_id: str, work: Path | None = None) -> dict:
    work = Path(work) if work else C.work_dir(run_id, create=False)
    return C.read_json(work / CATALOG_NAME)


# ── 프리즘 프롬프트용 블록 ───────────────────────────────────────────────────

def prompt_block(cat: dict, max_len: int = 0) -> str:
    """프리즘에게 주는 '쓸 수 있는 값의 전부'. 여기 없는 값은 문서에 못 쓴다."""
    lines = [
        "[값 카탈로그 — 문서에 쓸 수 있는 값의 전부]",
        "규율: 숫자를 직접 타이핑하지 않는다. 아래 키를 참조로만 인용한다.",
        "  {{v:키}} 값+단위 · {{n:키}} 값만 · {{u:키}} 단위 · {{f:키}} 계산식 · {{s:키}} 출처",
        "골격 슬롯을 채울 때는 슬롯이 정한 역할을 그대로 둔다 — {{v:키|역할}}.",
        "역할이 키의 역할과 다르면 게이트가 반려한다(키 오배치 검사).",
        "카탈로그에 없는 값은 문서에 쓸 수 없다 — 계산해서 만들지도 않는다.",
        "",
    ]
    for k, e in cat["entries"].items():
        val = e["render_with_unit"] or "(빈 값)"
        extra = ""
        if e.get("판정") and e["판정"] != "대조 없음":
            extra = f"  [{e['판정']}]"
        role = e.get("role") or "(역할 미등재)"
        lines.append(f"  {k}\n      = {val}   — {e['label']}{extra}\n      역할 {role}")
    txt = "\n".join(lines)
    return txt[:max_len] if max_len and len(txt) > max_len else txt


# ── 문서 골격 — 역할의 독립 출처 ─────────────────────────────────────────────
# 골격은 **결정론**으로 만든다. 슬롯이 역할을 정하고 프리즘은 키만 고른다.
# 역할과 키의 출처가 다르기 때문에 오배치가 대조로 드러난다 — 이것이 골격의 존재 이유다.

def _gap_block(sec: dict, gaps: list[dict]) -> list[str]:
    """업데이트 필요 대장 — 빈 필수 절이 **한 곳에** 모인다.

    왜 절마다 찍지 않나
        필수 절이 열두 개인데 다섯이 비면 문서가 "내용 없음" 다섯 덩어리로 끊긴다.
        읽는 사람이 알아야 하는 것은 "무엇을 하면 채워지나"인데, 그건 흩어 놓으면
        절대 안 보인다. I-5(빈 값은 빈 채 + 담당)를 버린 것이 아니라 **자리를 옮겼다**.

    왜 종류를 가르나
        "말하면 되는 것"과 "파일을 기다려야 하는 것"은 다른 일이다. 섞어 놓으면
        아는 사람이 눈앞에 있어도 아무도 묻지 않는다.

    ★ 이 표는 결정론이다 — 사유·담당·적을 자리는 전부 양식 정본이 쓴 것이고
      프리즘은 아래 서술 슬롯에만 쓴다. 게이트가 `template_modified` 로 지킨다.
    """
    cols = sec["columns"]
    field = {"gap_section": "section", "gap_what": "why", "gap_kind": "kind",
             "open_owner": "owner", "gap_slot": "slot"}
    out = [f"## {sec['title']}", ""]
    if (sec.get("note") or "").strip():
        out += ["> " + " ".join(sec["note"].split()), ""]
    out += ["| " + " | ".join(c["label"] for c in cols) + " |",
            "| " + " | ".join(["---"] * len(cols)) + " |"]
    for g in gaps:
        cells = []
        for c in cols:
            v = " ".join(str(g.get(field.get(c["role"], ""), "") or "").split())
            cells.append(v.replace("|", "·") or "—")
        out.append("| " + " | ".join(cells) + " |")
    return out + [""]


def _figure_gap_rows(cat: dict, spec: dict) -> list[dict]:
    """그림을 **왜 못 그렸나**를 대장의 행으로 편다 (결함 F-37).

    왜 절이 비었는지와 따로 세는가
        형상 절은 추출값이 있어 차 있는데 그림만 없을 수 있다. 그때 절은 공백이 아니고,
        대장은 조용하고, 문서는 "그림이 원래 없는 문서"처럼 보인다. 그림이 없다는 것은
        절이 비었는지와 **무관한 사실**이라 따로 세야 한다.

    산지는 카탈로그다 — 게이트가 골격을 다시 만들 때 같은 파일을 읽으므로 양쪽이 같아진다.
    """
    gaps = cat.get("figure_gaps") or []
    if not gaps:
        return []
    where = {}                        # 역할 → 그 그림이 붙기로 되어 있던 절
    for s in spec["sections"]:
        for role in s.get("figures") or []:
            where.setdefault(role, s["title"])
    rows = []
    for g in gaps:
        rows.append({
            "section": f"{where.get(g.get('role'), '그림')} — {g.get('label') or '그림'}",
            "why": g.get("why") or "",
            "kind": g.get("종류") or "도구",
            "owner": g.get("담당") or "",
            "slot": g.get("자리") or "",
        })
    return rows


def _matrix_rows(sec: dict, cat: dict, used: set) -> tuple[list[str], set]:
    """행렬 절의 행을 **결정론으로** 편다.

    한 행 = 카탈로그의 한 group. 축 칸은 그 group 안에서 축 역할을 가진 항목,
    나머지 칸은 열 역할을 가진 항목이다. 프리즘은 여기에 손대지 않는다 —
    행을 고르게 두면 9300 행에 9400 의 이득이 들어가도 **역할은 맞아서** 게이트가
    잡지 못한다. 역할 대조는 칸의 종류를 지키지 행의 짝을 지키지 않는다.

    빈 칸은 `—` 다. 없는 항목에는 키가 없고, 키가 없으면 참조할 것도 없다 —
    `{{r:}}` 로 사유를 달 자리조차 없다는 뜻이므로 지어내지 않는다.
    """
    ax_roles = set((sec.get("axis") or {}).get("roles") or [])
    cols = sec.get("columns") or []
    col_roles = [c["role"] for c in cols]
    wanted = ax_roles | set(col_roles)

    groups: dict[str, dict] = {}
    order: list[str] = []
    for e in cat["entries"].values():
        g, role = e.get("group"), e.get("role")
        if not g or role not in wanted or e["key"] in used:
            continue
        if g not in groups:
            groups[g] = {}
            order.append(g)
        groups[g].setdefault(role, e)

    rows, taken = [], set()
    for g in order:
        got = groups[g]
        ax = next((got[r] for r in (sec["axis"]["roles"]) if r in got), None)
        if ax is None:
            continue                  # 축이 없는 묶음은 행이 될 수 없다 — 지어내지 않는다
        if not any(r in got for r in col_roles):
            continue                  # 축만 있고 실을 것이 없는 행은 만들지 않는다
        cells = [f"{{{{l:{ax['key']}|{ax['role']}}}}}"]
        taken.add(ax["key"])
        for role in col_roles:
            e = got.get(role)
            if e is None or not e.get("render_with_unit"):
                cells.append("—")
                if e is not None:
                    taken.add(e["key"])
                continue
            cells.append(f"{{{{v:{e['key']}|{role}}}}}")
            taken.add(e["key"])
        rows.append("| " + " | ".join(cells) + " |")
    return rows, taken


def skeleton(cat: dict, spec: dict | None = None) -> str:
    """문서 양식 정본(`registry/document_spec.yaml`)대로 골격을 만든다.

    · 절 순서·역할 배치·표/목록은 **데이터**가 정한다 — 여기에 박아 두지 않는다.
    · 카탈로그에 없는 역할의 슬롯은 만들지 않는다 — 빈 슬롯을 두면 프리즘이 채우려 든다.
    · 서술 슬롯은 마커로 남긴다. **마커 사이가 LLM 이 쓰는 유일한 자리**이고,
      그 밖은 결정론이라 게이트가 `template_modified` 로 불변을 지킨다.
    """
    import docspec as DS
    spec = spec or DS.load()

    by_role: dict[str, list[dict]] = {}
    for e in cat["entries"].values():
        by_role.setdefault(e.get("role") or "", []).append(e)

    tr = spec.get("title_role") or "식별.원천명"
    out = [f"# {spec.get('title', '안테나 통합 문서')} — {{{{v:<키>|{tr}}}}}", ""]
    used: set[str] = set()
    gaps: list[dict] = []              # 빈 필수 절 — 마지막 대장으로 모인다

    for sec in spec["sections"]:
        head, rows, filled = [], [], 0

        # 대장은 카탈로그에서 오지 않는다 — 앞 절들이 남긴 공백이 행이 된다.
        if sec["render"] == "gaps":
            rows_all = gaps + _figure_gap_rows(cat, spec)
            if not rows_all:
                continue               # 빈 절이 없으면 대장도 없다 — 축하할 일이다
            out += _gap_block(sec, rows_all)
            for pr in sec.get("prose") or []:
                slot = f"{sec['id']}.{pr['slot']}"
                guide = " ".join((pr.get("guide") or "").split())
                cap = f" (최대 {pr['max_sentences']}문장)" if pr.get("max_sentences") else ""
                out += [DS.PROSE_OPEN.format(slot=slot, guide=guide + cap),
                        DS.PROSE_PLACEHOLDER,
                        DS.PROSE_CLOSE.format(slot=slot), ""]
            continue

        # (1) 행렬 — 행은 카탈로그의 group 이 정한다. 프리즘이 고르지 않는다.
        core_empty = True              # 절의 **본체**가 비었나 — 머리표만으로는 안 찬다
        if sec["render"] == "matrix":
            mrows, mused = _matrix_rows(sec, cat, used)
            core_empty = not mrows
            if mrows:
                ax = sec["axis"]
                cols = sec["columns"]
                head = ["| " + " | ".join([ax["label"]] + [c["label"] for c in cols]) + " |",
                        "| " + " | ".join(["---"] * (len(cols) + 1)) + " |"]
                rows += mrows
                filled += len(mrows)
                used |= mused

        # (2) 값 — 항목 하나에 한 줄. 행렬 절에도 붙을 수 있다(행렬 위 머리표).
        #     ★ reuse 절(요약)은 `used` 를 소비하지 않는다 — 요약은 정의상 재진술이다.
        #       소비하면 아래 상세 절이 같은 값을 잃는다.
        reuse = bool(sec.get("reuse"))
        vrows = []
        for role in sec.get("roles") or []:
            for e in by_role.get(role, []):
                if e["key"] in used or e.get("figure") or e.get("group"):
                    continue
                empty = not e.get("render_with_unit")
                if empty and not e.get("reason"):
                    continue
                if not reuse:
                    used.add(e["key"])
                if sec["render"] in ("table", "list"):
                    core_empty = False
                # 항목 이름도 참조다 — 이름 안의 숫자(ro3003 · N=28)를 손으로 쓰지 않게 한다
                name = f"{{{{l:<키>|{role}}}}}"
                cell = (f"(빈 값 — {{{{r:<키>|{role}}}}})" if empty
                        else f"{{{{v:<키>|{role}}}}}")
                if not empty:
                    filled += 1
                if sec["render"] == "list":
                    vrows.append(f"- {name}: {cell}")
                else:
                    vrows.append(f"| {name} | {cell} | {{{{p:<키>|{role}}}}} | "
                                 f"{{{{s:<키>|{role}}}}} |")
        if vrows:
            if sec["render"] in ("table", "matrix"):
                if rows:
                    rows += [""]           # 행렬 밑에 붙는 머리표는 한 줄 띄운다
                    rows += ["| 항목 | 값 | 판정 | 출처 |", "| --- | --- | --- | --- |"]
                else:
                    head = ["| 항목 | 값 | 판정 | 출처 |", "| --- | --- | --- | --- |"]
            rows += vrows

        # (3) 그림 — `figures:` 가 정한 순서 그대로. 절마다 붙는다(본문 사이에 그림).
        for role in sec.get("figures") or []:
            for e in by_role.get(role, []):
                if e["key"] in used or not e.get("figure"):
                    continue
                if not reuse:
                    used.add(e["key"])
                if sec["render"] == "figures":
                    core_empty = False
                # 그림 블록 — 캡션은 `g` 시길이 결정론으로 만든다. 여기서 쓰지 않는다.
                # 앞에 표가 있으면 한 줄 띄운다 — 안 띄우면 표가 그림 줄을 삼킨다.
                if rows and rows[-1] != "":
                    rows.append("")
                rows += [f"{{{{g:<키>|{role}}}}}", ""]
                filled += 1

        # 절마다 "내용 없음"을 찍지 않는다 — 문서가 부재 표기로 도배된다.
        # 필수 절의 공백은 **마지막 대장 한 곳**으로 모은다(I-5 는 자리를 옮긴 것).
        #
        # ★ 본체가 비면 머리표가 차 있어도 공백이다. 시뮬레이션 절이 "성능 데이터 보유:
        #   아니오" 한 줄만 싣고 대장에 안 오르면, 문서는 그 절이 채워진 것처럼 보인다.
        if core_empty and sec.get("required"):
            gaps.append({"section": sec["title"], **(sec.get("absent") or {})})
        if not rows:
            continue
        out += [f"## {sec['title']}", ""]
        if sec.get("lane"):
            out += [f"*레인: {sec['lane']}*", ""]
        # 절 단서 — **어떻게 읽으면 안 되는지**를 결정론으로 못 박는 자리.
        # 프리즘의 서술은 마커 안에 있고 사람이 안 읽을 수도 있다. 표 바로 위에
        # 붙는 이 한 줄은 표를 보는 순간 함께 보인다.
        if (sec.get("note") or "").strip():
            out += ["> " + " ".join(sec["note"].split()), ""]
        out += head + rows + [""]

        for pr in sec.get("prose") or []:
            if pr.get("skip_if_all_empty") and filled == 0:
                continue                   # 값이 하나도 없는 절에 소견을 요구하지 않는다
            slot = f"{sec['id']}.{pr['slot']}"
            guide = " ".join((pr.get("guide") or "").split())
            cap = f" (최대 {pr['max_sentences']}문장)" if pr.get("max_sentences") else ""
            out += [DS.PROSE_OPEN.format(slot=slot, guide=guide + cap),
                    DS.PROSE_PLACEHOLDER,
                    DS.PROSE_CLOSE.format(slot=slot), ""]

    out += ["> 슬롯 `<키>` 를 카탈로그의 키로 바꾼다. `|역할` 은 **건드리지 않는다** —",
            "> 역할은 골격이 정한 것이고, 키와 어긋나면 게이트가 오배치로 반려한다.",
            "> 서술은 `PROSE` 마커 **사이에만** 쓴다. 마커 밖을 고치면 게이트가 반려한다.", ""]
    return "\n".join(out)


# ── 자기 시험 ────────────────────────────────────────────────────────────────

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

    print("[catalog.py 자기 시험]")

    # 렌더 규칙 — 무손실
    chk("render 50.0 → '50'", render_value(50.0) == "50", render_value(50.0))
    chk("render 1.3742 유지", render_value(1.3742) == "1.3742", render_value(1.3742))
    chk("render -9.055 유지", render_value(-9.055) == "-9.055", render_value(-9.055))
    chk("render 0.4883 유지", render_value(0.4883) == "0.4883", render_value(0.4883))
    chk("render None → 빈 문자열", render_value(None) == "")
    chk("render 정수 28", render_value(28) == "28")
    chk("render 구간 [1.0, 100.0]", render_value([1.0, 100.0]) == "1 ~ 100", render_value([1.0, 100.0]))
    chk("반올림하지 않는다", render_value(15.681234) == "15.681234", render_value(15.681234))

    # 키 생성
    k = make_key("해석", "배열인자 · −3 dB 전폭(φ=0 절단) · CST test2")
    chk("키에 공백 없음", " " not in k, k)
    chk("키가 계층 3단", k.count(".") == 3, k)
    chk("키 결정론", k == make_key("해석", "배열인자 · −3 dB 전폭(φ=0 절단) · CST test2"))
    chk("빈 부분 제거", make_key("해석", "a ·  · b") == "해석.a.b", make_key("해석", "a ·  · b"))

    # 참조 정규식
    m = REF_RE.findall("{{v:해석.a}} 와 {{ n : 해석.b }} 와 {{f:x}}")
    chk("참조 3건 파싱", len(m) == 3, str(m))
    chk("공백 허용 파싱", m[1][:2] == ("n", "해석.b"), str(m[1]))
    chk("맨텍스트는 참조 아님", REF_RE.findall("v:해석.a") == [])
    m2 = REF_RE.findall("{{v:해석.a|af_hpbw_deg}}")
    chk("역할 선언 파싱", m2 == [("v", "해석.a", "af_hpbw_deg")], str(m2))
    chk("역할 미선언은 빈 문자열", REF_RE.findall("{{v:해석.a}}")[0][2] == "")

    # ── 골격 — 행렬 · 절별 그림 · 필수 절 부재 표기 ─────────────────────────
    import docspec as DS
    spec = DS.load()

    def _mk(entries):
        return {"run_id": "T", "entries": {e["key"]: e for e in entries}}

    # 요구 명세를 행렬로 편다 — 축 한 칸 + 열 칸들이 **한 줄**이 되는가
    reqs = _requirement_rows({"requirements": {
        "product": "T", "band_ghz": [9.3, 9.5],
        "requirements_spec": {
            "gain_dbi": {"min": 23.0, "max": None, "note": "> 23 dBi"},
            "hbw_deg": {"min": None, "max": 4.0, "axis": "수평(방위)", "note": "< 4.0 deg"},
        }}})
    sk = skeleton(_mk(reqs), spec)
    chk("행렬 머리글이 열 label 로 선다",
        "| 요구 항목 | 하한(이상) | 상한(이하) | 적용 축 | 근거 |" in sk, sk[:400])
    chk("요구 3행이 각각 한 줄", sk.count("|open_item}} |") == 3,
        str(sk.count("|open_item}} |")))
    chk("행렬 칸은 **실제 키**를 쓴다 — 프리즘이 행을 고르지 않는다",
        "{{v:요구.gain_dbi.하한|req_min}}" in sk)
    chk("없는 칸은 대시 — 없는 키를 지어내지 않는다",
        "{{v:요구.gain_dbi.상한|req_max}}" not in sk and "| — |" in sk)
    chk("하한과 상한이 다른 칸에 선다",
        "{{v:요구.hbw_deg.상한|req_max}}" in sk and "{{v:요구.hbw_deg.하한" not in sk)

    # 축이 없는 묶음은 행이 되지 않는다
    orphan = [e for e in reqs if not e["key"].endswith(".항목")]
    chk("축 없는 묶음은 행을 만들지 않는다",
        "req_min}}" not in skeleton(_mk(orphan), spec))
    # 빈 필수 절 — 절마다 찍지 않고 **대장 한 곳**에 모인다
    def _title(sid):
        return next(x["title"] for x in spec["sections"] if x["id"] == sid)

    def _block(txt, sid):
        h = "## " + _title(sid)
        return txt.split(h)[1].split("\n## ")[0] if h in txt else ""

    gapsec = spec["sections"][-1]
    #   제목은 대장의 '절' 칸에 남으므로 **제목 줄**로 확인한다
    chk("빈 필수 절은 본문에서 사라진다", ("## " + _title("시험결과")) not in sk)
    chk("대장 절이 선다", _title(gapsec["id"]) in sk)
    _g = _block(sk, gapsec["id"])
    chk("대장에 빈 절이 한 줄씩 실린다", "시험 결과" in _g and "방사 패턴" in _g, _g[:160])
    chk("대장이 사유를 싣는다", "측정 성적이 없다" in _g)
    chk("대장이 담당을 싣는다", "시험 담당" in _g)
    chk("대장이 **적을 자리**를 싣는다", "측정 성적서" in _g)
    chk("공백 종류를 가른다 — 말하면 되는 것과 기다려야 하는 것",
        "반입" in _g and "도구" in _g, _g[:200])
    chk("빈 절에 서술 슬롯을 만들지 않는다", "PROSE:시험결과." not in sk)
    chk("본문에 '내용 없음' 을 찍지 않는다", "내용 없음" not in sk)
    chk("머리표만 찬 절도 공백으로 본다 — 본체가 비면 빈 것이다",
        "시뮬레이션 결과" in _g, _g[:200])

    # 요약은 재진술이다 — 값을 소비하지 않는다(소비하면 상세 절이 잃는다)
    dup = [_entry("B.lo", "선언 주파수 대역 하한", 9.3, unit="GHz")]
    skd = skeleton(_mk(dup), spec)
    chk("요약이 값을 소비하지 않는다", skd.count("|band_lo_ghz}}") >= 2,
        str(skd.count("|band_lo_ghz}}")))

    # 절별 그림 — 그림이 한 절에 몰리지 않고 제 절에 붙는가
    figs = [_entry("F.iso", "3D 사시", "figures/a.png", role="figure_3d_iso"),
            _entry("F.det", "2D 상세", "figures/b.svg", role="figure_2d_detail")]
    for f in figs:
        f["figure"] = True
    sk2 = skeleton(_mk(figs), spec)
    chk("3D 사시는 요약 preview 에", _block(sk2, "요약").count("figure_3d_iso") == 1,
        _block(sk2, "요약")[:120])
    chk("2D 상세는 시제품 · 도면 절에",
        "figure_2d_detail" in _block(sk2, "시제품"), _block(sk2, "시제품")[:120])
    chk("그림 앞은 빈 줄로 뗀다 — 표가 그림 줄을 삼키지 않는다", "\n\n{{g:" in sk2)
    chk("그림이 있으면 그 절은 대장에 오르지 않는다",
        "시제품 · 도면" not in _block(sk2, gapsec["id"]))

    # ── 결함 F-37 회귀 — 그림을 못 그린 사유가 **대장에 선다**
    #   절은 값으로 차 있는데 그림만 없는 경우가 이 결함의 본체다. 절이 비지 않으므로
    #   기존 대장 규칙으로는 한 줄도 서지 않고, 문서는 그림이 원래 없는 것처럼 보인다.
    fg = dict(_mk(figs))
    fg["figure_gaps"] = [{
        "kind": "cad", "role": "figure_2d_overview", "label": "2D/3D 도면 그림",
        "why": "그릴 CAD 도면(DXF/DWG)이 원천에 없다 — .sab(1건) · .cby(2건). "
               "구제: CST 에서 DXF 또는 STEP 으로 export",
        "종류": "반입", "담당": "설계", "자리": "원천 폴더"}]
    sk3 = skeleton(fg, spec)
    _g3 = _block(sk3, gapsec["id"])
    chk("그림 공백이 대장에 선다", ".cby" in _g3, _g3[:200])
    chk("그림 공백이 붙기로 되어 있던 **절 이름**과 함께 선다",
        "2D/3D 도면 그림" in _g3 and "—" in _g3, _g3[:200])
    chk("그림 공백도 종류·담당·자리를 싣는다",
        "반입" in _g3 and "설계" in _g3 and "원천 폴더" in _g3, _g3[:200])
    chk("그림 공백은 절이 차 있어도 선다",
        "figure_2d_detail" in sk3 and ".cby" in _g3)
    # 사유가 없으면 대장을 억지로 세우지 않는다 — 빈 대장은 소음이다
    chk("사유가 없으면 그림 공백 행도 없다", ".cby" not in _block(sk2, gapsec["id"]))
    # 게이트가 골격을 다시 만들 때 같은 카탈로그를 읽으므로 결과가 같아야 한다
    chk("같은 카탈로그면 골격이 같다 — 게이트가 template_modified 로 오판하지 않는다",
        skeleton(fg, spec) == sk3)

    print(f"\n결과: {ok}/{ok + fail} PASS")
    return 0 if fail == 0 else 1


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd = argv[1]
    if cmd == "self-test":
        return self_test()
    if cmd == "build":
        p = write(argv[2])
        cat = C.read_json(p)
        print(f"{p}  — 항목 {cat['n_entries']}건")
        return 0
    if cmd in ("show", "prompt"):
        run_id = argv[2]
        try:
            cat = load(run_id)
        except Exception:
            cat = build(run_id)
        if cmd == "prompt":
            print(prompt_block(cat))
            return 0
        filt = argv[3] if len(argv) > 3 else ""
        for k, e in cat["entries"].items():
            if filt and filt not in k:
                continue
            print(f"{k:<70} {e['render_with_unit']}")
        return 0
    print(f"알 수 없는 명령: {cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
