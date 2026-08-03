#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/timeline.py — 작업 시각 추출 · 세션 군집 · 꼬리 제외 (LLM 0콜)

무엇을 하나
    원천에서 **작업이 실제로 일어난 시각**을 모아 시작·끝을 정한다.
    등록 순번과 무관하게 "언제 만든 안테나인가"를 읽을 수 있게 하는 것이 목적이다.

★ 파일 수정 시각(mtime)을 쓰지 않는다 — 실측으로 확인했다
    `handoff/04_experiment_data` 의 파일 mtime 은 **전부 2026-07-30**(복사한 날)이다.
    원천을 복사·압축 해제·동기화하면 mtime 이 통째로 뭉개진다. 그 위에 타임라인을 세우면
    "모든 안테나가 같은 날 만들어졌다"는 거짓을 말하게 된다.
    → mtime 은 **증거로 쓰지 않고**, 다른 근거가 하나도 없을 때만 `mtime_only` 로 표시한다.

근거의 등급 — **두 층**이다. 층을 먼저 고르고, 층 안에서 전부 쓴다
    ┌ 실측(측정된 것) ─ 하나라도 있으면 **이 층만** 쓴다. 층 안의 근거는 **함께** 쓴다
    │ solver_log   `*.log` 의 타임스탬프 — 실제 실행 활동. 여러 점이라 분포가 나온다
    │ dwg_header   DWG `AcDb:SummaryInfo` 의 저장 시각 — 헤더를 결정론으로 판독(dwgmeta.py)
    └ 선언(사람·도구가 적어 넣은 것) ─ 실측이 하나도 없을 때만. **강한 하나만** 쓴다
      filename_date `holes_drill_20260227.dwg` 처럼 이름에 박힌 날짜. 사람이 붙인 것이다
      declared      `ModelHistory.json general.date` — **작업을 언제 시작했나**의 선언.
                    확정 2026-07-31 사람: "언제 시작했는지와 작업 기간 표기로 넣자".
                    → 시작만 말한다. **끝은 말하지 않는다**(기간은 실측이 있어야 나온다).
                    ※ `20250522…` 는 이 값이 2024-12-16 이고 CST **빌드 날짜**와 같다.
                      실제 작업은 로그가 말하는 2025-05-21~22 다. 그래서 실측이 있으면
                      실측이 이긴다 — 이 어긋남은 나중에 다른 도구로 다시 본다.
    mtime_only   위가 전부 없을 때. **신뢰하지 않음**을 이름으로 밝힌다

    왜 solver_log 와 dwg_header 를 줄 세우지 않나
        둘은 **다른 파일에서 나오는 같은 등급의 실측**이다(시뮬 로그 · 도면 저장 시각).
        줄을 세우면 CST 로그가 있는 원천에서 도면 저장 시각이 통째로 사라진다.
        서로 다른 활동이므로 함께 두어야 "언제 시뮬하고 언제 도면을 냈나"가 남는다.

★ 이름 날짜와 헤더가 어긋나면 — 맞추지 않고 `conflicts[]` 에 **정보로만** 남긴다 (N-3)
    실물 `holes_drill_20260227.dwg` 의 헤더 저장 시각은 KST 로도 **2026-02-26** 이라
    이름과 하루 어긋난다. 표준시가 한국으로 확정되었으므로 시차로는 설명되지 않는다.
    그렇다고 경고로 올리지 않는다 — **이름 짓는 규칙은 사람마다 다르다**(사용자 확인
    2026-07-31: "사람에 따라 다르게 이름 지을 수 있어. 그래서 중요도가 낮아").
    이름 날짜는 근거가 아니라 **정황**이다. 어긋난 폭(`gap_days`)만 적고 판정하지 않는다.

작업 기간 표기
    `work_span_days` · `work_span_text` 를 함께 낸다. 실측 층에서만 나온다 —
    선언 1점에는 기간이 없다("시작만 안다"). 없는 기간을 0 으로 적지 않는다.

