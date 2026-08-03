#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/discover.py — 원천 폴더 → 자산 entry 분해 (LLM 0콜)

설계 흐름도의 맨 앞 상자 **「원천 폴더 큐 투입 · run_id 발급」** 이 하는 일이다.
클래스 순서(A-2)는 건드리지 않는다 — entry 마다 같은 파이프라인을 처음부터 돌린다.

## "CST 프로젝트 파일을 기준으로" 를 비판적으로 본 결과

제안은 **강한 닻이지만 규칙 전부가 될 수 없다.** 실물 3종이 세 가지 반례를 준다.

| 반례 | 무엇이 깨지나 | 대응 |
| --- | --- | --- |
| `Antenna_CAD_ECO` — 도면만 있고 CST 프로젝트가 **없다** | CST 기준만 쓰면 이 원천은 **entry 0개**가 된다. 실재하는 안테나가 통째로 사라진다 | 규칙 2(잔여 도면 묶음) |
| `test2/Model/3D/KORIL_2.5_1.dxf` — CST 프로젝트 **안**의 임포트 DXF | "도면도 entry"를 파일 단위로 적용하면 한 안테나가 둘로 쪼개진다 | **포함관계가 이긴다** — CST 컨테이너가 하위 전체를 claim |
| 한 프로젝트에 안테나가 여럿일 수 있다 | "1 프로젝트 = 1 entry"가 항상 참은 아니다 | 나누지 **않는다**(아래) |

세 번째가 중요하다. 한 프로젝트 안에 변형이 여럿이어도 **선언만으로는 구별할 수 없다.**
우리가 나누면 없는 경계를 지어내는 것이다(N-3). 1 프로젝트 = 1 entry 로 두고, 다중 여부는
해석이 드러내게 한다 — `test2` 의 콤 라인 28줄이 칩 4 × (RX 4 + TX 3) 인 것은 해석의
`array_grouping` 이 말한다. **문서가 "여럿이다"라고 말하는 것과 파이프라인이 쪼개는 것은
다른 문제다.**

## 규칙 — 우선순위대로, 앞이 이긴다

1. **CST 컨테이너** (`Model.prj` · `*.cst`) → entry. **하위 전체를 claim** 한다.
2. **잔여 CAD 묶음** — claim 안 된 도면(dxf·dwg·gerber)을 **직접 담은 폴더** 단위로 묶는다.
3. **잔여 기타** → entry 로 만들지 않고 `unassigned` 로 보고한다. 조용히 버리지 않는다.

## 배포용 도안의 연결 — 선언과 추정을 섞지 않는다

CST 프로젝트에서 배포용 DXF/DWG 를 뽑아 두는 경우, 그 도안은 **독립 entry** 다(자기 판독
수준과 자기 형상을 갖는다). 다만 연결을 잃으면 안 되므로 태그를 남긴다. 근거를 두 등급으로
**나눈다** — 우리 규율에서 추정은 확정이 아니다.

    derived_from       CST 가 **스스로 선언한** 임포트 경로와 파일명이 일치한다 → 확정.
                       ModelHistory.json 의 `import dxf file: …` 이 근거다. 추정이 아니다.
    link_candidates[]  이름 접두 공유 · 형제 폴더 같은 **정황**. confidence + basis 를 달고
                       `confirmed_by: null` 로 남는다. 사람이 확정한다(A-1).

기하 지문(bbox·레이어·소자 수)으로 후보를 좁히는 것은 **추출 뒤**라야 가능하다 —
`python tools/assets.py link` 가 그 일을 한다. 이것이 "CST 에서 뽑은 정보를 DXF 분석에
쓴다"의 실체다.

CLI
    python tools/discover.py scan <원천폴더> [--json]
    python tools/discover.py self-test
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

# CST 프로젝트를 알리는 파일 — vendor 관례이지 우리가 정한 값이 아니다((b) 외부 포맷 규약)
CST_MARKERS = ("Model.prj",)
CST_SUFFIXES = (".cst",)

CAD_SUFFIXES = (".dxf", ".dwg")
GERBER_SUFFIXES = (".gbr", ".ger", ".gtl", ".gbl", ".gts", ".gbs", ".gto", ".gbo", ".drl", ".txt")
GERBER_HINT = re.compile(r"gerber", re.I)

