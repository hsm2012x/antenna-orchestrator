#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""_antenna/cad_render.py — CAD 도면 → 고화질 2D SVG · 오프라인 3D 뷰(HTML) 렌더러.

원칙
  · **외부 JS/CSS 없음**(폐쇄망). three.js·CDN 미사용 — 순수 canvas2D + 인라인 바닐라 JS.
  · DXF는 형상에서 직접 벡터로 그린다(썸네일 아님) → 확대해도 안 깨진다.
  · DWG는 본문이 압축이라 형상을 못 읽는다 → 내장 프리뷰(저해상 512px)만 가능.
    고화질이 필요하면 ODA 변환이 선행되어야 한다는 사실을 화면에 명시한다.
정본 참조: docs/study/notebooks/08_antenna_cad_em.ipynb (동일 파서·기하 추출 로직)
"""
from __future__ import annotations
import json, math, struct, re, html, sys
from pathlib import Path

# Windows 콘솔 기본 코덱(cp949)은 '—'(U+2014) 같은 문자를 못 쓴다 → 표준출력을 UTF-8로 고정.
# 파일 IO는 전부 encoding="utf-8" 명시(아래). 환경변수 PYTHONUTF8=1 로도 같은 효과.
for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

# 밝은 회색 배경 기준 팔레트(2026-07-29 사용자 요청). 어두운 배경은 뷰어의 "배경" 버튼으로 전환.
PAL = {"copper": "#b05f33", "copper_dark": "#7d4123", "sub": "#4f8f5f", "sub_dark": "#396a44",
       "gnd": "#97753f", "gnd_dark": "#6f5730", "hole": "#333b44", "bg": "#e9ecef",
       "bg_dark": "#0f1419", "page": "#f5f7f9", "card": "#ffffff", "line": "#dde3e8",
       "ink": "#1f2933", "muted": "#667585", "accent": "#2f6096"}

# ───────────────────────────── DXF 파서(노트북 08과 동일 규약) ─────────────────
def dxf_tags(path):
    L = [l.strip() for l in Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()]
    out = []
    for i in range(0, len(L) - 1, 2):
        try: out.append((int(L[i]), L[i + 1]))
        except ValueError: pass
    return out

def dxf_read(path):
    polys, circles, lines, cur = [], [], [], None
    ents, e = [], None
    for code, val in dxf_tags(path):
        if code == 0:
            if e: ents.append(e)
            e = None if val in ("SECTION", "ENDSEC", "EOF") else {"t": val, "g": []}
        elif e is not None: e["g"].append((code, val))
    if e: ents.append(e)
    for en in ents:
        d = {}
        for k, v in en["g"]: d.setdefault(k, v)
        t = en["t"]
        if t == "POLYLINE":
            cur = {"layer": d.get(8, "0"), "flag": int(d.get(70, 0)),
                   "elev": float(d.get(30, 0) or 0), "verts": []}
        elif t == "VERTEX" and cur is not None:
            cur["verts"].append((float(d.get(10, 0)), float(d.get(20, 0))))
        elif t == "SEQEND":
            if cur: polys.append(cur); cur = None
        elif t == "CIRCLE":
            circles.append({"layer": d.get(8, "0"), "c": (float(d.get(10, 0)), float(d.get(20, 0))),
                            "z": float(d.get(30, 0) or 0), "r": float(d.get(40, 0))})
    if cur: polys.append(cur)
    return {"polylines": polys, "circles": circles, "lines": lines}

def segments(g, tol=1e-6):
    H = []
    for p in g["polylines"]:
        vs = p["verts"]
        seq = vs + [vs[0]] if (p["flag"] & 1) and vs else vs
        for a, b in zip(seq, seq[1:]):
            if abs(a[1] - b[1]) < tol and abs(a[0] - b[0]) > tol:
                H.append((min(a[0], b[0]), max(a[0], b[0]), a[1]))
    return H

def extract_array(top_dxf, y_split=2.0, min_len=5.0):
    g = dxf_read(top_dxf); H = segments(g)
    up = sorted([h for h in H if h[2] > y_split and h[1] - h[0] >= min_len])
    lo = sorted([h for h in H if h[2] < -y_split and h[1] - h[0] >= min_len])
    patches = []
    for u in up:
        m = [l for l in lo if abs(l[0] - u[0]) < 0.05 and abs(l[1] - u[1]) < 0.05]
        patches.append({"x0": u[0], "x1": u[1], "W": (u[2] - m[0][2]) if m else 2 * u[2]})
    feed = sorted([(h[0], h[1], 2 * h[2]) for h in H if 0 < h[2] <= y_split and h[1] - h[0] > 0.3])
    return {"patches": patches, "feed": feed}

# ───────────────────────────── DWG 내장 프리뷰 ────────────────────────────────
_SENT = bytes([0x1F,0x25,0x6D,0x07,0xD4,0x36,0x28,0x28,0x9D,0x57,0xCA,0x3F,0x9D,0x44,0x10,0x2B])

def dwg_preview(path, out_dir):
    b = Path(path).read_bytes()
    try:
        off = struct.unpack_from("<I", b, 0x0D)[0]
        if not (0 < off < len(b)) or b[off:off + 16] != _SENT: return None
        p = off + 20; n = b[p]; p += 1
        for _ in range(n):
            code = b[p]; s = struct.unpack_from("<I", b, p + 1)[0]
            sz = struct.unpack_from("<I", b, p + 5)[0]; p += 9
            data = b[s:s + sz]
            stem = Path(out_dir) / (Path(path).stem + "_preview")
            if code == 6 and data[:8] == b"\x89PNG\r\n\x1a\n":
                f = stem.with_suffix(".png"); f.write_bytes(data); return str(f)
            if code == 2 and sz > 40:
                hsz, w, h, _pl, bpp = struct.unpack_from("<IiiHH", data, 0)
                ncol = struct.unpack_from("<I", data, 32)[0] or (1 << bpp if bpp <= 8 else 0)
                f = stem.with_suffix(".bmp")
                f.write_bytes(b"BM" + struct.pack("<IHHI", 14 + sz, 0, 0, 14 + hsz + ncol * 4) + data)
                return str(f)
    except Exception:
        return None
    return None

def enhance_preview(src, out=None, target_w=1600, bg_tol=18):
    """DWG 내장 프리뷰(512px 저해상)를 그나마 볼 만하게: 배경 여백 자동 크롭 + 업스케일.
    Pillow가 있으면 쓰고(없으면 원본 그대로). matplotlib가 이미 Pillow에 의존하므로 사실상 항상 있다.
    ※ 근본 해결은 아니다 — 원본이 512px다. 고화질은 ODA 변환 후 벡터 렌더가 정답."""
    try:
        from PIL import Image
    except ImportError:
        return str(src)
    im = Image.open(src).convert("RGB")
    px = im.load(); w, h = im.size
    bg = px[0, 0]
    def near(c): return all(abs(c[i] - bg[i]) <= bg_tol for i in range(3))
    # 행·열 밀도로 자른다 — 구석의 탭 라벨처럼 픽셀 수가 미미한 이질 영역은 버린다
    step = max(1, min(w, h) // 500)
    rows = [0] * h; cols = [0] * w
    for yy in range(0, h, step):
        for xx in range(0, w, step):
            if not near(px[xx, yy]): rows[yy] += 1; cols[xx] += 1
    def span(cnt, n):
        """내용이 있는 구간 중 **가장 큰 덩어리** 하나를 고른다.
        first~last를 그냥 쓰면 구석의 탭 라벨 한 줄 때문에 전체가 잡힌다."""
        mx = max(cnt) if cnt else 0
        if mx == 0: return 0, n - 1
        thr = max(1, mx * 0.03); gap = max(4, n // 40)
        groups, cur, blank = [], None, 0
        for i, c in enumerate(cnt):
            if c >= thr:
                if cur is None: cur = [i, i, 0]
                cur[1] = i; cur[2] += c; blank = 0
            elif cur is not None:
                blank += 1
                if blank > gap: groups.append(cur); cur = None
        if cur is not None: groups.append(cur)
        if not groups: return 0, n - 1
        g = max(groups, key=lambda t: t[2])          # 픽셀 총량이 가장 큰 덩어리
        return g[0], g[1]
    y0, y1 = span(rows, h); x0, x1 = span(cols, w)
    if x1 - x0 < w * 0.05 or y1 - y0 < h * 0.05:
        x0, y0, x1, y1 = 0, 0, w - 1, h - 1
    m = max(4, int(0.02 * max(x1 - x0, y1 - y0)))
    im = im.crop((max(0, x0 - m), max(0, y0 - m), min(w, x1 + m), min(h, y1 + m)))
    k = max(1.0, target_w / max(im.width, 1))
    im = im.resize((int(im.width * k), int(im.height * k)), Image.LANCZOS)
    out = out or (Path(src).with_name(Path(src).stem + "_hi.png"))
    im.save(out)
    # 잉크 비율 — 원본이 거의 비어 있으면 확대해봐야 의미 없다(정직하게 표시하려고 같이 반환)
    ink = len(XS) if False else sum(1 for c in rows) and sum(rows)
    total = max(1, (im.width // max(1, int(k))) * (im.height // max(1, int(k))))
    return {"path": str(out), "ink_ratio": round(min(1.0, sum(rows) * step * step / max(1, w * h)), 4)}


# ───────────────────────────── 2D SVG (벡터, 무한 확대) ───────────────────────
def render_svg(top_dxf=None, bottom_dxf=None, out=None, pad=8.0, title=""):
    """DXF 형상을 그대로 SVG 벡터로. 썸네일이 아니라 원본 좌표라 확대해도 안 깨진다."""
    shapes, xs, ys = [], [], []
    if bottom_dxf and Path(bottom_dxf).exists():
        gb = dxf_read(bottom_dxf)
        for p in gb["polylines"]:
            if len(p["verts"]) < 3: continue
            d = "M" + "L".join(f"{x:.4f},{-y:.4f}" for x, y in p["verts"]) + "Z"
            shapes.append(f'<path d="{d}" fill="{PAL["gnd"]}" fill-opacity=".35" '
                          f'stroke="{PAL["gnd_dark"]}" stroke-width=".3"/>')
            xs += [v[0] for v in p["verts"]]; ys += [v[1] for v in p["verts"]]
        for c in gb["circles"]:
            shapes.append(f'<circle cx="{c["c"][0]:.4f}" cy="{-c["c"][1]:.4f}" r="{c["r"]:.4f}" '
                          f'fill="{PAL["bg"]}" stroke="{PAL["accent"]}" stroke-width=".4"/>')
    if top_dxf and Path(top_dxf).exists():
        arr = extract_array(top_dxf)
        for p in arr["patches"]:
            shapes.append(f'<rect x="{p["x0"]:.4f}" y="{-p["W"]/2:.4f}" '
                          f'width="{p["x1"]-p["x0"]:.4f}" height="{p["W"]:.4f}" '
                          f'fill="{PAL["copper"]}" stroke="{PAL["copper_dark"]}" stroke-width=".15"/>')
            xs += [p["x0"], p["x1"]]; ys += [-p["W"] / 2, p["W"] / 2]
        for a, b, w in arr["feed"]:
            shapes.append(f'<rect x="{a:.4f}" y="{-w/2:.4f}" width="{b-a:.4f}" height="{w:.4f}" '
                          f'fill="{PAL["copper"]}" stroke="none"/>')
    if not xs: xs, ys = [0, 1], [0, 1]
    x0, x1, y0, y1 = min(xs) - pad, max(xs) + pad, min(ys) - pad, max(ys) + pad
    W, H = x1 - x0, y1 - y0
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{x0:.3f} {-y1:.3f} {W:.3f} {H:.3f}" '
           f'width="{min(2400, W*4):.0f}" height="{min(2400, W*4)*H/W:.0f}">'
           f'<rect x="{x0:.3f}" y="{-y1:.3f}" width="{W:.3f}" height="{H:.3f}" fill="{PAL["bg"]}"/>'
           + "".join(shapes) +
           (f'<text x="{x0+2:.2f}" y="{-y1+5:.2f}" font-size="3.2" fill="{PAL["muted"]}" '
            f'font-family="monospace">{html.escape(title)}</text>' if title else "") + "</svg>")
    if out: Path(out).write_text(svg, encoding="utf-8")
    return svg

# ───────────────────────────── 3D 뷰 (오프라인 · 라이브러리 0) ────────────────
# 페인터 알고리즘은 큰 면 하나에 작은 면이 얹히면 깨진다(기판 상면 위의 패치).
# 층이 z로 엄격히 쌓여 있으므로 **그룹 우선순위**를 먼저 주고 그 안에서만 깊이 정렬한다.
_PRIO = {"gnd": 0, "substrate": 1, "hole": 2, "top": 3}

def _box(x0, x1, y0, y1, z0, z1, color, group):
    v = [(x0,y0,z0),(x1,y0,z0),(x1,y1,z0),(x0,y1,z0),(x0,y0,z1),(x1,y0,z1),(x1,y1,z1),(x0,y1,z1)]
    f = [(0,1,2,3),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)]
    return {"v": [[round(c,4) for c in p] for p in v], "f": f, "c": color, "g": group,
            "p": _PRIO.get(group, 5)}

def build_model(top_dxf=None, bottom_dxf=None, h_sub=1.524, t_cu=0.035, y_split=2.0):
    """DXF → 3D 박스 집합. 좌표는 실제 mm(과장은 뷰어에서 z배율로)."""
    boxes, meta = [], {"board": None, "holes": []}
    if bottom_dxf and Path(bottom_dxf).exists():
        gb = dxf_read(bottom_dxf)
        for p in gb["polylines"]:
            if len(p["verts"]) < 3: continue
            X = [v[0] for v in p["verts"]]; Y = [v[1] for v in p["verts"]]
            bx0, bx1, by0, by1 = min(X), max(X), min(Y), max(Y)
            meta["board"] = [round(bx1-bx0,3), round(by1-by0,3), h_sub]
            boxes.append(_box(bx0, bx1, by0, by1, -h_sub, 0.0, PAL["sub"], "substrate"))
            boxes.append(_box(bx0, bx1, by0, by1, -h_sub-t_cu, -h_sub, PAL["gnd"], "gnd"))
        for c in gb["circles"]:
            cx, cy, r = c["c"][0], c["c"][1], c["r"]
            n, ring = 16, []
            for i in range(n):
                a1 = 2*math.pi*i/n; a2 = 2*math.pi*(i+1)/n
                ring.append({"v": [[round(cx+r*math.cos(a1),4), round(cy+r*math.sin(a1),4), round(-h_sub-t_cu-.01,4)],
                                   [round(cx+r*math.cos(a2),4), round(cy+r*math.sin(a2),4), round(-h_sub-t_cu-.01,4)],
                                   [round(cx+r*math.cos(a2),4), round(cy+r*math.sin(a2),4), round(0.01,4)],
                                   [round(cx+r*math.cos(a1),4), round(cy+r*math.sin(a1),4), round(0.01,4)]],
                             "f": [(0,1,2,3)], "c": PAL["hole"], "g": "hole", "p": _PRIO["hole"]})
            boxes += ring
            meta["holes"].append({"x": cx, "y": cy, "d": 2*r})
    if top_dxf and Path(top_dxf).exists():
        arr = extract_array(top_dxf, y_split)
        for p in arr["patches"]:
            boxes.append(_box(p["x0"], p["x1"], -p["W"]/2, p["W"]/2, 0.0, t_cu, PAL["copper"], "top"))
        for a, b, w in arr["feed"]:
            boxes.append(_box(a, b, -w/2, w/2, 0.0, t_cu, PAL["copper"], "top"))
        meta["n_patch"] = len(arr["patches"])
        if arr["patches"]:
            meta["aperture_mm"] = round(arr["patches"][-1]["x1"] - arr["patches"][0]["x0"], 3)
    return boxes, meta

_VIEWER_JS = r"""
const M = MODEL, GROUPS = {};
let yaw=-0.62, pitch=1.02, scale=1.0, panx=0, pany=0, zex=18, drag=null;
let BG='#e9ecef';      // 밝은 회색 기본 · '배경' 버튼으로 어둡게 전환
const cv=document.getElementById('cv'), ctx=cv.getContext('2d');
M.forEach(b=>GROUPS[b.g]=true);
function bounds(){let a=[1e9,1e9,1e9],b=[-1e9,-1e9,-1e9];
  M.forEach(o=>o.v.forEach(p=>{for(let i=0;i<3;i++){a[i]=Math.min(a[i],p[i]);b[i]=Math.max(b[i],p[i]);}}));
  return [a,b];}
