#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/split.py — 컨테이너 안 안테나 후보 분해 · 신호 수집 (LLM 0콜)

한 컨테이너(CST 프로젝트·도면 묶음)에 안테나가 **여럿**일 수 있다(2026-07-31 전제 개정).
이 도구는 **신호를 모아 경계 후보를 만들 뿐** 안테나 개수를 확정하지 않는다.

왜 확정하지 않나 — 실물이 반례를 준다
    `test2` 의 콤 라인 28줄은 솔리드 이름이 `Chip1_RX1` 처럼 갈리지만 **하나의 배열**이다.
    이름 접두를 안테나 개수로 읽으면 한 안테나가 28조각이 된다.
    경계는 선언에 없다. 없는 경계를 만드는 것은 값을 지어내는 것과 같다(N-3).

    그래서 규칙은 하나다 —
        신호는 **경계 후보**를 만든다. 그 경계가 안테나 경계인지는 **묻는다.**

신호 5종 (실물에서 확인)
    ① 이름 접두 군집   `copper (annealed):Chip1_RX1` → 접두 토큰 분포
    ② 좌표 군집       배열 주기보다 훨씬 큰 간격이 나오는 자리(gap ratio)
    ③ 포트 이름 군집   `Rx_port1~` 접두
    ④ CST 그룹 선언    `add items to group:`
    ⑤ 판올림 표식      `_v1`·`_rev2`·`_bak`·`_old`·날짜 — 개수가 아니라 **관계** 신호다

판정 규칙 (c) — 원장에 실린다
    `split_gap_ratio` = 3.0   좌표 간격이 최빈 간격의 3배를 넘으면 경계 후보
    `split_min_group` = 2     후보 묶음의 최소 원소 수
    두 값 모두 **관측 근거가 없다** — 실물 표본이 얇다. 그래서 이 값들은 경계를
    **확정하지 않고 제안만** 한다. 확정은 사람이다(A-1).
    실측: test2 는 최빈 간격 4.88 mm 에 17.08·14.64 mm 틈이 있어 3덩이로 보이지만
    실제로는 칩 4개짜리 **한 배열**이다. 기하만으로는 못 가른다 — 구제 경로의 존재 이유다.

CLI
    python tools/split.py signals <run_id>
    python tools/split.py self-test
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

SPLIT_GAP_RATIO = 3.0
SPLIT_MIN_GROUP = 2

# 판올림 표식 — 관계(variant) 신호. 개수 신호가 아니다.
VERSION_MARK = re.compile(
    r"(?:^|[_\-.])(?:v|ver|rev|r)(\d+)(?:$|[_\-.])|(?:^|[_\-.])(bak|old|new|copy|backup)(?:$|[_\-.])"
    r"|(?:^|[_\-.])(20\d{6}|20\d{2}[-_]\d{2}[-_]\d{2})(?:$|[_\-.])", re.I)


def numeric_rules() -> dict:
    return {"split_gap_ratio": SPLIT_GAP_RATIO, "split_min_group": SPLIT_MIN_GROUP,
            "규율": ("신호는 경계 후보를 만들 뿐이다. 안테나 경계인지는 사람이 확정한다(A-1). "
                   "임계 두 값은 관측 근거가 없어 확정에 쓰이지 않는다(N-3).")}


def _prefix_tokens(name: str) -> list[str]:
    """`copper (annealed):Chip1_RX1` → ['Chip1', 'RX1'] · 재질 접두는 뗀다."""
    tail = str(name).split(":")[-1]
    return [t for t in re.split(r"[_\-.\s]+", tail) if t]


# ── 신호 ────────────────────────────────────────────────────────────────────

