#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/route.py — 클래스 「식별」. 파일이 무엇인지만 판정한다. LLM 0콜.

핵심 규칙(2026-07-30 보강): **판독 가능성은 확장자가 아니라 실측으로 정한다.**
  확장자는 "어느 레인을 시도할지" 배정만 한다 → 레인별 **판독 프로브**를 돌려 그림·선언 요약을
  실제로 산출해 보고, 그 실적으로 판독수준과 어댑터를 정한다.

  **어댑터 선택은 배타 선택이 아니다** — 한 원천은 여러 레인으로 동시에 읽힌다.
    주도(primary)      우선순위 표(CST > Gerber > DWG > DXF) 최상위 산출 레인 — 문서 구성의 기준
    기여(contributing) 산출 실적 있는 나머지 레인 — **추출을 계속한다**
    프리뷰 기여        프리뷰만 산출 — 어댑터 자격만 없고 그림은 문서에 쓴다
    탈락(rejected)     산출 실적 0 — 사유만 남긴다
  CST 프로젝트는 선언값(cst 레인)과 임포트 원본 형상(dxf 레인)을 함께 가진다. 하위 레인을
  버리면 그 형상을 영구히 잃는다 — 임포트 원본은 `source_origin="cst-import"` 로 표시해
  같은 안테나의 두 얼굴임을 밝힌다. 선택 권한은 사람(A-1) — 규칙은 기본값을 제시할 뿐이다.

