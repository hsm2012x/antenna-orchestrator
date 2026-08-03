# render_page.py — 문서 기준 도안 렌더 (2D + 정적 3D) · stdlib + Pillow만 사용
#
# vendor_srs/cad_render.py(dxf_read · segments · build_model)를 감싸는 래퍼다.
# vendor 코드는 수정하지 않는다(AGENTS.md T-5). 출처: 08_antenna_cad_em.ipynb 계열.
#
# ★ 용지가 아니라 **문서**가 기준이다 (2026-07-31-2 사용자 확정)
#     A4 고정은 형식 시험이었다. 570×40 mm(종횡비 14:1)를 A4 에 넣으면 1:2.12 로 줄어
#     가느다란 띠가 되고 세로 여백이 대부분이며 패치 형상을 못 본다.
#     → 캔버스 **폭은 문서 본문 폭으로 고정**하고 **높이는 내용에 맞춘다.**
#       남는 여백을 만들지 않으므로 그림이 문서에 그대로 들어간다.
#
# ★ 축척이 판독성을 이기지 못한다 — 넘치면 줄이지 말고 **나눈다**
#     `min_scale`(기본 1.0 = 원척) 밑으로는 축척을 낮추지 않는다. 폭이 넘치면 상세 시트를
#     N 장으로 분할하고 겹침 구간을 둔다. 전체도는 따로 1 장 내고 분할 경계를 표시한다.
#     도면의 통상 관행(전체도 + 상세도)이고, 2D 는 **정밀 판독**용이라 분할이 맞다.
#
# 2D 와 3D 의 역할 (둘 다 만든다 — 어느 하나가 다른 하나를 대신하지 않는다)
#     형상 preview   3D 렌더에서 캡처한 2D 이미지 (`views`)
#     형상 정밀      2D 벡터 도면 — **치수·축척의 정본**
#     ※ 캡처본은 치수 정본이 아니다. 투영과 z 확대가 걸려 있다.
#
# 진입점:
#   render_doc_2d(top_dxf, bottom_dxf, out_dir, dpi=300, min_scale=1.0, ...)
#   render_doc_3d(top_dxf, bottom_dxf, out_dir, dpi=300, views=("iso","top"), ...)
#   render_a4_2d · render_a4_3d — 옛 이름(호환 별칭)
# CLI:
#   python render_page.py TOP.dxf [BOTTOM.dxf] --out DIR [--dpi 300]
#                         [--doc-width 170] [--min-scale 1.0] [--views iso,top]
#                         [--yaw -35] [--pitch 32] [--proj ortho] [--title "..."]
#
# 전제(참조 규약):
#   · 좌표 단위 = mm — **DXF R12 에 `$INSUNITS` 가 없다.** 측정값이 아니라 **가정**이므로
#     표제란에 산지를 함께 찍는다: `단위 mm ($INSUNITS 미기재 — 사내 관행 가정)`
#   · +X 우측 · +Y 상단 · 3D는 z=0이 기판 상면, 아래로 -h_sub (build_model 규약)
#   · TEXT/SHX 엔티티는 렌더하지 않는다(형상만). 표제란 텍스트는 SVG/PIL 내장 폰트 사용
#   · 판독 실패는 `ValueError` 다 — **`SystemExit` 을 던지지 않는다.** 라이브러리가
#     SystemExit 을 던지면 호출부의 `except Exception` 이 못 잡아 파이프라인이 통째로
#     죽는다("렌더 실패는 파이프라인을 멈추지 않는다"는 규약이 무력해진다). 결함 F-27
#   · 산출: <이름>_2d_overview.svg/.png · <이름>_2d_detail<i>of<n>.svg/.png
#           <이름>_3d_<view>.svg/.png  — 전부 자기완결, 외부 의존 0

import argparse, math, sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "vendor_srs"))
import cad_render as CR  # noqa: E402

# 문서 기준 — 용지가 아니라 **문서 본문 폭**이 캔버스 폭이다
DOC_W_MM = 170.0          # A4 세로에서 좌우 여백 20 mm 씩 뺀 본문 폭
DOC_MAX_H_MM = 200.0      # 그림 하나가 차지할 최대 높이(캡션 자리를 남긴다)
MIN_SCALE = 1.0           # **이보다 축척을 낮추지 않는다.** 넘치면 분할한다(원척 기준)
MAX_SHEETS = 8            # 분할 상한 — 넘으면 넘겼다고 **말하고** 축척을 낮춘다
OVERLAP_MM = 8.0          # 분할 시트 겹침 구간
MARGIN_MM = 6.0           # 도곽 여백
TITLE_H_MM = 16.0         # 표제란 높이 — 3줄
TITLE_CPL = 110           # 표제란 한 줄 글자 수 상한(문서 폭 기준). 넘으면 잘랐다고 말한다
LINE_MM = 0.30            # 기본 선폭(mm)
UNIT_NOTE = "단위 mm ($INSUNITS 미기재 — 사내 관행 가정)"