const [BMIN,BMAX]=bounds();
const CEN=[(BMIN[0]+BMAX[0])/2,(BMIN[1]+BMAX[1])/2,(BMIN[2]+BMAX[2])/2];
function rot(p){const x=p[0]-CEN[0], y=p[1]-CEN[1], z=(p[2]-CEN[2])*zex;
  const cy=Math.cos(yaw), sy=Math.sin(yaw), cp=Math.cos(pitch), sp=Math.sin(pitch);
  const x1=x*cy-y*sy, y1=x*sy+y*cy;
  return [x1, y1*cp - z*sp, y1*sp + z*cp];}
function shade(hex, k){const n=parseInt(hex.slice(1),16);
  let r=(n>>16)&255,g=(n>>8)&255,b=n&255;
  r=Math.min(255,r*k)|0; g=Math.min(255,g*k)|0; b=Math.min(255,b*k)|0;
  return 'rgb('+r+','+g+','+b+')';}
let FIT=1, FCX=0, FCY=0;
function fit(){                                  // 현재 회전에서 화면에 꽉 차게
  const W=cv.clientWidth||900, H=cv.clientHeight||600;
  let a=[1e9,1e9], b=[-1e9,-1e9];
  for(const o of M){ if(!GROUPS[o.g]) continue;
    for(const p of o.v){ const r=rot(p);
      a[0]=Math.min(a[0],r[0]); b[0]=Math.max(b[0],r[0]);
      a[1]=Math.min(a[1],-r[2]); b[1]=Math.max(b[1],-r[2]); }}
  const w=Math.max(b[0]-a[0],1e-6), h=Math.max(b[1]-a[1],1e-6);
  FIT=Math.min((W-48)/w,(H-48)/h); FCX=-(a[0]+b[0])/2*FIT; FCY=-(a[1]+b[1])/2*FIT;
}
function draw(){
  const W=cv.width=cv.clientWidth*devicePixelRatio, H=cv.height=cv.clientHeight*devicePixelRatio;
  ctx.setTransform(1,0,0,1,0,0); ctx.fillStyle=BG; ctx.fillRect(0,0,W,H);
  const s=scale*FIT*devicePixelRatio;
  const cx=W/2+(panx+FCX)*devicePixelRatio, cy=H/2+(pany+FCY)*devicePixelRatio;
  const tris=[];
  for(const o of M){ if(!GROUPS[o.g]) continue;
    const R=o.v.map(rot);
    for(const f of o.f){
      const pts=f.map(i=>R[i]);
      const d=pts.reduce((a,p)=>a+p[1],0)/pts.length;
      const u=[pts[1][0]-pts[0][0],pts[1][1]-pts[0][1],pts[1][2]-pts[0][2]];
      const v=[pts[2][0]-pts[0][0],pts[2][1]-pts[0][1],pts[2][2]-pts[0][2]];
      const n=[u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0]];
      const L=Math.hypot(n[0],n[1],n[2])||1;
      const lam=Math.abs((n[0]*0.35 + n[1]*-0.5 + n[2]*0.79)/L);
      tris.push({d, p:o.p, pts, c: shade(o.c, (BG==='#e9ecef'?0.62:0.45)+0.62*lam)});
    }}
  tris.sort((a,b)=> (a.p-b.p) || (b.d-a.d));   // 층 우선 → 같은 층 안에서 깊이
  for(const t of tris){ ctx.beginPath();
    t.pts.forEach((p,i)=>{const X=cx+p[0]*s, Y=cy-p[2]*s; i?ctx.lineTo(X,Y):ctx.moveTo(X,Y);});
    ctx.closePath(); ctx.fillStyle=t.c; ctx.fill();
    ctx.strokeStyle=(BG==='#e9ecef'?'rgba(0,0,0,.20)':'rgba(0,0,0,.28)');
    ctx.lineWidth=.5*devicePixelRatio; ctx.stroke();}
  document.getElementById('hud').textContent =
    'yaw '+(yaw*57.3).toFixed(0)+'°  pitch '+(pitch*57.3).toFixed(0)+'°  z×'+zex+'  faces '+tris.length;
}
cv.addEventListener('mousedown',e=>drag={x:e.clientX,y:e.clientY,b:e.button});
addEventListener('mouseup',()=>drag=null);
addEventListener('mousemove',e=>{if(!drag)return;
  const dx=e.clientX-drag.x, dy=e.clientY-drag.y; drag.x=e.clientX; drag.y=e.clientY;
  if(drag.b===2||e.shiftKey){panx+=dx;pany+=dy;} else {yaw+=dx*0.008; pitch=Math.max(0.02,Math.min(1.55,pitch+dy*0.008)); fit();}
  draw();});