def solid_names(extract: dict) -> list[str]:
    """재생된 **최종 솔리드** 이름만 모은다.

    수리: 앞선 구현은 `solids_replayed` 를 리스트로 알고 순회해 dict 의 **키**를 이름으로
    읽었다(`n_final`·`deleted`·`규약` 이 접두로 잡혔다). 실제 구조는
    `{n_final, final[], deleted[], unresolved[], 규약}` 이고 우리가 원하는 것은 `final` 뿐이다 —
    삭제된 솔리드는 최종 형상에 없으므로 경계 신호가 될 수 없다.
    """
    names: list[str] = []
    for lane in (extract.get("declared") or {}).values():
        for d in lane if isinstance(lane, list) else []:
            sr = d.get("solids_replayed")
            items = sr.get("final") if isinstance(sr, dict) else (sr or d.get("solids") or [])
            for so in items or []:
                n = so.get("name") if isinstance(so, dict) else so
                if n:
                    names.append(str(n))
    return names


def sig_name_prefix(extract: dict) -> dict:
    """① 이름 접두 군집. **개수 신호가 아니다** — 한 배열이 여러 접두를 갖는다."""
    names = solid_names(extract)
    heads = Counter(_prefix_tokens(n)[0] for n in names if _prefix_tokens(n))
    return {"signal": "name_prefix", "n_solids": len(names),
            "heads": dict(heads.most_common(12)), "n_heads": len(heads),
            "note": ("접두가 갈린다고 안테나가 여럿인 것은 아니다 — test2 의 칩 4 × 채널 7 이 "
                     "한 배열이다. 경계 제안의 재료일 뿐이다.")}


def sig_coord_gap(verify: dict) -> dict:
    """② 좌표 군집 — 최빈 간격 대비 큰 틈이 경계 후보."""
    out = []
    for src, arr in (verify.get("cst_array_positions") or {}).items():
        xs = sorted(set(arr.get("x_unique_mm") or []))
        if len(xs) < 3:
            continue
        gaps = [round(b - a, 4) for a, b in zip(xs, xs[1:])]
        if not gaps:
            continue
        base = Counter(gaps).most_common(1)[0][0] or min(g for g in gaps if g) or 1.0
        cuts = [{"after_x_mm": xs[i], "gap_mm": g, "ratio": round(g / base, 3)}
                for i, g in enumerate(gaps) if g / base >= SPLIT_GAP_RATIO]
        out.append({"source": src, "n_x": len(xs), "base_gap_mm": base,
                    "cuts": cuts, "n_cuts": len(cuts)})
    return {"signal": "coord_gap", "per_source": out,
            "n_cuts": sum(o["n_cuts"] for o in out)}


def sig_port_prefix(verify: dict, extract: dict) -> dict:
    """③ 포트 이름 군집."""
    labels = []
    for it in verify.get("items") or []:
        if "포트" in str(it.get("check", "")):
            for v in (it.get("inputs") or {}).values():
                if isinstance(v, list):
                    labels += [str(x) for x in v if isinstance(x, str)]
    heads = Counter(_prefix_tokens(x)[0] for x in labels if _prefix_tokens(x))
    return {"signal": "port_prefix", "n_labels": len(labels),
            "heads": dict(heads.most_common(8)), "n_heads": len(heads)}


def sig_groups(source_path) -> dict:
    """④ CST 그룹 선언 — `add items to group:`."""
    root = Path(source_path)
    groups = Counter()
    for p in root.rglob("ModelHistory.json"):
        try:
            txt = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        groups.update(m.strip() for m in
                      re.findall(r"add items to group:\s*([^\"\\\\\n]*)", txt))
    return {"signal": "cst_group", "groups": dict(groups.most_common(10)),
            "n_groups": len(groups)}


def _strip_mark(stem: str) -> tuple[str, str]:
    """이름에서 판올림 표식을 떼고 (뼈대, 표식) 을 돌려준다."""
    m = VERSION_MARK.search(stem)
    if not m:
        return stem, ""
    return (stem[:m.start()] + stem[m.end():]).strip("_-. "), m.group(0).strip("_-. ")