PAPERS = {"A4": (210.0, 297.0), "A3": (297.0, 420.0), "LETTER": (215.9, 279.4)}  # 호환용

# 3D 캡처 시점 — (yaw°, pitch°, z확대). top 은 두께를 과장하지 않는다(위에서 안 보인다)
#
# iso 의 yaw 가 -35 가 아니라 -18 인 이유 — **캔버스 높이가 자유이기 때문**이다.
#   yaw 를 키우면 긴 형상이 대각선으로 서서 캔버스가 세로로 길어지고 여백만 는다.
#   실측(잉크 비율): yaw -35 → 0.161 · -18 → 0.216 · -8 → 0.288.
#   -8 은 거의 정면이라 깊이감이 사라진다. -18 이 절충이다.
VIEWS = {"iso": (-18.0, 32.0, 18.0), "top": (0.0, 90.0, 1.0), "front": (0.0, 0.0, 18.0)}
COL = {"top": "#b87333", "bottom": "#2f4f6f", "hole": "#c0392b",
       "frame": "#222222", "text": "#222222", "bg": "#ffffff", "cut": "#9aa3ad"}


# ── 폰트: 한글 지원 폰트 탐색(없으면 ASCII 대체) ────────────────────────────
# 폐쇄망 전제 — 다운로드하지 않는다. env RENDER_PAGE_FONT 로 강제 지정 가능.

import os
_FONT_CANDIDATES = [
    os.environ.get("RENDER_PAGE_FONT", ""),
    r"C:\Windows\Fonts\malgun.ttf", r"C:\Windows\Fonts\malgunbd.ttf",   # Windows
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",                   # Linux(나눔)
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",            # Linux(Noto)
]
_KOR_LABELS = {"축척": "Scale", "단위": "unit", "원천": "src",
               "z확대": "z-exagg", "두께 과장 — 치수 판독 금지": "thickness exaggerated",
               "$INSUNITS 미기재 — 사내 관행 가정": "$INSUNITS absent - assumed mm (convention)",
               "전체도": "overview", "상세": "detail", "분할 경계": "cut line",
               "치수 정본 아님": "not dimensional master",
               "색: top=구리 · bottom=군청 · hole=적": "color: top=copper / bottom=navy / hole=red"}


def _load_font(size_px):
    """(font, 한글가능여부). 후보에 없으면 PIL 기본 폰트 + ASCII 대체."""
    from PIL import ImageFont
    for p in _FONT_CANDIDATES:
        if p and Path(p).exists():
            try:
                return ImageFont.truetype(p, size_px), True
            except Exception:
                continue
    try:
        return ImageFont.load_default(size=size_px), False
    except TypeError:
        return ImageFont.load_default(), False


def _ascii_fallback(s):
    for k, v in _KOR_LABELS.items():
        s = s.replace(k, v)
    return "".join(ch if ord(ch) < 0x2500 else "?" for ch in s)


# ── 공통: 페이지 좌표 변환 ──────────────────────────────────────────────────

class Sheet:
    """도형 bbox → **문서 폭 고정 · 높이 자유** 캔버스. sx()·sy()는 mm 캔버스 좌표.

    `scale` 을 주면 그 축척으로 그린다(분할 상세 시트). 주지 않으면 폭·높이에 맞춘다.
    남는 여백을 만들지 않는 것이 요점이다 — 그림이 문서에 그대로 들어간다.
    """
    def __init__(self, bbox, doc_w=DOC_W_MM, max_h=DOC_MAX_H_MM, scale=None):
        x0, y0, x1, y1 = bbox
        w, h = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
        fw = doc_w - 2 * MARGIN_MM
        fh_max = max_h - 2 * MARGIN_MM - TITLE_H_MM
        self.scale = scale if scale else min(fw / w, fh_max / h)
        fh = min(h * self.scale, fh_max)
        self.paper_w = doc_w
        self.paper_h = fh + 2 * MARGIN_MM + TITLE_H_MM
        self.ox = MARGIN_MM + (fw - w * self.scale) / 2 - x0 * self.scale
        # Y 반전 — `+ y1*scale` 이 빠지면 원점이 0 이 아닌 도면이 통째로 밀린다(결함 F-25)
        self.oy = MARGIN_MM + (fh - h * self.scale) / 2 + y1 * self.scale
        self.bbox = bbox

    def sx(self, x): return self.ox + x * self.scale
    def sy(self, y): return self.oy - y * self.scale

    def scale_note(self):
        s = self.scale
        return f"축척 1:{1/s:.2f}" if s < 0.999 else (
            f"축척 {s:.2f}:1" if s > 1.001 else "축척 1:1")


