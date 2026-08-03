#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/declare.py — 사람이 아는 값을 **말로 받아** 선언 자리에 넣는다 (LLM 0콜)

무엇을 푸나
    문서의 빈 절 중에는 파일을 기다려야 하는 것(반입)과 도구를 기다려야 하는 것(도구)
    말고, **아는 사람이 말하면 그만인 것**(선언)이 있다 — 용도 · 요구 · 재질 같은 것들.
    지금까지 그것은 "누군가 registry 를 손으로 고친다"였고, 그래서 아무도 안 고쳤다.

★ 값이 문서로 바로 들어가지 않는다
    사람이 "기판은 Rogers AD255C 1.6t" 라고 말했을 때, 그 값을 **문서에 타이핑하면**
    참조 바인딩(D-22)이 뚫린다 — 출처 없는 숫자가 문서에 실리고, 게이트는 그것이
    사람이 한 말인지 LLM 이 지어낸 말인지 구별할 수 없다.
    그래서 값은 **선언 자리**로 들어가고, 파이프라인이 다시 돌아 참조로 실린다.
    문서에 숫자를 타이핑하는 주체는 여전히 없다.

★ `products.yaml` 을 고치지 않는다
    선언은 `registry/declared/<제품>.yaml` 에 쌓이고 적재 시점에 **위에 얹힌다**
    (`_common.load_registry`). 정본은 사람이 손으로 관리하는 파일 그대로 남는다 —
    무엇을 사람이 정했고 무엇을 말로 넣었는지가 파일 경로로 갈린다. 되돌리려면 지운다.

★ 양식 정본에 없는 자리에는 쓰지 않는다
    쓸 수 있는 경로는 `document_spec.yaml` 의 `absent.fields[].path` 가 전부다.
    "이것도 넣어 두면 좋겠다"로 경로가 늘어나면 선언 자리가 곧 아무 데나가 된다.

CLI
    python tools/declare.py gaps <run_id>              지금 무엇을 물으면 되나 (JSON)
    python tools/declare.py ask  <run_id>              같은 것을 사람이 읽는 질문 목록으로
    python tools/declare.py set  <경로> <값> [--product P] [--by 이름] [--why 사유]
    python tools/declare.py show [제품]                 지금까지 선언된 값
    python tools/declare.py self-test
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402
import docspec as DS  # noqa: E402

TARGETS = ("products",)          # 쓸 수 있는 대상. 늘리려면 여기와 spec 을 함께 고친다
PRODUCT_TOKEN = "{제품}"


class DeclareError(ValueError):
    pass


# ── 무엇을 물으면 되나 ───────────────────────────────────────────────────────

def declarable(spec: dict | None = None) -> list[dict]:
    """양식 정본이 **선언으로 채울 수 있다고 말한** 자리 전부."""
    spec = spec or DS.load()
    out = []
    for sec in spec["sections"]:
        ab = sec.get("absent") or {}
        if ab.get("kind") != "선언":
            continue
        for f in ab.get("fields") or []:
            out.append({"section": sec["title"], "section_id": sec["id"],
                        "owner": ab.get("owner"), **f})
    return out