# 임포트 선언을 훑을 때 읽는 파일 — 전수 파싱이 아니라 **선언 스캔**이다
HISTORY_NAMES = ("ModelHistory.json", "Model.prj")
IMPORT_RE = re.compile(r'["\']?([A-Za-z]:[\\/][^"\'\n]+?\.(?:dxf|dwg|gds|stp|step|sat))["\']?', re.I)

MAX_FILES = 20000        # 폭주 방지. 넘으면 잘랐다고 **말한다**(조용한 절단 금지)


def _is_cst_root(d: Path) -> bool:
    """CST 프로젝트 루트인가. `test2/Model/Model.prj` → 루트는 `test2`."""
    if any((d / "Model" / m).exists() for m in CST_MARKERS):
        return True
    return any(p.suffix.lower() in CST_SUFFIXES for p in d.glob("*") if p.is_file())


def _cad_kind(p: Path) -> str | None:
    s = p.suffix.lower()
    if s in CAD_SUFFIXES:
        return s.lstrip(".")
    if s in GERBER_SUFFIXES and (GERBER_HINT.search(str(p)) or s not in (".txt",)):
        return "gerber"
    return None


def _declared_imports(root: Path) -> list[str]:
    """CST 가 **선언한** 임포트 원본 경로. 파싱이 아니라 선언 스캔이다."""
    out: list[str] = []
    for name in HISTORY_NAMES:
        for p in root.rglob(name):
            try:
                txt = p.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            out += IMPORT_RE.findall(txt)
    seen, uniq = set(), []
    for x in out:
        k = x.replace("\\", "/").lower()
        if k not in seen:
            seen.add(k)
            uniq.append(x)
    return uniq


def _slug(s: str) -> str:
    s = re.sub(r"[^0-9A-Za-z가-힣_.\-]+", "_", str(s)).strip("._-")
    return s or "entry"


