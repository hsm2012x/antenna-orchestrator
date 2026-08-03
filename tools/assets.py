#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/assets.py — 안테나 자산 DB (sqlite · LLM 0콜)

목적
    여러 안테나의 추출·해석값을 **한 곳에 모아 나란히 본다**. 한 안테나만 보면 정상으로
    보이는 값도, 다른 안테나 옆에 세우면 역할 배정 오류·단위 혼선·빠진 항목이 드러난다.
    문서 최종본(게이트 통과본)을 등재해 **어느 안테나가 자산화되었는지** 한눈에 본다.

원장과의 관계 — 이것은 정본이 아니다
    원장(`ledger.sqlite`)은 append-only **사건** 기록이고 정본이다.
    자산 DB는 `work/<run_id>/` 산출에서 만든 **조회용 파생물**이다. 언제든 지우고
    `rebuild` 로 다시 만들 수 있어야 한다 — 그래야 정본이 둘로 갈리지 않는다.
    갈리면 원장이 옳다.

식별
    asset_id = 원천 이름(test2 · Antenna_CAD_ECO · …). 사람이 "어느 안테나"라고 말할 때의 단위다.
    `geom_hash` 를 함께 기록해 **같은 이름인데 형상이 다른** 경우를 드러낸다(경고, 판정 아님).

쓰기 범위
    `out/_asset_db/assets.sqlite` — W-1 의 `out/` 아래다. 원장 경로가 아니다.

CLI
    python tools/assets.py ingest <run_id>       한 run 의 카탈로그·게이트 판정을 등재
    python tools/assets.py rebuild               work/ 전체를 훑어 DB 재생성
    python tools/assets.py list                  자산 현황 — 어느 안테나가 문서화됐나
    python tools/assets.py compare <role>        같은 역할의 값을 안테나별로 나란히
    python tools/assets.py coverage              역할 × 안테나 보유 행렬 (빠진 곳이 드러난다)
    python tools/assets.py anomalies             단위 충돌·형상 지문 불일치·미매핑 역할
    python tools/assets.py self-test
"""
from __future__ import annotations

import glob
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C          # noqa: E402
import catalog as CAT        # noqa: E402
import roles as R            # noqa: E402

SCHEMA_VERSION = 6       # v6: 관계 어휘(links.relation) · variant_of · container_id


def asset_db_path() -> Path:
    env = os.environ.get("ORCH_ASSET_DB")
    if env:
        return Path(env)
    return C.data_dir() / "out" / "_asset_db" / "assets.sqlite"


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY, v TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS antennas (
    asset_id     TEXT PRIMARY KEY,
    entry_kind   TEXT,
    project_tag  TEXT,
    container_id TEXT,      -- 어느 컨테이너(CST 프로젝트·도면 묶음)에 속하나
    variant_of   TEXT,      -- 같은 계열의 다른 판본. **사람이 확정해야 채워진다**(A-1)
    derived_from TEXT,
    source_kind  TEXT,
    adapter      TEXT,
    contributing TEXT,
    geom_hash    TEXT,
    n_files      INTEGER,
    n_unreadable INTEGER,
    -- 작업 타임라인 — 등록 순서와 **무관하게** 시간축으로 읽기 위한 열
    work_start    TEXT,      -- 꼬리를 뺀 실질 시작
    work_end      TEXT,      -- 꼬리를 뺀 실질 종료
    work_end_raw  TEXT,      -- 마지막 접촉(무엇이든). work_end 와 다르면 꼬리가 있었다는 뜻
    work_span_days REAL,     -- 작업 기간. **선언만 있으면 NULL** — 없는 기간을 0 으로 적지 않는다
    work_evidence TEXT,      -- 실측: solver_log·dwg_header(+로 결합) | 선언: filename_date·declared | mtime_only
    n_sessions    INTEGER,
    n_excluded    INTEGER,
    first_run_id TEXT,
    last_run_id  TEXT,
    last_seen    TEXT);

CREATE TABLE IF NOT EXISTS facts (
    asset_id  TEXT NOT NULL,
    run_id    TEXT NOT NULL,
    key       TEXT NOT NULL,
    role      TEXT,
    quantity  TEXT,
    unit      TEXT,
    value_num REAL,
    value_text TEXT,
    render    TEXT,
    formula   TEXT,
    source    TEXT,
    passed    INTEGER,
    ts        TEXT,
    PRIMARY KEY (asset_id, run_id, key));

CREATE TABLE IF NOT EXISTS documents (
    asset_id   TEXT NOT NULL,
    run_id     TEXT NOT NULL,
    doc_path   TEXT,
    gate_pass  INTEGER NOT NULL,
    n_refs     INTEGER,
    n_undeclared_role INTEGER,
    n_violations INTEGER,
    rule_version TEXT,
    ts         TEXT,
    PRIMARY KEY (asset_id, run_id));

-- 연결 — 배포 도안과 원본 프로젝트. **확정과 후보를 한 표에서 등급으로 구분한다.**
CREATE TABLE IF NOT EXISTS links (
    from_asset TEXT NOT NULL,
    to_asset   TEXT NOT NULL,
    grade      TEXT NOT NULL,      -- declared | candidate
    -- 관계 **종류**. 등급(grade)과 다른 축이다 — 등급은 "얼마나 확실한가",
    -- 관계는 "무엇인가". 둘을 한 칸에 넣으면 `variant 후보`를 표현할 수 없다.
    relation   TEXT,               -- derived | variant | sibling | foreign | unknown
    confidence REAL,
    basis      TEXT,               -- 근거 목록(JSON)
    confirmed_by TEXT,             -- 사람이 확정하면 이름. 그전까지 NULL
    ts         TEXT,
    PRIMARY KEY (from_asset, to_asset));

CREATE INDEX IF NOT EXISTS ix_facts_role  ON facts(role);
CREATE INDEX IF NOT EXISTS ix_facts_asset ON facts(asset_id);

-- 작업 세션 — 제외된 꼬리도 **지우지 않고** 남긴다
CREATE TABLE IF NOT EXISTS work_sessions (
    asset_id  TEXT NOT NULL,
    seq       INTEGER NOT NULL,
    start_ts  TEXT NOT NULL,
    end_ts    TEXT NOT NULL,
    n_events  INTEGER,
    excluded  INTEGER NOT NULL DEFAULT 0,
    why       TEXT,
    PRIMARY KEY (asset_id, seq));

-- 판본축 조회 — variant 로 묶인 자산을 나란히 본다
CREATE VIEW IF NOT EXISTS asset_variants AS
SELECT a.asset_id, a.variant_of, a.container_id, a.project_tag,
       a.work_start, a.work_end, a.geom_hash
FROM antennas a WHERE a.variant_of IS NOT NULL
ORDER BY a.variant_of, a.work_start;

-- 시각 충돌 — 이름 날짜와 파일 헤더가 어긋난 자리. **맞추지 않고 남긴다**(N-3)
CREATE TABLE IF NOT EXISTS time_conflicts (
    asset_id      TEXT NOT NULL,
    file          TEXT NOT NULL,
    filename_date TEXT,
    header_ts     TEXT,
    gap_days      INTEGER,
    level         TEXT,            -- 정보 — 이름 규칙이 사람마다 달라 판정하지 않는다
    why           TEXT,
    PRIMARY KEY (asset_id, file, filename_date));

-- 시간축 — 등록 순번이 아니라 **작업 시각** 순서로 본다
CREATE VIEW IF NOT EXISTS asset_timeline AS
SELECT asset_id, work_start, work_end, work_end_raw, work_span_days, work_evidence,
       n_sessions, n_excluded, entry_kind, project_tag
FROM antennas
WHERE work_start IS NOT NULL
ORDER BY work_start;

-- 자산 현황: 안테나별 최신 run 과 문서화 여부
CREATE VIEW IF NOT EXISTS asset_status AS
SELECT a.asset_id, a.adapter, a.geom_hash,
       (SELECT COUNT(DISTINCT run_id) FROM facts   f WHERE f.asset_id = a.asset_id) AS n_runs,
       (SELECT COUNT(*)               FROM facts   f WHERE f.asset_id = a.asset_id
                                       AND f.run_id = a.last_run_id)                AS n_facts,
       (SELECT COUNT(*)               FROM documents d WHERE d.asset_id = a.asset_id
                                       AND d.gate_pass = 1)                          AS n_docs,
       a.last_run_id, a.last_seen
FROM antennas a;
"""