def gaps(run_id: str, spec: dict | None = None, work: Path | None = None) -> dict:
    """이 run 에서 **지금 비어 있는** 것 중 말로 채울 수 있는 것.

    ★ 절 단위가 아니라 **값 단위**로 본다.
      절 단위로만 보면 스택업 절에 두께 하나만 있어도 "채워진 절"이 되어, 재질명도
      유전율도 없는데 아무것도 묻지 않는다. 사람이 아는 값을 못 받는 것이 이 도구의
      실패이므로, 짚을 수 있는 것은 값으로 짚는다.

      `role` 은 값 하나짜리 자리(재질명 · 유전율), `key` 는 행렬의 칸(요구 항목별 하한).
      둘 다 없으면 절 단위로 떨어진다.
    """
    import catalog as CT
    spec = spec or DS.load()
    cat = CT.load(run_id, work)
    sk = CT.skeleton(cat, spec)

    empty_titles = _gap_titles(sk, spec)
    product = _product_of(cat)
    entries = cat.get("entries", {})
    filled_roles = {e.get("role") for e in entries.values() if e.get("render_with_unit")}
    filled_keys = {k for k, e in entries.items() if e.get("render_with_unit")}

    rows = []
    for d in declarable(spec):
        if d.get("key"):
            missing, how = d["key"] not in filled_keys, "값"
        elif d.get("role"):
            missing, how = d["role"] not in filled_roles, "값"
        else:
            missing, how = d["section"] in empty_titles, "절"
        if missing:
            rows.append(dict(d, path=_bind(d["path"], product), 판정근거=how))
    return {"run_id": run_id, "product": product,
            "n_empty_sections": len(empty_titles),
            "declarable": rows,
            "규율": ("값은 문서가 아니라 아래 path 로 들어간다. 넣은 뒤 파이프라인을 다시 돌리면 "
                   "참조로 실린다 — 문서에 숫자를 타이핑하는 주체는 없다(D-22)."),
            }


def _gap_titles(skeleton: str, spec: dict) -> set:
    """골격의 대장 표에 실린 절 제목 — 지금 비어 있는 필수 절."""
    titles = {s["title"] for s in spec["sections"]}
    out = set()
    for ln in skeleton.splitlines():
        if not ln.startswith("| "):
            continue
        first = ln.split("|")[1].strip()
        if first in titles:
            out.add(first)
    return out


def _product_of(cat: dict) -> str | None:
    for e in cat.get("entries", {}).values():
        if e.get("role") == "run.product" and e.get("render"):
            p = str(e["render"])
            return None if p.startswith("default(") else p
    return None


def _bind(path: str, product: str | None) -> str:
    if PRODUCT_TOKEN not in path:
        return path
    if not product:
        return path                # 제품이 없으면 자리를 못 정한다 — 그대로 보여 준다
    return path.replace(PRODUCT_TOKEN, product)


# ── 값 넣기 ─────────────────────────────────────────────────────────────────

def _coerce(value, typ: str):
    if typ == "str":
        s = str(value).strip()
        if not s:
            raise DeclareError("빈 문자열은 선언이 아니다 — 모르면 넣지 않는다")
        return s
    if typ == "num":
        try:
            f = float(value)
        except (TypeError, ValueError):
            raise DeclareError(f"숫자가 아니다: {value!r}")
        return int(f) if f == int(f) and "." not in str(value) else f
    if typ == "pair_num":
        v = value
        if isinstance(v, str):
            v = [x for x in v.replace("[", " ").replace("]", " ")
                 .replace(",", " ").split() if x]
        if not isinstance(v, (list, tuple)) or len(v) != 2:
            raise DeclareError(f"두 개가 필요하다(하한 상한): {value!r}")
        lo, hi = float(v[0]), float(v[1])
        if lo >= hi:
            raise DeclareError(f"하한이 상한보다 크거나 같다: {lo} ≥ {hi} — "
                               "순서를 바꿔 적었을 수 있다")
        return [lo, hi]
    raise DeclareError(f"모르는 형: {typ}")


def _find_field(path: str, product: str | None, spec: dict | None = None) -> dict:
    """양식 정본에 **선언 자리로 등재된** 경로인가. 아니면 쓰지 않는다."""
    spec = spec or DS.load()
    for d in declarable(spec):
        if _bind(d["path"], product) == path or d["path"] == path:
            return d
    known = sorted({_bind(d["path"], product) for d in declarable(spec)})
    raise DeclareError(
        f"선언 자리가 아니다: {path}\n"
        "양식 정본(document_spec.yaml)의 absent.fields 에 없는 경로에는 쓰지 않는다 —\n"
        "자리가 늘어나면 선언 자리가 곧 아무 데나가 된다.\n  쓸 수 있는 자리:\n    "
        + "\n    ".join(known))