cv.addEventListener('contextmenu',e=>e.preventDefault());
cv.addEventListener('wheel',e=>{e.preventDefault(); scale*=Math.exp(-e.deltaY*0.0012); draw();},{passive:false});
function setView(v){ if(v==='top'){yaw=0;pitch=1.5707;} if(v==='iso'){yaw=-0.62;pitch=1.02;}
  if(v==='front'){yaw=0;pitch=0.03;} if(v==='side'){yaw=1.5707;pitch=0.05;}
  scale=1; panx=pany=0; fit(); draw();}
function toggle(g,el){GROUPS[g]=el.checked; fit(); draw();}
function setZ(v){zex=+v; fit(); draw();}
function refit(){scale=1;panx=pany=0;fit();draw();}
function toggleBG(){BG = (BG==='#e9ecef') ? '#0f1419' : '#e9ecef';
  document.getElementById('cv').style.background=BG; draw();}
addEventListener('resize',()=>{fit();draw();});
addEventListener('keydown',e=>{if(e.key==='f'||e.key==='F')refit();});
fit(); draw();
"""

_VIEWER_CSS = """
*{box-sizing:border-box} body{margin:0;background:#f5f7f9;color:#1f2933;
  font:13px/1.5 -apple-system,'Malgun Gothic',Segoe UI,sans-serif}