경계: 판독 불가 포맷에 파서를 만들지 않는다(T-1). vendor_srs 는 감싸서만 쓴다(T-5).
출력: work/<run_id>/식별_결과.json · work/<run_id>/probe/<레인>/ (레인별 산출물)
사용: python tools/route.py --source <원천폴더> --run-id <id> [--product <name>]
"""
from __future__ import annotations
import argparse, json, re, sys, traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (ADAPTER_PRIORITY, BOOST_ARCHIVE_MARK, READABILITY, RULE_VERSION,
                     TEXT_PRINTABLE_MIN, TEXT_PROBE_BYTES, numeric_rules, sha256_16,
                     source_fingerprint, vendor, work_dir, write_json)

# ── 레인 배정 (판정이 아니다 — "무엇을 시도할지"만 정한다) ───────────────────
_GERBER_EXT = {".gbr", ".ger", ".gtl", ".gbl", ".gko", ".gm1"}
_LANE_BY_EXT = {".dxf": "dxf", ".dwg": "dwg", **{e: "gerber" for e in _GERBER_EXT}}

# 판독수준 등급과 그것이 산출하는 것. 어댑터 후보 자격은 produces ∈ {geometry, declared}.
_PRODUCES = {"full": "geometry", "declared-only": "declared", "preview-only": "preview",
             "unreadable": None}

NEEDS_HUMAN = ["product", "design_intent", "lifecycle", "mates_with", "spec.source"]


def _try(fn):
    try: return fn(), None
    except Exception as e: return None, f"{type(e).__name__}: {e}"


# ── 판독 프로브 · dxf 레인 ──────────────────────────────────────────────────
def _probe_dxf(cr, cands: list[Path], root: Path, pdir: Path) -> dict:
    """DXF를 실제로 파싱하고 2D 벡터 그림까지 그려 본다. 그림이 나오면 그것이 판독 증거다."""
    lane = {"adapter": "dxf", "attempted": [], "files": {}, "artifacts": {}, "reasons": [],
            "produces": None}
    geoms = {}
    for p in cands:
        rel = str(p.relative_to(root)).replace("\\", "/")
        lane["attempted"].append(rel)
        g, err = _try(lambda: cr.dxf_read(p))
        n = (len(g["polylines"]) + len(g["circles"])) if g else 0
        v, verr = _try(lambda: cr.classify(p, pdir))
        role = (v or {}).get("role")
        rec = {"readability": "full" if n > 0 else "unreadable",
               "format": (v or {}).get("format") or "DXF",
               "role_candidates": ([{"role": role, "confidence": float(v.get("confidence") or 0.0)}]
                                   if role and not str(role).startswith("unknown") else []),
               "evidence": {"entities": n, "polyline": len(g["polylines"]) if g else 0,
                            "circle": len(g["circles"]) if g else 0,
                            "layers": sorted({q["layer"] for q in g["polylines"]}
                                             | {c["layer"] for c in g["circles"]}) if g else [],
                            "bbox_mm": (v or {}).get("bbox_mm")},
               "reasons": ([] if n > 0 else
                           [err or "엔티티 0건 — 파싱은 되었으나 형상이 없다"]) + ((v or {}).get("why") or []),
               "preview": None}
        if verr: rec["reasons"].append(f"classify 실패: {verr}")
        lane["files"][rel] = rec
        if n > 0: geoms[rel] = (p, len(g["polylines"]))

    if geoms:
        lane["produces"] = "geometry"
        def by_role(r):
            return next((rel for rel, rc in lane["files"].items()
                         if (rc["role_candidates"] or [{}])[0].get("role") == r), None)
        # signal/ground 역할이 없으면 폴리라인이 가장 많은 DXF를 대표로 그린다 — 그림은 반드시 나온다.
        top = by_role("pcb-signal-layer") or max(geoms, key=lambda k: geoms[k][1])
        bot = by_role("pcb-ground-plane")
        svg = pdir / "layout_2d.svg"
        _, err = _try(lambda: cr.render_svg(str(geoms[top][0]), str(geoms[bot][0]) if bot else None,
                                           str(svg), title=f"{root.name} (판독 프로브 · 2D 벡터)"))
        if svg.exists():
            lane["artifacts"]["layout_2d_svg"] = str(svg)
            lane["reasons"].append(f"2D 벡터 그림 산출 성공 — 대표 {top}"
                                   + (f" · GND {bot}" if bot else " · GND 없음"))
        else:
            lane["reasons"].append(f"형상은 판독했으나 그림 산출 실패: {err}")
    else:
        lane["reasons"].append("DXF 후보에서 형상을 얻지 못했다")
    return lane


# ── 판독 프로브 · dwg 레인 ──────────────────────────────────────────────────
def _probe_dwg(cr, cands: list[Path], root: Path, pdir: Path) -> dict:
    """DWG는 본문이 압축이라 형상을 못 읽는다. 내장 프리뷰를 실제로 뽑아 보고 그 실적만 남긴다.

    수리 1: vendor classify() 가 r["why"] 에 프리뷰 품질 사유를 넣은 뒤 같은 키를 고정 2줄로
            덮어써 사유를 잃는다. vendor 는 수정하지 않고(T-5) preview_quality·preview_ink 로 복원한다.
    """
    lane = {"adapter": "dwg", "attempted": [], "files": {}, "artifacts": {}, "reasons": [],
            "produces": None}
    got = 0
    for p in cands:
        rel = str(p.relative_to(root)).replace("\\", "/")
        lane["attempted"].append(rel)
        v, err = _try(lambda: cr.classify(p, pdir))
        v = v or {}
        pv = v.get("preview")
        reasons = list(v.get("why") or ([] if not err else [f"classify 실패: {err}"]))
        q, ink = v.get("preview_quality"), v.get("preview_ink")
        if q == "poor":
            reasons.append("내장 프리뷰가 사실상 비어 있음"
                           + (" (잉크 %.2f%%)" % (ink * 100) if isinstance(ink, (int, float)) else "")
                           + " — 뷰어 저장본으로 화면 상태만 담겼다")
        lane["files"][rel] = {
            "readability": "preview-only" if pv else "unreadable",
            "format": v.get("format") or "DWG", "role_candidates": [],
            "evidence": {"preview": pv, "preview_quality": q, "preview_ink": ink,
                         "saved_by": v.get("saved_by"), "geometry": None},
            "reasons": reasons or ["내장 프리뷰를 얻지 못했다"], "preview": pv}
        if pv:
            got += 1
            lane["artifacts"][f"preview::{rel}"] = pv
    # 프리뷰는 그림이지 형상이 아니다 → produces 는 preview 로 남고 어댑터 후보 자격이 없다.
    lane["produces"] = "preview" if got else None
    lane["reasons"].append(f"프리뷰 {got}/{len(cands)}건 산출 · 형상 판독 0건"
                           " — DWG 본문 압축(ODA 변환 없이는 벡터 없음, T-1)")
    return lane


# ── 판독 프로브 · cst 레인 ──────────────────────────────────────────────────
def _probe_cst(root: Path, containers: list[dict], pdir: Path) -> dict:
    """CST 바이너리는 건드리지 않고, ASCII 선언 파일만 실제로 읽어 선언 요약을 산출한다."""
    lane = {"adapter": "cst", "attempted": [], "files": {}, "artifacts": {}, "reasons": [],
            "produces": None}
    summary = []
    for c in containers:
        proj = root / c["path"] if c["path"] != "." else root
        got = {}
        prj = proj / "Model" / "Model.prj"
        if prj.exists():
            j, _ = _try(lambda: json.loads(prj.read_text(encoding="utf-8", errors="replace")))
            if j: got["project"] = {k: j.get(k) for k in ("Last Solver Type", "Mesh Cells", "Results")}
        hist = proj / "Model" / "3D" / "ModelHistory.json"
        if hist.exists():
            j, _ = _try(lambda: json.loads(hist.read_text(encoding="utf-8", errors="replace")))
            if j:
                got["general"] = (j.get("general") or {})
                txt = json.dumps(j, ensure_ascii=False)
                got["n_frequency_range_decl"] = len(
                    re.findall(r'Solver\.FrequencyRange\s+"[^"]+"\s*,\s*"[^"]+"', txt))
                got["n_history"] = len(j.get("history") or [])
        par = proj / "Model" / "Parameters.json"
        if par.exists():
            j, _ = _try(lambda: json.loads(par.read_text(encoding="utf-8", errors="replace")))
            if j: got["n_parameters"] = len(j.get("parameters") or [])
        lane["attempted"].append(c["path"])
        ok = bool(got.get("general") or got.get("project"))
        summary.append({"container": c["name"], "path": c["path"], "read": got, "ok": ok})
        lane["reasons"].append(f"{c['name']}: 선언 판독 {'성공' if ok else '실패'} · "
                               f"이력 {got.get('n_history', 0)}항 · "
                               f"FrequencyRange 선언 {got.get('n_frequency_range_decl', 0)}회")
    if any(s["ok"] for s in summary):
        lane["produces"] = "declared"
        f = pdir / "선언요약.json"
        f.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        lane["artifacts"]["선언요약_json"] = str(f)
        lane["reasons"].append("그림 없음 — CST 형상은 바이너리다. 선언 요약이 이 레인의 산출물이다")
    return lane


# ── 판독 프로브 · gerber 레인 ───────────────────────────────────────────────
def _probe_gerber(cands: list[Path], root: Path) -> dict:
    lane = {"adapter": "gerber", "attempted": [str(p.relative_to(root)).replace("\\", "/") for p in cands],
            "files": {}, "artifacts": {}, "produces": None,
            "reasons": ["Gerber 파서 미반입 — 판독 시도 없이 판독 불가로 기록한다(T-1: 파서를 만들지 않는다)"]}
    for p in cands:
        rel = str(p.relative_to(root)).replace("\\", "/")
        lane["files"][rel] = {"readability": "unreadable", "format": "Gerber", "role_candidates": [],
                              "evidence": {}, "reasons": ["Gerber 파서 미반입"], "preview": None}
    return lane


# ── 레인 밖 파일: 확장자가 아니라 실제 읽어 보고 판정한다 ────────────────────
def _probe_plain(p: Path) -> dict:
    b, err = _try(lambda: p.read_bytes())
    if b is None:
        return {"readability": "unreadable", "format": "읽기 실패", "role_candidates": [],
                "evidence": {}, "reasons": [err or "읽기 실패"], "preview": None}
    j, _ = _try(lambda: json.loads(b.decode("utf-8", errors="strict")))
    if isinstance(j, (dict, list)):
        return {"readability": "declared-only", "format": "JSON 선언", "role_candidates": [],
                "evidence": {"json_top_keys": (list(j)[:8] if isinstance(j, dict) else f"list[{len(j)}]")},
                "reasons": ["json.loads 성공 — 선언값 판독 가능"], "preview": None}
    head = b[:TEXT_PROBE_BYTES]
    txt = head.decode("utf-8", errors="replace")
    printable = sum(1 for ch in txt if ch.isprintable() or ch in "\r\n\t")
    ratio = printable / max(len(txt), 1)
    nul = b.count(0)
    first = (txt.splitlines() or [""])[0][:120]
    ev = {"printable_ratio": round(ratio, 3), "nul_bytes": nul, "size": len(b), "first_line": first}
    # NUL 바이트가 하나라도 있으면 텍스트 비율과 무관하게 바이너리다 — 앞부분만 ASCII인 포맷에 속지 않는다.
    if nul:
        return {"readability": "unreadable", "format": "바이너리", "role_candidates": [],
                "evidence": ev,
                "reasons": [f"NUL 바이트 {nul}개({nul / len(b):.1%}) — 바이너리다. "
                            f"파서를 만들지 않는다(T-1)"], "preview": None}
    if ratio >= TEXT_PRINTABLE_MIN:
        # 읽히지만 스키마가 공개되지 않은 직렬화 아카이브는 "읽힘 ≠ 파싱 가능"으로 분리한다.
        opaque = BOOST_ARCHIVE_MARK in txt
        return {"readability": "declared-only",
                "format": "직렬화 아카이브(스키마 미공개)" if opaque else "ASCII 선언/로그",
                "role_candidates": [], "evidence": dict(ev, schema_documented=not opaque),
                "reasons": [f"텍스트 비율 {ratio:.1%} ≥ 임계 {TEXT_PRINTABLE_MIN:.0%} — ASCII 로 판독"] +
                           (["boost 직렬화 아카이브 — 텍스트지만 필드 스키마가 공개되지 않았다. "
                             "값을 추출하지 않는다(T-1)"] if opaque else []),
                "preview": None}
    return {"readability": "unreadable", "format": "바이너리", "role_candidates": [], "evidence": ev,
            "reasons": [f"텍스트 비율 {ratio:.1%} < 임계 {TEXT_PRINTABLE_MIN:.0%} — 바이너리다. "
                        f"파서를 만들지 않는다(T-1)"], "preview": None}


# ── 판독 프로브 비교 화면 (자기완결 HTML · 외부 JS/CSS 0) ────────────────────
def _probe_report(root: Path, lanes: list, chosen, adapter, rejected: list, out: Path) -> Path:
    """레인별 산출물을 한 화면에 나란히 놓아 **그림을 보고 고를 수 있게** 한다.

    탈락 레인도 산출물과 사유를 함께 보인다 — orch_inventory_contract 의 rejected 요구(빈 화면 금지).
    이미지는 data URL 로 인라인해 파일 하나로 옮겨도 깨지지 않게 한다(폐쇄망 · 외부 의존 0).
    """
    import base64, html as H

    def embed(p: Path):
        ext = p.suffix.lower()
        if ext == ".svg":
            return f'<div class="art">{p.read_text(encoding="utf-8", errors="replace")}</div>'
        if ext in (".png", ".bmp", ".jpg", ".jpeg"):
            mime = {"png": "image/png", "bmp": "image/bmp"}.get(ext[1:], "image/jpeg")
            b64 = base64.b64encode(p.read_bytes()).decode("ascii")
            return f'<div class="art"><img src="data:{mime};base64,{b64}" alt="{H.escape(p.name)}"></div>'
        if ext == ".json":
            t = p.read_text(encoding="utf-8", errors="replace")
            return f'<pre>{H.escape(t[:4000])}{"…" if len(t) > 4000 else ""}</pre>'
        return f'<p class="muted">{H.escape(p.name)} — 화면 표시 형식 아님</p>'

    rej = {j["adapter"]: j for j in rejected}
    cards = []
    for l in lanes:
        picked = (l is chosen)
        badge = ('<span class="b ok">채택</span>' if picked else
                 f'<span class="b no">탈락</span>')
        why = "" if picked else f'<p class="why">{H.escape(rej.get(l["adapter"], {}).get("reason", ""))}</p>'
        arts = "".join(embed(Path(v)) for v in l["artifacts"].values() if Path(v).exists()) \
               or '<p class="muted">산출물 없음</p>'
        cards.append(f"""<section class="card{' pick' if picked else ''}">
