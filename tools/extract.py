#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/extract.py — 클래스 「추출」. 파일 안에 무엇이 있는지만 담는다. LLM 0콜.

규율:
  · 원본 불변(I-2) — 읽기만 한다.
  · **선언값(declared)과 판독 형상(geometry)을 필드에서 분리한다**(3.2 규칙).
    declared = 파일이 스스로 말하는 값 · geometry = 파일에서 읽어낸 형상. 계산은 「해석」의 일.
  · 특이 표기는 심볼·위치만 — 의미를 판정하지 않는다(I-6 · N-4).
  · 판독 불가 포맷에 파서를 만들지 않는다(T-1). CST 바이너리는 선언 파일(ASCII)만 읽는다.

출력: work/<run_id>/추출_결과.json
사용: python tools/extract.py --run-id <id>
"""
from __future__ import annotations
import argparse, json, re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (ACAD_DEFAULT_COLORS, ARRAY_MIN_SEG_MM, ARRAY_Y_SPLIT_MM,
                     CST_SUBVOLUME_COORDS, GEOM_ROUND_DIGITS, PARAM_ROUND_DIGITS,
                     canonical_hash, numeric_rules, read_json, vendor, work_dir, write_json)

# 라운딩 자리수는 _common 이 단일 출처다 — 추출·해석·게이트가 같은 값을 써야 대조가 성립한다.
ND = GEOM_ROUND_DIGITS


def _r(v):
    return round(float(v), ND)


# ── DXF ─────────────────────────────────────────────────────────────────────
# 특이 표기로 볼 엔티티·속성. 있는지만 본다 — 의미는 registry/annotation_vocab.yaml(사람)이 맡는다.
_ANNO_ENTITIES = ("TEXT", "MTEXT", "ATTRIB", "ATTDEF", "DIMENSION", "LEADER", "TOLERANCE", "POINT")
# ACAD 색 코드는 _common (b) 외부 포맷 규약에서 온다


def _dxf_tags(path):
    L = [l.strip() for l in Path(path).read_text(encoding="utf-8", errors="ignore").splitlines()]
    out = []
    for i in range(0, len(L) - 1, 2):
        try: out.append((int(L[i]), L[i + 1]))
        except ValueError: pass
    return out


def _dxf_annotations(path, rel):
    """DXF 특이 표기 스캔 — 엔티티 종류 + 기본색이 아닌 색 지정. 값 해석 없음."""
    tags = _dxf_tags(path)
    found, cur, buf = [], None, {}
    for code, val in tags:
        if code == 0:
            if cur in _ANNO_ENTITIES:
                found.append({"kind": cur, "sheet": rel,
                              "bbox": [buf.get(10), buf.get(20), buf.get(10), buf.get(20)],
                              "color": buf.get(62), "text": buf.get(1),
                              "meaning": None})  # 의미 미확정 — 사람 사전이 채운다(I-6)
            cur, buf = val, {}
        elif cur is not None and code in (1, 8, 10, 20, 62) and code not in buf:
            buf[code] = val
    colors = {v for c, v in tags if c == 62} - ACAD_DEFAULT_COLORS
    for c in sorted(colors):
        found.append({"kind": f"색 지정(ACI {c})", "sheet": rel, "bbox": None, "color": c,
                      "text": None, "meaning": None})
    return found


POLY_BBOX_MAX = 500          # 폴리라인 bbox 를 싣는 상한. 넘으면 잘랐다고 기록한다


def _poly_bboxes(polys: list) -> list[dict]:
    """폴리라인별 레이어·bbox. 급전선 판독처럼 **개별 도형**을 가리켜야 하는 대조의 재료다."""
    out = []
    for q in polys[:POLY_BBOX_MAX]:
        vs = q.get("verts") or []
        if not vs:
            continue
        xs = [v[0] for v in vs]; ys = [v[1] for v in vs]
        out.append({"layer": q["layer"], "n_verts": len(vs),
                    "closed": bool(q.get("flag", 0) & 1),
                    "x0": _r(min(xs)), "x1": _r(max(xs)),
                    "y0": _r(min(ys)), "y1": _r(max(ys)),
                    "w_mm": _r(max(xs) - min(xs)), "h_mm": _r(max(ys) - min(ys))})
    return out


def _extract_dxf(cr, rec: dict) -> dict:
    p = rec["path"]; rel = rec["rel"]
    g = cr.dxf_read(p)
    V = [v for q in g["polylines"] for v in q["verts"]] + [c["c"] for c in g["circles"]]
    bb = ([_r(min(v[0] for v in V)), _r(max(v[0] for v in V)),
           _r(min(v[1] for v in V)), _r(max(v[1] for v in V))] if V else None)
    layers = sorted({q["layer"] for q in g["polylines"]} | {c["layer"] for c in g["circles"]})
    elev = sorted({_r(q["elev"]) for q in g["polylines"]} | {_r(c["z"]) for c in g["circles"]})
    out = {"rel": rel, "readability": "full", "layers": layers,
           "entities": {"polyline": len(g["polylines"]), "circle": len(g["circles"])},
           "bbox_mm": bb, "bbox_size_mm": ([_r(bb[1] - bb[0]), _r(bb[3] - bb[2])] if bb else None),
           "elevations_mm": elev,
           "circles": [{"c": [_r(c["c"][0]), _r(c["c"][1])], "r_mm": _r(c["r"]), "layer": c["layer"]}
                       for c in g["circles"]],
           # 폴리라인의 **bbox 만** 싣는다. 정점을 다 실으면 산출이 부풀고, bbox 없이는
           # CST 포트 선언(xrange·yrange)과 형상을 대조할 수 없다(crosscheck 6·7).
           # 상한을 넘으면 **잘랐다고 말한다** — 조용히 자르지 않는다.
           "polylines": _poly_bboxes(g["polylines"]),
           "n_polylines_kept": min(len(g["polylines"]), POLY_BBOX_MAX),
           "array": None}
    # 배열 기하 — vendor extract_array 는 상/하 수평 세그먼트 짝짓기 규약(노트북 08과 동일).
    if (rec.get("role_candidates") or [{}])[0].get("role") == "pcb-signal-layer":
        # 짝짓기 규약 값을 명시해 넘긴다 — vendor 기본값에 의존하면 판정 근거가 코드 밖에 숨는다.
        a = cr.extract_array(p, y_split=ARRAY_Y_SPLIT_MM, min_len=ARRAY_MIN_SEG_MM)
        pat = [{"x0": _r(q["x0"]), "x1": _r(q["x1"]), "L_mm": _r(q["x1"] - q["x0"]),
                "W_mm": _r(q["W"])} for q in a["patches"]]
        xs = [q["x0"] for q in pat]
        pitch = [_r(xs[i + 1] - xs[i]) for i in range(len(xs) - 1)]
        out["array"] = {
            "n_patches": len(pat), "patches": pat,
            "patch_L_mm": sorted({q["L_mm"] for q in pat}),
            "patch_W_mm": sorted({q["W_mm"] for q in pat}),
            "pitch_mm": pitch,
            "pitch_min_mm": _r(min(pitch)) if pitch else None,
            "pitch_max_mm": _r(max(pitch)) if pitch else None,
            "pitch_mean_mm": _r(sum(pitch) / len(pitch)) if pitch else None,
            "uniform": (len(set(pitch)) == 1) if pitch else None,
            "n_feed_segments": len(a["feed"]),
            # 급전선 세그먼트의 **좌표**를 싣는다. 개수만 실으면 CST 포트 선언(xrange·yrange)과
            # 대조할 수 없다 — 교차검증 6·7 이 통째로 판정 불가가 된다(crosscheck.py).
            # 두 값은 **레인이 다르다**(cst 선언 vs dxf 형상)는 것이 대조의 근거다.
            "feed": [{"x0": _r(f0), "x1": _r(f1), "W_mm": _r(w)} for f0, f1, w in a["feed"]],
            "feed_x_min_mm": _r(min(f[0] for f in a["feed"])) if a["feed"] else None,
            "feed_W_mm": sorted({_r(f[2]) for f in a["feed"]}) if a["feed"] else [],
            "aperture_L_mm": _r(max(q["x1"] for q in pat) - min(q["x0"] for q in pat)) if pat else None,
            "aperture_W_mm": _r(max(q["W_mm"] for q in pat)) if pat else None,
            "규약": (f"vendor_srs.extract_array(y_split={ARRAY_Y_SPLIT_MM}, "
                    f"min_len={ARRAY_MIN_SEG_MM}) — 상/하 수평 세그먼트 짝짓기"),
        }
    return out


# ── CST (선언값만 — 바이너리는 건드리지 않는다) ──────────────────────────────
def _hist_code(e) -> str:
    c = e.get("code", "")
    return "\n".join(c) if isinstance(c, list) else (c or "")


def _kv(code: str) -> dict:
    """VBA 이력의 `.Key "v1", "v2"` 를 {Key: [v1, v2]} 로 읽는다(마지막 선언 우선).

    선언을 해석하지 않고 그대로 담기 위한 범용 판독기다 — 명령마다 정규식을 새로 쓰지 않는다.
    """
    out = {}
    for k, rest in re.findall(r'^\s*\.([A-Za-z_]\w*)\s+((?:"[^"]*"\s*,?\s*)+)$', code, re.M):
        out[k] = re.findall(r'"([^"]*)"', rest)
    return out


def _block(code: str, name: str) -> str:
    """`With <name> … End With` 한 덩어리만 잘라낸다. 템플릿처럼 여러 블록이 붙어 있을 때
    키가 섞이는 것을 막는다 — Background 의 Epsilon 과 Boundary 의 Xmin 은 다른 선언이다."""
    m = re.search(rf"With\s+{re.escape(name)}\b(.*?)End\s+With", code, re.S | re.I)
    return m.group(1) if m else ""


def _boundary_of(code: str, src: str):
    b = _block(code, "Boundary")
    if not b or ".Xmin" not in b: return None, None
    kv = _kv(b)
    bd = {k: _one(kv, k) for k in ("Xmin", "Xmax", "Ymin", "Ymax", "Zmin", "Zmax")}
    bd["open_add_space_factor"] = _one(kv, "OpenAddSpaceFactor")
    bd["출처"] = src
    sym = {k.lower(): _one(kv, k) for k in ("Xsymmetry", "Ysymmetry", "Zsymmetry")}
    sym["출처"] = src
    return bd, sym


def _kv_set(code: str) -> dict:
    """`.Set "key", "value"` 형태(MeshSettings 등)."""
    return {k: v for k, v in re.findall(r'\.Set\s+"([^"]+)"\s*,\s*"?([^",\n]*)"?', code)}


def _one(d: dict, key: str, i: int = 0):
    v = d.get(key)
    return v[i] if isinstance(v, list) and len(v) > i else None


def _num(s):
    try: return round(float(s), PARAM_ROUND_DIGITS)
    except (TypeError, ValueError): return None


def _cst_history(j: dict) -> dict:
    """ModelHistory.json 을 한 번 훑어 선언을 종류별로 모은다. 계산·의미 판정 없음.

    담는 것은 CST 프로젝트가 **스스로 선언한 것**뿐이다 — 경계·대칭·포트·모니터·솔버·
    스택업·배열 변환·소자 이름·치수·임포트 원천. 이 선언들이 안테나 정보의 본체다.
    """
    d = {"frequency_ranges": [], "materials": [], "dxf_imports": [], "gerber_imports": [],
         "stackup": [], "transforms": [], "extrudes": [], "blocks_renamed": [], "ports": [],
         "port_labels": [], "monitors": [], "dimensions": [], "anchor_points": [], "groups": [],
         "boundary": None, "symmetry": None, "background": None, "solver": {}, "pml": {},
         "mesh": {}, "template": None, "farfield_plot": {},
         # 솔리드 생성·이름·변환·삭제를 **순서대로** 남긴다 — 최종 상태는 재생으로만 나온다.
         "solid_events": []}
    for i, e in enumerate(j.get("history") or []):
        c, cap = _hist_code(e), (e.get("caption") or "")
        head = re.sub(r":.*$", "", cap).strip()
        kv = _kv(c)
        src = f"ModelHistory.json #{i} · {cap}"

        for lo, hi in re.findall(r'Solver\.FrequencyRange\s+"([^"]+)"\s*,\s*"([^"]+)"', c):
            d["frequency_ranges"].append({"order": i, "min": lo, "max": hi, "caption": cap})

        if head == "use template":
            d["template"] = {"name": cap.split(":", 1)[-1].strip(), "출처": src}
            bg = _kv(_block(c, "Background"))
            if bg:
                d["background"] = {"epsilon": _one(bg, "Epsilon"), "mu": _one(bg, "Mu"), "출처": src}
            # 템플릿이 경계를 함께 선언한다 — 뒤에 `define boundaries` 가 오면 그것이 덮어쓴다.
            bd, sym = _boundary_of(c, src)
            if bd: d["boundary"], d["symmetry"] = bd, sym
        elif head == "define material":
            d["materials"].append({
                "name": _one(kv, "Name"), "epsilon": _one(kv, "Epsilon"), "mu": _one(kv, "Mu"),
                "tand": _one(kv, "TanD"), "sigma": kv.get("Sigma"), "rho": _one(kv, "Rho"),
                "출처": src})
        elif head == "import dxf file":
            layers = [{"layer": a, "material": b, "z_mm": _num(z), "thickness_mm": _num(t)}
                      for a, b, z, t in re.findall(
                          r'\.AddLayer\s+"([^"]*)",\s*"([^"]*)",\s*"([^"]*)",\s*"([^"]*)"', c)]
            d["solid_events"].append({"i": i, "kind": "import", "file": _one(kv, "FileName"),
                                      "layers": [a for a, *_ in re.findall(
                                          r'\.AddLayer\s+"([^"]*)",\s*"([^"]*)",\s*"([^"]*)",\s*"([^"]*)"', c)],
                                      "출처": src})
            d["dxf_imports"].append({"file": _one(kv, "FileName"), "import_units": _one(kv, "ImportFileUnits"),
                                     "preserve_holes": _one(kv, "PreserveHoles"),
                                     "layers": layers, "출처": src})
            d["stackup"] += [dict(l, 출처=src) for l in layers]
        elif head == "import gerber file":
            d["gerber_imports"].append({"source_file": _one(kv, "SourceFileName"),
                                        "ldb": _one(kv, "LdbFileName"),
                                        "pcb_type": _one(kv, "PcbType"), "출처": src})
        elif head == "define boundaries":
            bd, sym = _boundary_of(c, src)
            if bd: d["boundary"], d["symmetry"] = bd, sym
        elif head == "define pml specials":
            d["pml"] = {"reflection_level": _one(kv, "ReflectionLevel"),
                        "min_distance_per_wavelength": _one(kv, "MinimumDistancePerWavelengthNewMeshEngine"),
                        "reference_frequency": _one(kv, "FrequencyForMinimumDistance"), "출처": src}
        elif head in ("define port", "modify port"):
            d["ports"].append({
                "number": _one(kv, "PortNumber") or cap.split(":")[-1].strip(),
                "kind": "waveguide", "action": head, "modes": _one(kv, "NumberOfModes"),
                "coordinates": _one(kv, "Coordinates"), "orientation": _one(kv, "Orientation"),
                "shield": _one(kv, "Shield"),
                "xrange": kv.get("Xrange"), "yrange": kv.get("Yrange"), "zrange": kv.get("Zrange"),
                "xrange_add": kv.get("XrangeAdd"), "yrange_add": kv.get("YrangeAdd"),
                "zrange_add": kv.get("ZrangeAdd"), "출처": src})
        elif head == "define discrete port":
            d["ports"].append({
                "number": _one(kv, "PortNumber") or cap.split(":")[-1].strip(),
                "kind": "discrete", "action": head, "type": _one(kv, "Type"),
                "impedance_ohm": _one(kv, "Impedance"), "voltage": _one(kv, "Voltage"),
                "p1": kv.get("SetP1"), "p2": kv.get("SetP2"),
                "local_coordinates": _one(kv, "LocalCoordinates"), "출처": src})
        elif head == "delete port":
            d["ports"].append({"number": cap.split(":")[-1].strip(), "kind": "삭제됨",
                               "action": head, "출처": src})
        elif head == "rename port":
            for a, b in re.findall(r'Port\.RenameLabel\s+"([^"]*)"\s*,\s*"([^"]*)"', c):
                d["port_labels"].append({"number": a, "label": b, "출처": src})
        elif head == "define farfield monitor":
            d["monitors"].append({
                "name": _one(kv, "Name"), "field_type": _one(kv, "FieldType"),
                "domain": _one(kv, "Domain"), "value": _one(kv, "MonitorValue"),
                "subvolume": kv.get("SetSubvolume"),
                # UseSubvolume=False 면 이 좌표는 **적용되지 않는다** — 모델 범위로 읽으면 오독이다.
                "use_subvolume": _one(kv, "UseSubvolume"),
                "coordinates": _one(kv, "Coordinates"),
                "nearfield": _one(kv, "EnableNearfieldCalculation"), "출처": src})
        elif head == "farfield plot options":
            d["farfield_plot"] = {"plottype": _one(kv, "Plottype"), "frequency": _one(kv, "SetFrequency"),
                                  "theta": [_one(kv, "SetThetaStart"), _one(kv, "SetThetaEnd")],
                                  "phi": [_one(kv, "SetPhiStart"), _one(kv, "SetPhiEnd")], "출처": src}
        elif head.startswith("define time domain solver") or head == "time domain solver":
            for k in ("Method", "CalculationType", "StimulationPort", "SteadyStateLimit",
                      "MeshAdaption", "NormingImpedance", "AutoNormImpedance"):
                if k in kv: d["solver"][k] = _one(kv, k)
            pr = kv.get("PhaseReferenceFrequency")
            if pr: d["solver"]["PhaseReferenceFrequency"] = pr
            d["solver"].setdefault("출처", []).append(src) if isinstance(
                d["solver"].get("출처"), list) else d["solver"].update({"출처": [src]})
        elif head.startswith("set mesh properties"):
            s = _kv_set(c)
            d["mesh"] = {k: s[k] for k in ("StepsPerWaveNear", "StepsPerWaveFar", "StepsPerBoxNear",
                                           "StepsPerBoxFar", "Version") if k in s}
            d["mesh"]["mesh_type"] = _one(kv, "MeshType") or _one(kv, "SetMeshType")
            d["mesh"]["출처"] = src
        elif head == "define extrude":
            d["extrudes"].append({"name": _one(kv, "Name"), "component": _one(kv, "Component"),
                                  "material": _one(kv, "Material"), "height": _one(kv, "Height"),
                                  "mode": _one(kv, "Mode"), "출처": src})
            d["solid_events"].append({"i": i, "kind": "create", "component": _one(kv, "Component"),
                                      "name": _one(kv, "Name"), "출처": src})
        elif head.startswith("transform"):
            v = kv.get("Vector")
            op = (kv.get("Transform") or [None, None])[-1]
            d["solid_events"].append({
                "i": i, "kind": "transform", "op": (op or "").lower() or None,
                "target": _one(kv, "Name"), "vector_expr": v, "angle_expr": kv.get("Angle"),
                "center_expr": kv.get("Center"),
                "multiple_objects": (_one(kv, "MultipleObjects") or "").lower() == "true",
                "repetitions": _one(kv, "Repetitions"), "출처": src})
            d["transforms"].append({
                "op": cap.split(":")[1].strip() if ":" in cap else None,
                "target": _one(kv, "Name"), "vector_expr": v,
                "repetitions": _one(kv, "Repetitions"),
                "multiple_objects": _one(kv, "MultipleObjects"),
                "symbols": sorted({s for s in re.findall(r"[A-Za-z_]\w*", " ".join(v))}) if v else [],
                "출처": src})
        elif head == "rename block":
            for a, b in re.findall(r'Solid\.Rename\s+"([^"]*)"\s*,\s*"([^"]*)"', c):
                d["blocks_renamed"].append({"from": a, "to": b, "출처": src})
                d["solid_events"].append({"i": i, "kind": "rename", "from": a, "to": b, "출처": src})
        elif head in ("delete shape", "delete shapes"):
            for a in re.findall(r'Solid\.Delete\s+"([^"]*)"', c):
                d["solid_events"].append({"i": i, "kind": "delete", "target": a, "출처": src})
        elif head == "define distance dimension":
            d["dimensions"].append({"type": _one(kv, "SetType"), "distance": _one(kv, "SetDistance"),
                                    "between": [_one(kv, "SetConnectedElement1"),
                                                _one(kv, "SetConnectedElement2")], "출처": src})
        elif head == "store anchor point":
            d["anchor_points"].append({"name": cap.split(":")[-1].strip(), "출처": src})
        elif head in ("create group", "add items to group"):
            d["groups"].append({"caption": cap, "출처": src})
    return d




def _replay_solids(events: list) -> dict:
    """솔리드 이력을 순서대로 재생해 **최종 상태**를 얻는다.

    선언을 모아 놓는 것만으로는 최종 형상을 알 수 없다 — 삭제·회전·사본 이름 이어붙이기가
    순서에 달렸기 때문이다. 포트에 이미 적용한 방식(정의 → 수정 → 삭제 재생)과 같은 규율이다.

    CST 규약: `MultipleObjects=True` 변환은 사본 `<이름>_1` 을 만들고, 바로 뒤 `Solid.Rename`
    이 그 사본에 최종 이름을 준다. `False` 면 원본을 제자리에서 바꾼다.
    """
    live, deleted, unknown = {}, [], []
    for e in events:
        k = e["kind"]
        if k == "import":
            # 임포트 솔리드의 이름(import_N)과 폴리라인 대응은 선언되지 않는다 — 이름만 남긴다.
            live.setdefault("__import__", {"note": "import_N 이름은 CST 가 붙인다(대응 미선언)",
                                           "출처": e["출처"], "layers": e.get("layers")})
        elif k == "create":
            nm = f"{e.get('component')}:{e.get('name')}" if e.get("component") else e.get("name")
            live[nm] = {"name": nm, "ops": [], "origin": "extrude", "출처": e["출처"]}
        elif k == "delete":
            tgt = e["target"]
            deleted.append({"name": tgt, "at": e["i"], "출처": e["출처"],
                            "존재했는가": tgt in live})
            live.pop(tgt, None)
        elif k == "rename":
            src_n, new_short = e["from"], e["to"]
            layer = src_n.split(":")[0] if ":" in src_n else None
            new_n = f"{layer}:{new_short}" if layer else new_short
            if src_n in live:
                live[new_n] = dict(live.pop(src_n), name=new_n)
            else:
                live[new_n] = {"name": new_n, "ops": [], "origin": f"이름 변경(원본 {src_n} 미추적)",
                               "출처": e["출처"]}
                unknown.append({"at": e["i"], "from": src_n, "reason": "재생 중 원본을 찾지 못했다"})
        elif k == "transform":
            tgt, op = e.get("target"), e.get("op")
            base = live.get(tgt) or {"name": tgt, "ops": [], "origin": "미추적"}
            step = {"op": op, "vector_expr": e.get("vector_expr"),
                    "angle_expr": e.get("angle_expr"), "center_expr": e.get("center_expr"), "at": e["i"]}
            if e.get("multiple_objects"):
                cp = f"{tgt}_1"
                live[cp] = {"name": cp, "ops": list(base["ops"]) + [step],
                            "origin": f"사본(from {tgt} @#{e['i']})", "출처": e["출처"]}
            else:
                base["ops"] = list(base["ops"]) + [step]
                live[tgt] = base
    live.pop("__import__", None)
    return {"n_final": len(live), "final": sorted(live.values(), key=lambda s: s["name"]),
            "deleted": deleted, "unresolved": unknown,
            "규약": "MultipleObjects=True → 사본 <이름>_1 생성 후 다음 Solid.Rename 이 최종 이름을 준다"}


def _monitor_subvolume(monitors: list) -> dict | None:
    """모니터 SetSubvolume 선언의 축별 min/max 집계.

    ★ **모델 상자가 아니다.** `UseSubvolume="False"` 면 이 좌표는 적용되지 않는 잔여값이고,
      실측에서 그 값은 임포트 원본 DXF 의 bbox 와 일치했다(test2: 59 × 6 mm = KORIL dxf bbox).
      형상의 실제 범위는 변환 선언으로 배치된 소자 좌표가 말한다 — 해석의 aperture 를 보라.
      이름을 model_box 로 두면 반드시 오독된다.
    """
    boxes = [(m, [_num(x) for x in m["subvolume"]]) for m in monitors
             if m.get("subvolume") and len(m["subvolume"]) == CST_SUBVOLUME_COORDS]
    boxes = [(m, b) for m, b in boxes if all(v is not None for v in b)]
    if not boxes: return None
    applied = [b for m, b in boxes if str(m.get("use_subvolume")).lower() == "true"]
    all_b = [b for _, b in boxes]
    xs = [b[0] for b in all_b] + [b[1] for b in all_b]
    ys = [b[2] for b in all_b] + [b[3] for b in all_b]
    zs = [b[4] for b in all_b] + [b[5] for b in all_b]
    return {"x_mm": [min(xs), max(xs)], "y_mm": [min(ys), max(ys)], "z_mm": [min(zs), max(zs)],
            "size_mm": [_r(max(xs) - min(xs)), _r(max(ys) - min(ys)), _r(max(zs) - min(zs))],
            "n_declarations": len(all_b), "n_applied": len(applied),
            "applied": bool(applied),
            "출처": "farfield monitor SetSubvolume 선언 (min/max 집계)",
            "경고": ("UseSubvolume=False — 이 좌표는 적용되지 않는다. 모델 범위로 읽지 말 것."
                    if not applied else None)}


def _extract_cst(root: Path, container: dict, skip: dict) -> dict:
    """skip: 식별이 실측으로 판정한 {rel: 사유} — 추출은 확장자를 다시 판정하지 않는다(판정 산지 단일화)."""
    proj = root / container["path"] if container["path"] != "." else root
    d = {"name": container["name"], "path": container["path"], "readability": "declared-only",
         "sources_read": [], "sources_skipped": []}

    prj = proj / "Model" / "Model.prj"
    if prj.exists():
        j = json.loads(prj.read_text(encoding="utf-8", errors="replace"))
        d["sources_read"].append("Model/Model.prj")
        d["project"] = {k: j.get(k) for k in ("Author", "Last Problem Type", "Last Solver Type",
                                              "Mesh Type", "Mesh Cells", "Results", "ProjectVersion")}
        uid = j.get("Modeler Unique ID") or ""
        ports = sorted({int(m) for m in re.findall(r"port=(\d+)>", uid)})
        d["ports_declared"] = {"n": len(ports), "ids": ports, "출처": "Model.prj · Modeler Unique ID"}
        npar = re.search(r"numberofparameters=(\d+)", uid)
        if npar: d["n_parameters_declared"] = int(npar.group(1))

    par = proj / "Model" / "Parameters.json"
    if par.exists():
        j = json.loads(par.read_text(encoding="utf-8", errors="replace"))
        d["sources_read"].append("Model/Parameters.json")
        d["parameters"] = ({q["name"]: {"expr": q.get("expr"), "value": q.get("value")}
                            for q in (j.get("parameters") or [])})

    hist = proj / "Model" / "3D" / "ModelHistory.json"
    if hist.exists():
        j = json.loads(hist.read_text(encoding="utf-8", errors="replace"))
        d["sources_read"].append("Model/3D/ModelHistory.json")
        d["general"] = j.get("general")
        g = j.get("general") or {}
        f = g.get("frequency") or {}
        d["units"] = {"length": g.get("length"), "frequency": f.get("unit"), "time": g.get("time")}
        d["cst_version_declared"] = {k: g.get(k) for k in ("version", "date", "acis", "created")}
        d["frequency_declared_ghz"] = ({"min": f.get("minimum"), "max": f.get("maximum")}
                                       if f.get("unit") == "GHz" else
                                       {"min": f.get("minimum"), "max": f.get("maximum"),
                                        "주의": f"단위가 GHz가 아니다({f.get('unit')}) — 환산하지 않았다"})
        H = _cst_history(j)
        d["n_history"] = len(j.get("history") or [])

        # 주파수 선언 — 여러 번 선언될 수 있다. 전부 순서대로 보존하고 마지막을 유효값으로 본다.
        fr = H["frequency_ranges"]
        d["solver_frequency_range_declared"] = fr
        d["solver_frequency_range_effective"] = (fr[-1] if fr else None)
        if len(fr) > 1:
            d.setdefault("주의", []).append(
                f"Solver.FrequencyRange 가 {len(fr)}회 선언되었다 — 마지막 선언을 유효값으로 보되 "
                "해석·문서에서 두 선언을 모두 표시한다(임의 선택 금지)")

        d["materials_declared"] = H["materials"]
        d["imports_declared"] = H["dxf_imports"]
        d["gerber_imports_declared"] = H["gerber_imports"]
        d["stackup_declared"] = H["stackup"]          # DXF AddLayer: 레이어별 z · 두께 · 재질
        d["extrudes_declared"] = H["extrudes"]
        d["boundary_declared"] = H["boundary"]
        d["symmetry_declared"] = H["symmetry"]
        d["background_declared"] = H["background"]
        d["solver_declared"] = H["solver"]
        d["pml_declared"] = H["pml"]
        d["mesh_declared"] = H["mesh"]
        d["template_declared"] = H["template"]
        d["farfield_plot_declared"] = H["farfield_plot"]
        d["dimensions_declared"] = H["dimensions"]
        d["anchor_points_declared"] = H["anchor_points"]

        # 포트 — 정의·수정·삭제 이력을 그대로 보존하고, 최종 상태만 따로 집계한다.
        live = {}
        for p in H["ports"]:
            n = p.get("number")
            if p["kind"] == "삭제됨": live.pop(n, None)
            elif n in live: live[n].update({k: v for k, v in p.items() if v is not None})
            else: live[n] = dict(p)
        labels = {x["number"]: x["label"] for x in H["port_labels"]}
        for n, p in live.items(): p["label"] = labels.get(n)
        d["ports_declared_history"] = {"n_final": len(live), "items": list(live.values()),
                                       "n_events": len(H["ports"]), "labels": H["port_labels"]}

        # 원거리장 모니터 — 결과 파일이 없어도 "무엇을 어느 주파수에서 계산하도록 요청했는가"는 남는다.
        d["monitors_declared"] = H["monitors"]
        ff = [m for m in H["monitors"] if (m.get("field_type") or "").lower() == "farfield"]
        d["farfield_requested"] = {
            "n": len(ff), "frequencies": [m.get("value") for m in ff],
            "unit": f.get("unit"),
            "출처": "ModelHistory.json · define farfield monitor 선언",
            "의미": "요청 목록이다 — 계산 결과 파일의 존재는 results_declared 가 말한다"}
        d["monitor_subvolume_declared"] = _monitor_subvolume(H["monitors"])

        # 배열 변환 — 좌표를 심볼 식으로 선언한다. 식과 심볼만 담고 mm 환산은 「해석」이 한다.
        tr = H["transforms"]
        syms = sorted({s for t in tr for s in t["symbols"]})
        d["transforms_declared"] = {
            "n": len(tr), "symbols_used": syms, "items": tr,   # 절단하지 않는다 — 조용한 상한 금지
            "규칙": "vector_expr 은 선언 그대로다 — 심볼 값을 곱해 mm 로 바꾸는 것은 해석의 일"}

        # 소자 이름 — 명명 규약 관측이다. 이름의 뜻(RX/TX 등)을 판정하지 않는다(I-6).
        names = [b["to"] for b in H["blocks_renamed"]]
        toks = {}
        for nm in names:
            for t in re.findall(r"[A-Za-z]+", nm or ""): toks[t] = toks.get(t, 0) + 1
        d["blocks_declared"] = {"n_renamed": len(names), "names": names,
                                "name_tokens": dict(sorted(toks.items(), key=lambda x: -x[1])),
                                "규칙": "명명 규약 관측 — 토큰의 의미는 판정하지 않는다(I-6). "
                                       "사전은 registry/annotation_vocab.yaml(사람)"}
        d["groups_declared"] = H["groups"]

        # 솔리드 이력 재생 — 최종 형상 집합. 선언만으로는 알 수 없는 것을 여기서 얻는다.
        rp = _replay_solids(H["solid_events"])
        d["solids_replayed"] = rp
        # 스택업 선언의 **레이어별로** 최종 모델에 솔리드가 남았는지 본다.
        # 선언은 남아도 솔리드가 삭제되었으면 "선언된 층"과 "최종 모델의 층"이 다르다.
        alive_names = [s["name"] for s in rp["final"]]
        layer_state = []
        for l in H["stackup"]:
            mat = (l.get("material") or "")
            n_alive = sum(1 for nmz in alive_names if mat and nmz.startswith(mat + ":"))
            layer_state.append({"layer": l.get("layer"), "material": mat,
                                "z_mm": l.get("z_mm"), "thickness_mm": l.get("thickness_mm"),
                                "n_solids_final": n_alive, "alive": n_alive > 0})
        d["stackup_state"] = {"layers": layer_state,
                              "deleted_solids": [x["name"] for x in rp["deleted"]],
                              "규칙": "선언(AddLayer)과 최종 솔리드 존재를 분리해 본다"}
        gone = [x for x in layer_state if not x["alive"]]
        if gone:
            d.setdefault("주의", []).append(
                "스택업 선언 중 최종 모델에 솔리드가 남지 않은 층이 있다 — "
                + " · ".join(f"{x['layer']}(z={x['z_mm']}, 두께={x['thickness_mm']})" for x in gone)
                + f". 삭제된 솔리드: {[x['name'] for x in rp['deleted']]}. "
                "두께·유전율은 '선언값'으로만 쓰고 최종 모델의 층으로 단정하지 않는다")

    res = proj / "Result" / "Model.res"
    if res.exists():
        t = res.read_text(encoding="utf-8", errors="replace")
        d["sources_read"].append("Result/Model.res")
        blocks = [b for b in t.split("\n\n") if "type=s:" in b]
        paths = re.findall(r"treepath=s:(.+)", t)
        d["results_declared"] = {
            "n_entries": len(blocks),
            "treepaths": sorted(set(x.strip() for x in paths)),
            # 성능 절이 채워질 수 있는가의 유일한 판단 근거. 없으면 없다고 쓴다(I-5).
            "has_sparameter": any(re.search(r"S-Parameters|S\d+,\d+", x) for x in paths),
            "has_farfield": any("Farfield" in x for x in paths),
            "출처": "Result/Model.res · treepath 목록",
        }

    # Model.dsn — 실측으로 ASCII 임이 확인된 파일. 포트 선언이 평문으로 들어 있다.
    dsn = proj / "Model" / "3D" / "Model.dsn"
    dsn_rel = str(dsn.relative_to(root)).replace("\\", "/") if dsn.exists() else None
    if dsn_rel and dsn_rel not in skip:
        t = dsn.read_text(encoding="utf-8", errors="replace")
        d["sources_read"].append("Model/3D/Model.dsn")
        n = re.search(r"Number of ports:\s*(\d+)", t)
        names = re.search(r'Portnames:\s*((?:"[^"]*"\s*)+)', t)
        ver = re.search(r"File Version:\s*([\d.]+)", t)
        d["ports_declared_dsn"] = {
            "n": int(n.group(1)) if n else None,
            "names": re.findall(r'"([^"]*)"', names.group(1)) if names else [],
            "file_version": ver.group(1) if ver else None,
            "출처": "Model/3D/Model.dsn (ASCII 평문 선언)"}
        a, b = (d.get("ports_declared") or {}).get("n"), d["ports_declared_dsn"]["n"]
        if a is not None and b is not None and a != b:
            d.setdefault("주의", []).append(
                f"포트 수 선언이 두 출처에서 다르다 — Model.prj={a} · Model.dsn={b}. 임의 선택 금지")

    # Model.ifm — 임포트 원본 매핑(첫 줄이 원본 파일명)
    ifm = proj / "Model" / "3D" / "Model.ifm"
    ifm_rel = str(ifm.relative_to(root)).replace("\\", "/") if ifm.exists() else None
    if ifm_rel and ifm_rel not in skip:
        lines = [x.strip() for x in ifm.read_text(encoding="utf-8", errors="replace").splitlines() if x.strip()]
        d["sources_read"].append("Model/3D/Model.ifm")
        d["import_origin_declared"] = {"source_file": lines[0] if lines else None,
                                       "raw": lines[:4], "출처": "Model/3D/Model.ifm"}

    # 메시 엔진 로그 — 시뮬 실행 흔적(언제 돌렸는가). 성능 수치가 아니다.
    mlog = proj / "Result" / "hexmeshengine.log"
    mlog_rel = str(mlog.relative_to(root)).replace("\\", "/") if mlog.exists() else None
    if mlog_rel and mlog_rel not in skip:
        t = mlog.read_text(encoding="utf-8", errors="replace")
        ts = re.findall(r">(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", t)
        d["sources_read"].append("Result/hexmeshengine.log")
        d["mesh_runs_declared"] = {"n_timestamps": len(ts), "first": ts[0] if ts else None,
                                   "last": ts[-1] if ts else None,
                                   "n_configure": t.count("Configuring HexMeshEngine"),
                                   "출처": "Result/hexmeshengine.log",
                                   "의미": "메시 생성 실행 흔적 — 성능 수치가 아니다"}

    # 건너뛴 파일: 식별의 실측 판정을 그대로 옮긴다 — 여기서 다시 판정하지 않는다.
    for q in sorted(proj.rglob("*")):
        if not q.is_file(): continue
        rel = str(q.relative_to(root)).replace("\\", "/")
        if rel in skip:
            d["sources_skipped"].append({"rel": rel, "reason": skip[rel]})
    return d


# ── 본체 ────────────────────────────────────────────────────────────────────
def extract(run_id: str) -> dict:
    cr = vendor()
    wd = work_dir(run_id)
    ident = read_json(wd / "식별_결과.json")
    root = Path(ident["source"]["path"])

    dxf, previews, unread, annos, scanned = [], [], [], [], []
    for rec in ident["files"]:
        if rec["readability"] == "full" and rec["rel"].lower().endswith(".dxf"):
            dxf.append(_extract_dxf(cr, rec))
            annos += _dxf_annotations(rec["path"], rec["rel"]); scanned.append(rec["rel"])
        elif rec["readability"] == "preview-only":
            previews.append({"rel": rec["rel"], "preview": rec.get("preview"),
                             "quality": rec.get("preview_quality"),
                             "note": "형상 판독 불가 — 프리뷰만. ODA 변환 없이는 벡터 추출 없음(T-1)"})
            unread.append({"rel": rec["rel"], "reason": "DWG 본문 압축 — 형상 판독 불가(ODA 변환 필요)"})
        elif rec["readability"] == "unreadable":
            unread.append({"rel": rec["rel"], "reason": rec["reasons"][0] if rec["reasons"] else "사유 미기재"})

    # 판정 산지 단일화: "무엇을 읽지 않는가"는 식별의 실측 결과가 정본이다.
    #   · unreadable        → 읽지 않는다
    #   · schema_documented=False → 읽히지만 필드 스키마가 없다 → 값을 추출하지 않는다(T-1)
    skip = {}
    for rec in ident["files"]:
        if rec["readability"] == "unreadable":
            skip[rec["rel"]] = rec["reasons"][0] if rec["reasons"] else "판독 불가(사유 미기재)"
        elif (rec.get("evidence") or {}).get("schema_documented") is False:
            skip[rec["rel"]] = "텍스트로 읽히지만 필드 스키마 미공개 — 값을 추출하지 않는다(T-1)"

    cst = [_extract_cst(root, c, skip) for c in ident.get("containers", [])]

    geometry = {"dxf": dxf}
    res = {
        "run_id": run_id, "source": ident["source"], "adapter": ident["adapter"],
        "rounding": {"digits_mm": ND,
                     "규칙": f"모든 mm 수치는 round(v, {ND}) 후 저장 — 게이트가 같은 규칙으로 대조",
                     "산지": "_common.GEOM_ROUND_DIGITS (단일 출처)"},
        "numeric_rules": numeric_rules(),
        "geometry": geometry,
        "declared": {"cst": cst},
        "annotations": annos,
        "annotation_scan": {"scanned_files": scanned, "found": len(annos),
                            "looked_for": list(_ANNO_ENTITIES) + ["기본색 아닌 ACI 색 지정"],
                            "미스캔": [r["rel"] for r in ident["files"]
                                      if r["readability"] in ("preview-only", "unreadable")],
                            "규칙": "심볼·위치만 기록 · 의미 판정 없음(I-6). meaning=null 은 "
                                   "registry/annotation_vocab.yaml(사람)이 채운다"},
        "previews": previews,
        "unreadable": unread,
    }
    res["geom_hash"] = canonical_hash({"geometry": geometry,
                                       "declared": [{k: v for k, v in c.items() if k != "sources_skipped"}
                                                    for c in cst]})
    write_json(wd / "추출_결과.json", res)
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description="추출 — 파일 안에 무엇이 있는지만 담는다")
    ap.add_argument("--run-id", required=True)
    a = ap.parse_args(argv)
    r = extract(a.run_id)
    arr = next((d["array"] for d in r["geometry"]["dxf"] if d.get("array")), None)
    print(f"추출: DXF {len(r['geometry']['dxf'])}건 · CST {len(r['declared']['cst'])}건 · "
          f"특이표기 {len(r['annotations'])}건 · 판독불가 {len(r['unreadable'])}건 · geom_hash={r['geom_hash']}")
    if arr:
        print(f"  배열: 패치 {arr['n_patches']} · 주기 {arr['pitch_min_mm']}~{arr['pitch_max_mm']} mm"
              f"(균일={arr['uniform']}) · 개구 {arr['aperture_L_mm']}×{arr['aperture_W_mm']} mm")
    for c in r["declared"]["cst"]:
        fr = c.get("solver_frequency_range_effective")
        print(f"  CST {c['name']}: 주파수 선언 {fr['min']}~{fr['max']} GHz" if fr else
              f"  CST {c['name']}: 주파수 선언 없음")
        rd = c.get("results_declared") or {}
        print(f"    결과: 항목 {rd.get('n_entries', 0)} · S-파라미터 {rd.get('has_sparameter')} · "
              f"원거리장 {rd.get('has_farfield')}")
    print(f"산출: {work_dir(a.run_id, False) / '추출_결과.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