def declared_path(product: str, registry_path=None) -> Path:
    import os
    base = Path(registry_path or os.environ.get(
        "ORCH_PRODUCT_REGISTRY", str(C.REPO / "registry" / "products.yaml")))
    d = base.parent / C.DECLARED_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{product}.yaml"


def set_value(path: str, value, *, product: str | None = None, by: str = "",
              why: str = "", spec: dict | None = None, registry_path=None) -> dict:
    """선언 하나를 기록한다. **덮어쓰기는 기록을 남기고 한다.**"""
    if not by.strip():
        raise DeclareError("누가 말했는지 없이 선언하지 않는다 — `--by` 가 필요하다(A-1)")
    target, _, rest = path.partition(":")
    if target not in TARGETS:
        raise DeclareError(f"쓸 수 없는 대상: {target!r} — 지금 쓸 수 있는 것은 {TARGETS}")
    field = _find_field(path, product, spec)
    if PRODUCT_TOKEN in field["path"] and not product:
        raise DeclareError("어느 제품인지 없이 선언할 수 없다 — `--product` 가 필요하다")
    prod = product or rest.split(".")[0]
    val = _coerce(value, field.get("type", "str"))

    import yaml
    p = declared_path(prod, registry_path)
    doc = (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p.exists() else {}
    doc.setdefault("product", prod)
    doc.setdefault("규율", "사람이 말한 값이다. products.yaml 을 고치지 않고 위에 얹는다.")
    vals = doc.setdefault("values", {})
    trail = doc.setdefault("기록", [])

    keys = rest.split(".")[1:]         # 앞은 제품 이름
    if not keys:
        raise DeclareError(f"경로에 필드가 없다: {path}")
    node = vals
    for k in keys[:-1]:
        node = node.setdefault(k, {})
        if not isinstance(node, dict):
            raise DeclareError(f"{path}: 중간 경로가 값이다 — 구조가 어긋난다")
    prev = node.get(keys[-1], None)
    node[keys[-1]] = val

    from datetime import datetime, timedelta, timezone
    # 표준시는 한국 — 원장·타임라인과 같은 기준을 쓴다(D-28). 표기에 오프셋을 남긴다.
    at = datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="seconds")
    doc["by"], doc["at"] = by, at
    trail.append({"path": path, "before": prev, "after": val,
                  "by": by, "at": at, "why": why or "(사유 없음)",
                  "label": field.get("label"), "unit": field.get("unit") or ""})
    p.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False),
                 encoding="utf-8")
    return {"path": path, "product": prod, "label": field.get("label"),
            "before": prev, "after": val, "file": str(p),
            "overwrote": prev is not None,
            "다음": "파이프라인을 다시 돌리면 이 값이 참조로 문서에 실린다"}


def show(product: str | None = None, registry_path=None) -> list[dict]:
    import os
    import yaml
    base = Path(registry_path or os.environ.get(
        "ORCH_PRODUCT_REGISTRY", str(C.REPO / "registry" / "products.yaml")))
    d = base.parent / C.DECLARED_DIR
    if not d.is_dir():
        return []
    out = []
    for f in sorted(d.glob("*.yaml")):
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        if product and doc.get("product") != product:
            continue
        out.append({"file": f.name, **doc})
    return out


# ── 자기 시험 ────────────────────────────────────────────────────────────────