Page = Sheet          # 옛 이름 — 호출부 호환


def _split_ranges(x0, x1, n, overlap=OVERLAP_MM):
    """폭을 n 등분하되 이웃과 `overlap` 만큼 겹친다. 겹침이 없으면 경계 형상을 못 읽는다."""
    seg = (x1 - x0) / n
    out = []
    for i in range(n):
        a = x0 + i * seg - (overlap if i else 0.0)
        b = x0 + (i + 1) * seg + (overlap if i < n - 1 else 0.0)
        out.append((a, b))
    return out


def _plan_sheets(bbox, doc_w, max_h, min_scale):
    """분할 계획. 반환 {n, scale, ranges, capped, why}.

    축척을 낮춰 한 장에 밀어 넣지 않는다 — `min_scale` 밑으로는 **나눈다**.
    상한(MAX_SHEETS)에 걸리면 그때만 축척을 낮추고 **낮췄다고 말한다**(조용히 줄이지 않는다).
    """
    x0, y0, x1, y1 = bbox
    w, h = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
    fw = doc_w - 2 * MARGIN_MM
    fh_max = max_h - 2 * MARGIN_MM - TITLE_H_MM
    s_h = fh_max / h                      # 높이가 허용하는 상한 축척
    want = min(min_scale, s_h)
    n = max(1, math.ceil(w * want / fw))
    capped = n > MAX_SHEETS
    if capped:
        n = MAX_SHEETS
    seg_w = w / n
    scale = min(fw / (seg_w + (2 * OVERLAP_MM if n > 1 else 0)), s_h)
    return {"n": n, "scale": scale, "ranges": _split_ranges(x0, x1, n),
            "capped": capped,
            "why": (f"분할 상한 {MAX_SHEETS}장에 걸려 축척을 {1/scale:.2f} 분의 1 로 "
                    f"낮췄다 — 판독성이 목표에 못 미친다" if capped else
                    f"{n}장으로 나눠 축척 {scale:.2f} 확보(최소 {min_scale:g} 요구)")}


def _collect_2d(top_dxf, bottom_dxf):
    """(폴리라인 경로 목록, 원 목록, bbox). 경로 = (layer_tag, [(x,y)...])"""
    paths, circles = [], []
    xs, ys = [], []
    for tag, path in (("top", top_dxf), ("bottom", bottom_dxf)):
        if not path or not Path(path).exists():
            continue
        g = CR.dxf_read(str(path))
        for p in g["polylines"]:
            vs = list(p["verts"])
            if not vs:
                continue
            if p["flag"] & 1:
                vs = vs + [vs[0]]
            paths.append((tag, vs))
            xs += [v[0] for v in vs]; ys += [v[1] for v in vs]
        for c in g["circles"]:
            circles.append((tag, c["c"][0], c["c"][1], c["r"]))
            xs += [c["c"][0] - c["r"], c["c"][0] + c["r"]]
            ys += [c["c"][1] - c["r"], c["c"][1] + c["r"]]
    if not xs:
        raise ValueError("판독 가능한 형상이 없다 — DXF 경로를 확인할 것")
    return paths, circles, (min(xs), min(ys), max(xs), max(ys))


# ── SVG / PNG 공용 페인터 ──────────────────────────────────────────────────

def _fit(t, cpl=TITLE_CPL):
    """표제란 한 줄에 맞춘다. 넘치면 **잘랐다고 말한다** — 조용히 넘겨 글자를 깨뜨리지 않는다."""
    return t if len(t) <= cpl else t[:cpl - 1] + "…"


def _frame_and_title(shapes, page, title, sources, extra="", unit_note=UNIT_NOTE,
                     scale_text=None):
    """도곽 + 표제란. **단위는 값이 아니라 산지와 함께** 적는다.

    `$INSUNITS` 가 없는 도면이므로 mm 는 측정값이 아니라 가정이다. 다른 모든 값에 적용하는
    규율과 같게, 무엇을 근거로 mm 라 했는지 도면 위에 남긴다(Q-13).
    """
    pw, ph = page.paper_w, page.paper_h
    m = MARGIN_MM * 0.5
    ty = ph - MARGIN_MM * 0.5 - TITLE_H_MM
    # 표제란 바탕 — 분할 시트에서 도형이 표제란을 침범해 글씨를 덮는 것을 막는다
    shapes.append({"k": "rect", "x0": m, "y0": ty, "x1": pw - m, "y1": ph - m,
                   "stroke": "none", "fill": COL["bg"], "w": 0})
    shapes.append({"k": "rect", "x0": m, "y0": m, "x1": pw - m, "y1": ph - m,
                   "stroke": COL["frame"], "w": 0.5})
    shapes.append({"k": "line", "a": (m, ty), "b": (pw - m, ty),
                   "stroke": COL["frame"], "w": 0.35})
    rows = [(f"{title}   |   {scale_text or page.scale_note()}   |   "
             f"{date.today().isoformat()}", 2.9),
            (unit_note, 2.4),
            (f"원천: {sources}   {extra}", 2.4)]
    for i, (txt, size) in enumerate(rows):
        shapes.append({"k": "text", "x": MARGIN_MM, "y": ty + 4.6 + i * 3.9,
                       "s": _fit(txt), "size": size})