def scan(source_path) -> dict:
    root = Path(source_path).resolve()
    if not root.exists():
        raise FileNotFoundError(f"원천 없음: {root}")
    if root.is_file():
        root = root.parent

    files = []
    truncated = False
    for p in sorted(root.rglob("*")):
        if p.is_file():
            files.append(p)
            if len(files) >= MAX_FILES:
                truncated = True
                break

    # ── 1) CST 컨테이너 — 하위 전체를 claim ────────────────────────────────
    cst_roots: list[Path] = []
    for d in sorted({p.parent for p in files} | {root}):
        cur = d
        # Model/Model.prj 형태면 프로젝트 루트는 두 단계 위
        if cur.name == "3D" or cur.name == "DS":
            cur = cur.parent
        if cur.name == "Model":
            cur = cur.parent
        if _is_cst_root(cur) and cur not in cst_roots:
            cst_roots.append(cur)
    # 중첩 제거 — 상위 프로젝트가 하위를 이미 claim 한다
    cst_roots = [r for r in cst_roots
                 if not any(r != o and o in r.parents for o in cst_roots)]

    claimed: set[Path] = set()
    entries: list[dict] = []
    for r in sorted(cst_roots):
        sub = [f for f in files if r == f.parent or r in f.parents]
        claimed |= set(sub)
        imports = _declared_imports(r)
        entries.append({
            "entry_id": _slug(r.name),
            "kind": "cst_project",
            "path": str(r),
            "rel": str(r.relative_to(root)) if r != root else ".",
            "anchor": "CST 프로젝트 컨테이너(Model.prj·*.cst) — 하위 전체를 이 entry 가 갖는다",
            "n_files": len(sub),
            "cad_inside": sorted({f.name for f in sub if _cad_kind(f)}),
            "declared_imports": imports,
            "project_tag": _slug(r.name),
            "derived_from": None,
            "link_candidates": [],
        })

    # ── 2) 잔여 CAD 묶음 — 도면을 **직접 담은 폴더** 단위 ──────────────────
    groups: dict[Path, list[Path]] = {}
    for f in files:
        if f in claimed:
            continue
        if _cad_kind(f):
            groups.setdefault(f.parent, []).append(f)

    by_import = {}
    for e in entries:
        for imp in e["declared_imports"]:
            by_import.setdefault(Path(imp.replace("\\", "/")).name.lower(), []).append(e)

    for d, fs in sorted(groups.items()):
        claimed |= set(fs)
        names = sorted(f.name for f in fs)
        eid = _slug(d.name if d != root else root.name)

        # 확정 연결 — CST 가 선언한 임포트 파일명과 일치할 때만
        derived, basis = None, []
        for n in names:
            for e in by_import.get(n.lower(), []):
                derived = e["project_tag"]
                basis.append(f"{e['entry_id']} 가 선언한 임포트 파일명과 일치: {n}")
        # 후보 연결 — 정황. 확정이 아니다
        cands = []
        if not derived:
            for e in entries:
                why, conf = [], 0.0
                stem = {Path(n).stem.lower() for n in names}
                istem = {Path(i.replace("\\", "/")).stem.lower()
                         for i in e["declared_imports"]}
                shared = {a for a in stem for b in istem
                          if a[:4] and (a.startswith(b[:4]) or b.startswith(a[:4]))}
                if shared:
                    conf += 0.4
                    why.append(f"이름 접두 공유: {sorted(shared)[:3]}")
                if d.parent == Path(e["path"]).parent:
                    conf += 0.2
                    why.append("같은 부모 폴더(형제)")
                if conf > 0:
                    cands.append({"project_tag": e["project_tag"], "confidence": round(conf, 2),
                                  "basis": why, "confirmed_by": None,
                                  "규율": "후보다 — 확정은 사람(A-1). 기하 대조는 assets.py link"})

        entries.append({
            "entry_id": eid,
            "kind": "cad_group",
            "path": str(d),
            "rel": str(d.relative_to(root)) if d != root else ".",
            "anchor": "CST 에 속하지 않은 도면 묶음 — 도면만 있는 원천이 실재한다",
            "n_files": len(fs),
            "cad_inside": names,
            "declared_imports": [],
            "project_tag": _slug(eid),
            "derived_from": derived,
            "derived_basis": basis,
            "link_candidates": sorted(cands, key=lambda c: -c["confidence"]),
        })

    # entry_id 중복 해소 — 부모 이름을 붙인다(조용한 덮어쓰기 금지)
    seen: dict[str, int] = {}
    for e in entries:
        base = e["entry_id"]
        if base in seen:
            seen[base] += 1
            parent = Path(e["path"]).parent.name
            e["entry_id"] = _slug(f"{parent}_{base}") if parent else f"{base}_{seen[base]}"
        else:
            seen[base] = 1

    unassigned = sorted(str(f.relative_to(root)) for f in files if f not in claimed)
    return {
        "source": str(root),
        "rule_version": C.effective_rule_version(),
        "n_files": len(files),
        "truncated": truncated,
        "n_entries": len(entries),
        "entries": sorted(entries, key=lambda e: (e["kind"] != "cst_project", e["rel"])),
        "unassigned": unassigned,
        "unassigned_note": ("entry 가 되지 못한 파일이다. 버린 것이 아니라 **분류되지 않은** "
                            "것이며, 규칙을 늘려야 하는지 사람이 본다."),
        "규율": ("CST 컨테이너가 하위를 갖는다(과분할 방지) · 도면만 있는 원천도 entry 가 된다 · "
               "한 프로젝트를 여러 안테나로 쪼개지 않는다(경계를 지어내지 않는다, N-3)"),
    }


# ── 자기 시험 — 실물 트리 ────────────────────────────────────────────────────