def self_test() -> int:
    import shutil
    import tempfile
    ok = fail = 0

    def chk(n, cond, d=""):
        nonlocal ok, fail
        if cond:
            ok += 1; print(f"  PASS  {n}")
        else:
            fail += 1; print(f"  FAIL  {n}  {d}")

    print("[declare.py 자기 시험]")
    spec = DS.load()

    ds = declarable(spec)
    chk(f"양식이 선언 자리 {len(ds)}종을 밝힌다", len(ds) >= 15, str(len(ds)))
    chk("전부 label 과 type 을 갖는다",
        all(d.get("label") and d.get("type") for d in ds))
    chk("전부 쓸 수 있는 대상이다",
        all(d["path"].split(":")[0] in TARGETS for d in ds),
        str({d["path"].split(":")[0] for d in ds}))
    chk("반입·도구 절은 선언 자리를 갖지 않는다",
        all((s.get("absent") or {}).get("kind") == "선언"
            for s in spec["sections"] if (s.get("absent") or {}).get("fields")))

    # 격리된 레지스트리에서 쓴다 — W-1 을 어기지 않는다
    tmp = Path(tempfile.mkdtemp())
    shutil.copy(C.REPO / "registry" / "products.yaml", tmp / "products.yaml")
    reg = tmp / "products.yaml"

    r = set_value("products:example_x_band.substrate.name", "Rogers AD255C",
                  product="example_x_band", by="시험", why="자기 시험", registry_path=reg)
    chk("선언이 파일로 남는다", Path(r["file"]).exists() and r["after"] == "Rogers AD255C")
    chk("products.yaml 은 손대지 않는다",
        reg.read_text(encoding="utf-8") ==
        (C.REPO / "registry" / "products.yaml").read_text(encoding="utf-8"))

    r2 = set_value("products:example_x_band.substrate.er", "2.55",
                   product="example_x_band", by="시험", registry_path=reg)
    chk("숫자로 형변환한다", r2["after"] == 2.55, str(r2["after"]))

    r3 = set_value("products:example_x_band.substrate.er", "2.57",
                   product="example_x_band", by="시험", why="2차 설계", registry_path=reg)
    chk("덮어쓰면 이전 값을 기록에 남긴다", r3["overwrote"] and r3["before"] == 2.55, str(r3))

    docs = show("example_x_band", registry_path=reg)
    chk("기록이 누적된다", len(docs) == 1 and len(docs[0]["기록"]) == 3,
        str(len(docs[0]["기록"]) if docs else 0))

    # 적재하면 얹힌다 — 이것이 "말하면 채워진다"의 실체다
    merged = C.load_registry(reg)["products"]["example_x_band"]
    chk("적재 시 선언이 얹힌다", merged["substrate"]["er"] == 2.57
        and merged["substrate"]["name"] == "Rogers AD255C", str(merged.get("substrate")))
    chk("얹힌 값의 산지가 남는다",
        any("example_x_band.yaml" in s for s in merged.get("선언_출처", [])),
        str(merged.get("선언_출처")))
    chk("선언하지 않은 값은 원본 그대로",
        merged["substrate"]["er_ref_ghz"] == 10.0, str(merged["substrate"]))

    # 거부해야 하는 것들
    for name, fn, frag in (
        ("양식에 없는 경로 거부",
         lambda: set_value("products:example_x_band.hidden.x", "1",
                           product="example_x_band", by="시험", registry_path=reg),
         "선언 자리가 아니다"),
        ("쓸 수 없는 대상 거부",
         lambda: set_value("secrets:example_x_band.label", "x",
                           product="example_x_band", by="시험", registry_path=reg),
         "쓸 수 없는 대상"),
        ("누가 말했는지 없으면 거부",
         lambda: set_value("products:example_x_band.use", "x",
                           product="example_x_band", by="  ", registry_path=reg),
         "누가 말했는지"),
        ("숫자 자리에 말 거부",
         lambda: set_value("products:example_x_band.f0_ghz", "구 점 사",
                           product="example_x_band", by="시험", registry_path=reg),
         "숫자가 아니다"),
        ("빈 문자열 거부",
         lambda: set_value("products:example_x_band.use", "   ",
                           product="example_x_band", by="시험", registry_path=reg),
         "빈 문자열"),
        ("뒤집힌 대역 거부",
         lambda: set_value("products:example_x_band.band_ghz", "9.5 9.3",
                           product="example_x_band", by="시험", registry_path=reg),
         "하한이 상한보다"),
    ):
        try:
            fn()
            chk(name, False, "통과해 버렸다")
        except DeclareError as e:
            chk(name, frag in str(e), str(e)[:70])

    r4 = set_value("products:example_x_band.band_ghz", "[9.3, 9.5]",
                   product="example_x_band", by="시험", registry_path=reg)
    chk("대역은 두 값으로 들어간다", r4["after"] == [9.3, 9.5], str(r4["after"]))

    # run 에 붙여 본다 — 지금 비어 있는 절만 물어야 한다
    try:
        g = gaps("L1-test2", spec)
        paths = {d["path"] for d in g["declarable"]}
        # 값 단위로 짚는다 — 절에 값 하나만 있어도 "채워진 절"로 보면 아무것도 못 묻는다
        #   test2 는 CST 가 유전율을 선언하고 있다 — 있는 것을 다시 묻지 않는다
        chk("이미 있는 값은 묻지 않는다",
            not any(p.endswith(".substrate.er") for p in paths), str(sorted(paths)))
        chk("비어 있는 값은 묻는다",
            any(p.endswith(".use") for p in paths), str(sorted(paths)))
        chk("무엇으로 판정했는지 밝힌다",
            all(d.get("판정근거") in ("값", "절") for d in g["declarable"]))
        chk("제품 자리를 실제 제품으로 묶는다",
            all(PRODUCT_TOKEN not in d["path"] for d in g["declarable"]) or not g["product"],
            str(sorted(paths)[:3]))
    except FileNotFoundError:
        chk("run 이 없으면 건너뛴다", True)

    shutil.rmtree(tmp, ignore_errors=True)
    print(f"\n결과: {ok}/{ok + fail} PASS")
    return 0 if fail == 0 else 1