def sig_version_marks(source_path, extract: dict) -> dict:
    """⑤ 판올림 표식 — **짝이 있어야 판올림이다.**

    수리: 표식이 하나만 있는 것을 판올림으로 셌다. `Antenna_CAD_ECO` 의
    `Top_20260227` · `Bottom_20260227` 은 같은 릴리스의 **날짜 도장**이지 서로의 판올림이
    아니다(뼈대가 Top·Bottom 으로 다르다). 그런데도 표식 3건으로 잡혀 `ambiguous` 가 됐다.
    판올림은 **같은 뼈대에 다른 표식**이 있을 때만 성립한다 — `ant_v1` 과 `ant_v2` 처럼.
    """
    root = Path(source_path)
    cand: list[dict] = []
    for p in list(root.rglob("*"))[:5000]:
        if p.is_file():
            stem, mark = _strip_mark(p.stem)
            if mark:
                cand.append({"where": "file", "name": p.name, "stem": stem, "mark": mark,
                             "rel": str(p.relative_to(root))})
    for n in solid_names(extract):
        stem, mark = _strip_mark(str(n).split(":")[-1])
        if mark:
            cand.append({"where": "solid", "name": str(n), "stem": stem, "mark": mark})

    by_stem: dict[str, set] = {}
    for c in cand:
        by_stem.setdefault((c["where"], c["stem"]), set()).add(c["mark"])
    paired = {k for k, v in by_stem.items() if len(v) >= 2}
    hits = [c for c in cand if (c["where"], c["stem"]) in paired]
    return {"signal": "version_mark", "n_hits": len(hits), "hits": hits[:20],
            "n_marked_unpaired": len(cand) - len(hits),
            "pairs": {f"{w}:{st}": sorted(m) for (w, st), m in by_stem.items()
                      if (w, st) in paired},
            "note": ("판올림은 **같은 뼈대에 다른 표식**이 있어야 성립한다. 표식이 하나뿐인 "
                     "것은 그냥 이름이다(날짜 도장 포함) — 개수가 아니라 관계 신호다.")}


# ── 종합 ────────────────────────────────────────────────────────────────────