<h2>레인 {H.escape(l['adapter'])} {badge}</h2>
<p class="meta">산출: <b>{H.escape(str(l['produces'] or '없음'))}</b> ·
 시도 {len(l['attempted'])}건 · 산출물 {len(l['artifacts'])}건</p>{why}
{arts}
<ul class="rs">{''.join(f'<li>{H.escape(str(x))}</li>' for x in l['reasons'])}</ul>
<details><summary>시도한 파일 {len(l['attempted'])}건</summary>
<ul class="fs">{''.join(f'<li>{H.escape(x)}</li>' for x in l['attempted'])}</ul></details>
</section>""")

    doc = f"""<!doctype html><html lang="ko"><meta charset="utf-8">
<title>판독 프로브 비교 — {H.escape(root.name)}</title><style>
:root{{--ink:#1f2933;--mut:#667585;--line:#dde3e8;--ok:#3f7d5a;--no:#b05252;--bg:#f5f7f9}}
*{{box-sizing:border-box}}body{{margin:0;padding:22px;background:var(--bg);color:var(--ink);
font:14px/1.55 "Malgun Gothic","Apple SD Gothic Neo",system-ui,sans-serif}}
h1{{font-size:17px;margin:0 0 4px}}h2{{font-size:14px;margin:0 0 6px}}
.rule{{color:var(--mut);font-size:12px;margin:0 0 16px;max-width:76ch}}
.wrap{{display:flex;gap:14px;flex-wrap:wrap;align-items:flex-start}}
.card{{background:#fff;border:1px solid var(--line);border-radius:8px;padding:14px;flex:1 1 380px;
min-width:340px}}.card.pick{{border-color:var(--ok);box-shadow:0 0 0 2px #3f7d5a22}}
.b{{font-size:11px;padding:1px 7px;border-radius:9px;vertical-align:2px}}
.b.ok{{background:#dcefe2;color:var(--ok)}}.b.no{{background:#eef0f2;color:var(--mut)}}
.meta,.why{{font-size:12px;color:var(--mut);margin:0 0 8px}}.why{{color:var(--no)}}
.art{{border:1px solid var(--line);border-radius:6px;overflow:auto;background:#e9ecef;margin:8px 0;
max-height:340px}}.art svg,.art img{{display:block;max-width:100%;height:auto}}
pre{{background:#f0f3f5;border:1px solid var(--line);border-radius:6px;padding:8px;font-size:11px;
max-height:260px;overflow:auto}}
.rs,.fs{{font-size:12px;color:var(--mut);margin:8px 0 0;padding-left:18px}}
summary{{font-size:12px;color:var(--mut);cursor:pointer;margin-top:8px}}
</style>
<h1>판독 프로브 비교 — {H.escape(root.name)} · 어댑터 판정 <b>{H.escape(str(adapter))}</b></h1>
<p class="rule">규칙: 확장자로 판정하지 않는다. 레인마다 실제로 읽고 그려 본 뒤,
우선순위 표(CST &gt; Gerber &gt; DWG &gt; DXF) 최상위 레인 중 <b>형상 또는 선언값을 산출한</b> 레인을
어댑터로 삼는다. 프리뷰만 나온 레인은 탈락하되 산출물은 남긴다.
최종 선택 권한은 사람이다 — 이 화면은 그 판단의 근거다.</p>
<div class="wrap">{''.join(cards)}</div></html>"""
    out.write_text(doc, encoding="utf-8")
    return out


def _cst_containers(root: Path) -> list[dict]:
    out = []
    for prj in sorted(root.rglob("Model.prj")):
        proj = prj.parent.parent
        signals = [f"Model.prj 존재 → CST 프로젝트({proj.name})",
                   f"Result/Model.res {'존재' if (proj / 'Result' / 'Model.res').exists() else '없음'}",
                   f"Model/3D/ModelHistory.json "
                   f"{'존재' if (proj / 'Model' / '3D' / 'ModelHistory.json').exists() else '없음'}"]
        out.append({"kind": "cst-project", "name": proj.name,
                    "path": str(proj.relative_to(root)).replace("\\", "/") or ".",
                    "signals": signals})
    return out


# ── 본체 ────────────────────────────────────────────────────────────────────
def identify(source_path, run_id: str, product: str | None = None) -> dict:
    cr = vendor()
    root = Path(source_path).resolve()
    if not root.exists(): raise FileNotFoundError(f"원천 경로 없음: {root}")
    wd = work_dir(run_id)
    probe_root = wd / "probe"

    allfiles = sorted([q for q in root.rglob("*") if q.is_file()], key=lambda q: str(q).lower())
    by_lane: dict[str, list[Path]] = {}
    for p in allfiles:
        ln = _LANE_BY_EXT.get(p.suffix.lower())
        if ln: by_lane.setdefault(ln, []).append(p)
    containers = _cst_containers(root)

    def pdir(name):
        d = probe_root / name; d.mkdir(parents=True, exist_ok=True); return d

    lanes = []
    if containers: lanes.append(_probe_cst(root, containers, pdir("cst")))
    if by_lane.get("gerber"): lanes.append(_probe_gerber(by_lane["gerber"], root))
    if by_lane.get("dwg"): lanes.append(_probe_dwg(cr, by_lane["dwg"], root, pdir("dwg")))
    if by_lane.get("dxf"): lanes.append(_probe_dxf(cr, by_lane["dxf"], root, pdir("dxf")))

    # ── 어댑터 선택: 주도 레인 1 + 기여 레인 N ───────────────────────────────
    # 배타 선택이 아니다. 한 원천은 여러 레인으로 동시에 읽힌다 — CST 프로젝트는 선언값(cst)과
    # 임포트 원본 형상(dxf)을 함께 가진다. 하위 레인을 버리면 그 형상을 영구히 잃는다.
    #   주도(primary)  = 우선순위 최상위 산출 레인 — 문서 구성·라우팅의 기준
    #   기여(contributing) = 산출 실적(형상·선언값) 있는 나머지 레인 — **추출 대상이다**
    #   프리뷰 기여    = 프리뷰만 산출 — 어댑터 자격 없으나 그림은 문서에 쓴다
    #   탈락(rejected) = 산출 실적 0 — 사유만 남긴다
    eligible = [l for l in lanes if l["produces"] in ("geometry", "declared")]
    order = {a: i for i, a in enumerate(ADAPTER_PRIORITY)}
    eligible.sort(key=lambda l: order.get(l["adapter"], 99))
    chosen = eligible[0] if eligible else None
    adapter = chosen["adapter"] if chosen else None
    for l in lanes:
        l["role"] = ("주도" if l is chosen else
                     "기여" if l["produces"] in ("geometry", "declared") else
                     "프리뷰 기여" if l["produces"] == "preview" else "탈락")
    contributing = [l["adapter"] for l in lanes if l["role"] == "기여"]
    rejected = [{"adapter": l["adapter"], "produces": l["produces"], "role": l["role"],
                 "reason": ("판독 실적 없음(형상·선언값 0) — 어댑터 후보 자격 없음, 추출 대상 아님"
                            if l["produces"] is None else
                            "프리뷰만 산출 — 어댑터 자격 없음. 프리뷰 이미지는 문서에 쓴다"
                            if l["produces"] == "preview" else
                            f"우선순위 하위 — 주도는 {adapter}. **추출은 계속한다(기여 레인)**"),
                 "artifacts": l["artifacts"], "reasons": l["reasons"],
                 "attempted": l["attempted"]}
                for l in lanes if l is not chosen]

    report = _probe_report(root, lanes, chosen, adapter, rejected,
                           probe_root / "판독프로브_비교.html") if lanes else None

    # ── 파일 레코드 병합 (판독수준은 프로브 실측값) ──────────────────────────
    from_lane = {}
    for l in lanes:
        for rel, rec in l["files"].items():
            from_lane[rel] = dict(rec, lane=l["adapter"])
    # CST 프로젝트가 선언한 임포트 원본과 실제 파일을 대조한다 — 그 파일은 독립 도면이 아니라
    # CST 모델의 원천이다. 표시해 두면 「추출」이 형상과 선언을 같은 안테나로 묶을 수 있다.
    imported = {}
    for c in containers:
        proj = root / c["path"] if c["path"] != "." else root
        hist = proj / "Model" / "3D" / "ModelHistory.json"
        if not hist.exists(): continue
        txt, _ = _try(lambda: hist.read_text(encoding="utf-8", errors="replace"))
        for m in re.findall(r'\.(?:FileName|SourceFileName)\s+"\*?([^"]+)"', txt or ""):
            imported[Path(m.replace("\\", "/")).name.lower()] = {"container": c["name"], "declared_as": m}

    files, unreadable = [], []
    for p in allfiles:
        rel = str(p.relative_to(root)).replace("\\", "/")
        rec = from_lane.get(rel) or dict(_probe_plain(p), lane="선언/기타")
        rec.update({"rel": rel, "path": str(p), "size": p.stat().st_size,
                    "sha256_16": sha256_16(p), "needs_human": list(NEEDS_HUMAN),
                    "판정_근거": "판독 프로브 실측 — 확장자 판정 아님"})
        # 임포트 원본 매칭: 정확 이름 또는 CST가 붙이는 `_N` 접미(KORIL_2.5.dxf → KORIL_2.5_1.dxf)
        low = p.name.lower()
        hit = imported.get(low) or next(
            (v for k, v in imported.items()
             if re.sub(r"_\d+(\.\w+)$", r"\1", low) == k or re.sub(r"_\d+(\.\w+)$", r"\1", k) == low), None)
        if hit:
            rec["source_origin"] = "cst-import"
            rec["source_origin_detail"] = dict(hit, matched_by="ModelHistory 임포트 선언과 파일명 대조")
            rec.setdefault("reasons", []).append(
                f"CST 프로젝트 {hit['container']} 의 임포트 원본으로 선언됨 — 독립 도면이 아니다")
            if not rec.get("role_candidates"):
                rec["role_candidates"] = [{"role": "cst-import-source", "confidence": 1.0,
                                           "근거": "ModelHistory 임포트 선언(추측 아님)"}]
        rec.setdefault("confidence", (rec.get("role_candidates") or [{}])[0].get("confidence", 0.0))
        assert rec["readability"] in READABILITY, rec["readability"]
        if rec["readability"] == "unreadable":
            unreadable.append({"rel": rel, "format": rec["format"],
                               "reason": rec["reasons"][0] if rec["reasons"] else "사유 미기재"})
        files.append(rec)

    signals = [f"레인 배정(확장자 기준, 판정 아님): "
               f"{ {k: len(v) for k, v in by_lane.items()} } + CST 컨테이너 {len(containers)}",
               "판독 프로브 실적: " + " · ".join(f"{l['adapter']}→{l['produces'] or '없음'}" for l in lanes),
               f"어댑터 규칙: 우선순위 {' > '.join(ADAPTER_PRIORITY)} 중 형상·선언값을 산출한 레인 "
               f"→ {adapter or '판정 불가'}"]
    for c in containers: signals += c["signals"]

    res = {"run_id": run_id, "rule_version": RULE_VERSION,
           "source": {"path": str(root), "kind": "folder" if root.is_dir() else "file",
                      "name": root.name},
           "product": product or "미배정",
           "adapter": adapter,
           "adapter_rule": "우선순위 표(CST>Gerber>DWG>DXF) 최상위 레인 중, 판독 프로브에서 "
                           "형상(geometry) 또는 선언값(declared)을 실제로 산출한 레인. "
                           "프리뷰만 나온 레인은 탈락. 최종 선택 권한은 사람(A-1)",
           "adapter_candidates": [l["adapter"] for l in eligible],
           "contributing_lanes": contributing,
           "source_origin": contributing + [l["adapter"] for l in lanes if l["produces"] == "preview"],
           "lanes": [{k: l[k] for k in ("adapter", "role", "produces", "attempted", "artifacts",
                                        "reasons")} for l in lanes],
           "rejected": rejected,
           "probe_report": str(report) if report else None,
           "signals": signals, "containers": containers, "files": files, "unreadable": unreadable,
           "numeric_rules": numeric_rules(),   # 판정에 쓴 규칙 상수 — 원장 기록·재현용
           "outcome": "ROUTE_OK" if adapter else "HOLD(판독 실적 있는 레인 없음)",
           "source_fingerprint_before": source_fingerprint(root)}
    write_json(wd / "식별_결과.json", res)
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description="식별 — 판독 프로브 실측으로 어댑터를 정한다")
    ap.add_argument("--source", required=True)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--product", default=None)
    a = ap.parse_args(argv)
    r = identify(a.source, a.run_id, a.product)
    lv = {f["readability"] for f in r["files"]}
    cnt = {k: sum(1 for f in r["files"] if f["readability"] == k) for k in sorted(lv)}
    print(f"식별: 어댑터={r['adapter']} · 파일 {len(r['files'])}건 {cnt} · 컨테이너 {len(r['containers'])}")
    print(f"  주도={r['adapter']} · 기여={r['contributing_lanes'] or '없음'}")
    for l in r["lanes"]:
        print(f"  레인 {l['adapter']} [{l['role']}]: 산출={l['produces'] or '없음'} · "
              f"산출물 {len(l['artifacts'])}건")
        for a2 in l["artifacts"].values(): print(f"      {a2}")
    for j in r["rejected"]: print(f"  탈락 {j['adapter']}: {j['reason']}")
    for u in r["unreadable"]: print(f"  판독 불가: {u['rel']} — {u['reason']}")
    print(f"산출: {work_dir(a.run_id, False) / '식별_결과.json'}")
    return 0 if r["adapter"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