def _write_svg(shapes, page, out):
    pw, ph = page.paper_w, page.paper_h
    L = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{pw}mm" height="{ph}mm" '
         f'viewBox="0 0 {pw} {ph}">',
         f'<rect x="0" y="0" width="{pw}" height="{ph}" fill="{COL["bg"]}"/>']
    for s in shapes:
        if s["k"] == "poly":
            pts = " ".join(f"{x:.3f},{y:.3f}" for x, y in s["pts"])
            fill = s.get("fill", "none")
            L.append(f'<polyline points="{pts}" fill="{fill}" stroke="{s["stroke"]}" '
                     f'stroke-width="{s.get("w", LINE_MM)}" stroke-linejoin="round"/>')
        elif s["k"] == "circle":
            L.append(f'<circle cx="{s["cx"]:.3f}" cy="{s["cy"]:.3f}" r="{s["r"]:.3f}" '
                     f'fill="none" stroke="{s["stroke"]}" stroke-width="{s.get("w", LINE_MM)}"/>')
        elif s["k"] == "rect":
            fill, stroke = s.get("fill", "none"), s.get("stroke", "none")
            sw = f' stroke="{stroke}" stroke-width="{s["w"]}"' if stroke != "none" else ""
            L.append(f'<rect x="{s["x0"]:.2f}" y="{s["y0"]:.2f}" width="{s["x1"]-s["x0"]:.2f}" '
                     f'height="{s["y1"]-s["y0"]:.2f}" fill="{fill}"{sw}/>')
        elif s["k"] == "line":
            dash = ' stroke-dasharray="3 2"' if s.get("dash") else ""
            L.append(f'<line x1="{s["a"][0]:.2f}" y1="{s["a"][1]:.2f}" x2="{s["b"][0]:.2f}" '
                     f'y2="{s["b"][1]:.2f}" stroke="{s["stroke"]}" '
                     f'stroke-width="{s["w"]}"{dash}/>')
        elif s["k"] == "text":
            L.append(f'<text x="{s["x"]:.2f}" y="{s["y"]:.2f}" font-size="{s["size"]}" '
                     f'font-family="sans-serif" fill="{COL["text"]}">{s["s"]}</text>')
    L.append("</svg>")
    Path(out).write_text("\n".join(L), encoding="utf-8")
    return str(out)