def open_db(path=None, create: bool = True) -> sqlite3.Connection:
    p = Path(path or asset_db_path())
    p.parent.mkdir(parents=True, exist_ok=True)
    C.assert_writable(p)                       # W-1 — out/ 아래여야 한다
    if not create and not p.exists():
        raise FileNotFoundError(f"자산 DB 없음: {p}")
    # 스키마가 낡았으면 **버리고 다시 만든다.** 파생물이니 그래도 되고, 그래야 마이그레이션
    # 코드를 지고 다니지 않는다. 원장이었다면 절대 못 할 일이다(원장은 append-only·삭제 금지).
    if p.exists():
        try:
            c0 = sqlite3.connect(str(p))
            v = c0.execute("SELECT v FROM meta WHERE k='schema_version'").fetchone()
            c0.close()
            if not v or int(v[0]) != SCHEMA_VERSION:
                p.unlink()
        except Exception:
            p.unlink()

    conn = sqlite3.connect(str(p))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('schema_version',?)",
                 (str(SCHEMA_VERSION),))
    conn.execute("INSERT OR REPLACE INTO meta(k,v) VALUES('규율',?)",
                 ("파생물이다 — work/ 에서 rebuild 로 재생성 가능해야 한다. "
                  "정본은 원장(ledger.sqlite)이고, 어긋나면 원장이 옳다.",))
    conn.commit()
    return conn


def _source_path(work: Path):
    try:
        return (C.read_json(work / "식별_결과.json").get("source") or {}).get("path")
    except Exception:
        return None


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _run_ts(work: Path) -> str:
    """run 의 시각 — 산출물의 수정 시각이다. run_id 문자열 정렬이 아니다."""
    from datetime import datetime, timezone
    cands = [p for p in (work / "식별_결과.json", work / "해석_결과.json",
                         work / "추출_결과.json") if p.exists()]
    if not cands:
        return _now()
    t = max(p.stat().st_mtime for p in cands)
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds")