def main(argv):
    if len(argv) < 2:
        print(__doc__); return 2
    cmd = argv[1]
    if cmd == "self-test":
        return self_test()
    if cmd in ("gaps", "ask"):
        g = gaps(argv[2])
        if cmd == "gaps":
            print(json.dumps(g, ensure_ascii=False, indent=2)); return 0
        print(f"제품 {g['product'] or '(미배정)'} · 지금 빈 필수 절 {g['n_empty_sections']}개\n")
        if not g["declarable"]:
            print("  말로 채울 수 있는 자리는 없다 — 남은 공백은 반입이나 도구를 기다린다.")
            return 0
        print("아래는 **아시면 말씀만 하시면 채워집니다.** 값은 문서가 아니라 선언 자리로 들어갑니다.\n")
        sec = None
        for d in g["declarable"]:
            if d["section"] != sec:
                sec = d["section"]
                print(f"  [{sec}]  담당 {d['owner']}")
            u = f" ({d['unit']})" if d.get("unit") else ""
            print(f"      · {d['label']}{u}\n          → {d['path']}")
        print("\n  넣는 법:  python tools/declare.py set <경로> <값> "
              "--product <제품> --by <이름> --why <사유>")
        return 0
    if cmd == "set":
        import argparse
        ap = argparse.ArgumentParser(prog="declare.py set")
        ap.add_argument("path"); ap.add_argument("value")
        ap.add_argument("--product"); ap.add_argument("--by", default="")
        ap.add_argument("--why", default="")
        a = ap.parse_args(argv[2:])
        try:
            r = set_value(a.path, a.value, product=a.product, by=a.by, why=a.why)
        except DeclareError as e:
            print(f"거부: {e}"); return 1
        print(json.dumps(r, ensure_ascii=False, indent=2)); return 0
    if cmd == "show":
        print(json.dumps(show(argv[2] if len(argv) > 2 else None),
                         ensure_ascii=False, indent=2)); return 0
    print(__doc__); return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