꼬리 제외 — 요구사항
    "마지막에 실수로 수정해서 되돌리기 · 추출/배포 때문에 잠깐 들어가기 · 어떻게 했는지
    보려고 들어간 것"이 타임라인 끝을 끌고 가는 것을 막는다.

    **지우지 않는다.** `excluded[]` 로 사유와 함께 남긴다 — 조용히 버리면 나중에
    "왜 이 날짜가 없지"를 답할 수 없다. 끝 날짜를 **둘** 낸다.
        work_end       꼬리를 뺀 실질 종료
        work_end_raw   마지막 접촉(무엇이든)

판정 규칙 (c) — 원장에 실린다. **관측 근거가 없다**(표본 1종)
    임계를 상수로 박는 대신 **분포에서 뽑는다** — split.py 의 간격 비율과 같은 방식이다.
      session_gap_hours   24    이만큼 비면 다른 작업 세션으로 본다
      outlier_share_max   0.05  전체의 이 비율 이하인 꼬리만 뺀다
      outlier_gap_ratio   3.0   직전 세션과의 간격이 세션 내부 중앙 간격의 이 배 이상
    셋 다 만족해야 뺀다. 하나라도 못 미치면 **빼지 않는다** — 실제 작업을 지우는 쪽이 더 나쁘다.

CLI
    python tools/timeline.py scan <원천경로>
    python tools/timeline.py of   <run_id>
    python tools/timeline.py self-test
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402
import dwgmeta  # noqa: E402

SESSION_GAP_HOURS = 24.0
OUTLIER_SHARE_MAX = 0.05
OUTLIER_GAP_RATIO = 3.0
MAX_BYTES = 8 << 20          # 파일당 읽는 상한. 넘으면 잘랐다고 **말한다**

MEASURED = ("solver_log", "dwg_header")      # 실측 층 — 함께 쓴다
DECLARED = ("filename_date", "declared")     # 선언 층 — 앞의 것이 강하다
# 이름 날짜 ↔ 헤더 대조는 **판정하지 않는다**. 이름 규칙이 사람마다 다르기 때문이다
# (사용자 확인 2026-07-31). 어긋난 폭만 적는다 — 큰 폭은 사람이 정렬해서 본다.
NAME_VS_HEADER_LEVEL = "정보"

_TS = re.compile(rb"(20\d{2})[-/.](\d{2})[-/.](\d{2})[T ](\d{2}):(\d{2}):(\d{2})")
_DATE = re.compile(rb"(20\d{2})[-/.](\d{2})[-/.](\d{2})")
_LOGLIKE = (".log", ".txt", ".out")


def numeric_rules() -> dict:
    return {"session_gap_hours": SESSION_GAP_HOURS,
            "outlier_share_max": OUTLIER_SHARE_MAX,
            "outlier_gap_ratio": OUTLIER_GAP_RATIO,
            "name_vs_header_level": NAME_VS_HEADER_LEVEL,
            "규율": ("셋을 **모두** 만족해야 꼬리를 뺀다. 뺀 것은 지우지 않고 excluded 로 "
                   "남긴다. 관측 근거가 없는 임계이므로 보수적으로 — 실제 작업을 지우는 쪽이 "
                   "잘못된 날짜를 남기는 것보다 나쁘다.")}


# ── 수집 ────────────────────────────────────────────────────────────────────

def _parse(m) -> datetime | None:
    try:
        g = [int(x) for x in m]
        return datetime(*g) if len(g) == 6 else datetime(g[0], g[1], g[2])
    except Exception:
        return None