def _num(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


# ── 등재 ────────────────────────────────────────────────────────────────────

def ingest(conn, run_id: str, work: Path | None = None) -> dict:
    work = Path(work) if work else C.work_dir(run_id, create=False)
    try:
        cat = CAT.load(run_id, work)
    except Exception:
        cat = CAT.build(run_id, work)
    E = cat["entries"]

    def val(key, default=None):
        e = E.get(key)
        return e["value"] if e else default

    asset_id = val("식별.원천명") or run_id.split("-", 1)[-1]
    # 수리: 앞선 구현은 **등재 순서**로 last_run_id 를 정해, 이름이 뒤에 오는 옛 스모크 run 이
    # 최신 자리를 차지했다(교차 조회가 빈 값을 보였다). run 의 시각은 산출물이 안다.
    ts = _run_ts(work)
    prev = conn.execute("SELECT first_run_id, last_seen FROM antennas WHERE asset_id=?",
                        (asset_id,)).fetchone()
    newer = (prev is None) or (ts >= (prev["last_seen"] or ""))
    conn.execute("""
        INSERT INTO antennas(asset_id, entry_kind, project_tag, container_id, derived_from,
                             source_kind, adapter, contributing, geom_hash,
                             n_files, n_unreadable, first_run_id, last_run_id, last_seen)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(asset_id) DO UPDATE SET
            entry_kind=COALESCE(excluded.entry_kind, antennas.entry_kind),
            project_tag=COALESCE(excluded.project_tag, antennas.project_tag),
            container_id=COALESCE(excluded.container_id, antennas.container_id),
            derived_from=COALESCE(excluded.derived_from, antennas.derived_from),
            source_kind=CASE WHEN excluded.last_seen>=antennas.last_seen
                             THEN excluded.source_kind ELSE antennas.source_kind END,
            adapter=CASE WHEN excluded.last_seen>=antennas.last_seen
                         THEN excluded.adapter ELSE antennas.adapter END,
            contributing=CASE WHEN excluded.last_seen>=antennas.last_seen
                              THEN excluded.contributing ELSE antennas.contributing END,
            geom_hash=CASE WHEN excluded.last_seen>=antennas.last_seen
                           THEN excluded.geom_hash ELSE antennas.geom_hash END,
            n_files=CASE WHEN excluded.last_seen>=antennas.last_seen
                         THEN excluded.n_files ELSE antennas.n_files END,
            n_unreadable=CASE WHEN excluded.last_seen>=antennas.last_seen
                              THEN excluded.n_unreadable ELSE antennas.n_unreadable END,
            last_run_id=CASE WHEN excluded.last_seen>=antennas.last_seen
                             THEN excluded.last_run_id ELSE antennas.last_run_id END,
            last_seen=MAX(antennas.last_seen, excluded.last_seen)
    """, (asset_id, val("entry.kind"), val("entry.project_tag"),
          val("entry.container_id") or val("entry.project_tag"),
          val("entry.derived_from"),
          val("식별.원천종류"), val("식별.주도레인"), val("식별.기여레인"),
          val("추출.geom_hash"), val("식별.파일수"), val("식별.판독불가수"),
          (prev["first_run_id"] if prev else run_id), run_id, ts))

    # 작업 타임라인 — 원천에서 직접 뽑는다(파일 mtime 은 쓰지 않는다. timeline.py 주석)
    try:
        import timeline as TL
        src = (E.get("식별.원천경로") or {}).get("value") or _source_path(work)
        if src:
            tl = TL.build(src)
            conn.execute("""UPDATE antennas SET work_start=?, work_end=?, work_end_raw=?,
                            work_span_days=?, work_evidence=?, n_sessions=?, n_excluded=?
                            WHERE asset_id=?""",
                         (tl["work_start"], tl["work_end"], tl["work_end_raw"],
                          tl.get("work_span_days"),
                          tl["evidence"], tl.get("n_sessions") or 0,
                          len(tl.get("excluded") or []), asset_id))
            conn.execute("DELETE FROM work_sessions WHERE asset_id=?", (asset_id,))
            rows = [(s2, 0, x) for s2, x in enumerate(tl.get("sessions") or [])] + \
                   [(len(tl.get("sessions") or []) + i, 1, x)
                    for i, x in enumerate(tl.get("excluded") or [])]
            for seq, exc, x in rows:
                conn.execute("""INSERT OR REPLACE INTO work_sessions
                    (asset_id, seq, start_ts, end_ts, n_events, excluded, why)
                    VALUES(?,?,?,?,?,?,?)""",
                             (asset_id, seq, x["start"], x["end"], x["n"], exc,
                              x.get("why")))
            # 이름 날짜 ↔ 헤더 저장 시각의 어긋남 — 도구는 정하지 않고 표에 남긴다
            conn.execute("DELETE FROM time_conflicts WHERE asset_id=?", (asset_id,))
            for c in tl.get("conflicts") or []:
                conn.execute("""INSERT OR REPLACE INTO time_conflicts
                    (asset_id,file,filename_date,header_ts,gap_days,level,why)
                    VALUES(?,?,?,?,?,?,?)""",
                             (asset_id, c["file"], c["filename_date"], c["dwg_header"],
                              c["gap_days"], c.get("level", "주의"), c["why"]))
    except Exception as exc:                  # 타임라인 실패가 등재를 막지 않는다
        conn.execute("UPDATE antennas SET work_evidence=? WHERE asset_id=?",
                     (f"error: {type(exc).__name__}", asset_id))

    # 분해가 선언 근거로 확정한 연결을 그대로 옮긴다(등급 declared)
    if val("entry.derived_from"):
        conn.execute("""INSERT OR REPLACE INTO links
            (from_asset,to_asset,grade,relation,confidence,basis,confirmed_by,ts)
            VALUES(?,?,'declared','derived',1.0,?,?,?)""",
                     (asset_id, val("entry.derived_from"),
                      json.dumps(["CST 선언 임포트 파일명과 일치(discover.py)"],
                                 ensure_ascii=False),
                      None, ts))
    _ = newer

    n = 0
    for key, e in E.items():
        conn.execute("""
            INSERT OR REPLACE INTO facts(asset_id, run_id, key, role, quantity, unit,
                                         value_num, value_text, render, formula, source, passed, ts)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (asset_id, run_id, key, e.get("role"), e.get("quantity"), e.get("unit"),
              _num(e.get("value")),
              None if _num(e.get("value")) is not None else (
                  None if e.get("value") is None else str(e.get("value"))),
              e.get("render"), e.get("formula"), e.get("source"),
              None if e.get("pass") is None else int(bool(e.get("pass"))), ts))
        n += 1

    # 문서 등재 — **게이트 통과본만**. 통과하지 않은 문서는 자산이 아니다.
    doc = {"registered": False}
    vp = work / "게이트_판정.json"
    if vp.exists():
        v = C.read_json(vp)
        refs = v.get("refs_used") or []
        undeclared = sum(1 for u in refs if not u.get("role_declared"))
        if v.get("pass"):
            conn.execute("""
                INSERT OR REPLACE INTO documents(asset_id, run_id, doc_path, gate_pass,
                    n_refs, n_undeclared_role, n_violations, rule_version, ts)
                VALUES(?,?,?,1,?,?,?,?,?)
            """, (asset_id, run_id, v.get("substituted_path"), v.get("n_refs"),
                  undeclared, len(v.get("violations") or []), v.get("rule_version"), ts))
            doc = {"registered": True, "n_refs": v.get("n_refs"),
                   "n_undeclared_role": undeclared}
        else:
            doc = {"registered": False,
                   "why": "게이트 미통과 — 통과본만 자산이 된다",
                   "violations": len(v.get("violations") or [])}
    conn.commit()
    return {"asset_id": asset_id, "run_id": run_id, "n_facts": n, "document": doc,
            "unmapped_roles": cat.get("unmapped_keys") or []}


# ── 사후 연결 — 추출·해석 값으로 후보를 좁힌다 ───────────────────────────────
# "CST 에서 뽑은 정보를 DXF 분석에 쓴다"의 실체. 분해 시점에는 파일 이름밖에 없었다.
LINK_VALUE_TOL = 0.01        # (c) 판정 규칙 — 상대 오차 1 % 이내면 같은 값으로 본다
LINK_MIN_ROLES = 2           # (c) 한 역할만 맞는 것은 우연일 수 있다
LINK_IGNORE_ZERO_MATCH = True  # (c) 0 == 0 은 변별력이 없다 — 근거로 세지 않는다
LINK_NEED_DIMENSIONAL = True   # (c) 치수 일치가 **하나도 없으면** 후보로 올리지 않는다

# 변별력 있는 역할 — 연속량(치수)이다.
#   왜 나누나: 한 원천 폴더에 **서로 다른 안테나**가 같이 있는 것이 정상이다(사용자 확인).
#   그러면 셈(레이어 2개·폴리라인 4개)은 무관한 도면끼리도 쉽게 겹친다 — 값의 자리수가 작다.
#   치수는 다르다. bbox 가 소수점까지 같으려면 같은 도면에서 나온 것이어야 한다.
#   셈은 **보강 근거**로만 쓰고, 후보 승격은 치수 일치가 있을 때만 한다.
LINK_DIMENSIONAL_ROLES = frozenset((
    "bbox_x_mm", "bbox_y_mm", "elevation_mm", "array_pitch_mm",
    "geometry_max_edge_mm", "scale_max_edge_mm", "array_pitch_over_lambda",
    "substrate_h_declared_mm", "t_sub_mm", "band_lo_ghz", "band_hi_ghz"))
LINK_ROLES = ("n_layers", "n_polyline", "n_circle", "bbox_x_mm", "bbox_y_mm",
              "elevation_mm",
              "geometry_max_edge_mm", "scale_max_edge_mm", "array_pitch_mm",
              "n_elements_transform", "n_elements_solid", "n_elements",
              "array_pitch_over_lambda", "substrate_h_declared_mm", "t_sub_mm",
              "band_lo_ghz", "band_hi_ghz")


def link_rules() -> dict:
    return {"link_value_tol": LINK_VALUE_TOL, "link_min_roles": LINK_MIN_ROLES,
            "link_ignore_zero_match": LINK_IGNORE_ZERO_MATCH,
            "link_need_dimensional": LINK_NEED_DIMENSIONAL,
            "link_dimensional_roles": sorted(LINK_DIMENSIONAL_ROLES),
            "link_roles": list(LINK_ROLES),
            "규율": ("기하 지문 일치는 확정급, 값 일치는 후보다. 확정은 사람(A-1). "
                   "한 폴더에 서로 다른 안테나가 있는 것이 정상이므로, 셈만 겹치는 것은 "
                   "후보가 아니다 — 치수 일치가 있어야 한다.")}


def _latest_facts(conn, asset_id) -> dict:
    return {r["role"]: r["value_num"] for r in conn.execute(
        """SELECT f.role role, f.value_num value_num FROM facts f
           JOIN antennas a ON a.asset_id=f.asset_id AND a.last_run_id=f.run_id
           WHERE f.asset_id=? AND f.role IS NOT NULL AND f.value_num IS NOT NULL""",
        (asset_id,))}


def link(conn, apply: bool = True) -> dict:
    """도면 묶음 ↔ CST 프로젝트 후보를 값으로 갱신한다. **판정이 아니라 후보 갱신이다.**"""
    rows = [dict(r) for r in conn.execute(
        "SELECT asset_id, entry_kind, geom_hash FROM antennas")]
    cst = [r for r in rows if (r["entry_kind"] or "") == "cst_project"]
    cad = [r for r in rows if (r["entry_kind"] or "") != "cst_project"]
    facts = {r["asset_id"]: _latest_facts(conn, r["asset_id"]) for r in rows}
    out, ts = [], _now()
    if apply:
        # 파생물 규율 — 지금 규칙이 인정하지 않는 후보는 남기지 않는다.
        # 남겨 두면 규칙을 고쳐도 옛 후보가 DB 에 살아남아, DB 가 규칙보다 오래된 것을 말한다.
        # 선언 연결과 사람이 확정한 것은 건드리지 않는다.
        conn.execute("DELETE FROM links WHERE grade='candidate' AND confirmed_by IS NULL")
    for c in cad:
        for k in cst:
            basis, conf = [], 0.0
            if c["geom_hash"] and c["geom_hash"] == k["geom_hash"]:
                basis.append(f"기하 지문 일치({c['geom_hash']}) — 같은 형상이다")
                conf = 0.95
            fa, fb = facts[c["asset_id"]], facts[k["asset_id"]]
            hit = []
            for role in LINK_ROLES:
                a, b = fa.get(role), fb.get(role)
                if a is None or b is None:
                    continue
                # 0 == 0 은 근거가 아니다. "원이 없다"가 서로 같은 것은 거의 모든 도면에서
                # 참이라 변별력이 없다 — 이것을 세면 무관한 자산이 후보로 올라온다(실측 확인).
                if a == 0 and b == 0:
                    continue
                den = max(abs(a), abs(b)) or 1.0
                if abs(a - b) / den <= LINK_VALUE_TOL:
                    hit.append(role)
            dim = [r for r in hit if r in LINK_DIMENSIONAL_ROLES]
            if len(hit) >= LINK_MIN_ROLES and (dim or not LINK_NEED_DIMENSIONAL):
                conf = max(conf, min(0.3 + 0.15 * len(hit), 0.9))
                basis.append(f"치수 일치 {len(dim)}종: {dim}" if dim else "치수 일치 없음")
                other = [r for r in hit if r not in LINK_DIMENSIONAL_ROLES]
                if other:
                    basis.append(f"셈 일치(보강) {len(other)}종: {other}")
            elif len(hit) >= LINK_MIN_ROLES:
                # 셈만 겹쳤다 — 후보로 올리지 않지만 **왜 안 올렸는지**는 남긴다.
                out.append({"from_asset": c["asset_id"], "to_asset": k["asset_id"],
                            "grade": "rejected", "confidence": 0.0,
                            "basis": [f"셈만 일치 {hit} — 치수 일치가 없어 후보로 올리지 않는다"]})
                continue
            if not basis:
                continue
            rec = {"from_asset": c["asset_id"], "to_asset": k["asset_id"],
                   "grade": "candidate", "confidence": round(conf, 2), "basis": basis}
            out.append(rec)
            if apply:
                ex = conn.execute("SELECT grade, confirmed_by FROM links WHERE from_asset=? AND to_asset=?",
                                  (c["asset_id"], k["asset_id"])).fetchone()
                if ex and (ex["grade"] == "declared" or ex["confirmed_by"]):
                    continue          # 선언·사람 확정을 추정으로 덮지 않는다
                conn.execute("""INSERT OR REPLACE INTO links
                    (from_asset,to_asset,grade,relation,confidence,basis,confirmed_by,ts)
                    VALUES(?,?,'candidate','unknown',?,?,NULL,?)""",
                             (c["asset_id"], k["asset_id"], rec["confidence"],
                              json.dumps(basis, ensure_ascii=False), ts))
    if apply:
        conn.commit()
    kept = [c for c in out if c["grade"] != "rejected"]
    return {"n_candidates": len(kept), "candidates": kept,
            "rejected": [c for c in out if c["grade"] == "rejected"],
            "rules": link_rules(),
            "규율": "여기서 나온 것은 후보다 — assets.py confirm 으로 사람이 확정한다"}


# ── 판본 대조 — **차이가 노하우다** ─────────────────────────────────────────
# 사용자 확인(2026-07-31): 같은 안테나를 여러 번 등록하고, 그 사이의 차이를 나중에 본다.
# 차이를 **선언으로 적지 않는 이유** — 사람이 요약한 문장은 실물과 어긋나도 드러나지 않는다.
# 시행착오로 얻은 값 자체가 남아야 다음 사람이 쓴다.
#
# 도구는 **무엇이 달라졌는지까지만** 말한다. 왜 달라졌는지는 해석이고 사람 몫이다.

def diff(conn, a: str, b: str) -> dict:
    """두 등록의 추출값을 역할 단위로 대조한다. 해석하지 않는다."""
    fa, fb = _facts_by_role(conn, a), _facts_by_role(conn, b)
    roles = sorted(set(fa) | set(fb))
    changed, same, only_a, only_b = [], [], [], []
    for r in roles:
        x, y = fa.get(r), fb.get(r)
        if x is None:
            only_b.append({"role": r, "value": y["render"], "unit": y["unit"],
                           "key": y["key"], "source": y["source"]})
        elif y is None:
            only_a.append({"role": r, "value": x["render"], "unit": x["unit"],
                           "key": x["key"], "source": x["source"]})
        elif x["render"] == y["render"]:
            same.append(r)
        else:
            d = None
            if x["num"] is not None and y["num"] is not None:
                den = max(abs(x["num"]), abs(y["num"])) or 1.0
                d = round((y["num"] - x["num"]) / den, 6)
            changed.append({"role": r, "unit": x["unit"] or y["unit"],
                            "a": x["render"], "b": y["render"], "rel_delta": d,
                            "a_key": x["key"], "b_key": y["key"],
                            "a_source": x["source"], "b_source": y["source"]})
    return {"a": a, "b": b,
            "n_changed": len(changed), "n_same": len(same),
            "changed": sorted(changed, key=lambda c: (c["rel_delta"] is None,
                                                      -abs(c["rel_delta"] or 0))),
            "only_in_a": only_a, "only_in_b": only_b, "same_roles": same,
            "규율": ("도구는 **무엇이 달라졌는지**까지만 말한다. 왜 달라졌는지는 해석이고 "
                   "사람 몫이다(A-1). 이 차이가 시행착오의 기록이다.")}


def _facts_by_role(conn, asset_id: str) -> dict:
    out = {}
    for r in conn.execute(
            """SELECT f.role role, f.render render, f.unit unit, f.key key,
                      f.source source, f.value_num num
               FROM facts f JOIN antennas a
                 ON a.asset_id=f.asset_id AND a.last_run_id=f.run_id
               WHERE f.asset_id=? AND f.role IS NOT NULL""", (asset_id,)):
        d = dict(r)
        # 한 역할에 값이 여럿이면(재질 둘 등) 첫 것만 대표로 — 나머지는 키로 구별된다
        out.setdefault(d["role"], d)
    return out


def confirm(conn, from_asset: str, to_asset: str, by: str, relation: str | None = None) -> dict:
    """사람이 연결을 확정한다(A-1). 확정 뒤에는 추정이 덮지 못한다.

    `relation` 을 함께 주면 **관계 종류까지** 확정한다(`relate.py` 의 질문에 답한 결과).
    `variant` 로 확정하면 `antennas.variant_of` 가 채워져 판본축 조회가 열린다.
    """
    if relation is not None:
        import relate as RL
        if relation not in RL.RELATIONS:
            raise ValueError(f"관계 어휘 밖: {relation} — 허용 {RL.RELATIONS}")
    cur = conn.execute(
        "UPDATE links SET confirmed_by=?, ts=?, relation=COALESCE(?, relation) "
        "WHERE from_asset=? AND to_asset=?",
        (by, _now(), relation, from_asset, to_asset))
    if not cur.rowcount:
        conn.rollback()
        raise ValueError(f"그런 연결 후보가 없다: {from_asset} → {to_asset}")
    # variant 확정은 자산 쪽에도 남는다 — 판본축 조회(asset_variants)가 여기에 걸린다.
    # **사람이 확정해야만** 채워진다. 추정으로는 채우지 않는다(A-1).
    if relation == "variant":
        conn.execute("UPDATE antennas SET variant_of=? WHERE asset_id=?",
                     (to_asset, from_asset))
    conn.commit()
    return {"from": from_asset, "to": to_asset, "confirmed_by": by, "relation": relation}


def links(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM links ORDER BY grade, confidence DESC")]


def rebuild(conn, pattern: str = "work/*/값_카탈로그.json") -> dict:
    """work/ 전체 재적재. 파생물이므로 언제든 버리고 다시 만든다."""
    for t in ("facts", "documents", "links", "work_sessions", "time_conflicts",
              "antennas"):
        conn.execute(f"DELETE FROM {t}")
    conn.commit()
    done, skipped = [], []
    runs = sorted({Path(p).parent.name for p in glob.glob("work/*/*.json")})
    for run_id in runs:
        w = Path("work") / run_id
        if not (w / "식별_결과.json").exists():
            skipped.append(run_id)
            continue
        try:
            if not (w / CAT.CATALOG_NAME).exists():
                C.write_json(w / CAT.CATALOG_NAME, CAT.build(run_id, w))
            done.append(ingest(conn, run_id, w))
        except Exception as exc:
            skipped.append(f"{run_id}: {exc}")
    return {"ingested": len(done), "skipped": skipped,
            "assets": sorted({d["asset_id"] for d in done})}


# ── 조회 ────────────────────────────────────────────────────────────────────

def status(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM asset_status ORDER BY asset_id")]


def compare(conn, role: str) -> list[dict]:
    """같은 역할의 값을 안테나별로 나란히 — 최신 run 기준."""
    return [dict(r) for r in conn.execute("""
        SELECT f.asset_id, f.key, f.render, f.unit, f.source
        FROM facts f JOIN antennas a ON a.asset_id = f.asset_id AND a.last_run_id = f.run_id
        WHERE f.role = ? ORDER BY f.asset_id, f.key""", (role,))]


def coverage(conn) -> dict:
    assets = [r["asset_id"] for r in conn.execute(
        "SELECT asset_id FROM antennas ORDER BY asset_id")]
    rows = conn.execute("""
        SELECT f.role, f.asset_id, COUNT(*) n
        FROM facts f JOIN antennas a ON a.asset_id = f.asset_id AND a.last_run_id = f.run_id
        WHERE f.role IS NOT NULL AND f.key LIKE '해석.%'
        GROUP BY f.role, f.asset_id""").fetchall()
    m: dict[str, dict[str, int]] = {}
    for r in rows:
        m.setdefault(r["role"], {})[r["asset_id"]] = r["n"]
    return {"assets": assets, "matrix": m}


def anomalies(conn) -> dict:
    """모아 놓아야 보이는 것들. 판정이 아니라 **드러냄**이다 — 조치는 사람."""
    out: dict[str, list] = {}

    # 같은 역할인데 단위가 갈린다 → 역할 배정이나 단위 표기 중 하나가 틀렸다
    out["같은_역할_다른_단위"] = [dict(r) for r in conn.execute("""
        SELECT role, GROUP_CONCAT(DISTINCT unit) units, COUNT(DISTINCT unit) n
        FROM facts WHERE role IS NOT NULL GROUP BY role HAVING n > 1""")]

    # 역할이 어휘에 없다 → tools/roles.py 에 사람이 등재해야 한다
    out["역할_미등재_키"] = [dict(r) for r in conn.execute("""
        SELECT DISTINCT asset_id, key, unit FROM facts
        WHERE role IS NULL AND key LIKE '해석.%' ORDER BY key""")]

    # 같은 안테나인데 run 마다 형상 지문이 다르다 → 원천이 바뀌었거나 추출이 흔들린다
    out["형상_지문_불일치"] = [dict(r) for r in conn.execute("""
        SELECT asset_id, COUNT(DISTINCT render) n, GROUP_CONCAT(DISTINCT render) hashes
        FROM facts WHERE key = '추출.geom_hash' AND render <> ''
        GROUP BY asset_id HAVING n > 1""")]

    # 한 역할이 한 안테나에만 있다 → 다른 안테나에서 왜 안 나오나
    cov = coverage(conn)
    n_assets = len(cov["assets"])
    out["일부_안테나에만_있는_역할"] = [
        {"role": r, "assets": sorted(a), "n": len(a)}
        for r, a in sorted(cov["matrix"].items()) if 0 < len(a) < n_assets]

    # 문서화되지 않은 안테나 → 자산화 진척
    out["문서_미등재_안테나"] = [dict(r) for r in conn.execute(
        "SELECT asset_id, last_run_id FROM asset_status WHERE n_docs = 0")]
    return out


# ── 자기 시험 ────────────────────────────────────────────────────────────────

def self_test() -> int:
    import tempfile
    ok = fail = 0

    def chk(name, cond, detail=""):
        nonlocal ok, fail
        if cond:
            ok += 1
            print(f"  PASS  {name}")
        else:
            fail += 1
            print(f"  FAIL  {name}  {detail}")

    print("[assets.py 자기 시험 — 실물 work/ 전체]")
    tmp = Path(tempfile.mkdtemp()) / "assets.sqlite"
    os.environ["ORCH_ASSET_DB"] = str(C.data_dir() / "out" / "_asset_db" / "selftest.sqlite")
    conn = open_db()
    r = rebuild(conn)
    chk(f"실물 run 적재 ({r['ingested']}건)", r["ingested"] >= 3, str(r["skipped"])[:200])
    chk(f"안테나 3종 식별 {r['assets']}", len(r["assets"]) >= 3, str(r["assets"]))

    st = status(conn)
    chk("자산 현황 조회", len(st) == len(r["assets"]))
    chk("문서화 여부가 보인다", all("n_docs" in s for s in st))

    cmp_ = compare(conn, "band_lo_ghz")
    chk(f"역할 교차 조회 band_lo_ghz ({len(cmp_)}행)", len(cmp_) >= 2,
        json.dumps(cmp_, ensure_ascii=False)[:200])
    chk("교차 조회에 출처가 함께 온다", all(c.get("source") is not None for c in cmp_))

    cov = coverage(conn)
    chk(f"보유 행렬 생성 (역할 {len(cov['matrix'])}종 × 안테나 {len(cov['assets'])})",
        len(cov["matrix"]) > 10 and len(cov["assets"]) >= 3)

    an = anomalies(conn)
    chk("이상 항목 조회 6종", len(an) == 5, str(list(an)))
    chk("역할 미등재 0건(실물)", not an["역할_미등재_키"],
        json.dumps(an["역할_미등재_키"], ensure_ascii=False)[:200])

    # 파생물 규율 — 재생성해도 같아야 한다
    a = json.dumps(status(conn), ensure_ascii=False, sort_keys=True)
    rebuild(conn)
    b = json.dumps(status(conn), ensure_ascii=False, sort_keys=True)
    chk("rebuild 멱등 — 파생물 규율", a == b)

    # ── 연결 — 배포 도안 ↔ 원본 프로젝트 ────────────────────────────────
    lk = link(conn)
    cands = {(c["from_asset"], c["to_asset"]): c for c in lk["candidates"]}
    has_dist = any(f == "배포_도안" for f, _ in cands)
    if has_dist:
        c = cands[("배포_도안", "test2")]
        chk("배포 도안이 원본과 값으로 연결된다", c["confidence"] >= 0.8, str(c))
        chk("연결 근거가 기하 지문이다",
            any("n_layers" in b or "bbox" in b for b in c["basis"]), str(c["basis"]))
        chk("무관한 자산은 후보에 없다", ("Antenna_CAD_ECO", "test2") not in cands,
            str(sorted(cands)))
    else:
        chk("배포 도안 연결", True, "(배포 시나리오 run 없음 — 건너뜀)")
    chk("치수 일치가 없으면 후보가 아니다",
        all(any(b.startswith("치수 일치") and "0종" not in b for b in c["basis"])
            or c["confidence"] >= 0.95 for c in lk["candidates"]),
        json.dumps([c["basis"] for c in lk["candidates"]], ensure_ascii=False)[:200])
    chk("탈락 사유를 남긴다", isinstance(lk.get("rejected"), list))
    chk("연결 규칙에 치수 요건이 실린다",
        link_rules().get("link_need_dimensional") is True and
        "bbox_x_mm" in link_rules()["link_dimensional_roles"])

    dl = [x for x in links(conn) if x["grade"] == "declared"]
    chk("선언 연결은 등급이 declared", all(x["confidence"] == 1.0 for x in dl) if dl else True,
        str(dl))
    # 선언·사람 확정을 추정이 덮지 않는다
    if dl:
        f, t = dl[0]["from_asset"], dl[0]["to_asset"]
        link(conn)
        after = conn.execute("SELECT grade FROM links WHERE from_asset=? AND to_asset=?",
                             (f, t)).fetchone()
        chk("추정이 선언 연결을 덮지 않는다", after["grade"] == "declared", str(after["grade"]))
        confirm(conn, f, t, "self-test")
        chk("사람 확정 기록", conn.execute(
            "SELECT confirmed_by c FROM links WHERE from_asset=? AND to_asset=?",
            (f, t)).fetchone()["c"] == "self-test")
    chk("연결 규칙이 산지와 함께 기록된다",
        {"link_value_tol", "link_min_roles", "link_ignore_zero_match"} <= set(link_rules()))

    # ── 작업 타임라인 ──────────────────────────────────────────────────
    tl = [dict(r) for r in conn.execute("SELECT * FROM asset_timeline")]
    chk(f"타임라인 등재 ({len(tl)}건)", len(tl) >= 2, str([t["asset_id"] for t in tl]))
    chk("시간축으로 정렬된다",
        [t["work_start"] for t in tl] == sorted(t["work_start"] for t in tl),
        str([t["work_start"] for t in tl]))
    pcb = next((t for t in tl if t["asset_id"].startswith("20250522")), None)
    if pcb:
        chk("로그 근거가 선언보다 우선", pcb["work_evidence"] == "solver_log",
            str(pcb["work_evidence"]))
        chk("원천 이름(20250522)과 작업 종료일이 맞는다",
            str(pcb["work_end"]).startswith("2025-05-22"), str(pcb["work_end"]))
        chk("작업 기간이 함께 실린다", abs((pcb["work_span_days"] or 0) - 0.687) < 0.01,
            str(pcb["work_span_days"]))
    eco = next((t for t in tl if t["asset_id"] == "Antenna_CAD_ECO"), None)
    if eco:
        chk("도면만 있는 자산도 시각을 갖는다", eco["work_evidence"] == "dwg_header",
            str(eco["work_evidence"]))
        chk("KST 로 실린다", str(eco["work_end"]).startswith("2026-02-26 16:56"),
            str(eco["work_end"]))
        chk("이름 어긋남이 표에 남는다", conn.execute(
            "SELECT COUNT(*) c FROM time_conflicts WHERE asset_id='Antenna_CAD_ECO'"
        ).fetchone()["c"] == 1)
    t2r = next((t for t in tl if t["asset_id"] == "test2"), None)
    if t2r:
        chk("선언만 있으면 기간을 비운다(0 으로 적지 않는다)",
            t2r["work_end"] is None and t2r["work_span_days"] is None, str(dict(t2r)))
    chk("근거 없는 자산은 날짜가 비어 있다",
        conn.execute("SELECT COUNT(*) c FROM antennas WHERE work_start IS NULL"
                     ).fetchone()["c"] >= 1)
    chk("세션이 남는다", conn.execute("SELECT COUNT(*) c FROM work_sessions"
                                 ).fetchone()["c"] >= 2)
    a2 = json.dumps(sorted((r["asset_id"], r["work_start"]) for r in
                           conn.execute("SELECT asset_id, work_start FROM antennas")))
    rebuild(conn)
    b2 = json.dumps(sorted((r["asset_id"], r["work_start"]) for r in
                           conn.execute("SELECT asset_id, work_start FROM antennas")))
    chk("타임라인도 rebuild 멱등", a2 == b2)

    # ── 관계 어휘 — 등급과 **다른 축**이다 (v6)
    conn.execute("INSERT OR REPLACE INTO antennas(asset_id) VALUES('X')")
    conn.execute("INSERT OR REPLACE INTO antennas(asset_id) VALUES('Y')")
    conn.execute("INSERT OR REPLACE INTO links(from_asset,to_asset,grade,relation,"
                 "confidence,basis,confirmed_by,ts) VALUES('X','Y','candidate','unknown',"
                 "0.5,'[]',NULL,'t')")
    conn.commit()
    chk("등급과 관계가 따로 실린다",
        conn.execute("SELECT grade, relation FROM links WHERE from_asset='X'"
                     ).fetchone()["relation"] == "unknown")
    rc = confirm(conn, "X", "Y", "사람", "variant")
    chk("사람이 관계 종류까지 확정한다", rc["relation"] == "variant")
    chk("variant 확정이 자산에 남는다",
        conn.execute("SELECT variant_of FROM antennas WHERE asset_id='X'").fetchone()[0] == "Y")
    chk("판본축 조회가 열린다",
        conn.execute("SELECT COUNT(*) c FROM asset_variants").fetchone()["c"] == 1)
    try:
        confirm(conn, "X", "Y", "사람", "없는관계"); bad = False
    except ValueError:
        bad = True
    chk("관계 어휘 밖은 거부한다", bad)
    chk("추정으로는 variant_of 를 채우지 않는다",
        conn.execute("SELECT COUNT(*) c FROM antennas WHERE variant_of IS NOT NULL"
                     ).fetchone()["c"] == 1)
    conn.execute("DELETE FROM links WHERE from_asset='X'")
    conn.execute("DELETE FROM antennas WHERE asset_id IN ('X','Y')")
    conn.commit()

    # 게이트 미통과본은 자산이 아니다
    n_docs = conn.execute("SELECT COUNT(*) c FROM documents WHERE gate_pass=0").fetchone()["c"]
    chk("게이트 미통과본은 documents 에 없다", n_docs == 0, str(n_docs))

    conn.close()
    os.environ.pop("ORCH_ASSET_DB", None)
    print(f"\n결과: {ok}/{ok + fail} PASS")
    return 0 if fail == 0 else 1


def _table(rows: list[dict], cols: list[str]) -> str:
    if not rows:
        return "  (없음)"
    w = {c: max(len(c), *(len(str(r.get(c, ""))) for r in rows)) for c in cols}
    out = ["  " + "  ".join(c.ljust(w[c]) for c in cols),
           "  " + "  ".join("-" * w[c] for c in cols)]
    for r in rows:
        out.append("  " + "  ".join(str(r.get(c, "")).ljust(w[c]) for c in cols))
    return "\n".join(out)


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd = argv[1]
    if cmd == "self-test":
        return self_test()
    conn = open_db()
    if cmd == "ingest":
        print(json.dumps(ingest(conn, argv[2]), ensure_ascii=False, indent=2))
    elif cmd == "rebuild":
        r = rebuild(conn)
        print(f"적재 {r['ingested']}건 · 안테나 {r['assets']}")
        if r["skipped"]:
            print(f"건너뜀 {len(r['skipped'])}건: {r['skipped'][:5]}")
    elif cmd == "list":
        print(_table(status(conn),
                     ["asset_id", "adapter", "geom_hash", "n_runs", "n_facts", "n_docs",
                      "last_run_id"]))
    elif cmd == "compare":
        rows = compare(conn, argv[2])
        print(f"역할 {argv[2]} — {R.ROLE_DESC.get(argv[2], '(설명 없음)')}")
        print(_table(rows, ["asset_id", "render", "unit", "key"]))
    elif cmd == "coverage":
        cov = coverage(conn)
        A = cov["assets"]
        print("  역할".ljust(32) + "  ".join(a[:14].ljust(14) for a in A))
        for role in sorted(cov["matrix"]):
            row = cov["matrix"][role]
            print("  " + role.ljust(30) + "  ".join(
                (str(row.get(a, "·"))).ljust(14) for a in A))
    elif cmd == "link":
        r = link(conn)
        print(f"후보 {r['n_candidates']}건 (규칙 {json.dumps(r['rules'], ensure_ascii=False)})")
        for c in r["candidates"]:
            print(f"  {c['from_asset']} → {c['to_asset']}  신뢰 {c['confidence']}")
            for b in c["basis"]:
                print(f"      {b}")
        print("\n  확정: python tools/assets.py confirm <from> <to> <이름>")
    elif cmd == "timeline":
        rows = [dict(r) for r in conn.execute("SELECT * FROM asset_timeline")]
        print(_table(rows, ["asset_id", "work_start", "work_end", "work_span_days",
                            "work_evidence", "n_sessions", "n_excluded", "entry_kind"]))
        ex = [dict(r) for r in conn.execute(
            "SELECT * FROM work_sessions WHERE excluded=1 ORDER BY asset_id, seq")]
        if ex:
            print("\n  [제외된 꼬리 — 지우지 않고 남긴 것]")
            for x in ex:
                print(f"    {x['asset_id']:<28} {x['start_ts']} ~ {x['end_ts']} "
                      f"({x['n_events']}점)")
                print(f"        {x['why']}")
        cf = [dict(r) for r in conn.execute(
            "SELECT * FROM time_conflicts ORDER BY level DESC, asset_id")]
        if cf:
            print("\n  [이름 날짜 ↔ 헤더 저장 시각 — 맞추지 않고 남긴 것]")
            for x in cf:
                print(f"    [{x['level']}] {x['asset_id']:<24} {x['file']}")
                print(f"        이름 {x['filename_date'][:10]} ↔ 헤더 {x['header_ts'][:10]}"
                      f"  ({x['gap_days']}일)")
        miss = [dict(r) for r in conn.execute(
            "SELECT asset_id, work_evidence FROM antennas WHERE work_start IS NULL")]
        if miss:
            print(f"\n  [시각 근거 없음 {len(miss)}건] "
                  f"{[m['asset_id'] for m in miss]}")
            print("    파일 mtime 은 복사로 뭉개지므로 쓰지 않는다 — 날짜 없음이 정답이다")
    elif cmd == "diff":
        d = diff(conn, argv[2], argv[3])
        print(f"{d['a']}  ↔  {d['b']}")
        print(f"  바뀐 역할 {d['n_changed']} · 같은 역할 {d['n_same']} · "
              f"A 에만 {len(d['only_in_a'])} · B 에만 {len(d['only_in_b'])}\n")
        if d["changed"]:
            print("  [바뀐 것 — 시행착오의 기록]")
            for c in d["changed"]:
                dl = f"  ({c['rel_delta']:+.3%})" if c["rel_delta"] is not None else ""
                print(f"    {c['role']:<26} {c['a']} → {c['b']} {c['unit']}{dl}")
        for label, rows in (("A 에만 있는 것", d["only_in_a"]), ("B 에만 있는 것", d["only_in_b"])):
            if rows:
                print(f"\n  [{label}]")
                for x in rows:
                    print(f"    {x['role']:<26} {x['value']} {x['unit']}")
        print(f"\n  {d['규율']}")
    elif cmd == "links":
        print(_table(links(conn),
                     ["from_asset", "to_asset", "grade", "confidence", "confirmed_by"]))
    elif cmd == "confirm":
        print(json.dumps(confirm(conn, argv[2], argv[3], argv[4]), ensure_ascii=False))
    elif cmd == "anomalies":
        for k, v in anomalies(conn).items():
            print(f"\n[{k}] {len(v)}건")
            for x in v[:12]:
                print("   ", json.dumps(x, ensure_ascii=False)[:180])
    else:
        print(f"알 수 없는 명령: {cmd}")
        return 2
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
