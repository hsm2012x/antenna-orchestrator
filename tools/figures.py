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

★ 실패와 없음을 가른다 (결함 F-37)
    `failed[]`   그리려다 깨진 것 — 도구의 문제다.
    `skipped[]`  **그릴 입력이 아예 없던 것** — 도구의 문제가 아니다.
    이 둘을 한 통에 담으면 `n_failed: 0` 이 "다 잘됐다"로 읽힌다. 실제로는 그림이
    한 장도 없는데 실패도 0인 상태가 나오고, 보는 사람은 도구가 고장 났다고 여긴다.
    조용한 것이 결함이다 — **왜 안 그렸는지**는 값과 똑같이 남겨야 한다(I-5 · B-3).

산출
    work/<run_id>/figures/*.png · *.svg      렌더 산출
    work/<run_id>/그림_결과.json              항목 목록 + 실패 사유 + **미시도 사유**

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
    # ── 렌더러가 여럿인 자리 ────────────────────────────────────────────────
    "cad_vector": ("figure_cad_vector", "2D 벡터 레이아웃", True,
                   "벡터 — 확대해도 깨지지 않는다. 축척은 뷰어의 확대율에 달렸다"),
    "view3d":     ("figure_view3d", "오프라인 3D 뷰", False,
                   "치수 정본 아님 — 돌려 보는 용도다. **본체는 옆의 링크**이고 위 그림은 "
                   "정지 화면(요약의 3D 사시와 같은 장면)이다. 치수는 2D 도면에서 읽는다"),
    "shape_rep":  ("figure_shape_rep", "형상 대표", False,
                   "대표 그림 — 아래 캡션이 어느 렌더러의 것인지 말한다"),
}

# ── 형상 대표의 후보 렌더러 (D-59) ──────────────────────────────────────────
#
# ★ 왜 셋을 다 그리나
#   같은 DXF 를 세 도구가 각각 그리고 있었는데, 문서로 가는 길은 하나뿐이라
#   나머지 둘은 만들어 놓고 아무도 안 썼다. 무엇이 나은지는 **취향과 용도**의 문제라
#   도구가 정할 수 없다(A-1). 그래서 셋을 다 그려 두고 사람이 고른다.
#
# ★ 왜 기본값이 있나
#   고를 때까지 문서가 막히면 안 된다. 기본값이 서고, 대장에 "아직 안 골랐다"가 뜬다.
VARIANTS = {
    "raster": {
        "label": "래스터 300dpi",
        "renderer": "tools/render_page.py render_doc_2d",
        "특징": "축척이 캡션에 실린다 · 치수 정본 · 어디서나 열린다",
        "kind": "2d_overview",
    },
    "vector": {
        "label": "벡터 SVG",
        "renderer": "vendor_srs/cad_render.py render_svg",
        "특징": "확대 무손실 · 파일이 작다 · 축척은 뷰어 확대율에 달렸다",
        "kind": "cad_vector",
    },
    "view3d": {
        "label": "오프라인 3D 뷰",
        "renderer": "vendor_srs/cad_render.py render_html3d",
        "특징": "돌려 볼 수 있다 · 폐쇄망에서 열린다(CDN 없음) · 치수 정본 아님",
        "kind": "view3d",
    },
}
DEFAULT_VARIANT = "raster"

_TOP = re.compile(r"(?:^|[_\-])top(?:$|[_\-])", re.I)
_BOT = re.compile(r"(?:^|[_\-])(?:bot|bottom)(?:$|[_\-])", re.I)


# ── 형상이 들어 있으나 우리가 못 읽는 포맷 ──────────────────────────────────
#
# ★ 왜 이 표가 필요한가 (결함 F-38)
#   인덱서는 이것들을 "판독 불가 바이너리"로만 적는다. 그러면 원천에 형상이
#   **있는데** 못 읽은 것과, 애초에 형상이 **없는** 것이 같은 말로 보인다.
#   둘은 할 일이 정반대다 — 앞은 변환·리더가 필요하고 뒤는 도면을 그려야 한다.
#
# ★ 파서를 만들지 않는다(T-1). 여기 적는 것은 "무엇이 있으면 읽을 수 있게 되는가"뿐이다.
GEOMETRY_UNREADABLE = {
    ".sab": ("ACIS 바이너리", "SAB/STEP 리더 반입(OpenCASCADE 등)", "도구"),
    ".sat": ("ACIS 텍스트", "SAB/STEP 리더 반입(OpenCASCADE 등)", "도구"),
    ".cby": ("CST 독점 형상", "CST 에서 DXF 또는 STEP 으로 export(EXT-2)", "반입"),
    ".stp": ("STEP", "STEP 리더 반입(OpenCASCADE 등)", "도구"),
    ".step": ("STEP", "STEP 리더 반입(OpenCASCADE 등)", "도구"),
    ".igs": ("IGES", "IGES 리더 반입", "도구"),
    ".iges": ("IGES", "IGES 리더 반입", "도구"),
    ".dwg": ("DWG(본문 압축)", "ODA 변환기 반입(EXT-1)", "도구"),
}

# 그림 종류 → 그것이 없을 때 대신 채워지는 문서 절의 역할.
# 대장이 "어느 절이 비었나"를 말할 때 쓴다.
SKIP_ROLE = {
    "cad": ("figure_2d_overview", "2D/3D 도면 그림"),
    "stackup": ("figure_stackup", "스택업 단면 그림"),
    "array_factor": ("figure_array_factor", "배열인자 곡선"),
    "shape_rep": ("figure_shape_rep", "형상 대표 그림"),
}


def geometry_inventory(files: list[dict]) -> dict:
    """원천에 **형상이 들어 있는데 우리가 못 읽는** 파일이 무엇이었나.

    개수와 확장자만 센다. 안을 열어 보지 않는다 — 여는 순간 파서를 만드는 것이다(T-1).
    """
    seen: dict[str, int] = {}
    for f in files:
        ext = Path(str(f.get("rel") or f.get("path") or "")).suffix.lower()
        if ext in GEOMETRY_UNREADABLE:
            seen[ext] = seen.get(ext, 0) + 1
    return seen


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
        # ★ 조용히 None 을 내면 "도구가 안 됐다"로 읽힌다. 사유는 stackup_reason() 이 낸다.

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


def stackup_reason(work: Path) -> str | None:
    """스택업을 **왜 못 그렸나.** 그릴 수 있으면 None.

    draw_stackup 이 조용히 None 을 내면 읽는 사람은 도구가 고장 났다고 여긴다.
    그릴 값이 없다는 것과 그리려다 깨졌다는 것은 완전히 다른 일이다.
    """
    ver = C.read_json(work / "해석_결과.json") if (work / "해석_결과.json").exists() else {}
    sub = ((ver.get("requirements") or {}).get("substrate") or {})
    if sub.get("name") or sub.get("er") or sub.get("h_mm"):
        return None
    return ("기판 선언이 없다 — 재질명 · 유전율 · 두께 가운데 하나도 없어 층을 배치할 수 없다. "
            "아는 사람이 말하면 채워진다(declare_set 으로 substrate.name · er · h_mm)")


def _skip(kind: str, why: str, gap_kind: str, owner: str, slot: str) -> dict:
    role, label = SKIP_ROLE[kind]
    return {"kind": kind, "role": role, "label": label,
            "why": " ".join(why.split()), "종류": gap_kind, "담당": owner, "자리": slot}


def _skipped(work: Path, tg: dict, files: list[dict], has_af: bool,
             stack_why: str | None) -> list[dict]:
    """**시도조차 못 한 것**의 목록. 실패가 아니다 — 입력이 없었던 것이다(F-37).

    빈 목록이 나오는 것이 정상이다. 목록이 비지 않았는데 문서가 조용하면 그게 결함이다.
    """
    out = []
    if not tg["dxf_pairs"] and not tg["dwg"]:
        inv = geometry_inventory(files)
        if inv:
            # 형상은 있다 — 우리가 못 읽을 뿐이다. 구제 경로를 확장자별로 그대로 옮긴다.
            what = " · ".join(f"{ext}({n}건, {GEOMETRY_UNREADABLE[ext][0]})"
                              for ext, n in sorted(inv.items(), key=lambda x: -x[1]))
            paths = sorted({GEOMETRY_UNREADABLE[e][1] for e in inv})
            gk = "반입" if any(GEOMETRY_UNREADABLE[e][2] == "반입" for e in inv) else "도구"
            why = (f"그릴 CAD 도면(DXF/DWG)이 원천에 없다. 형상은 있으나 판독 불가 포맷이다 — "
                   f"{what}. 구제: {' 또는 '.join(paths)}")
        else:
            gk = "반입"
            why = ("그릴 CAD 도면(DXF/DWG)이 원천에 없고, 판독 불가 형상 파일도 없다 — "
                   "원천에 형상 자체가 들어오지 않았다. 도면을 반입해야 한다")
        out.append(_skip("cad", why, gk, "설계", "원천 폴더 — DXF/DWG 또는 STEP export"))
    if stack_why:
        out.append(_skip("stackup", stack_why, "선언", "설계",
                         "registry/declared/<제품>.yaml — substrate.name · er · h_mm"))
    if not has_af:
        out.append(_skip("array_factor",
                         "해석이 배열인자를 내지 않았다 — 소자 배열(주기 · 개수)을 추출하지 "
                         "못했거나 파장을 정할 대역이 없다. 대역 또는 배열 추출이 먼저다",
                         "도구", "해석", "products.yaml band_ghz · 추출 배열"))
    return out


def chosen_variant(work: Path) -> tuple[str, str]:
    """어느 렌더러를 형상 대표로 쓸 것인가. `(변형, 산지)`.

    산지는 선언이다 — `registry/declared/<제품>.yaml` 의 `figure_preference.shape`.
    도구가 고르지 않는다(A-1). 선언이 없으면 기본값을 쓰고 **그 사실을 함께 돌려준다** —
    "기본값이라서 이것"과 "사람이 골라서 이것"은 다른 상태이고, 문서가 그것을 말해야 한다.
    """
    ver = C.read_json(work / "해석_결과.json") if (work / "해석_결과.json").exists() else {}
    pref = ((ver.get("requirements") or {}).get("figure_preference") or {}).get("shape")
    if pref in VARIANTS:
        return pref, "선언"
    return DEFAULT_VARIANT, "기본값"


def _variant_pool(work: Path, out_dir: Path, raster: dict | None,
                  snapshot: dict | None) -> list[dict]:
    """후보 셋을 한 목록으로. **있는 것만** 담는다 — 없는 것을 선택지로 내놓지 않는다."""
    rd = wd_render(work)
    pool = []
    if raster:
        pool.append({"variant": "raster", **VARIANTS["raster"],
                     "path": raster["figure"]["path"], "fs_path": raster["figure"]["fs_path"],
                     "key": raster["key"]})
    svg = rd / "layout_2d.svg"
    if svg.exists():
        pool.append({"variant": "vector", **VARIANTS["vector"],
                     "path": "render/layout_2d.svg", "fs_path": "render/layout_2d.svg",
                     "key": "그림.벡터레이아웃"})
    html = rd / "view_3d.html"
    if html.exists():
        # 본체는 HTML 이고 마크다운은 그것을 그리지 못한다. 정지 화면(3D 사시)을 얹어
        # 종이로 뽑아도 형상이 남게 하고, 돌려 보기는 링크로 보낸다.
        pool.append({"variant": "view3d", **VARIANTS["view3d"],
                     "path": (snapshot or {}).get("path") or "render/view_3d.html",
                     "fs_path": "render/view_3d.html",
                     "snapshot": (snapshot or {}).get("path"),
                     "view_html": "render/view_3d.html",
                     "key": "그림.3D뷰"})
    return pool


def build(run_id: str, work: Path | None = None) -> dict:
    """원천을 렌더하고 그림 항목을 만든다. **실패는 실패로 남긴다**(T-4).

    ★ 그리고 **없음은 없음으로 남긴다**(F-37) — `skipped[]`.
    """
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
    has_af = af.exists()
    if has_af:
        entries.append(_entry("array_factor", "그림.배열인자", af, out_dir,
                              "tools/render.py — 해석이 계산한 곡선을 그리기만 한다",
                              "가로축 각도 · 세로축 상대 레벨"))
    stack_why = stackup_reason(work)
    try:
        sp = draw_stackup(run_id, work, out_dir) if not stack_why else None
        if sp:
            entries.append(_entry("stackup", "그림.스택업", sp, out_dir,
                                  "tools/figures.py draw_stackup — 선언된 층 구성",
                                  "모식도 — 두께 비율 과장"))
    except Exception as e:
        stack_why = None               # 깨진 것은 `failed` 다 — `skipped` 로 세지 않는다
        failed.append({"target": "스택업", "stage": "stackup",
                       "why": f"{type(e).__name__}: {e}"[:200]})

    # ── 형상 대표 — 셋을 다 그려 놓고 **사람이 고른 것**을 세운다 (D-59) ────
    #
    # 여기까지 오면 세 렌더러의 산출이 모두 디스크에 있다. 그런데 지금까지 문서로 가는
    # 길은 render_page 것 하나뿐이었고, cad_render 의 벡터·3D 뷰는 만들어 놓고 아무도
    # 안 썼다(결함 F-42). 셋을 한 후보 목록으로 묶고, 고른 것을 대표 자리에 세운다.
    raster = next((e for e in entries if e["figure"]["kind"] == "2d_overview"), None)
    snap = next((e for e in entries if e["figure"]["kind"] == "3d_iso"), None)
    pool = _variant_pool(work, out_dir, raster, snap and snap["figure"])
    variant, 산지 = chosen_variant(work)
    picked = next((v for v in pool if v["variant"] == variant), None)
    if picked is None and pool:          # 고른 것이 이번 run 에 없으면 있는 것으로 선다
        picked, 산지 = pool[0], f"{산지} → 대체(고른 '{variant}' 가 이번 run 에 없다)"
    if picked:
        rep = _entry("shape_rep", "그림.형상대표", (work / picked["fs_path"]), out_dir,
                     f"{picked['renderer']} · 대표 선택 산지: {산지}",
                     f"{picked['label']} — {picked['특징']}",
                     {"variant": picked["variant"], "선택_산지": 산지,
                      **({"view_html": picked["view_html"]} if picked.get("view_html") else {}),
                      **({"snapshot": picked["snapshot"]} if picked.get("snapshot") else {})})
        if picked.get("path"):            # 스냅샷이 따로 있으면 본문에 그것을 건다
            rep["render"] = rep["render_with_unit"] = picked["path"]
            rep["figure"]["path"] = picked["path"]
        entries.append(rep)
        # 고른 것은 대표 자리로 갔다 — 제 자리에 또 실어 같은 그림을 두 번 보이지 않는다
        entries = [e for e in entries
                   if not (e is not rep and e["figure"]["path"] == rep["figure"]["path"])]

    # 안 고른 후보도 문서에 자리가 있다 — 대표가 아닐 뿐 없는 것이 아니다
    for v in pool:
        if picked and v["variant"] == picked["variant"]:
            continue
        if v["variant"] == "vector":
            entries.append(_entry("cad_vector", v["key"], work / v["fs_path"], out_dir,
                                  v["renderer"], v["특징"]))
        elif v["variant"] == "view3d":
            e = _entry("view3d", v["key"], work / (v.get("snapshot") or v["fs_path"]),
                       out_dir, v["renderer"], v["특징"],
                       {"view_html": v["view_html"], "snapshot": v.get("snapshot")})
            if v.get("snapshot"):
                e["render"] = e["render_with_unit"] = v["snapshot"]
                e["figure"]["path"] = v["snapshot"]
            entries.append(e)

    skipped = _skipped(work, tg, files, has_af, stack_why)
    if len(pool) > 1 and 산지 == "기본값":
        skipped.append(_skip("shape_rep",
                             "형상 대표를 아직 고르지 않았다 — 후보 "
                             + " · ".join(f"{v['variant']}({v['label']})" for v in pool)
                             + f". 지금은 기본값 '{DEFAULT_VARIANT}' 가 서 있다. "
                             "figure_variants 로 셋을 보고 figure_choose 로 고른다",
                             "선언", "설계",
                             "registry/declared/<제품>.yaml — figure_preference.shape"))
    res = {"run_id": run_id, "n_figures": len(entries), "n_failed": len(failed),
           "variants": pool, "variant_chosen": picked and picked["variant"],
           "variant_source": 산지,
           "n_skipped": len(skipped),
           "entries": entries, "failed": failed, "skipped": skipped, "targets": tg,
           "형상_판독불가": geometry_inventory(files),
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
                                              "entries": [], "failed": [], "skipped": []}


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

    # ── 결함 F-37 회귀 — **없음이 조용하면 안 된다**
    #   실물이 없어도 도는 시험이다. 이 결함은 정확히 "입력이 없는 run"에서만 나므로
    #   실물 있는 시험만 두면 영원히 못 잡는다.
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="figskip-"))
    cst = [{"rel": "Model.sab", "path": "/x/Model.sab"},
           {"rel": "Model/3D/a.cby", "path": "/x/a.cby"},
           {"rel": "Model/3D/b.cby", "path": "/x/b.cby"},
           {"rel": "readme.txt", "path": "/x/readme.txt"}]
    inv = geometry_inventory(cst)
    chk("판독 불가 형상을 확장자별로 센다", inv == {".sab": 1, ".cby": 2}, str(inv))
    chk("일반 파일은 형상으로 세지 않는다", ".txt" not in inv, str(inv))

    sk = _skipped(tmp, {"dxf_pairs": [], "dwg": []}, cst, has_af=False,
                  stack_why=stackup_reason(tmp))
    kinds_s = {s["kind"] for s in sk}
    chk("도면 0개면 **사유가 남는다**", "cad" in kinds_s, str(kinds_s))
    cad = next(s for s in sk if s["kind"] == "cad")
    chk("사유가 '무엇이 있었는지'를 말한다",
        ".sab" in cad["why"] and ".cby" in cad["why"], cad["why"])
    chk("사유가 **구제 경로**를 말한다",
        "export" in cad["why"] or "반입" in cad["why"], cad["why"])
    chk("공백 종류가 셋 중 하나다", cad["종류"] in ("선언", "반입", "도구"), cad["종류"])
    chk("담당과 적을 자리가 있다", bool(cad["담당"] and cad["자리"]))
    chk("스택업도 사유가 남는다", "stackup" in kinds_s, str(kinds_s))
    stk = next(s for s in sk if s["kind"] == "stackup")
    chk("스택업 공백은 **선언**이다 — 말하면 채워진다", stk["종류"] == "선언", stk["종류"])
    chk("배열인자도 사유가 남는다", "array_factor" in kinds_s, str(kinds_s))

    # 형상 파일조차 없으면 말이 달라져야 한다 — "못 읽었다"와 "안 들어왔다"는 다른 일이다
    sk0 = _skipped(tmp, {"dxf_pairs": [], "dwg": []},
                   [{"rel": "a.txt", "path": "/x/a.txt"}], has_af=True, stack_why=None)
    c0 = next(s for s in sk0 if s["kind"] == "cad")
    chk("형상 자체가 없으면 그렇게 말한다", "형상 자체가 들어오지 않았다" in c0["why"], c0["why"])
    chk("그릴 수 있는 것에는 사유를 달지 않는다",
        {s["kind"] for s in sk0} == {"cad"}, str([s["kind"] for s in sk0]))

    # 그릴 대상이 있으면 cad 사유는 서지 않는다 — 사유가 도배되면 안 읽힌다
    sk1 = _skipped(tmp, {"dxf_pairs": [{"top": "/x/a.dxf", "bottom": None}], "dwg": []},
                   cst, has_af=True, stack_why=None)
    chk("대상이 있으면 사유를 만들지 않는다", sk1 == [], str(sk1))

    chk("스택업 사유가 무엇을 말하면 되는지 알려준다",
        "declare_set" in (stackup_reason(tmp) or ""), str(stackup_reason(tmp)))

    base = C.data_dir() / "handoff" / "04_experiment_data" / "Antenna_CAD_ECO"
    if not base.exists():
        print("  건너뜀 — 실물 없음"); return 0 if fail == 0 else 1

    # W-1 — 쓰기는 work/ 아래에서만 한다
    work = C.work_dir("fig-selftest", create=True)
    C.write_json(work / "식별_결과.json", {
        "source": {"path": str(base), "name": "Antenna_CAD_ECO"},
        "files": [{"rel": p.name, "path": str(p)} for p in sorted(base.iterdir())]})
    r = build("fig-selftest", work)

    chk(f"그림 {r['n_figures']}건 생성", r["n_figures"] >= 7, str(r["n_figures"]))
    kinds = {e["figure"]["kind"] for e in r["entries"]}
    # 전체도는 **대표 자리로 승격**된다(D-59) — 같은 그림을 두 번 싣지 않는다
    chk("2D 상세·3D 두 시점이 있고 전체도는 대표로 선다",
        {"2d_detail", "3d_iso", "3d_top", "shape_rep"} <= kinds, str(sorted(kinds)))
    chk("승격된 그림을 제 자리에 또 싣지 않는다", "2d_overview" not in kinds,
        str(sorted(kinds)))
    chk("DWG 는 내장 프리뷰로 들어온다", "dwg_preview" in kinds, str(sorted(kinds)))

    # ── D-59 회귀 — 후보 셋을 다 그리고 **고른 것**을 세운다
    pool = {v["variant"] for v in r["variants"]}
    chk("후보 목록이 선다", "raster" in pool, str(sorted(pool)))
    chk("고르기 전에는 기본값이고 그 사실을 말한다",
        r["variant_chosen"] == DEFAULT_VARIANT and r["variant_source"] == "기본값",
        f"{r['variant_chosen']} / {r['variant_source']}")
    rep = next(e for e in r["entries"] if e["figure"]["kind"] == "shape_rep")
    chk("대표 캡션이 **어느 렌더러의 것인지** 말한다",
        "래스터" in rep["figure"]["caption"], rep["figure"]["caption"][:120])
    chk("대표 출처에 선택 산지가 실린다", "선택 산지" in rep["source"], rep["source"])
    if len(pool) > 1:
        chk("안 고른 후보도 문서에 자리가 있다",
            (kinds & {"cad_vector", "view3d"}) != set(), str(sorted(kinds)))
        chk("안 골랐다는 사실이 대장으로 간다",
            any(s["kind"] == "shape_rep" for s in r["skipped"]),
            str([s["kind"] for s in r["skipped"]]))

    # 사람이 고르면 그것이 선다 — 선언이 기본값을 이긴다
    ver_p = work / "해석_결과.json"
    if ver_p.exists() and "vector" in pool:
        _v = C.read_json(ver_p)
        _v.setdefault("requirements", {})["figure_preference"] = {"shape": "vector"}
        C.write_json(ver_p, _v)
        r_v = build("fig-selftest", work)
        chk("선언이 기본값을 이긴다",
            r_v["variant_chosen"] == "vector" and r_v["variant_source"] == "선언",
            f"{r_v['variant_chosen']} / {r_v['variant_source']}")
        chk("골랐으면 대장에 안 고름 행이 없다",
            not any(s["kind"] == "shape_rep" for s in r_v["skipped"]),
            str([s["kind"] for s in r_v["skipped"]]))
        _v["requirements"].pop("figure_preference", None)
        C.write_json(ver_p, _v)
        r = build("fig-selftest", work)

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