def _write_png(shapes, page, out, dpi):
    from PIL import Image, ImageDraw
    SS = 2  # 슈퍼샘플링 배율(안티앨리어스)
    px = lambda mm: mm / 25.4 * dpi * SS
    W, H = int(px(page.paper_w)), int(px(page.paper_h))
    im = Image.new("RGB", (W, H), COL["bg"])
    dr = ImageDraw.Draw(im)
    for s in shapes:
        w = max(1, round(px(s.get("w", LINE_MM))))
        if s["k"] == "poly":
            pts = [(px(x), px(y)) for x, y in s["pts"]]
            if s.get("fill") and s["fill"] != "none":
                dr.polygon(pts, fill=s["fill"], outline=s["stroke"])
            else:
                dr.line(pts, fill=s["stroke"], width=w, joint="curve")
        elif s["k"] == "circle":
            cx, cy, r = px(s["cx"]), px(s["cy"]), px(s["r"])
            dr.ellipse([cx - r, cy - r, cx + r, cy + r], outline=s["stroke"], width=w)
        elif s["k"] == "rect":
            st = s.get("stroke", "none")
            dr.rectangle([px(s["x0"]), px(s["y0"]), px(s["x1"]), px(s["y1"])],
                         fill=(s.get("fill") if s.get("fill", "none") != "none" else None),
                         outline=(st if st != "none" else None), width=max(w, 1))
        elif s["k"] == "line":
            dr.line([px(s["a"][0]), px(s["a"][1]), px(s["b"][0]), px(s["b"][1])],
                    fill=s["stroke"], width=w)
        elif s["k"] == "text":
            font = _load_font(int(px(s["size"])))
            txt = s["s"] if font[1] else _ascii_fallback(s["s"])
            dr.text((px(s["x"]), px(s["y"] - s["size"])), txt, fill=COL["text"], font=font[0])
    im = im.resize((W // SS, H // SS), Image.LANCZOS)
    im.save(out, dpi=(dpi, dpi))
    return str(out)


# ── 진입점 1 : 2D A4 ───────────────────────────────────────────────────────

def _draw_shapes(paths, circles, sheet):
    out = []
    for tag, vs in paths:
        out.append({"k": "poly", "pts": [(sheet.sx(x), sheet.sy(y)) for x, y in vs],
                    "stroke": COL[tag]})
    for tag, cx, cy, r in circles:
        out.append({"k": "circle", "cx": sheet.sx(cx), "cy": sheet.sy(cy),
                    "r": r * sheet.scale, "stroke": COL["hole"]})
    return out


def render_doc_2d(top_dxf=None, bottom_dxf=None, out_dir=".", dpi=300,
                  doc_width=DOC_W_MM, max_height=DOC_MAX_H_MM, min_scale=MIN_SCALE,
                  title="", split=True, **_legacy):
    """2D 벡터 도면 — **치수·축척의 정본**. 전체도 1장 + 필요하면 상세 시트 N장.

    축척을 낮춰 한 장에 밀어 넣지 않는다. `min_scale` 밑으로 내려가야 하면 **나눈다**.
    """
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    paths, circles, bbox = _collect_2d(top_dxf, bottom_dxf)
    name = Path(top_dxf or bottom_dxf).stem
    srcs = " + ".join(Path(p).name for p in (top_dxf, bottom_dxf) if p)
    plan = _plan_sheets(bbox, doc_width, max_height, min_scale)
    sheets = []

    # ── 전체도 — 축척 제한 없이 한 장. 분할 경계를 표시한다
    ov = Sheet(bbox, doc_width, max_height)
    shapes = _draw_shapes(paths, circles, ov)
    if split and plan["n"] > 1:
        x0, _, x1, _ = bbox
        seg = (x1 - x0) / plan["n"]
        for i in range(1, plan["n"]):
            xc = ov.sx(x0 + i * seg)
            shapes.append({"k": "line", "a": (xc, MARGIN_MM),
                           "b": (xc, ov.paper_h - MARGIN_MM - TITLE_H_MM),
                           "stroke": COL["cut"], "w": 0.3, "dash": True})
    extra = "색: top=구리 · bottom=군청 · hole=적"
    if split and plan["n"] > 1:
        extra += f"   ·   분할 경계 {plan['n']-1}개 — 상세는 {plan['n']}장"
    _frame_and_title(shapes, ov, title or f"{name} — 2D 전체도", srcs, extra=extra)
    sheets.append({"kind": "overview", "i": 0, "scale": round(ov.scale, 4),
                   "svg": _write_svg(shapes, ov, out_dir / f"{name}_2d_overview.svg"),
                   "png": _write_png(shapes, ov, out_dir / f"{name}_2d_overview_{dpi}dpi.png", dpi)})

    # ── 상세 시트 — min_scale 을 지키는 축척으로 나눠 그린다(캔버스 밖은 자동으로 잘린다)
    if split and plan["n"] > 1:
        _, y0, _, y1 = bbox
        for i, (a, b) in enumerate(plan["ranges"], 1):
            sh = Sheet((a, y0, b, y1), doc_width, max_height, scale=plan["scale"])
            sp = _draw_shapes(paths, circles, sh)
            _frame_and_title(sp, sh, title or f"{name} — 2D 상세 {i}/{plan['n']}", srcs,
                             extra=f"· x {a:.1f}~{b:.1f} mm · 겹침 {OVERLAP_MM:g} mm")
            sheets.append({
                "kind": "detail", "i": i, "x_mm": [round(a, 3), round(b, 3)],
                "scale": round(sh.scale, 4),
                "svg": _write_svg(sp, sh, out_dir / f"{name}_2d_detail{i}of{plan['n']}.svg"),
                "png": _write_png(sp, sh,
                                  out_dir / f"{name}_2d_detail{i}of{plan['n']}_{dpi}dpi.png", dpi)})

    return {"sheets": sheets, "n_sheets": len(sheets), "plan": plan,
            "bbox_mm": [round(v, 3) for v in bbox], "unit_note": UNIT_NOTE,
            "svg": sheets[0]["svg"], "png": sheets[0]["png"],   # 옛 호출부 호환
            "scale": sheets[0]["scale"],
            "역할": "치수·축척의 정본. preview 는 3D 캡처(render_doc_3d)를 쓴다"}


def render_a4_2d(top_dxf=None, bottom_dxf=None, out_dir=".", dpi=300, paper="A4",
                 orientation="auto", title=""):
    """옛 이름 — 용지 인자를 문서 폭으로 옮겨 받는다(호환)."""
    pw, ph = PAPERS[paper.upper()]
    return render_doc_2d(top_dxf, bottom_dxf, out_dir, dpi,
                         doc_width=max(pw, ph) if orientation == "landscape" else pw,
                         title=title)


# ── 진입점 2 : 정적 3D · 시점 캡처 ──────────────────────────────────────────────────

def _project(v, cen, yaw, pitch, zex, projection, dist):
    x, y, z = v[0] - cen[0], v[1] - cen[1], (v[2] - cen[2]) * zex
    cy, sy = math.cos(yaw), math.sin(yaw)
    cp, sp = math.cos(pitch), math.sin(pitch)
    x1, y1 = x * cy - y * sy, x * sy + y * cy
    u, w, d = x1, y1 * cp - z * sp, y1 * sp + z * cp
    if projection == "persp":
        f = dist / max(dist - d, 1e-3)
        u, w = u * f, w * f
    return u, w, d


def _render_3d_one(boxes, cen, span, view, sheet_args, name, srcs, out_dir, dpi, title):
    yaw_deg, pitch_deg, zex, projection = view
    yaw, pitch = math.radians(yaw_deg), math.radians(pitch_deg)
    faces = []
    for b in boxes:
        pv = [_project(v, cen, yaw, pitch, zex, projection, dist=3 * span) for v in b["v"]]
        for f in b["f"]:
            poly = [pv[i] for i in f]
            faces.append((sum(p[2] for p in poly) / len(poly), b.get("p", 0), poly, b["c"]))
    # painter: 우선순위(기판→gnd→hole→top copper) 대분류 후 깊이순 — 대면적 기판면이
    # 평균 깊이 하나로 patch 절반을 덮는 문제를 막는다
    faces.sort(key=lambda t: (t[1], t[0]))
    us = [p[0] for f in faces for p in f[2]]
    ws = [p[1] for f in faces for p in f[2]]
    sh = Sheet((min(us), min(ws), max(us), max(ws)), *sheet_args)
    shapes = [{"k": "poly", "fill": c, "stroke": "#00000022", "w": 0.12,
               "pts": [(sh.sx(u), sh.sy(w)) for u, w, _ in poly]} for _, _, poly, c in faces]
    zn = f" · z확대 ×{zex:g} (두께 과장)" if zex > 1.001 else " · z확대 없음"
    note = f"{projection} · yaw {yaw_deg:.0f}° · pitch {pitch_deg:.0f}°{zn}"
    # ★ 축척 자리에 축척을 쓰지 않는다 — 투영본이다. 축척이 적혀 있으면 사람이 재려 든다.
    _frame_and_title(shapes, sh, title, srcs, extra=note,
                     scale_text="투영 — 치수 정본 아님(2D 도면이 정본)")
    return shapes, sh, note


def render_doc_3d(top_dxf=None, bottom_dxf=None, out_dir=".", dpi=300,
                  doc_width=DOC_W_MM, max_height=DOC_MAX_H_MM,
                  views=("iso", "top"), projection="ortho",
                  yaw_deg=None, pitch_deg=None, z_exaggerate=None,
                  h_sub=1.524, t_cu=0.035, title="", **_legacy):
    """정적 3D + **시점 캡처**. `views` 의 각 시점마다 한 장씩 낸다.

    preview 이미지는 여기서 나온다 — 3D 하나를 잘 만들면 preview 가 따라온다.
    ★ 어느 장도 **치수 정본이 아니다.** 투영과 z 확대가 걸려 있다(표제란에 명기).
    """
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    boxes, meta = CR.build_model(top_dxf=top_dxf and str(top_dxf),
                                 bottom_dxf=bottom_dxf and str(bottom_dxf),
                                 h_sub=h_sub, t_cu=t_cu)
    if not boxes:
        raise ValueError("build_model 결과가 비었다 — 입력 DXF를 확인할 것")
    pts = [p for b in boxes for p in b["v"]]
    cen = [(min(p[i] for p in pts) + max(p[i] for p in pts)) / 2 for i in range(3)]
    span = max(max(p[i] for p in pts) - min(p[i] for p in pts) for i in range(3))
    name = Path(top_dxf or bottom_dxf).stem
    srcs = " + ".join(Path(p).name for p in (top_dxf, bottom_dxf) if p)

    out = []
    for vname in views:
        y0, p0, z0 = VIEWS.get(vname, VIEWS["iso"])
        v = (yaw_deg if yaw_deg is not None and vname == "iso" else y0,
             pitch_deg if pitch_deg is not None and vname == "iso" else p0,
             z_exaggerate if z_exaggerate is not None and vname == "iso" else z0,
             projection)
        shapes, sh, note = _render_3d_one(
            boxes, cen, span, v, (doc_width, max_height), name, srcs, out_dir, dpi,
            title or f"{name} — 3D {vname}")
        stem = f"{name}_3d_{vname}"
        out.append({"view": vname, "yaw_deg": v[0], "pitch_deg": v[1], "z_exaggerate": v[2],
                    "projection": projection,
                    "svg": _write_svg(shapes, sh, out_dir / f"{stem}.svg"),
                    "png": _write_png(shapes, sh, out_dir / f"{stem}_{dpi}dpi.png", dpi)})

    prev = next((v for v in out if v["view"] == "iso"), out[0])
    return {"views": out, "n_views": len(out), "meta": meta,
            "preview": prev["png"],
            "svg": prev["svg"], "png": prev["png"],          # 옛 호출부 호환
            "역할": ("형상 preview. 치수를 읽는 자리가 아니다 — 정본은 2D 벡터 도면"
                   "(render_doc_2d)")}


def render_a4_3d(top_dxf=None, bottom_dxf=None, out_dir=".", dpi=300, paper="A4",
                 yaw_deg=-35.0, pitch_deg=32.0, projection="ortho", z_exaggerate=18.0,
                 h_sub=1.524, t_cu=0.035, title=""):
    """옛 이름 — iso 한 장만 낸다(호환)."""
    pw, ph = PAPERS[paper.upper()]
    return render_doc_3d(top_dxf, bottom_dxf, out_dir, dpi, doc_width=pw,
                         views=("iso",), projection=projection, yaw_deg=yaw_deg,
                         pitch_deg=pitch_deg, z_exaggerate=z_exaggerate,
                         h_sub=h_sub, t_cu=t_cu, title=title)


# ── 자기 시험 ──────────────────────────────────────────────────────────────

def self_test():
    ok = fail = 0

    def chk(n, cond, d=""):
        nonlocal ok, fail
        if cond:
            ok += 1; print(f"  PASS  {n}")
        else:
            fail += 1; print(f"  FAIL  {n}  {d}")

    print("[render_page.py 자기 시험 — 실물]")
    base = Path(__file__).resolve().parent.parent / "handoff/04_experiment_data/Antenna_CAD_ECO"
    top, bot = base / "Top_20260227.dxf", base / "Bottom_20260227.dxf"
    if not top.exists():
        print("  건너뜀 — 실물 없음"); return 2

    # ── 결함 F-25 회귀 — 원점이 0 이 아닌 도면이 통째로 밀리는가
    sh = Sheet((-285.0, -20.0, 285.0, 20.0))
    chk("원점이 0 이 아니어도 최상단이 도곽 안", abs(sh.sy(20.0) - MARGIN_MM) < 0.05,
        f"{sh.sy(20.0):.3f} vs {MARGIN_MM}")
    chk("최하단도 도곽 안", sh.sy(-20.0) <= sh.paper_h - MARGIN_MM - TITLE_H_MM + 0.05,
        f"{sh.sy(-20.0):.3f}")
    chk("좌우가 도곽 안", sh.sx(-285.0) >= MARGIN_MM - 0.05
        and sh.sx(285.0) <= sh.paper_w - MARGIN_MM + 0.05)

    # ── 문서 기준 — 폭 고정 · 높이 자유
    chk("캔버스 폭이 문서 폭", abs(sh.paper_w - DOC_W_MM) < 1e-9, str(sh.paper_w))
    tall = Sheet((0.0, 0.0, 10.0, 10.0))
    chk("높이는 내용을 따른다(정사각은 정사각으로)",
        abs((tall.paper_h - 2 * MARGIN_MM - TITLE_H_MM)
            - (DOC_W_MM - 2 * MARGIN_MM)) < 0.05, str(tall.paper_h))

    import tempfile
    out = Path(tempfile.mkdtemp())
    r2 = render_doc_2d(top, bot, out, dpi=100)

    # ── 축척이 판독성을 이기지 못한다
    det = [x for x in r2["sheets"] if x["kind"] == "detail"]
    chk(f"넘치면 나눈다 — 상세 {len(det)}장", len(det) >= 2, str(r2["plan"]))
    chk("상세는 min_scale 을 지킨다",
        all(x["scale"] >= MIN_SCALE * 0.99 for x in det), str([x["scale"] for x in det]))
    chk("전체도는 축척 제한 없이 한 장",
        r2["sheets"][0]["kind"] == "overview" and r2["sheets"][0]["scale"] < MIN_SCALE)
    chk("상세 구간이 겹친다",
        det[0]["x_mm"][1] > det[1]["x_mm"][0], f"{det[0]['x_mm']} {det[1]['x_mm']}")
    chk("전 구간을 덮는다",
        abs(det[0]["x_mm"][0] - r2["bbox_mm"][0]) < 0.05
        and abs(det[-1]["x_mm"][1] - r2["bbox_mm"][2]) < 0.05)

    # ── 단위는 값이 아니라 산지와 함께
    svg = Path(r2["sheets"][0]["svg"]).read_text(encoding="utf-8")
    chk("표제란에 단위 산지가 실린다", "INSUNITS 미기재" in svg)
    chk("가정임을 밝힌다", "가정" in UNIT_NOTE)

    # ── 산출물 오프라인 자기완결 (AGENTS T-6)
    chk("SVG 에 외부 URL 이 없다",
        "http://" not in svg.replace("http://www.w3.org/2000/svg", "")
        and "https://" not in svg)

    # ── 3D — preview 이고 치수 정본이 아니다
    r3 = render_doc_3d(top, bot, out, dpi=100, views=("iso", "top"))
    chk("시점마다 한 장", r3["n_views"] == 2, str(r3["n_views"]))
    chk("preview 는 iso", Path(r3["preview"]).name.endswith("_3d_iso_100dpi.png"),
        r3["preview"])
    s3 = Path(r3["views"][0]["svg"]).read_text(encoding="utf-8")
    chk("3D 표제란에 축척을 적지 않는다", "축척" not in s3)
    chk("치수 정본이 아님을 밝힌다", "치수 정본 아님" in s3)
    chk("top 시점은 두께를 과장하지 않는다",
        next(v for v in r3["views"] if v["view"] == "top")["z_exaggerate"] == 1.0)
    chk("iso 는 z 확대를 밝힌다", "z확대" in Path(r3["views"][0]["svg"]).read_text("utf-8"))

    # ── 역할 구분이 반환값에 실린다
    # 결함 F-27 회귀 — 라이브러리는 SystemExit 을 던지지 않는다
    try:
        render_doc_2d(out / "없는파일.dxf", None, out, dpi=50)
        caught = "없음"
    except SystemExit:
        caught = "SystemExit"
    except Exception as e:
        caught = type(e).__name__
    chk("판독 실패가 SystemExit 이 아니다", caught not in ("SystemExit", "없음"), caught)

    chk("2D 가 정본임을 말한다", "정본" in r2["역할"])
    chk("3D 가 preview 임을 말한다", "preview" in r3["역할"] and "정본" in r3["역할"])

    print(f"\n결과: {ok}/{ok + fail} PASS")
    return 0 if fail == 0 else 1


# ── CLI ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="문서 기준 도안 렌더 (2D 정본 + 3D 캡처)")
    ap.add_argument("top"); ap.add_argument("bottom", nargs="?")
    ap.add_argument("--out", default="render_out")
    ap.add_argument("--dpi", type=int, default=300)
    ap.add_argument("--doc-width", type=float, default=DOC_W_MM, dest="doc_width",
                    help="문서 본문 폭(mm) — 캔버스 폭. 용지가 아니다")
    ap.add_argument("--max-height", type=float, default=DOC_MAX_H_MM, dest="max_height")
    ap.add_argument("--min-scale", type=float, default=MIN_SCALE, dest="min_scale",
                    help="이보다 축척을 낮추지 않는다. 넘치면 분할한다")
    ap.add_argument("--no-split", action="store_true", help="분할하지 않고 전체도만")
    ap.add_argument("--views", default="iso,top", help="3D 시점 목록: iso,top,front")
    ap.add_argument("--proj", default="ortho", choices=["ortho", "persp"])
    ap.add_argument("--yaw", type=float, default=None)
    ap.add_argument("--pitch", type=float, default=None)
    ap.add_argument("--zex", type=float, default=None)
    ap.add_argument("--title", default="")
    if len(sys.argv) > 1 and sys.argv[1] == "self-test":
        raise SystemExit(self_test())
    a = ap.parse_args()
    r2 = render_doc_2d(a.top, a.bottom, a.out, a.dpi, a.doc_width, a.max_height,
                       a.min_scale, title=a.title, split=not a.no_split)
    r3 = render_doc_3d(a.top, a.bottom, a.out, a.dpi, a.doc_width, a.max_height,
                       views=tuple(v.strip() for v in a.views.split(",") if v.strip()),
                       projection=a.proj, yaw_deg=a.yaw, pitch_deg=a.pitch,
                       z_exaggerate=a.zex, title=a.title)
    print(f"2D  시트 {r2['n_sheets']}장 · {r2['plan']['why']}")
    for sh in r2["sheets"]:
        print(f"    [{sh['kind']}{sh['i'] or ''}] 축척 {sh['scale']}  {Path(sh['png']).name}")
    print(f"    {r2['unit_note']}")
    print(f"3D  시점 {r3['n_views']}종 · preview = {Path(r3['preview']).name}")
    for v in r3["views"]:
        print(f"    [{v['view']}] yaw {v['yaw_deg']:.0f}° pitch {v['pitch_deg']:.0f}° "
              f"z×{v['z_exaggerate']:g}  {Path(v['png']).name}")
