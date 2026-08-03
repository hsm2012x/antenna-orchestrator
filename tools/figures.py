#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/figures.py — 시각 근거를 만들고 **카탈로그 항목으로** 낸다 (LLM 0콜)

무엇을 하나
    run 의 원천에서 도안을 렌더하고(`render_page.py`), 그 결과를 **값 카탈로그와 같은 모양의
    항목**으로 만든다. 문서는 그림을 `{{g:키}}` 로 참조하고, 게이트가 결정론으로 치환한다.

★ 왜 그림도 참조인가 — 숫자와 같은 이유다 (D-22)
    문서가 그림 **파일 경로를 직접 쓰면** 참조 바인딩이 깨진다. 경로가 바뀌어도 문서는
    모르고, 없는 그림을 가리켜도 게이트가 못 잡는다. 그림을 항목으로 만들면
    `undefined_key` · `empty_value` · `role_mismatch` · `unsubstituted_ref` 검사를
    **그대로 물려받는다** — 게이트에 새 규칙을 거의 만들지 않아도 된다.

★ 캡션을 LLM 이 쓰지 않는다
    캡션에는 축척 · 단위 산지 · **치수 정본 여부**가 들어간다. 이것을 프리즘이 쓰게 두면
    "3D 그림 밑에 치수를 적는" 사고가 난다. 캡션은 `g` 시길이 **결정론으로** 만든다.

