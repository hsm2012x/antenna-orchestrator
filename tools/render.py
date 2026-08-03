#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/render.py — 클래스 「렌더」. 사람이 볼 수 있게만 만든다. LLM 0콜.

규율:
  · 외부 JS/CSS 의존 0 — 산출 HTML은 자기완결(폐쇄망). vendor_srs 가 이미 그 규약이다.
  · **렌더 실패는 파이프라인을 멈추지 않는다** — "렌더 실패 + 사유"로 남기고 다음으로 간다(3.4).
  · 수치를 만들지 않는다. 빔패턴은 원거리장 데이터가 없으면 그리지 않는다(N-3).

출력: work/<run_id>/render/ (layout_2d.svg · view_3d.html · cad_index.json) + 렌더_결과.json
사용: python tools/render.py --run-id <id>
"""
from __future__ import annotations
import argparse, json, shutil, sys, traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import read_json, vendor, work_dir, write_json


def _plot_af(af: dict, out_path, title: str):
    """해석이 계산한 배열인자 곡선을 PNG로 그린다. 여기서 어떤 수치도 계산하지 않는다.

    라벨은 ASCII로 쓴다 — 폐쇄망 Windows/VM에 한글 폰트가 없으면 matplotlib 이 두부(□)를 찍는다.
    한글 서술은 문서(md)와 JSON이 담당한다.
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    th, db = af["curve"]["theta_deg"], af["curve"]["af_db"]
    fig, ax = plt.subplots(figsize=(9, 4.2), dpi=130)
    ax.plot(th, db, lw=1.0, color="#2f6096")
    ax.axhline(-3.0, lw=0.8, ls="--", color="#b05f33")
    lo, hi = af.get("hpbw_edges_deg") or [None, None]
    if lo is not None and hi is not None:
        ax.axvspan(lo, hi, color="#2f6096", alpha=0.08)
    if af.get("sll_angle_deg") is not None:
        ax.plot([af["sll_angle_deg"]], [af["sll_db"]], "o", ms=4, color="#b05f33")
    ax.set_xlim(-90, 90); ax.set_ylim(-50, 2)
    ax.set_xlabel("theta [deg]"); ax.set_ylabel("|AF|^2 / N^2  [dB]")
    ax.set_title(f"{title} - Array Factor (N={af['n']}, lambda={af['lambda_mm']} mm)", fontsize=10)
    sub = (f"HPBW={af.get('hpbw_deg')} deg   SLL={af.get('sll_db')} dB   "
           "uniform excitation, no element pattern -- NOT a measured/simulated radiation pattern")
    ax.annotate(sub, (0.5, -0.28), xycoords="axes fraction", ha="center", fontsize=7.5, color="#667585")
    ax.grid(alpha=0.25, lw=0.5)
    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)