def self_test() -> int:
    ok = fail = 0

    def chk(n, cond, d=""):
        nonlocal ok, fail
        if cond:
            ok += 1
            print(f"  PASS  {n}")
        else:
            fail += 1
            print(f"  FAIL  {n}  {d}")

    print("[discover.py 자기 시험 — 실물 트리]")
    base = C.data_dir() / "handoff" / "04_experiment_data"
    if not base.exists():
        print("  건너뜀 — 실물 없음")
        return 2

    r = scan(base)
    ids = [e["entry_id"] for e in r["entries"]]
    kinds = {e["entry_id"]: e["kind"] for e in r["entries"]}
    chk(f"entry 3개 {ids}", r["n_entries"] == 3, str(ids))
    chk("CST 프로젝트 2건", sum(1 for k in kinds.values() if k == "cst_project") == 2, str(kinds))
    chk("도면 묶음 1건 (CST 없는 원천도 잡힌다)",
        kinds.get("Antenna_CAD_ECO") == "cad_group", str(kinds))

    # 과분할 방지 — test2 안의 DXF 가 별도 entry 가 되면 안 된다
    chk("CST 내부 DXF 는 별도 entry 가 아니다", "KORIL_2.5_1" not in " ".join(ids), str(ids))
    t2 = next(e for e in r["entries"] if e["entry_id"] == "test2")
    chk("내부 DXF 는 그 프로젝트가 갖는다", "KORIL_2.5_1.dxf" in t2["cad_inside"],
        str(t2["cad_inside"]))
    chk("선언 임포트를 읽었다", any("KORIL" in i for i in t2["declared_imports"]),
        str(t2["declared_imports"]))

    eco = next(e for e in r["entries"] if e["entry_id"] == "Antenna_CAD_ECO")
    chk("도면 4건 묶음", len(eco["cad_inside"]) == 4, str(eco["cad_inside"]))
    # 실물 반증 — ECO 는 test2 의 배포본이 **아니다**(이름·규모가 다르다)
    chk("근거 없는 연결을 만들지 않는다", eco["derived_from"] is None, str(eco["derived_from"]))

    # 하위 폴더 하나만 가리켜도 그 entry 하나
    r2 = scan(base / "cst_projects" / "test2")
    chk("깊은 폴더 단독 지정 → entry 1", r2["n_entries"] == 1 and
        r2["entries"][0]["kind"] == "cst_project", str(r2["n_entries"]))

    # 배포본 연결 — 선언된 임포트 파일명과 같은 이름의 도면을 밖에 둔 상황
    import shutil
    import tempfile
    tmp = Path(tempfile.mkdtemp()) / "원천"
    shutil.copytree(base / "cst_projects" / "test2", tmp / "test2")
    dist = tmp / "배포도안"
    dist.mkdir(parents=True)
    (dist / "KORIL_2.5.dxf").write_text("0\nSECTION\n", encoding="utf-8")
    r3 = scan(tmp)
    d = next(e for e in r3["entries"] if e["entry_id"] == "배포도안")
    chk("배포 도안은 **독립 entry**", d["kind"] == "cad_group")
    chk("선언 근거로 프로젝트에 연결(derived_from)", d["derived_from"] == "test2",
        str(d["derived_from"]))
    chk("연결 근거를 남긴다", bool(d.get("derived_basis")), str(d.get("derived_basis")))

    # 이름만 비슷한 경우 — 확정이 아니라 후보여야 한다
    dist2 = tmp / "비슷한이름"
    dist2.mkdir()
    (dist2 / "KORIL_rev9.dxf").write_text("0\nSECTION\n", encoding="utf-8")
    r4 = scan(tmp)
    d2 = next(e for e in r4["entries"] if e["entry_id"] == "비슷한이름")
    chk("이름 정황은 확정이 아니다", d2["derived_from"] is None, str(d2["derived_from"]))
    chk("후보로 남고 사람 확정 대기",
        bool(d2["link_candidates"]) and
        d2["link_candidates"][0]["confirmed_by"] is None,
        json.dumps(d2["link_candidates"], ensure_ascii=False)[:200])

    # 미분류를 조용히 버리지 않는다
    (tmp / "읽지못한것.bin").write_bytes(b"\x00\x01")
    r5 = scan(tmp)
    chk("미분류 파일을 보고한다", "읽지못한것.bin" in r5["unassigned"], str(r5["unassigned"])[:120])

    chk("entry_id 중복 없음", len(ids) == len(set(ids)), str(ids))
    print(f"\n결과: {ok}/{ok + fail} PASS")
    return 0 if fail == 0 else 1


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    if argv[1] == "self-test":
        return self_test()
    if argv[1] == "scan":
        r = scan(argv[2])
        if "--json" in argv:
            print(json.dumps(r, ensure_ascii=False, indent=2))
            return 0
        print(f"{r['source']}\n  파일 {r['n_files']}건 → entry {r['n_entries']}개"
              + ("  (목록 절단됨)" if r["truncated"] else ""))
        for e in r["entries"]:
            print(f"\n  [{e['kind']}] {e['entry_id']}   {e['rel']}")
            print(f"      {e['anchor']}")
            print(f"      파일 {e['n_files']}건" +
                  (f" · 도면 {e['cad_inside']}" if e["cad_inside"] else ""))
            if e.get("declared_imports"):
                print(f"      선언 임포트: {e['declared_imports']}")
            if e.get("derived_from"):
                print(f"      ← 확정 연결: {e['derived_from']}  ({'; '.join(e.get('derived_basis') or [])})")
            for c in e.get("link_candidates") or []:
                print(f"      ~ 후보 연결: {c['project_tag']} (신뢰 {c['confidence']}) "
                      f"— {'; '.join(c['basis'])}  [확정: 사람]")
        if r["unassigned"]:
            print(f"\n  미분류 {len(r['unassigned'])}건: {r['unassigned'][:8]}")
        return 0
    print(f"알 수 없는 명령: {argv[1]}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