def collect_events(source_path) -> dict:
    root = Path(source_path)
    if not root.exists():
        raise FileNotFoundError(f"원천 없음: {root}")
    events, truncated, conflicts = [], [], []

    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue

        # ── DWG — 헤더를 결정론으로 판독한다(전수 스캔 아님). tools/dwgmeta.py
        if p.suffix.lower() == ".dwg":
            events += _dwg_events(p, root, conflicts)
            continue

        try:
            b = p.read_bytes()
        except Exception:
            continue
        if len(b) > MAX_BYTES:
            b, _ = b[:MAX_BYTES], truncated.append(str(p.relative_to(root)))
        rel = str(p.relative_to(root))

        if p.suffix.lower() in _LOGLIKE:
            for m in _TS.findall(b):
                t = _parse(m)
                if t:
                    events.append({"ts": t.isoformat(sep=" "), "evidence": "solver_log",
                                   "file": rel})
        if p.name == "ModelHistory.json":
            try:
                gen = (json.loads(b.decode("utf-8", "ignore")).get("general") or {})
            except Exception:
                gen = {}
            d = gen.get("date")
            if d:
                m = _DATE.search(str(d).encode())
                t = _parse(m.groups()) if m else None
                if t:
                    events.append({
                        "ts": t.isoformat(sep=" "), "evidence": "declared", "file": rel,
                        "note": ("ModelHistory general.date — **작업 시작 선언**이다"
                                 "(확정 2026-07-31 사람). 끝은 말하지 않는다"),
                        "cst_build": gen.get("created")})

    # 원천 폴더 이름의 날짜도 선언이다 — `20250522_single_chip_full_pcb`
    for fd in dwgmeta.filename_dates(root):
        events.append({"ts": fd["ts"], "evidence": "filename_date", "file": root.name,
                       "text": fd["text"],
                       "note": "원천 폴더 이름의 날짜 — 사람이 붙인 선언이다"})

    by_ev = {}
    for e in events:
        by_ev.setdefault(e["evidence"], []).append(e)
    return {"source": str(root), "n_events": len(events), "events": events,
            "by_evidence": {k: len(v) for k, v in by_ev.items()},
            "truncated_files": truncated, "conflicts": conflicts}


def _dwg_events(p: Path, root: Path, conflicts: list) -> list[dict]:
    """DWG 한 장 → 실측(dwg_header) + 선언(filename_date). 어긋나면 conflicts 에 남긴다."""
    rel = str(p.relative_to(root))
    out = []
    try:
        d = dwgmeta.dwg_dates(p)
    except Exception as e:
        return [{"ts": None, "evidence": "unreadable", "file": rel, "note": str(e)[:80]}][:0]

    hdr = None
    if d["readable"] == "ok" and d.get("modified"):
        hdr = datetime.fromisoformat(d["modified"]).replace(microsecond=0)
        out.append({"ts": hdr.isoformat(sep=" "), "evidence": "dwg_header", "file": rel,
                    "kind": "modified", "last_saved_by": d.get("last_saved_by"),
                    "tz": d["tz"], "tz_twin": d.get("tz_twin"), "산지": d["산지"]})
        if d.get("created"):
            # ★ 생성 시각은 타임라인을 끌지 않는다 — 등급 이름으로 그렇게 못 박는다.
            #   DWG 의 생성 시각은 **바탕 도면(템플릿)에서 물려받는 일이 흔하다.**
            #   실물 `antenna reflector.dwg` 는 생성 2020-12-16 · 수정 2026-02-26 으로
            #   5년이 벌어진다. 이것을 작업 시작으로 쓰면 "5년 걸린 안테나"가 된다.
            #   빼는 규칙(꼬리 제외)을 앞쪽에 새로 만드는 대신 **층 밖에 둔다** —
            #   관측 근거 없는 임계를 또 만들지 않기 위해서다. 값은 지우지 않고 남긴다.
            out.append({"ts": datetime.fromisoformat(d["created"]).replace(microsecond=0)
                        .isoformat(sep=" "), "evidence": "dwg_created", "file": rel,
                        "kind": "created", "산지": d["산지"],
                        "note": ("생성 시각 — 템플릿에서 물려받았을 수 있어 타임라인에 "
                                 "쓰지 않는다. 뜻 확정: 사람")})

    for fd in dwgmeta.filename_dates(p):
        out.append({"ts": fd["ts"], "evidence": "filename_date", "file": rel,
                    "text": fd["text"],
                    "note": ("이름에 박힌 날짜 — 사람이 붙인 선언이다. 저장 시각이 아니고, "
                             "짓는 규칙도 사람마다 다르다")})
        if hdr is not None:
            gap = abs((datetime.fromisoformat(fd["ts"]).date() - hdr.date()).days)
            if gap:
                conflicts.append({
                    "file": rel, "filename_date": fd["ts"], "dwg_header": hdr.isoformat(sep=" "),
                    "gap_days": gap, "level": NAME_VS_HEADER_LEVEL,
                    "why": ("이름 날짜와 헤더 저장 시각이 어긋난다. 표준시(KST)로도 설명되지 "
                            "않지만 **경고로 올리지 않는다** — 이름 짓는 규칙은 사람마다 "
                            "다르다. 이름 날짜는 근거가 아니라 정황이다")})
    return out