def render(run_id: str) -> dict:
    cr = vendor()
    wd = work_dir(run_id)
    ident = read_json(wd / "식별_결과.json")
    rdir = wd / "render"; rdir.mkdir(parents=True, exist_ok=True)

    def role_of(rec):
        rc = rec.get("role_candidates") or []
        return rc[0]["role"] if rc else None

    top = next((r["path"] for r in ident["files"] if role_of(r) == "pcb-signal-layer"), None)
    bot = next((r["path"] for r in ident["files"] if role_of(r) == "pcb-ground-plane"), None)
    # 역할 판정이 없어도 판독된 DXF가 있으면 그린다 — CST 임포트 원본이 여기 걸린다.
    # 역할이 없다는 것은 "무엇인지 모른다"는 뜻이고, "그릴 수 없다"는 뜻이 아니다.
    fallback = None
    if not top:
        cands = [r for r in ident["files"]
                 if r["readability"] == "full" and r["rel"].lower().endswith(".dxf")]
        if cands:
            pick = max(cands, key=lambda r: (r.get("evidence") or {}).get("polyline", 0))
            top, fallback = pick["path"], pick["rel"]
    name = ident["source"]["name"]

    arts, failures, notes = {}, [], []

    def attempt(key, label, path, fn):
        """수리 2: vendor render_svg/render_html3d 는 **문서 문자열**을 반환한다(경로 아님).
        반환값을 경로로 쓰면 SVG 본문이 산출 목록에 들어간다 — 반환값을 버리고 경로로 확인한다."""
        try:
            fn()
            if not Path(path).exists():
                raise FileNotFoundError(f"렌더 호출은 성공했으나 파일이 없다: {path}")
            arts[key] = str(path)
        except Exception as e:
            failures.append({"artifact": key, "label": label, "error": f"{type(e).__name__}: {e}",
                             "trace_tail": traceback.format_exc(limit=2).splitlines()[-1]})

    if top or bot:
        attempt("layout_2d_svg", "2D 벡터 레이아웃", rdir / "layout_2d.svg", lambda: cr.render_svg(
            top, bot, str(rdir / "layout_2d.svg"), title=f"{name} (2D 벡터 — 확대 무손실)"))
        attempt("view_3d_html", "오프라인 3D 뷰", rdir / "view_3d.html", lambda: cr.render_html3d(
            top, bot, str(rdir / "view_3d.html"), title=f"{name} — 3D",
            extra_rows=[("출처", Path(top).name if top else "—"),
                        ("GND", Path(bot).name if bot else "—")]))
        # 주의: arts 는 산출물 경로만 담는다 — 설명은 notes 로 분리한다(뷰어가 경로로 읽는다).
        if fallback:
            notes.append(f"2D 레이아웃 기준: 역할 미판정 — 폴리라인 최다 DXF({fallback})를 "
                         f"대표로 그렸다(signal/ground 역할 판정 없음)")
    else:
        failures.append({"artifact": "layout_2d_svg", "label": "2D 벡터 레이아웃",
                         "error": "판독된 DXF가 없다 — 렌더 대상 없음"})

    # 프리뷰(DWG 등)는 식별이 이미 만들었다 — render/ 로 모아 뷰어가 한 곳만 보게 한다.
    previews = []
    for rec in ident["files"]:
        pv = rec.get("preview")
        if pv and Path(pv).exists():
            dst = rdir / Path(pv).name
            if Path(pv).resolve() != dst.resolve(): shutil.copy2(pv, dst)
            previews.append({"rel": rec["rel"], "image": str(dst), "quality": rec.get("preview_quality"),
                             "note": "DWG 내장 프리뷰(512px) — 고화질은 ODA 변환 후 벡터 렌더"})

    # 빔패턴 절은 두 갈래다.
    #  ① 실측·시뮬 발산패턴: 원거리장·S-파라미터가 없으면 그리지 않는다 — "없음 + 채움 주체"(N-3).
    #  ② 배열인자(AF): 「해석」이 이미 계산해 둔 곡선을 **그리기만** 한다. 렌더는 수치를 만들지 않는다.
    beam = {"farfield": {"status": "없음",
                         "채움_주체": "CST export 규약(Result/export/) 또는 원거리장 데이터 반입",
                         "사유": "추출 단계에서 원거리장·S-파라미터 선언이 확인되지 않았다"},
            "array_factor": {"status": "없음", "사유": "해석_결과.json 에 array_factor 곡선이 없다"}}
    itp = wd / "해석_결과.json"
    if itp.exists():
        itp_j = read_json(itp) or {}
        afs = itp_j.get("array_factors") or ([itp_j["array_factor"]] if itp_j.get("array_factor") else [])
        drawn = []
        for i, af in enumerate(afs):
            if not af.get("curve"): continue
            key = f"array_factor_png_{i}"
            png = rdir / (f"array_factor_{i}.png" if len(afs) > 1 else "array_factor.png")
            attempt(key, f"배열인자 플롯 {i}", png,
                    lambda af=af, png=png: _plot_af(
                        af, png, f"{name} - {af.get('label_ascii') or ''}"))
            drawn.append({"label": af.get("label"), "image": arts.get(key),
                          "status": "산출" if key in arts else "렌더 실패",
                          "hpbw_deg": af.get("hpbw_deg"), "sll_db": af.get("sll_db"),
                          "grating_deg": af.get("grating_deg"),
                          "n_elements": af.get("n"), "lambda_mm": af.get("lambda_mm"),
                          "기준": af.get("기준"), "가정": af.get("가정"), "경고": af.get("경고")})
        if drawn:
            beam["array_factor"] = {"status": "산출", "n": len(drawn), "items": drawn,
                                    "수치_산지": "해석_결과.json · array_factors (렌더는 계산하지 않는다)"}

    res = {"run_id": run_id, "source": ident["source"], "dir": str(rdir),
           "artifacts": arts, "previews": previews, "beam_pattern": beam,
           "failures": failures, "notes": notes,
           "규율": "렌더 실패는 문서에 '렌더 실패'로 남기고 파이프라인을 멈추지 않는다(3.4)"}
    (rdir / "cad_index.json").write_text(
        json.dumps({"inventory": ident["files"], "artifacts": arts, "previews": previews},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    res["cad_index"] = str(rdir / "cad_index.json")
    write_json(wd / "렌더_결과.json", res)
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description="렌더 — 사람이 볼 수 있게만 만든다")
    ap.add_argument("--run-id", required=True)
    a = ap.parse_args(argv)
    r = render(a.run_id)
    print(f"렌더: 산출 {len(r['artifacts'])}건 · 프리뷰 {len(r['previews'])}건 · "
          f"실패 {len(r['failures'])}건 · 원거리장={r['beam_pattern']['farfield']['status']}"
          f" · 배열인자={r['beam_pattern']['array_factor']['status']}")
    for k, v in r["artifacts"].items(): print(f"  {k}: {v}")
    for f in r["failures"]: print(f"  렌더 실패 {f['artifact']}: {f['error']}")
    print(f"산출: {work_dir(a.run_id, False) / '렌더_결과.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