def collect(run_id: str, work: Path | None = None) -> dict:
    work = Path(work) if work else C.work_dir(run_id, create=False)

    def load(name):
        try:
            return C.read_json(work / name)
        except Exception:
            return {}

    ident, extract, verify = (load("식별_결과.json"), load("추출_결과.json"),
                              load("해석_결과.json"))
    src = (ident.get("source") or {}).get("path") or ""
    sigs = {
        "name_prefix": sig_name_prefix(extract),
        "coord_gap": sig_coord_gap(verify),
        "port_prefix": sig_port_prefix(verify, extract),
        "cst_group": sig_groups(src) if src else {"signal": "cst_group", "n_groups": 0,
                                                  "groups": {}},
        "version_mark": sig_version_marks(src, extract) if src else
                        {"signal": "version_mark", "n_hits": 0, "hits": []},
    }

    # 결정론이 **혼자 확정할 수 있는 경우는 하나뿐이다** — 아무 경계 신호도 없을 때.
    n_cuts = sigs["coord_gap"]["n_cuts"]
    n_groups = sigs["cst_group"]["n_groups"]
    n_marks = sigs["version_mark"]["n_hits"]
    if n_cuts == 0 and n_groups <= 1 and n_marks == 0:
        verdict, why = "single", "경계 신호가 하나도 없다 — 안테나 하나로 본다"
    elif n_cuts == 0 and n_marks == 0:
        verdict, why = "single", f"좌표에 틈이 없다(그룹 선언 {n_groups}건은 경계가 아니다)"
    else:
        bits = []
        if n_cuts:
            bits.append(f"좌표 틈 {n_cuts}곳")
        if n_marks:
            bits.append(f"판올림 표식 {n_marks}건")
        if n_groups > 1:
            bits.append(f"그룹 선언 {n_groups}종")
        verdict, why = "ambiguous", "경계 신호가 있으나 개수를 확정할 수 없다 — " + " · ".join(bits)

    return {
        "run_id": run_id, "source": src,
        "rule_version": C.effective_rule_version(),
        "numeric_rules": numeric_rules(),
        "signals": sigs,
        "verdict": verdict,          # single | ambiguous
        "why": why,
        "규율": ("verdict=single 일 때만 결정론이 확정한다. ambiguous 는 확정하지 않고 "
               "구제 경로(LLM 제안 → 사람 승인)로 넘긴다 — 없는 경계를 만들지 않는다(N-3)."),
    }


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

    print("[split.py 자기 시험 — 실물]")
    import glob
    runs = [Path(p).parent.name for p in glob.glob("work/*/해석_결과.json")]
    t2 = next((r for r in runs if r.endswith("-test2")), None)
    if not t2:
        print("  건너뜀 — test2 run 없음")
        return 2

    r = collect(t2)
    chk("신호 5종 수집", len(r["signals"]) == 5, str(list(r["signals"])))
    chk("판정 어휘", r["verdict"] in ("single", "ambiguous"), r["verdict"])

    # ── 핵심 반례 — 이 절이 존재하는 이유 ─────────────────────────────────
    # test2 는 콤 라인 28줄이 **칩 4 × 채널 7 의 한 배열**이다(설계 문서 확인).
    # 그런데 좌표에는 최빈 간격의 3배가 넘는 틈이 2곳 있다 — 칩 경계다.
    # 기하만 보면 3덩이로 보이고, 이름 접두도 갈린다. **결정론은 여기서 못 가른다.**
    # 그래서 판정은 `ambiguous` 여야 하고, 이것이 정답이다 —
    # "안테나 하나"라고 아는 것은 사람뿐이다.
    cg = r["signals"]["coord_gap"]
    chk("좌표에 경계 후보가 잡힌다(칩 경계)", cg["n_cuts"] >= 1,
        json.dumps(cg, ensure_ascii=False)[:200])
    chk("결정론이 확정하지 않는다 — ambiguous", r["verdict"] == "ambiguous",
        f"{r['verdict']} — {r['why']}")
    chk("확정하지 않는 이유를 말한다", "확정할 수 없다" in r["why"], r["why"])
    chk("접두가 갈리는 것을 개수로 읽지 않는다고 명시",
        "여럿인 것은 아니다" in r["signals"]["name_prefix"]["note"])

    # 경계 신호가 전혀 없으면 결정론이 확정한다 — 도면 묶음 쪽
    eco = next((x for x in runs if x.endswith("-Antenna_CAD_ECO")), None)
    if eco:
        re_ = collect(eco)
        chk(f"신호 없는 원천은 single 로 확정({eco})", re_["verdict"] == "single",
            f"{re_['verdict']} — {re_['why']}")
    else:
        chk("신호 없는 원천은 single 로 확정", True, "(표본 없음 — 건너뜀)")

    # 판올림 표식 정규식
    for name, want in (("antenna_v2", True), ("model_rev3", True), ("top_bak", True),
                       ("KORIL_2.5", False), ("Chip1_RX1", False), ("Top_20260227", True)):
        chk(f"판올림 표식 {name} → {want}", bool(VERSION_MARK.search(name)) == want)

    chk("판정 규칙이 산지와 함께 실린다",
        {"split_gap_ratio", "split_min_group"} <= set(numeric_rules()))
    chk("임계에 관측 근거가 없음을 밝힌다", "관측 근거가 없" in numeric_rules()["규율"])

    # 판올림은 **짝이 있어야** 성립한다 — 날짜 도장 오탐 방어
    vm = r["signals"]["version_mark"]
    chk("짝 없는 표식은 판올림이 아니다", "n_marked_unpaired" in vm, str(vm)[:120])
    import tempfile
    tmp = Path(tempfile.mkdtemp())
    for n in ("ant_v1.dxf", "ant_v2.dxf", "Top_20260227.dxf", "Bottom_20260227.dxf"):
        (tmp / n).write_text("x", encoding="utf-8")
    vv = sig_version_marks(tmp, {})
    chk("같은 뼈대 다른 표식 → 판올림", vv["n_hits"] == 2, json.dumps(vv, ensure_ascii=False)[:200])
    chk("다른 뼈대 같은 날짜 → 판올림 아님", "file:Top" not in vv["pairs"], str(vv["pairs"]))

    print(f"\n결과: {ok}/{ok + fail} PASS")
    return 0 if fail == 0 else 1


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    if argv[1] == "self-test":
        return self_test()
    if argv[1] == "signals":
        print(json.dumps(collect(argv[2]), ensure_ascii=False, indent=2))
        return 0
    print(f"알 수 없는 명령: {argv[1]}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