# ── 세션 군집 · 꼬리 제외 ───────────────────────────────────────────────────

def sessions(events: list[dict]) -> list[dict]:
    """시각을 세션으로 묶는다. `session_gap_hours` 이상 비면 다른 세션."""
    ts = sorted({e["ts"] for e in events})
    if not ts:
        return []
    out, cur = [], [ts[0]]
    for a, b in zip(ts, ts[1:]):
        gap = (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 3600
        if gap >= SESSION_GAP_HOURS:
            out.append(cur)
            cur = [b]
        else:
            cur.append(b)
    out.append(cur)
    return [{"start": s[0], "end": s[-1], "n": len(s),
             "span_hours": round((datetime.fromisoformat(s[-1])
                                  - datetime.fromisoformat(s[0])).total_seconds() / 3600, 2)}
            for s in out]


def _span_text(span, declared_only) -> str:
    """작업 기간 표기. 없는 기간을 0 으로 적지 않는다."""
    if declared_only:
        return "기간 모름 — 선언은 시작만 말한다"
    if span is None:
        return "기간 없음"
    h = span * 24
    return f"{h:.1f}시간" if h < 48 else f"{span:.1f}일"


def _median(xs):
    xs = sorted(xs)
    n = len(xs)
    return None if not n else (xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2)


def build(source_path) -> dict:
    """작업 시작·끝. 꼬리를 빼되 **지우지 않는다.**"""
    col = collect_events(source_path)
    evs = col["events"]

    # 근거 층 — 실측이 하나라도 있으면 실측만, 그 안의 근거는 **함께** 쓴다.
    # (선언 1점이 로그 63점을 끌고 가면 안 된다. 반대로 로그가 있다고 도면 저장 시각을
    #  버리면 "언제 도면을 냈나"가 사라진다 — 그래서 층 안에서는 줄을 세우지 않는다)
    have = col["by_evidence"]
    grades = [g for g in MEASURED if have.get(g)]
    if not grades:
        grades = next(([g] for g in DECLARED if have.get(g)), [])
    grade = "+".join(grades) if grades else None
    used = [e for e in evs if e["evidence"] in grades]

    if not used:
        return {"source": col["source"], "evidence": "mtime_only", "n_events": 0,
                "work_start": None, "work_end": None, "work_end_raw": None,
                "sessions": [], "excluded": [], "declared": None,
                "work_span_days": None, "work_span_text": "기간 없음",
                "conflicts": col["conflicts"], "by_evidence": col["by_evidence"],
                "why": ("원천 안에서 시각 근거를 찾지 못했다. 파일 mtime 은 복사로 뭉개지므로 "
                        "쓰지 않는다 — 날짜 없음이 정답이다"),
                "numeric_rules": numeric_rules()}

    ss = sessions(used)
    excluded = []
    kept = list(ss)
    # 선언 층은 **시작만** 말한다 — 끝과 기간을 지어내지 않는다(확정 2026-07-31 사람)
    declared_only = not any(g in MEASURED for g in grades)

    # 꼬리 제외 — 세 조건을 **모두** 만족할 때만
    if len(kept) >= 2:
        total = sum(s["n"] for s in kept)
        inner = [s["span_hours"] / max(s["n"] - 1, 1) for s in kept if s["n"] > 1]
        base = _median(inner) or 1.0
        while len(kept) >= 2:
            tail = kept[-1]
            gap = (datetime.fromisoformat(tail["start"])
                   - datetime.fromisoformat(kept[-2]["end"])).total_seconds() / 3600
            share = tail["n"] / total
            if share <= OUTLIER_SHARE_MAX and gap >= OUTLIER_GAP_RATIO * base:
                excluded.append({**tail, "gap_hours": round(gap, 2),
                                 "share": round(share, 4),
                                 "why": ("주 작업 분포에서 떨어진 꼬리다 — 되돌리기·추출/배포·"
                                         "열람 같은 접촉일 수 있다. **지우지 않고 남긴다**")})
                kept = kept[:-1]
            else:
                break

    w_start = kept[0]["start"] if kept else None
    w_end = None if declared_only else (kept[-1]["end"] if kept else None)
    span = (round((datetime.fromisoformat(w_end)
                   - datetime.fromisoformat(w_start)).total_seconds() / 86400, 3)
            if w_start and w_end else None)
    return {
        "source": col["source"], "evidence": grade,
        "n_events": len(used), "by_evidence": col["by_evidence"],
        "work_start": w_start,
        "work_end": w_end,
        "work_end_raw": None if declared_only else (ss[-1]["end"] if ss else None),
        "work_span_days": span,
        "work_span_text": _span_text(span, declared_only),
        "n_sessions": len(kept), "sessions": kept, "excluded": excluded,
        "conflicts": col["conflicts"],
        "dwg_created": [{"file": e["file"], "ts": e["ts"]}
                        for e in evs if e["evidence"] == "dwg_created"],
        "declared": next((e["ts"] for e in evs if e["evidence"] == "declared"), None),
        "declared_note": next((e.get("note") for e in evs if e["evidence"] == "declared"), None),
        "single_point": len(used) == 1,
        "declared_only": declared_only,
        "why": ("선언은 **시작만** 말한다 — 끝과 기간은 비운다(실측이 있어야 나온다)"
                if declared_only else
                "근거가 한 점뿐이라 분포가 없다 — 꼬리 판정을 하지 않는다"
                if len(used) == 1 else
                f"{grade} 근거 {len(used)}점 · 세션 {len(ss)}개 · 꼬리 제외 {len(excluded)}개"),
        "numeric_rules": numeric_rules(),
    }


def of_run(run_id: str, work: Path | None = None) -> dict:
    work = Path(work) if work else C.work_dir(run_id, create=False)
    ident = C.read_json(work / "식별_결과.json")
    src = (ident.get("source") or {}).get("path")
    if not src:
        raise ValueError(f"{run_id} 의 원천 경로를 모른다")
    return {**build(src), "run_id": run_id}


# ── 자기 시험 ────────────────────────────────────────────────────────────────

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

    print("[timeline.py 자기 시험 — 실물]")
    base = C.data_dir() / "handoff" / "04_experiment_data" / "cst_projects"
    pcb, t2 = base / "20250522_single_chip_full_pcb", base / "test2"
    if not pcb.exists():
        print("  건너뜀 — 실물 없음")
        return 2

    r = build(pcb)
    chk("로그를 근거로 쓴다", r["evidence"] == "solver_log", str(r["evidence"]))
    chk(f"시각 {r['n_events']}점 수집", r["n_events"] >= 50, str(r["n_events"]))
    chk("작업 시작 2025-05-21", str(r["work_start"]).startswith("2025-05-21"),
        str(r["work_start"]))
    chk("작업 끝 2025-05-22", str(r["work_end"]).startswith("2025-05-22"), str(r["work_end"]))
    chk("작업 기간 표기", abs(r["work_span_days"] - 0.687) < 0.01 and
        "시간" in r["work_span_text"], f"{r['work_span_days']} {r['work_span_text']}")
    # 원천 이름이 20250522 다 — 이름과 로그가 서로를 확인해 준다
    chk("이름(20250522)과 로그 날짜가 맞는다", "20250522" in str(pcb) and
        str(r["work_end"]).startswith("2025-05-22"))

    # 선언 날짜를 작업 날짜로 단정하지 않는다 — 실물이 반례다
    chk("선언 날짜(2024-12-16)를 함께 남긴다", str(r["declared"]).startswith("2024-12-16"),
        str(r["declared"]))
    chk("선언 날짜의 뜻이 확정되어 실린다(작업 시작)",
        "작업 시작 선언" in (r["declared_note"] or "") and
        "끝은 말하지 않는다" in (r["declared_note"] or ""), str(r["declared_note"]))
    chk("선언이 로그를 밀어내지 않는다",
        r["evidence"] == "solver_log" and not str(r["work_start"]).startswith("2024"),
        f"{r['evidence']} {r['work_start']}")

    # 한 점뿐이면 꼬리 판정을 하지 않는다
    if t2.exists():
        r2 = build(t2)
        chk("근거 1점 — 선언으로 떨어진다", r2["evidence"] == "declared", str(r2["evidence"]))
        chk("한 점이면 꼬리 판정 안 함", r2["single_point"] and not r2["excluded"],
            str(r2["excluded"]))
        chk("선언은 시작만 — 끝을 비운다", r2["work_start"] and r2["work_end"] is None,
            f"{r2['work_start']}~{r2['work_end']}")
        chk("없는 기간을 0 으로 적지 않는다",
            r2["work_span_days"] is None and "모름" in r2["work_span_text"],
            str(r2["work_span_text"]))

    # ── DWG 헤더 근거 (실물 Antenna_CAD_ECO) ────────────────────────────────
    eco = C.data_dir() / "handoff" / "04_experiment_data" / "Antenna_CAD_ECO"
    if eco.exists():
        r4 = build(eco)
        chk("도면만 있는 원천도 시각을 얻는다", r4["evidence"] == "dwg_header",
            str(r4["evidence"]))
        chk("헤더 저장 시각 KST 2026-02-26 16:56",
            str(r4["work_end"]).startswith("2026-02-26 16:56"), str(r4["work_end"]))
        chk("도면 작업 기간 표기", "시간" in r4["work_span_text"], str(r4["work_span_text"]))
        chk("생성 시각은 타임라인을 끌지 않는다",
            not str(r4["work_start"]).startswith("2020") and
            any(x["ts"].startswith("2020-12-16") for x in r4["dwg_created"]),
            f"{r4['work_start']} {r4['dwg_created']}")
        chk("실측이 있으면 이름 날짜를 쓰지 않는다", "filename_date" not in r4["evidence"],
            str(r4["evidence"]))
        chk("이름 날짜도 수집은 해 둔다", r4["by_evidence"].get("filename_date") == 1,
            str(r4["by_evidence"]))
        cf = r4["conflicts"]
        chk("이름과 헤더의 어긋남을 남긴다", len(cf) == 1 and cf[0]["gap_days"] == 1, str(cf))
        chk("이름 어긋남은 경고가 아니다(이름 규칙은 사람마다 다르다)",
            cf and cf[0]["level"] == "정보" and "사람마다" in cf[0]["why"], str(cf))
        chk("전수 스캔의 가짜(2031·2034)가 타임라인에 없다",
            not any("2031" in e["ts"] or "2034" in e["ts"] for e in
                    collect_events(eco)["events"]))

    # 층 규칙 — 실측이 있으면 선언을 쓰지 않는다(실물 pcb 는 이름에 20250522 가 있다)
    chk("이름 날짜(20250522)를 수집은 한다",
        collect_events(pcb)["by_evidence"].get("filename_date") == 1,
        str(collect_events(pcb)["by_evidence"]))
    chk("그래도 로그가 타임라인을 정한다", r["evidence"] == "solver_log", str(r["evidence"]))

    # 꼬리 제외 — 합성으로 규칙만 확인(실물에 꼬리가 없다)
    def ev(*xs):
        return [{"ts": x, "evidence": "solver_log", "file": "x.log"} for x in xs]
    main = ["2025-05-21 09:00", "2025-05-21 10:00", "2025-05-21 11:00",
            "2025-05-21 12:00", "2025-05-21 13:00", "2025-05-21 14:00",
            "2025-05-21 15:00", "2025-05-21 16:00", "2025-05-21 17:00",
            "2025-05-21 18:00", "2025-05-21 19:00", "2025-05-21 20:00",
            "2025-05-21 21:00", "2025-05-21 22:00", "2025-05-21 23:00",
            "2025-05-22 09:00", "2025-05-22 10:00", "2025-05-22 11:00",
            "2025-05-22 12:00", "2025-05-22 13:00"]
    import types
    m = types.SimpleNamespace()
    m.ss = sessions(ev(*main, "2026-03-01 15:00"))
    # 05-21 23:00 → 05-22 09:00 은 10h 라 같은 세션이다(24h 미만). 2026-03-01 만 갈린다.
    chk("하루 안쪽 간격은 한 세션", len(m.ss) == 2, str([(x["start"], x["end"]) for x in m.ss]))
    chk("멀리 떨어진 1점이 꼬리 세션", m.ss[-1]["n"] == 1 and
        m.ss[-1]["start"].startswith("2026-03-01"), str(m.ss[-1]))

    # build 를 합성 이벤트로 돌리기 위한 최소 우회 — 세션 로직만 본다
    kept, total = list(m.ss), sum(s["n"] for s in m.ss)
    tail = kept[-1]
    share = tail["n"] / total
    gap = (datetime.fromisoformat(tail["start"])
           - datetime.fromisoformat(kept[-2]["end"])).total_seconds() / 3600
    chk("꼬리 비중이 상한 이하", share <= OUTLIER_SHARE_MAX, f"{share:.3f}")
    chk("꼬리 간격이 비율 이상", gap >= OUTLIER_GAP_RATIO, f"{gap:.1f}h")

    # 비중이 크면 빼지 않는다 — 실제 작업을 지우는 쪽이 더 나쁘다
    ss2 = sessions(ev("2025-05-21 09:00", "2026-03-01 15:00", "2026-03-01 16:00"))
    big = ss2[-1]["n"] / sum(s["n"] for s in ss2)
    chk("비중 큰 꼬리는 빼지 않는다(규칙상)", big > OUTLIER_SHARE_MAX, f"{big:.3f}")

    chk("판정 규칙 셋이 산지와 함께 실린다",
        {"session_gap_hours", "outlier_share_max", "outlier_gap_ratio"} <= set(numeric_rules()))
    chk("모두 만족해야 뺀다고 명시", "모두" in numeric_rules()["규율"])

    # mtime 을 근거로 쓰지 않는다
    import tempfile
    empty = Path(tempfile.mkdtemp())
    (empty / "a.bin").write_bytes(b"\x00\x01")
    r3 = build(empty)
    chk("시각 근거 없으면 날짜 없음", r3["evidence"] == "mtime_only" and r3["work_start"] is None)
    chk("mtime 을 쓰지 않는 이유를 밝힌다", "복사로 뭉개" in r3["why"])

    print(f"\n결과: {ok}/{ok + fail} PASS")
    return 0 if fail == 0 else 1


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    if argv[1] == "self-test":
        return self_test()
    r = build(argv[2]) if argv[1] == "scan" else of_run(argv[2])
    print(f"{r['source']}")
    print(f"  근거 {r['evidence']} · 시각 {r['n_events']}점 · {r['why']}")
    print(f"  작업 {r['work_start']} ~ {r['work_end'] or '?'}   [{r.get('work_span_text')}]"
          + (f"   (마지막 접촉 {r['work_end_raw']})"
             if r["work_end_raw"] and r["work_end_raw"] != r["work_end"] else ""))
    for s in r["sessions"]:
        print(f"    세션 {s['start']} ~ {s['end']}  ({s['n']}점 · {s['span_hours']}h)")
    for e in r["excluded"]:
        print(f"    [제외] {e['start']} ~ {e['end']}  {e['n']}점 · 간격 {e['gap_hours']}h")
        print(f"           {e['why']}")
    for c in r.get("dwg_created") or []:
        print(f"    [생성] {c['ts']}  {c['file']}  — 템플릿 상속 가능. 타임라인에 쓰지 않음")
    for c in r.get("conflicts") or []:
        print(f"    [{c.get('level', '주의')}] {c['file']}  이름 {c['filename_date'][:10]}"
              f" ↔ 헤더 {c['dwg_header'][:10]}  ({c['gap_days']}일)")
        print(f"           {c['why']}")
    if r.get("declared"):
        print(f"  선언 날짜 {r['declared']}  — {r.get('declared_note', '')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