산출
    work/<run_id>/figures/*.png · *.svg      렌더 산출
    work/<run_id>/그림_결과.json              항목 목록 + 실패 사유

CLI
    python tools/figures.py build <run_id>
    python tools/figures.py of    <run_id>
    python tools/figures.py self-test
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

FIG_DIR = "figures"
RESULT_NAME = "그림_결과.json"

# 그림 종류 → (역할, 이름, 치수 정본인가, **정본이 아니면 어디를 보라고 할 것인가**)
#
# 마지막 칸이 중요하다 — "치수 정본 아님"만 적고 끝내면 사람이 어디서 읽어야 할지 모른다.
# ★ DWG 는 2D 도면으로 보내면 안 된다. 본문이 압축이라 **그 2D 도면이 존재하지 않는다**
#   (결함 F-28). 없는 곳으로 보내는 안내는 안내가 아니다.
KINDS = {
    "2d_overview": ("figure_2d_overview", "2D 전체도", True,
                    "벡터 도면 — 축척이 실려 있다"),
    "2d_detail":   ("figure_2d_detail", "2D 상세", True,
                    "벡터 도면 — 원척 이상으로 나눠 그린다"),
    "3d_iso":      ("figure_3d_iso", "3D 사시", False,
                    "치수 정본 아님 — 투영 + z 확대. 치수는 2D 도면에서 읽는다"),
    "3d_top":      ("figure_3d_top", "3D 평면", False,
                    "치수 정본 아님 — 투영. 치수는 2D 도면에서 읽는다"),
    "dwg_preview": ("figure_dwg_preview", "DWG 내장 프리뷰", False,
                    "치수 정본 없음 — DWG 본문이 압축이라 형상을 못 읽는다. "
                    "잴 수 있는 도면이 아직 없다(ODA 변환기 반입 필요)"),
    # ★ 배열인자는 **빔패턴이 아니다.** 캡션이 그렇게 말한다 — 그림만 보고
    #   "빔이 이렇게 생겼구나" 하고 읽는 것을 막을 수 있는 자리는 캡션뿐이다.
    "array_factor": ("figure_array_factor", "배열인자 곡선", False,
                     "치수 정본 아님 — **빔패턴이 아니다.** 소자 하나의 방사 패턴을 "
                     "곱하지 않은 배열 항뿐이고, 균일 여자를 가정했다(I-L)"),
    "stackup": ("figure_stackup", "스택업 단면", False,
                "치수 정본 아님 — 층 구성을 **읽기 쉽게** 그린 모식도다. "
                "두께 비율은 과장되어 있고 치수는 스택업 표에서 읽는다"),
}

_TOP = re.compile(r"(?:^|[_\-])top(?:$|[_\-])", re.I)
_BOT = re.compile(r"(?:^|[_\-])(?:bot|bottom)(?:$|[_\-])", re.I)


# ── 원천에서 렌더 대상 고르기 ───────────────────────────────────────────────

def pick_targets(files: list[dict]) -> dict:
    """DXF 는 top/bottom 짝으로, DWG 는 프리뷰 대상으로. **짝을 지어내지 않는다**.

    짝짓기 근거는 파일 이름의 `top`/`bottom` 표식과 **나머지 부분이 같다**는 것 둘 다다.
    표식만 보고 아무거나 묶으면 서로 다른 도면이 한 장에 겹쳐 그려진다.
    """
    dxf = [f for f in files if str(f.get("rel", "")).lower().endswith(".dxf")]
    dwg = [f for f in files if str(f.get("rel", "")).lower().endswith(".dwg")]

    def stem_wo_side(p):
        s = Path(p).stem
        return _BOT.sub("_", _TOP.sub("_", s)).strip("_-").lower()

    pairs, used = [], set()
    for f in dxf:
        if f["rel"] in used or not _TOP.search(Path(f["rel"]).stem):
            continue
        base = stem_wo_side(f["rel"])
        mate = next((g for g in dxf if g["rel"] not in used and g["rel"] != f["rel"]
                     and _BOT.search(Path(g["rel"]).stem) and stem_wo_side(g["rel"]) == base), None)
        used.add(f["rel"])
        if mate:
            used.add(mate["rel"])
        pairs.append({"top": f["path"], "bottom": mate["path"] if mate else None,
                      "why": ("이름의 top/bottom 표식 + 나머지 부분 일치"
                              if mate else "top 만 있다 — 단독으로 그린다")})
    for f in dxf:                       # 짝을 못 지은 나머지는 단독
        if f["rel"] not in used:
            pairs.append({"top": f["path"], "bottom": None, "why": "짝 표식 없음 — 단독"})
    return {"dxf_pairs": pairs, "dwg": [f["path"] for f in dwg]}


# ── 항목 만들기 ─────────────────────────────────────────────────────────────

def _entry(kind: str, key: str, path: Path, out_dir: Path, source: str,
           scale_text: str, extra: dict | None = None) -> dict:
    role, label_base, dimensional, why = KINDS[kind]
    label = f"{label_base}{extra.get('label_suffix', '') if extra else ''}"
    rel = str(path.relative_to(out_dir.parent)).replace("\\", "/")
    # 결함 F-29 — `antenna reflector.dwg` 처럼 이름에 공백이 있으면 마크다운 이미지
    # 경로가 거기서 끊긴다. 링크가 조용히 깨지므로 미리 인코딩해 둔다.
    md_path = rel.replace(" ", "%20")
    cap = [label, scale_text, C.UNIT_NOTE if hasattr(C, "UNIT_NOTE") else ""]
    cap = [c for c in cap if c]
    cap.append("치수 정본" if dimensional else why)
    return {
        "key": key, "role": role, "quantity": "figure", "unit": "",
        "label": label,
        "render": md_path, "render_with_unit": md_path,
        "source": source,
        "formula": "", "reason": "",
        # 그림 고유 — 게이트가 캡션을 만들 때 쓴다
        "figure": {"kind": kind, "path": md_path, "fs_path": rel, "alt": label,
                   "caption": " · ".join(cap), "scale_text": scale_text,
                   "dimensional": dimensional, "why": why,
                   **(extra or {})},
    }


def wd_render(work: Path) -> Path:
    """렌더 레인의 산출 폴더. 값 그림은 거기서 이미 그려져 있다(중복 계산 금지)."""
    return Path(work) / "render"


# 층 색 — 재질 종류가 아니라 **역할**로 가른다(도체 / 유전체 / 금속판).
_LAYER_STYLE = {
    "도체": ("#c8862a", "#8a5a12"),
    "기판": ("#3f7d5a", "#20503a"),
    "반사판": ("#8a8f96", "#4d5257"),
}


def draw_stackup(run_id: str, work: Path, out_dir: Path) -> Path | None:
    """선언된 층 구성을 **모식도**로 그린다. 값을 만들지 않는다 — 있는 값을 배치할 뿐이다.

    왜 필요한가 — 스택업은 표로 읽으면 층의 **순서와 상대 두께**가 안 보인다.
    숫자 다섯 줄보다 그림 한 장이 "위에서부터 무엇이 쌓여 있나"를 먼저 알려 준다.

    ★ 두께 비율은 과장한다. 기판이 도체보다 수십 배 두꺼워 실제 비율로 그리면 도체가
      선 한 줄이 되어 보이지 않는다. 그래서 **치수 정본이 아니다**라고 캡션이 말한다.
    """
    ver = C.read_json(work / "해석_결과.json") if (work / "해석_결과.json").exists() else {}
    req = ver.get("requirements") or {}
    sub = req.get("substrate") or {}
    stk = req.get("stackup") or {}
    ref = req.get("reflector") or {}
    if not (sub.get("name") or sub.get("er") or sub.get("h_mm")):
        return None                     # 선언이 없으면 그리지 않는다(빈 그림을 만들지 않는다)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    # 라벨은 ASCII — 폐쇄망 Windows/VM 에 한글 폰트가 없으면 두부(□)가 찍힌다.
    n_layer = stk.get("layer_count")

    def _ascii_ok(v) -> bool:
        """matplotlib 이 그릴 수 있는 글자인가.

        ★ 폐쇄망 Windows/VM 에는 한글 폰트가 없어 한글이 **두부(□)로 찍힌다.**
          두부는 값이 틀린 것보다 나쁘다 — 읽는 사람이 무엇이 적혔는지조차 모른다.
          그릴 수 없는 값은 **그리지 않고 표를 가리킨다.** 값의 정본은 어차피 표다.
        """
        try:
            str(v).encode("ascii")
            return True
        except UnicodeEncodeError:
            return False

    def _bit(prefix, v, suffix=""):
        if v in (None, ""):
            return ""
        return f"{prefix}{v}{suffix}" if _ascii_ok(v) else f"{prefix}(see table){suffix}"

    top = "Top copper" + _bit(" (", stk.get("copper_oz"), " oz)") \
        + _bit(" / ", stk.get("surface_finish"))
    mid = (sub.get("name") or "substrate") + _bit("  er=", sub.get("er")) \
        + _bit("  h=", sub.get("h_mm"), " mm")
    rows = [("도체", top, 0.10), ("기판", mid, 0.55), ("도체", "Bottom copper", 0.10)]
    if ref.get("material") or ref.get("thickness_mm"):
        mat = ref.get("material")
        rows.append(("반사판",
                     "Reflector: " + (str(mat) if _ascii_ok(mat) and mat else "(see table)")
                     + _bit("  t=", ref.get("thickness_mm"), " mm"), 0.25))

    fig, ax = plt.subplots(figsize=(7.2, 0.9 * len(rows) + 1.1), dpi=200)
    y = 0.0
    for kind, text, h in reversed(rows):
        fc, ec = _LAYER_STYLE[kind]
        ax.add_patch(Rectangle((0, y), 1.0, h, facecolor=fc, edgecolor=ec, linewidth=1.2))
        ax.text(1.03, y + h / 2, text, va="center", ha="left", fontsize=9)
        y += h + 0.06
    ax.set_xlim(0, 2.3); ax.set_ylim(-0.05, y)
    ax.axis("off")
    ax.set_title(f"Stack-up (schematic, thickness exaggerated)"
                 + (f" — {n_layer} conductor layers" if n_layer else ""), fontsize=10)
    out = Path(out_dir) / "stackup.png"
    fig.savefig(out, bbox_inches="tight")
    plt.close(fig)
    return out


def build(run_id: str, work: Path | None = None) -> dict:
    """원천을 렌더하고 그림 항목을 만든다. **실패는 실패로 남긴다**(T-4)."""
    import render_page as RP
    import dwgmeta as DM

    work = Path(work) if work else C.work_dir(run_id, create=True)
    out_dir = work / FIG_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    ident = C.read_json(work / "식별_결과.json")
    files = ident.get("files") or []
    tg = pick_targets(files)

    entries, failed = [], []

    for i, pr in enumerate(tg["dxf_pairs"]):
        tag = Path(pr["top"]).stem
        src = " + ".join(Path(p).name for p in (pr["top"], pr["bottom"]) if p)
        try:
            r2 = RP.render_doc_2d(pr["top"], pr["bottom"], out_dir, dpi=300)
        except Exception as e:
            failed.append({"target": src, "stage": "2d", "why": f"{type(e).__name__}: {e}"[:200]})
            continue
        for sh in r2["sheets"]:
            kind = "2d_overview" if sh["kind"] == "overview" else "2d_detail"
            suffix = "" if kind == "2d_overview" else f" {sh['i']}/{len(r2['sheets'])-1}"
            scale = (f"축척 1:{1/sh['scale']:.2f}" if sh["scale"] < 0.999
                     else f"축척 {sh['scale']:.2f}:1")
            key = f"그림.{tag}.{sh['kind']}{sh['i'] or ''}"
            entries.append(_entry(kind, key, Path(sh["png"]), out_dir,
                                  f"tools/render_page.py render_doc_2d · {src}",
                                  scale, {"label_suffix": suffix, "svg": sh["svg"],
                                          "x_mm": sh.get("x_mm")}))
        try:
            r3 = RP.render_doc_3d(pr["top"], pr["bottom"], out_dir, dpi=300)
        except Exception as e:
            failed.append({"target": src, "stage": "3d", "why": f"{type(e).__name__}: {e}"[:200]})
            continue
        for v in r3["views"]:
            kind = f"3d_{v['view']}"
            if kind not in KINDS:
                continue
            entries.append(_entry(
                kind, f"그림.{tag}.3d_{v['view']}", Path(v["png"]), out_dir,
                f"tools/render_page.py render_doc_3d · {src}",
                "투영 — 축척 없음",
                {"svg": v["svg"], "view": v["view"], "yaw_deg": v["yaw_deg"],
                 "pitch_deg": v["pitch_deg"], "z_exaggerate": v["z_exaggerate"]}))

    # DWG — 형상은 못 읽는다. 파일이 스스로 담은 프리뷰만 꺼낸다(T-1)
    for p in tg["dwg"]:
        prev = DM.dwg_preview(p, out_dir)
        if not prev:
            failed.append({"target": Path(p).name, "stage": "dwg_preview",
                           "why": ("내장 프리뷰가 없다 — 본문은 압축이라 형상을 못 읽는다. "
                                   "형상이 필요하면 ODA 변환기 반입(EXT-1)")})
            continue
        entries.append(_entry("dwg_preview", f"그림.{Path(p).stem}.preview",
                              Path(prev), out_dir,
                              f"tools/dwgmeta.py dwg_preview · {Path(p).name}",
                              "축척 없음 — 내장 프리뷰"))

    # ── 렌더 레인이 이미 그린 것 · 여기서 그리는 것 ─────────────────────────
    # 값 그림(배열인자·스택업)은 도면이 아니다. 도면이 없어도 값이 있으면 그린다 —
    # 그래야 "형상은 못 읽었지만 재질은 안다"가 문서에 **그림으로** 남는다.
    af = wd_render(work) / "array_factor.png"
    if af.exists():
        entries.append(_entry("array_factor", "그림.배열인자", af, out_dir,
                              "tools/render.py — 해석이 계산한 곡선을 그리기만 한다",
                              "가로축 각도 · 세로축 상대 레벨"))
    try:
        sp = draw_stackup(run_id, work, out_dir)
        if sp:
            entries.append(_entry("stackup", "그림.스택업", sp, out_dir,
                                  "tools/figures.py draw_stackup — 선언된 층 구성",
                                  "모식도 — 두께 비율 과장"))
    except Exception as e:
        failed.append({"target": "스택업", "stage": "stackup",
                       "why": f"{type(e).__name__}: {e}"[:200]})

    res = {"run_id": run_id, "n_figures": len(entries), "n_failed": len(failed),
           "entries": entries, "failed": failed, "targets": tg,
           "규율": ("그림은 카탈로그 항목이다 — 문서는 `{{g:키}}` 로 참조하고 경로를 직접 "
                  "쓰지 않는다. 캡션은 결정론으로 만든다(치수 정본 여부를 LLM 이 쓰게 "
                  "두지 않는다)."),
           "역할": {k: v[0] for k, v in KINDS.items()}}
    C.write_json(work / RESULT_NAME, res)
    return res


def of_run(run_id: str, work: Path | None = None) -> dict:
    work = Path(work) if work else C.work_dir(run_id, create=False)
    p = work / RESULT_NAME
    return C.read_json(p) if p.exists() else {"run_id": run_id, "n_figures": 0,
                                              "entries": [], "failed": []}


# ── 자기 시험 ────────────────────────────────────────────────────────────────

def self_test() -> int:
    ok = fail = 0

    def chk(n, cond, d=""):
        nonlocal ok, fail
        if cond:
            ok += 1; print(f"  PASS  {n}")
        else:
            fail += 1; print(f"  FAIL  {n}  {d}")

    print("[figures.py 자기 시험 — 실물]")

    # ── 짝짓기 — 표식만으로 묶지 않는다
    f = lambda r: {"rel": r, "path": f"/x/{r}"}
    tg = pick_targets([f("Top_20260227.dxf"), f("Bottom_20260227.dxf")])
    chk("top/bottom 짝을 짓는다", len(tg["dxf_pairs"]) == 1
        and tg["dxf_pairs"][0]["bottom"] is not None, str(tg["dxf_pairs"]))
    tg2 = pick_targets([f("Top_20260227.dxf"), f("Bottom_19990101.dxf")])
    chk("나머지가 다르면 짝짓지 않는다",
        all(p["bottom"] is None for p in tg2["dxf_pairs"]) and len(tg2["dxf_pairs"]) == 2,
        str(tg2["dxf_pairs"]))

    base = C.data_dir() / "handoff" / "04_experiment_data" / "Antenna_CAD_ECO"
    if not base.exists():
        print("  건너뜀 — 실물 없음"); return 2

    # W-1 — 쓰기는 work/ 아래에서만 한다
    work = C.work_dir("fig-selftest", create=True)
    C.write_json(work / "식별_결과.json", {
        "source": {"path": str(base), "name": "Antenna_CAD_ECO"},
        "files": [{"rel": p.name, "path": str(p)} for p in sorted(base.iterdir())]})
    r = build("fig-selftest", work)

    chk(f"그림 {r['n_figures']}건 생성", r["n_figures"] >= 7, str(r["n_figures"]))
    kinds = {e["figure"]["kind"] for e in r["entries"]}
    chk("2D 전체도·상세·3D 두 시점이 모두 있다",
        {"2d_overview", "2d_detail", "3d_iso", "3d_top"} <= kinds, str(sorted(kinds)))
    chk("DWG 는 내장 프리뷰로 들어온다", "dwg_preview" in kinds, str(sorted(kinds)))

    # ── 치수 정본 여부가 항목에 실린다 — 이것이 캡션의 근거다
    d2 = [e for e in r["entries"] if e["figure"]["kind"].startswith("2d")]
    d3 = [e for e in r["entries"] if e["figure"]["kind"].startswith("3d")]
    chk("2D 는 치수 정본", all(e["figure"]["dimensional"] for e in d2))
    chk("3D 는 치수 정본이 아니다", all(not e["figure"]["dimensional"] for e in d3))
    chk("3D 캡션이 정본이 아님을 말하고 **어디서 읽을지** 알려준다",
        all("치수 정본 아님" in e["figure"]["caption"]
            and "2D 도면에서 읽는다" in e["figure"]["caption"] for e in d3),
        str(d3[0]["figure"]["caption"]))
    # 결함 F-28 회귀 — DWG 는 2D 도면 자체가 없다. 없는 곳으로 보내면 안 된다
    dwg = [e for e in r["entries"] if e["figure"]["kind"] == "dwg_preview"]
    chk("DWG 프리뷰를 2D 도면으로 보내지 않는다",
        all("2D 도면에서 읽는다" not in e["figure"]["caption"]
            and "잴 수 있는 도면이 아직 없다" in e["figure"]["caption"] for e in dwg),
        str(dwg[0]["figure"]["caption"]) if dwg else "-")
    # 결함 F-29 회귀 — 이름의 공백이 마크다운 링크를 끊는다
    chk("경로에 공백이 남지 않는다",
        all(" " not in e["figure"]["path"] for e in r["entries"]),
        str([e["figure"]["path"] for e in r["entries"] if " " in e["figure"]["path"]]))
    chk("실제 파일 경로는 따로 보존한다",
        all((work / e["figure"]["fs_path"]).exists() for e in r["entries"]))
    chk("3D 에 축척을 적지 않는다",
        all("축척 1:" not in e["figure"]["caption"] for e in d3))
    chk("2D 캡션에 축척이 있다", any("축척" in e["figure"]["caption"] for e in d2))

    # ── 카탈로그 항목과 같은 모양이어야 게이트 검사를 물려받는다
    need = {"key", "role", "label", "render", "render_with_unit", "source", "unit"}
    chk("카탈로그 항목 모양", all(need <= set(e) for e in r["entries"]))
    chk("출처가 비지 않는다", all(e["source"] for e in r["entries"]))
    chk("역할이 붙는다", all(e["role"] for e in r["entries"]))

    # ── 파일이 실제로 있다
    chk("산출 파일이 실재한다",
        all((work / e["figure"]["fs_path"]).exists() for e in r["entries"]),
        str([e["figure"]["fs_path"] for e in r["entries"][:2]]))

    # ── 실패는 실패로 남긴다
    work2 = C.work_dir("fig-selftest2", create=True)
    C.write_json(work2 / "식별_결과.json", {"source": {"path": "/none", "name": "x"},
                                           "files": [{"rel": "a.dxf", "path": "/none/a.dxf"}]})
    r2 = build("fig-selftest2", work2)
    chk("판독 실패를 사유와 함께 남긴다", r2["n_failed"] >= 1 and r2["n_figures"] == 0,
        str(r2["failed"])[:120])

    print(f"\n결과: {ok}/{ok + fail} PASS")
    return 0 if fail == 0 else 1


def main(argv):
    if len(argv) < 2:
        print(__doc__); return 2
    if argv[1] == "self-test":
        return self_test()
    r = build(argv[2]) if argv[1] == "build" else of_run(argv[2])
    print(f"{r['run_id']} — 그림 {r['n_figures']}건 · 실패 {r['n_failed']}건")
    for e in r["entries"]:
        print(f"  {e['key']:<44} {e['role']:<22} {e['figure']['caption']}")
    for x in r["failed"]:
        print(f"  [실패] {x['target']} ({x['stage']}) — {x['why']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