header{padding:10px 16px;border-bottom:1px solid #dde3e8;background:#fff;
  display:flex;gap:16px;align-items:baseline;flex-wrap:wrap}
h1{font-size:15px;margin:0;font-weight:600} .sub{color:#667585;font-size:12px}
.wrap{display:grid;grid-template-columns:1fr 268px;height:calc(100vh - 46px)}
#cv{width:100%;height:100%;display:block;cursor:grab;background:#e9ecef}
aside{border-left:1px solid #dde3e8;background:#fff;padding:14px;overflow:auto}
h2{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:#667585;margin:16px 0 8px}
h2:first-child{margin-top:0}
label{display:flex;gap:8px;align-items:center;padding:3px 0}
button{background:#fff;color:#1f2933;border:1px solid #cfd7de;border-radius:5px;
  padding:5px 10px;cursor:pointer;font-size:12px;margin:0 4px 4px 0}
button:hover{background:#eef3f7;border-color:#2f6096}
table{border-collapse:collapse;width:100%;font-size:12px} td{padding:3px 0;vertical-align:top}
td:first-child{color:#667585;padding-right:10px;white-space:nowrap}
#hud{position:absolute;left:16px;bottom:12px;color:#8a95a1;font:11px monospace;pointer-events:none}
.note{color:#667585;font-size:11px;line-height:1.6;margin-top:10px;border-top:1px solid #dde3e8;padding-top:10px}
input[type=range]{width:100%}
.sw{width:11px;height:11px;border-radius:2px;display:inline-block;border:1px solid rgba(0,0,0,.12)}
"""

def render_html3d(top_dxf=None, bottom_dxf=None, out=None, title="", h_sub=1.524,
                  t_cu=0.035, extra_rows=None):
    """자립형 3D 뷰 HTML. 외부 리소스 0 — 폐쇄망/오프라인에서 그대로 열린다."""
    boxes, meta = build_model(top_dxf, bottom_dxf, h_sub, t_cu)
    rows = [("보드", f'{meta["board"][0]} × {meta["board"][1]} mm' if meta["board"] else "—"),
            ("기판 두께", f"{h_sub} mm"), ("동박", f"{t_cu} mm"),
            ("패치", str(meta.get("n_patch", "—"))),
            ("개구장", f'{meta.get("aperture_mm", "—")} mm'),
            ("홀", ", ".join(f'φ{h["d"]} @({h["x"]},{h["y"]})' for h in meta["holes"]) or "—")]
    rows += (extra_rows or [])
    tbl = "".join(f"<tr><td>{html.escape(str(k))}</td><td>{html.escape(str(v))}</td></tr>" for k, v in rows)
    groups = [("top", "상부 도체(패치·급전선)", PAL["copper"]), ("substrate", "기판", PAL["sub"]),
              ("gnd", "하부 GND", PAL["gnd"]), ("hole", "홀", PAL["hole"])]
    chk = "".join(f'<label><input type="checkbox" checked onchange="toggle(\'{g}\',this)">'
                  f'<span class="sw" style="background:{c}"></span>{n}</label>' for g, n, c in groups)
    doc = f"""<!doctype html><html lang="ko"><meta charset="utf-8">
<title>{html.escape(title or 'CAD 3D View')}</title><style>{_VIEWER_CSS}</style>
<header><h1>{html.escape(title or 'CAD 3D View')}</h1>
<span class="sub">드래그=회전 · 휠=확대 · Shift+드래그=이동 · F=맞춤 · 외부 라이브러리 0(오프라인)</span></header>
<div class="wrap"><div style="position:relative"><canvas id="cv"></canvas><div id="hud"></div></div>
<aside>
<h2>보기</h2>
<button onclick="setView('iso')">등각</button><button onclick="setView('top')">평면</button>
<button onclick="setView('front')">정면</button><button onclick="setView('side')">측면</button>
<button onclick="refit()">맞춤(F)</button><button onclick="toggleBG()">배경</button>
<h2>레이어</h2>{chk}
<h2>Z 배율 <span class="sub">(두께 과장)</span></h2>
<input type="range" min="1" max="60" value="18" oninput="setZ(this.value)">
<h2>제원</h2><table>{tbl}</table>
<p class="note">두께 방향은 실제 1.5 mm라 그대로 그리면 안 보인다 — Z 배율로 과장해 표시한다.
치수 표의 값은 DXF 원본 좌표 그대로다.</p>
</aside></div>
<script>const MODEL={json.dumps(boxes, separators=(',', ':'))};{_VIEWER_JS}</script></html>"""
    if out: Path(out).write_text(doc, encoding="utf-8")
    return doc

# ───────────────────────────── 파일 식별(노트북 08 STEP 0과 동일 규칙) ────────
_DWG_VER = {"AC1009":"R11/12","AC1012":"R13","AC1014":"R14","AC1015":"2000","AC1018":"2004",
            "AC1021":"2007","AC1024":"2010","AC1027":"2013","AC1032":"2018+"}
_LAYER_HINT = [(r"TIN|TOP|SIG|TRACE|PATTERN","signal"), (r"COPPER|GND|GROUND|BOT","ground"),
               (r"DRILL|HOLE|VIA","drill"), (r"DIM|TEXT|NOTE|ANNO","annotation")]

def _sha16(p, n=1 << 20):
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while (c := f.read(n)): h.update(c)
    return h.hexdigest()[:16]

def classify(path, out_dir):
    p = Path(path); head = p.open("rb").read(8)
    r = {"file": p.name, "path": str(p), "size": p.stat().st_size, "sha256_16": _sha16(p),
         "preview": None, "why": [], "needs_human":
         ["product","design_intent","lifecycle","mates_with","spec.source"]}
    if head[:2] == b"AC":
        b = p.read_bytes(); ver = b[:6].decode("ascii","replace")
        txt = b.decode("latin-1","ignore") + "\n" + b.decode("utf-16-le","ignore")
        app = re.search(r"(Teigha\(R\)[^\x00]{0,20}|AutoCAD[^\x00]{0,20})", txt)
        r.update({"format": f"DWG {ver} ({_DWG_VER.get(ver,'?')})", "readable": "preview-only",
                  "role": "unknown (변환 필요)", "confidence": 0.0,
                  "saved_by": app.group(0).strip() if app else None,
                  "fonts": sorted(set(re.findall(r"[A-Za-z0-9_\-]+\.(?:shx|ttf)", txt, re.I)))[:6],
                  "ext_paths": sorted(set(re.findall(r"[A-Z]:\\[A-Za-z0-9_\\ \.\-]{4,60}", txt)))[:3],
                  "preview": None})
        _pv = dwg_preview(p, out_dir)
        r["preview_raw"] = _pv
        if _pv:
            e = enhance_preview(_pv, Path(out_dir) / (p.stem + "_hi.png"))
            if isinstance(e, dict):
                r["preview"] = e["path"]; r["preview_ink"] = e["ink_ratio"]
                r["preview_quality"] = ("poor" if e["ink_ratio"] < 0.02 else
                                        "fair" if e["ink_ratio"] < 0.06 else "ok")
            else:
                r["preview"] = e; r["preview_quality"] = "unknown"
            if r.get("preview_quality") == "poor":
                r["why"].append("내장 프리뷰가 사실상 비어 있음(잉크 %.2f%%) — 뷰어로 저장된 파일이라 "
                                "화면 상태만 담겼다. ODA 변환 없이는 볼 수 없다." % (r["preview_ink"]*100))
        r["why"] = ["DWG 본문 압축 — 형상 판독 불가",
                    "내장 프리뷰는 512px 저해상 — 고화질은 ODA 변환 후 벡터 렌더"]
        return r
    g = dxf_read(p)
    npoly, ncirc = len(g["polylines"]), len(g["circles"])
    V = [v for q in g["polylines"] for v in q["verts"]] + [c["c"] for c in g["circles"]]
    bb = (min(v[0] for v in V), max(v[0] for v in V), min(v[1] for v in V), max(v[1] for v in V)) if V else (0,0,0,0)
    w, h = bb[1]-bb[0], bb[3]-bb[2]
    layers = sorted({q["layer"] for q in g["polylines"]} | {c["layer"] for c in g["circles"]})
    hints = {t for L in layers for pat, t in _LAYER_HINT if re.search(pat, L, re.I)}
    z = sorted({round(q["elev"],4) for q in g["polylines"]} | {round(c["z"],4) for c in g["circles"]})
    role, conf, why = "unknown", 0.3, []
    if "drill" in hints or (npoly+ncirc and ncirc/(npoly+ncirc) > 0.6):
        role, conf, why = "drill-map", 0.8, [f"원 비율 {ncirc}/{npoly+ncirc}"]
    elif "ground" in hints and npoly <= 3:
        role, conf, why = "pcb-ground-plane", 0.9, [f"레이어 {layers} · 폴리라인 {npoly}(단순 면)"]
    elif "signal" in hints or (npoly > 50 and w > 5*max(h,1e-9)):
        role, conf, why = "pcb-signal-layer", 0.85, [f"레이어 {layers} · 폴리라인 {npoly} · 종횡비 {w/max(h,1e-9):.1f}"]
    if z and min(z) < -1e-6: why.append(f"elevation {min(z)} mm → 하부 층")
    tg = dxf_tags(p)
    acad = next((v2 for (c,v),(c2,v2) in zip(tg, tg[1:]) if c == 9 and v == "$ACADVER" and c2 == 1), "?")
    r.update({"format": f"DXF {acad} ({_DWG_VER.get(acad,'?')}) ASCII", "readable": "full",
              "role": role, "confidence": conf, "why": why, "layers": layers,
              "n_poly": npoly, "n_circle": ncirc, "bbox_mm": [round(w,3), round(h,3)],
              "elevations": z})
    return r

def render_all(cad_dir, out_dir, title_prefix=""):
    """폴더 전체 → 인벤토리 + 산출물(SVG·3D HTML·프리뷰). 뷰어·노트북 공용 진입점."""
    cad_dir, out_dir = Path(cad_dir), Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    files = sorted([p for p in cad_dir.iterdir() if p.suffix.lower() in (".dxf", ".dwg")],
                   key=lambda p: p.name.lower())
    inv = [classify(p, out_dir) for p in files]
    top = next((r["path"] for r in inv if r.get("role") == "pcb-signal-layer"), None)
    bot = next((r["path"] for r in inv if r.get("role") == "pcb-ground-plane"), None)
    arts = {}
    if top or bot:
        name = f"{title_prefix or cad_dir.name}"
        arts["svg"] = str(out_dir / "layout_2d.svg")
        render_svg(top, bot, arts["svg"], title=f"{name}  (2D 벡터 — 확대 무손실)")
        arts["html3d"] = str(out_dir / "view_3d.html")
        render_html3d(top, bot, arts["html3d"], title=f"{name} — 3D",
                      extra_rows=[("출처", Path(top).name if top else "—"),
                                  ("GND", Path(bot).name if bot else "—")])
    (out_dir / "cad_index.json").write_text(
        json.dumps({"inventory": inv, "artifacts": arts}, ensure_ascii=False, indent=2),
        encoding="utf-8")     # ★ Windows 기본 인코딩(cp949)으로 쓰면 '—' 같은 문자에서 터진다
    return inv, arts
