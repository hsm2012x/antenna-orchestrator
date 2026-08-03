#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/ledger.py — 실행 원장 (orch_ledger_contract). append-only. LLM 0콜.

원장은 **판단의 정본이며 파생물이 아니다.** 기록 없는 동작은 하지 않은 것으로 간주한다(B-3).
UPDATE·DELETE 를 코드 경로에 만들지 않고, sqlite 트리거로 **DB 차원에서도** 막는다(W-3) —
코드 규율만으로는 다음 사람이 UPDATE 를 쓰는 것을 막지 못한다.

계약과 규칙 사이의 충돌 두 건을 이렇게 해소한다(정본 변경은 사람 승인 대상):

  ① `runs.run_id` 가 PK(run 당 1행)인데 `runs.state` 가 있고 W-3 은 UPDATE 를 금지한다 →
     세 조건은 동시에 성립할 수 없다. **해소(2026-07-30 승인): `runs.state` 열을 제거한다.**
     `runs` 는 불변 식별 정보(원천·레벨·버전·생성시각)만 담고, 상태는 `events.state` 에만 있으며
     현재 상태는 뷰 `run_state` 가 파생한다. 값이 변하지 않는 열은 데이터가 아니라 주석이고,
     주석으로 지키는 불변식은 언젠가 깨진다 — 구조가 지키게 한다.

  재투입 정책(2026-07-30 승인): HOLD 후 재투입은 **같은 run_id 로 이어간다.**
     따라서 `work/<run_id>/` 는 시도마다 덮여 쓰인다(설계상 폐기 가능 영역). 시도별 근거는
     원장이 보존해야 하므로 ① 시도 번호를 payload 에 적고(`attempt`, 파생값) ② 원천 지문
     요약을 routing payload 에 담아 "그 시도가 어떤 바이트를 봤는지" 를 남긴다.

  ② 이벤트 kind 10종에 **추출 · 렌더 · 문서 조립** 전이에 대응하는 것이 없었다
     (routing=식별 · verify=해석 · gate=게이트 · register=통합문서화 · human=검수 · failure=실패).
     해소: 그 셋 전용 `stage` 를 추가하고 **interfaces.yaml 에 등재**했다(2026-07-30 승인).
     모든 전이는 원장에 남아야 하므로(B-3) 어휘 공백을 남겨 둘 수 없다.

  state 의 소재 — 같은 어휘가 두 저장소에 있어 혼동되는 자리:
     · checkpoints.sqlite 의 state = LangGraph 상태 딕셔너리 → **파생물**, 노드마다 갱신(W-3 대상 아님)
     · ledger.sqlite 의 state     = 이 파일이 쓰는 것        → **정본**, append 만
     · run_id = LangGraph thread_id = orch_state_contract.run_id = runs.run_id (하나의 식별자)
     어긋나면 원장이 옳다.

사용:
    python tools/ledger.py init
    python tools/ledger.py self-test
    python tools/ledger.py ingest --run-id <id>      # work/<run_id>/*.json → 이벤트 등재
    python tools/ledger.py show --run-id <id>
    python tools/ledger.py runs
"""
from __future__ import annotations
import argparse, json, sqlite3, sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import RULE_VERSION, assert_writable, ledger_path, read_json, work_dir

# 계약 등재 kind — interfaces.yaml orch_ledger_contract 그대로. 순서도 계약 순서를 따른다.
# `stage` 는 2026-07-30 사용자 승인으로 정본 등재됨(추출·렌더·문서조립 전이 전용).
CONTRACT_KINDS = ("routing", "context", "verify", "gate", "question", "judgment",
                  "action", "human", "failure", "register", "stage")
EXT_KINDS = ()          # 계약 미등재 확장 후보 — 현재 없음
KINDS = CONTRACT_KINDS + EXT_KINDS

# 선택 주체 어휘 — "누가 이 시도의 방법을 골랐는가". 값을 만든 주체가 아니다(값은 항상 도구).
#   rule  결정론 규칙(기본) · human 사람 지시 · llm 오케스트레이터의 제한적 재량
CHOSEN_BY = ("rule", "human", "llm")

# 재량으로 바꿀 수 있는 것의 **닫힌 집합**. 이 밖은 재량이 아니라 규칙 위반이다.
#   허용: 등재 도구의 인자 · 기여 레인 재시도 · 레인 시도 순서
#   금지: 새 도구·파서 제작(T-1) · 등재 목록 밖 실행(T-2) · 설치(T-3) ·
#         클래스 순서 변경·생략(A-2) · 값 생성(N-1) · 게이트 완화(N-2)
VARIATION_KINDS = ("tool_args", "lane_retry", "lane_order")

# orch_state_contract 의 state 어휘. 이 밖의 값을 쓰지 않는다.
STATES = ("WAIT", "ROUTE", "EXTRACT", "RETRY", "VERIFY", "COMPOSE", "ASK", "ACT",
          "REVIEW", "HOLD", "DONE")

# 저널 모드는 **저장소가 정한다**. WAL 이 좋지만 공유 메모리 파일을 못 만드는 저장소가 있다 —
# 실측: FUSE 마운트(사용자 PC 폴더 브리지)는 WAL·DELETE 가 disk I/O error, TRUNCATE·PERSIST 는 동작.
# 고정하면 그런 저장소에서 원장을 아예 열 수 없다. 선호 순서로 협상하고 실제 모드를 보고한다.
JOURNAL_PREFERENCE = ("WAL", "TRUNCATE", "PERSIST", "DELETE")

# 스키마 버전 — v2: runs.state 제거 · v3: events.attempt 를 정식 열로.
# 구버전 파일은 migrate 로 새 파일에 옮긴다(계약: "연 단위 롤오버, 삭제 없음" — 지우지 않는다).
SCHEMA_VERSION = 3

_SCHEMA = f"""
PRAGMA foreign_keys=ON;
PRAGMA busy_timeout=5000;

CREATE TABLE IF NOT EXISTS ledger_meta (   -- 저널 협상 결과 등 환경 사실. 판정값이 아니다
    k TEXT PRIMARY KEY, v TEXT NOT NULL, ts TEXT NOT NULL
);

-- runs 는 **불변 식별 정보만** 담는다. 상태 열은 없다(2026-07-30 결정) —
-- run 당 1행 + UPDATE 금지에서 상태 열은 갱신될 수 없고, 갱신되지 않는 상태 열은 오해를 부른다.
CREATE TABLE IF NOT EXISTS runs (
    run_id           TEXT PRIMARY KEY,         -- = LangGraph thread_id
    source_path      TEXT NOT NULL,
    source_kind      TEXT CHECK (source_kind IN ('folder','file')),
    source_name      TEXT,
    level            INTEGER CHECK (level IN (1,2,3)),
    rule_version     TEXT,
    registry_version TEXT,
    created_at       TEXT NOT NULL             -- ISO8601
);

CREATE TABLE IF NOT EXISTS events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id   TEXT NOT NULL REFERENCES runs(run_id),
    ts       TEXT NOT NULL,                    -- ISO8601
    kind     TEXT NOT NULL CHECK (kind IN ({",".join(f"'{k}'" for k in KINDS)})),
    state    TEXT CHECK (state IS NULL OR state IN ({",".join(f"'{s}'" for s in STATES)})),
    -- 시도 회차. 같은 run_id 로 재투입하는 정책(2026-07-30)에서 시도를 구분하는 유일한 근거다.
    -- **추론하지 않고 선언받는다** — 재투입해도 산출이 같을 수 있어(도구 반입 실패 등)
    -- payload 변화로 회차를 세면 시도가 원장에서 사라진다. JSON 안이 아니라 열이어야 하는 이유:
    -- 조회가 json_extract 에 의존하지 않고(구 sqlite 빌드), 색인이 걸린다.
    attempt  INTEGER NOT NULL DEFAULT 1 CHECK (attempt >= 1),
    payload  TEXT NOT NULL                     -- JSON
);
CREATE INDEX IF NOT EXISTS ix_events_run ON events(run_id, event_id);
CREATE INDEX IF NOT EXISTS ix_events_kind ON events(kind);
CREATE INDEX IF NOT EXISTS ix_events_attempt ON events(run_id, attempt);

-- ── append-only 강제 (W-3) — 코드가 아니라 DB 가 막는다 ──────────────────────
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, '원장 events 는 append-only — UPDATE 금지(W-3). 판정이 바뀌면 새 행을 쓴다'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, '원장 events 는 append-only — DELETE 금지(W-3)'); END;
CREATE TRIGGER IF NOT EXISTS runs_no_update BEFORE UPDATE ON runs
BEGIN SELECT RAISE(ABORT, '원장 runs 는 append-only — UPDATE 금지(W-3). 상태 변화는 events 에 쌓는다'); END;
CREATE TRIGGER IF NOT EXISTS runs_no_delete BEFORE DELETE ON runs
BEGIN SELECT RAISE(ABORT, '원장 runs 는 append-only — DELETE 금지(W-3)'); END;

-- ── 상태는 events 에만 있고, 현재 상태는 여기서 파생된다 ────────────────────
-- 이벤트가 하나도 없으면 아직 아무 전이도 기록되지 않은 것이므로 WAIT 다(어휘의 시작값).
-- 시도 회차 = MAX(events.attempt) — 선언된 값이며 추론하지 않는다.
CREATE VIEW IF NOT EXISTS run_state AS
SELECT r.run_id, r.source_path, r.source_name, r.level, r.created_at,
       r.rule_version, r.registry_version,
       COALESCE((SELECT e.state FROM events e
                  WHERE e.run_id = r.run_id AND e.state IS NOT NULL
                  ORDER BY e.event_id DESC LIMIT 1), 'WAIT') AS state,
       (SELECT COUNT(*) FROM events e WHERE e.run_id = r.run_id) AS n_events,
       COALESCE((SELECT MAX(e.attempt) FROM events e WHERE e.run_id = r.run_id), 0) AS n_attempts,
       (SELECT COUNT(*) FROM events e WHERE e.run_id = r.run_id AND e.state='HOLD') AS n_hold,
       (SELECT MAX(e.ts) FROM events e WHERE e.run_id = r.run_id) AS last_ts
FROM runs r;
"""


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def negotiate_journal(conn) -> str:
    """저장소가 받아 주는 저널 모드를 찾는다. PRAGMA 성공만으로는 모른다 — 실제 쓰기까지 해 본다."""
    last = None
    for m in JOURNAL_PREFERENCE:
        try:
            got = conn.execute(f"PRAGMA journal_mode={m}").fetchone()[0]
            conn.execute("CREATE TABLE IF NOT EXISTS ledger_meta"
                         "(k TEXT PRIMARY KEY, v TEXT NOT NULL, ts TEXT NOT NULL)")
            conn.commit()
            return str(got).upper()
        except sqlite3.OperationalError as e:
            last = e
            continue
    raise RuntimeError(
        f"이 저장소는 sqlite 쓰기를 지원하지 않는다 — 시도한 저널 모드 {JOURNAL_PREFERENCE}, "
        f"마지막 오류 {type(last).__name__}: {last}. ORCH_LEDGER_DB 로 다른 저장소를 지정하라")


def open_ledger(path=None, create: bool = True, as_ledger: bool = False) -> sqlite3.Connection:
    """as_ledger=True 는 롤오버·마이그레이션 대상(원장 폴더의 다른 *.sqlite)을 열 때만 쓴다."""
    p = Path(path or ledger_path())
    if create:
        assert_writable(p, as_ledger=as_ledger)  # W-1 — 원장 경로(+ 롤오버 대상)만 허용된다
        p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    if create:
        mode = negotiate_journal(conn)
        _assert_schema_compatible(conn, p)
        conn.executescript(_SCHEMA)
        conn.execute("INSERT OR IGNORE INTO ledger_meta(k, v, ts) VALUES ('journal_mode', ?, ?)",
                     (mode, now_iso()))
        conn.execute("INSERT OR IGNORE INTO ledger_meta(k, v, ts) VALUES ('schema_version', ?, ?)",
                     (str(SCHEMA_VERSION), now_iso()))
        conn.commit()
        # sqlite3.Connection 은 임의 속성을 받지 않는다 → 협상 결과는 ledger_meta 에 남겨 읽는다.
    else:
        conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _assert_schema_compatible(conn, path) -> None:
    """구버전 스키마를 조용히 쓰지 않는다 — 원장은 정본이므로 애매한 상태로 여는 것이 더 위험하다."""
    rcols = [r["name"] for r in conn.execute("PRAGMA table_info(runs)").fetchall()]
    ecols = [r["name"] for r in conn.execute("PRAGMA table_info(events)").fetchall()]
    old = []
    if rcols and "state" in rcols: old.append("runs.state 존재(v1)")
    if ecols and "attempt" not in ecols: old.append("events.attempt 없음(v1·v2)")
    if old:
        raise RuntimeError(
            f"구버전 원장이다 — {path}\n  근거: {' · '.join(old)} (현재 v{SCHEMA_VERSION})\n"
            f"  옮기기: python tools/ledger.py migrate --from \"{path}\" "
            f"--to \"{Path(path).with_suffix('')}-v{SCHEMA_VERSION}.sqlite\"\n"
            f"  원장은 지우지 않는다(계약: 연 단위 롤오버, 삭제 없음) — 구파일은 그대로 보존하라.")


def migrate(src, dst) -> dict:
    """구버전 원장(v1·v2)을 현재 스키마로 새 파일에 옮긴다. 원본은 손대지 않는다.

    event_id 를 그대로 보존한다 — 원장 참조가 이벤트 번호로 이루어지므로 번호가 바뀌면
    이전 보고서가 가리키던 근거를 잃는다.
    """
    src, dst = Path(src), Path(dst)
    if not src.exists(): raise FileNotFoundError(f"원본 원장 없음: {src}")
    if dst.exists(): raise FileExistsError(f"대상이 이미 있다: {dst} — 덮어쓰지 않는다")
    old = sqlite3.connect(str(src)); old.row_factory = sqlite3.Row
    new = open_ledger(dst, as_ledger=True)       # 롤오버 대상임을 명시적으로 선언한다
    n_runs = n_ev = 0
    moved_ids = []
    for r in old.execute("SELECT * FROM runs ORDER BY created_at, run_id"):
        moved_ids.append(r["run_id"])
        new.execute("INSERT INTO runs(run_id, source_path, source_kind, source_name, level,"
                    " rule_version, registry_version, created_at) VALUES (?,?,?,?,?,?,?,?)",
                    (r["run_id"], r["source_path"], r["source_kind"],
                     r["source_name"] if "source_name" in r.keys() else None,
                     r["level"], r["rule_version"], r["registry_version"], r["created_at"]))
        n_runs += 1
    ev_cols = [r["name"] for r in old.execute("PRAGMA table_info(events)").fetchall()]
    for e in old.execute("SELECT * FROM events ORDER BY event_id"):
        att = e["attempt"] if "attempt" in ev_cols else None
        if att is None:                      # v1·v2 — payload 안에 있으면 쓰고, 없으면 1회차
            try: att = int((json.loads(e["payload"]) or {}).get("attempt") or 1)
            except Exception: att = 1
        new.execute("INSERT INTO events(event_id, run_id, ts, kind, state, attempt, payload)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (e["event_id"], e["run_id"], e["ts"], e["kind"], e["state"], att, e["payload"]))
        n_ev += 1
    new.execute("INSERT OR REPLACE INTO ledger_meta(k, v, ts) VALUES ('migrated_from', ?, ?)",
                (str(src), now_iso()))
    new.commit(); old.close(); new.close()
    return {"from": str(src), "to": str(dst), "runs": n_runs, "events": n_ev,
            "schema_version": SCHEMA_VERSION,
            "버린_것": "runs.state (갱신될 수 없는 열)",
            "채운_것": "events.attempt (구버전은 payload 값 또는 1회차)", "run_ids": moved_ids}


def journal_mode(conn) -> str:
    """실제 저널 모드. 협상 결과는 ledger_meta 에 남으므로 그것을 정본으로 읽는다."""
    try:
        r = conn.execute("SELECT v FROM ledger_meta WHERE k='journal_mode'").fetchone()
        if r: return str(r["v"]).upper()
    except sqlite3.Error:
        pass
    return str(conn.execute("PRAGMA journal_mode").fetchone()[0]).upper()


def start_run(conn, run_id: str, source_path, *, source_kind="folder", source_name=None,
              level: int = 1, rule_version=None, registry_version=None) -> str:
    """run 1행(불변 식별 정보)을 등재한다. 이미 있으면 그대로 둔다 — 재투입이 같은 run_id 로
    이어지므로(2026-07-30 결정) 두 번째 시도에서 이 함수는 아무것도 바꾸지 않는다.
    상태는 여기 없다 — `append(..., state=...)` 로 events 에 쌓인다."""
    if conn.execute("SELECT 1 FROM runs WHERE run_id=?", (run_id,)).fetchone():
        return run_id
    conn.execute(
        "INSERT INTO runs(run_id, source_path, source_kind, source_name, level,"
        " rule_version, registry_version, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (run_id, str(source_path), source_kind, source_name or Path(str(source_path)).name,
         level, rule_version or RULE_VERSION, registry_version, now_iso()))
    conn.commit()
    return run_id


def last_attempt(conn, run_id: str) -> int:
    """기록된 마지막 시도 회차(없으면 0)."""
    r = conn.execute("SELECT COALESCE(MAX(attempt), 0) FROM events WHERE run_id=?",
                     (run_id,)).fetchone()
    return int(r[0])


def append(conn, run_id: str, kind: str, payload: dict, state: str | None = None,
           attempt: int = 1, chosen_by: str = "rule", variation: dict | None = None) -> int:
    """이벤트 1행 append. 이것이 원장에 쓰는 **유일한** 경로다.

    attempt   호출자가 **선언**한다 — 재투입인지 아는 것은 파이프라인을 굴리는 쪽이다.
    chosen_by 이 시도의 **방법**을 누가 골랐는가. 값을 만든 주체가 아니다(값은 항상 도구다).
    variation 이 시도에서 무엇을 바꿨는가. {kind, changed:[...], reason, prev_attempt}.
              이것이 있어야 다음 시도를 정하는 쪽이 "이미 해 본 것"을 원장에서 읽을 수 있다.
    """
    if not isinstance(attempt, int) or attempt < 1:
        raise ValueError(f"attempt 는 1 이상 정수여야 한다: {attempt!r}")
    if chosen_by not in CHOSEN_BY:
        raise ValueError(f"chosen_by 어휘 밖: {chosen_by} — {CHOSEN_BY}")
    if variation is not None:
        vk = variation.get("kind")
        if vk not in VARIATION_KINDS:
            raise ValueError(f"variation.kind 어휘 밖: {vk} — 허용 {VARIATION_KINDS}. "
                             f"이 밖의 변경은 재량이 아니라 규칙 위반이다(T-1·T-2·A-2)")
        if not variation.get("changed"):
            raise ValueError("variation.changed 가 비었다 — 무엇을 바꿨는지 적지 않으면 "
                             "다음 시도가 같은 것을 되풀이한다")
    if kind not in KINDS:
        raise ValueError(f"kind 어휘 밖: {kind} — 등재 {CONTRACT_KINDS} · 확장 후보 {EXT_KINDS}")
    if state is not None and state not in STATES:
        raise ValueError(f"state 어휘 밖: {state}")
    body = dict(payload or {})
    body["chosen_by"] = chosen_by
    if variation is not None:
        body["variation"] = variation
    if kind in EXT_KINDS:
        # 계약 미등재 kind 임을 원장 자신이 밝힌다 — 나중에 "왜 이 kind 가 있나"를 묻지 않게.
        body["_contract"] = "proposed — interfaces.yaml orch_ledger_contract 미등재(승인 대기)"
    cur = conn.execute(
        "INSERT INTO events(run_id, ts, kind, state, attempt, payload) VALUES (?,?,?,?,?,?)",
        (run_id, now_iso(), kind, state, attempt,
         json.dumps(body, ensure_ascii=False, sort_keys=False)))
    conn.commit()
    return int(cur.lastrowid)


def budget_basis(conn) -> dict:
    """예산을 **관측으로 정할 근거가 있는가**를 원장에서 센다.

    예산 값에도 산지가 있어야 한다. AGENTS.md B-1 이 직접 정한 것([정본])은 우리가 고를 것이
    아니고, 그 밖의 값([관측])은 실행 이력의 분포에서 나와야 한다. 표본이 없으면 정할 수 없고,
    정할 수 없으면 **그럴듯한 값을 넣는 대신 null 로 두어 기능을 닫는다**.
    이 함수는 "얼마로 하자"를 말하지 않는다 — 말할 수 있는지 없는지만 말한다.
    """
    # ★ 실행 이력과 손으로 넣은 이벤트를 **구분해서** 센다.
    #   도구 산출에서 등재된 이벤트만 payload 에 _source_file 을 갖는다 — 그것만 표본이다.
    #   구분하지 않으면 시연으로 넣은 값이 "근거가 있다"는 답을 만든다(실제로 그렇게 틀렸다).
    REAL = "json_valid(payload) AND payload LIKE '%_source_file%'"
    q = lambda s, *a: conn.execute(s, a).fetchone()[0]
    try:
        conn.execute(f"SELECT COUNT(*) FROM events WHERE {REAL}").fetchone()
    except sqlite3.OperationalError:                 # json_valid 미지원 빌드
        REAL = "payload LIKE '%_source_file%'"
    n_runs = q("SELECT COUNT(*) FROM runs")
    n_ev = q("SELECT COUNT(*) FROM events")
    n_real = q(f"SELECT COUNT(*) FROM events WHERE {REAL}")
    n_hold = q(f"SELECT COUNT(*) FROM events WHERE state='HOLD' AND {REAL}")
    n_done = q(f"SELECT COUNT(*) FROM events WHERE state='DONE' AND {REAL}")
    n_gate = q(f"SELECT COUNT(*) FROM events WHERE kind='gate' AND {REAL}")
    n_fail = q(f"SELECT COUNT(*) FROM events WHERE kind='failure' AND {REAL}")
    n_disc = q(f"SELECT COUNT(*) FROM events WHERE payload LIKE '%\"chosen_by\": \"llm\"%'"
               f" AND {REAL}")
    n_manual = n_ev - n_real
    finished = [r for r in conn.execute(
        f"SELECT run_id FROM events WHERE state='DONE' AND {REAL} GROUP BY run_id")]
    return {
        "표본": {"runs": n_runs, "events(전체)": n_ev, "events(실행 이력)": n_real,
                "events(손입력·시연)": n_manual, "완주(DONE) run": len(finished),
                "HOLD": n_hold, "gate": n_gate, "failure": n_fail, "재량(llm)": n_disc},
        "예산별_근거": [
            {"budget": "max_gate_rejects", "산지": "[정본] AGENTS.md B-1 (≤3회)",
             "정할_수_있는가": "해당 없음 — 정본이 정했다", "필요한_관측": None},
            {"budget": "max_retries", "산지": "[정본] AGENTS.md B-1 (≤3회)",
             "정할_수_있는가": "해당 없음 — 정본이 정했다", "필요한_관측": None},
            {"budget": "compute_minutes", "산지": "[정본] AGENTS.md B-1 (원천당 10분)",
             "정할_수_있는가": "해당 없음 — 정본이 정했다", "필요한_관측": None},
            {"budget": "discretion_after_holds", "산지": "[관측]",
             "정할_수_있는가": bool(n_hold > 0 and n_fail > 0),
             "필요한_관측": "HOLD 원인별 분포 + 같은 원인 반복률 — failure payload 의 type 별 집계",
             "현재": f"HOLD {n_hold} · failure {n_fail}"},
            {"budget": "max_attempts", "산지": "[관측]",
             "정할_수_있는가": bool(n_disc > 0),
             "필요한_관측": "재량 시도의 회차별 해결률(N회차에서 풀린 비율)",
             "현재": f"재량 이벤트 {n_disc} — 재량이 닫혀 있으면 영원히 0 이다(닭과 달걀)"},
        ],
        "주의": (f"손입력·시연 이벤트 {n_manual}건은 표본에서 제외했다(payload 에 _source_file 이 "
                f"없는 것). 표본을 만드는 것은 4단계 그래프의 end-to-end 실행이다."),
    }


def attempt_context(conn, run_id: str, *, budgets: dict | None = None) -> dict:
    """다음 시도를 정하려는 쪽(사람 또는 오케스트레이터)이 읽는 요약.

    **같은 run_id 로 재투입하는 정책의 존재 이유가 이 함수다** — 과거 시도를 그대로
    되풀이하지 않으려면 무엇을 어떻게 시도했고 어떻게 끝났는지를 읽을 수 있어야 한다.
    재량 개시 조건은 registry budgets 에서 온다(코드 하드코딩 금지).
    """
    b = budgets or {}
    after = b.get("discretion_after_holds")
    cap = b.get("max_attempts")
    by_att: dict[int, list] = {}
    for e in events(conn, run_id):
        by_att.setdefault(e["attempt"], []).append(e)

    prior = []
    for a in sorted(by_att):
        evs = by_att[a]
        def pick(key):
            return next((e["payload"].get(key) for e in evs if e["payload"].get(key)), None)
        states = [e["state"] for e in evs if e["state"]]
        # 한 회차 안에서도 단계마다 주체가 다를 수 있다 — 첫 값만 집으면 재량 시도가 가려진다.
        prior.append({
            "attempt": a,
            "chosen_by": sorted({e["payload"].get("chosen_by") for e in evs
                                 if e["payload"].get("chosen_by")}),
            "variation": [e["payload"]["variation"] for e in evs if e["payload"].get("variation")],
            "adapter": pick("adapter"),
            "contributing": pick("contributing"),
            "outcome": pick("outcome"),
            "final_state": states[-1] if states else None,
            "n_events": len(evs),
            "digests": {f"{e['kind']}:{e['payload'].get('stage') or '-'}": e["payload"].get("_digest")
                        for e in evs if e["payload"].get("_digest")},
        })
    n_hold = sum(1 for x in prior if x["final_state"] == "HOLD")
    last = max(by_att) if by_att else 0
    open_ = (after is not None and n_hold >= int(after))
    capped = (cap is not None and last >= int(cap))
    return {
        "run_id": run_id, "last_attempt": last, "n_hold": n_hold, "prior": prior,
        "budgets": {"discretion_after_holds": after, "max_attempts": cap},
        "discretion_open": bool(open_ and not capped),
        "사유": ("예산 미지정 — registry budgets.discretion_after_holds 가 없다" if after is None else
                f"시도 상한 도달({last}/{cap}) — 더 시도하지 않는다(HOLD)" if capped else
                f"HOLD {n_hold}회 ≥ {after} → 제한적 재량 개시" if open_ else
                f"HOLD {n_hold}회 < {after} → 규칙값으로만 진행"),
        "재량_범위": {"허용": list(VARIATION_KINDS),
                     "금지": "새 도구·파서 제작(T-1) · 등재 목록 밖 실행(T-2) · 설치(T-3) · "
                            "클래스 순서 변경·생략(A-2) · 값 생성(N-1) · 게이트 완화(N-2)"},
    }


def events(conn, run_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT event_id, ts, kind, state, attempt, payload FROM events"
        " WHERE run_id=? ORDER BY event_id", (run_id,)).fetchall()
    return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]


def current_state(conn, run_id: str) -> str | None:
    r = conn.execute("SELECT state FROM run_state WHERE run_id=?", (run_id,)).fetchone()
    return r["state"] if r else None


def runs(conn) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM run_state ORDER BY created_at, run_id").fetchall()]


# ── 기존 도구 산출물 → 이벤트 등재 ───────────────────────────────────────────
# 그래프(4단계)가 아직 없어도 1단계 도구의 산출을 원장에 넣을 수 있어야 한다 —
# "기록 없는 실행은 하지 않은 것"이라는 규칙이 그래프 완성을 기다려 주지 않는다.
def _fp_digest(fp) -> str | None:
    """원천 지문 전체를 넣으면 payload 가 커진다 → 정규 직렬화 해시로 동일성만 남긴다."""
    if not fp: return None
    from _common import canonical_hash
    return canonical_hash(fp.get("files") or {})


_INGEST = [
    # (파일, kind, state, payload 추출 함수)
    ("식별_결과.json", "routing", "ROUTE", lambda j: {
        "adapter": j.get("adapter"), "candidates": j.get("adapter_candidates"),
        "contributing": j.get("contributing_lanes"), "source_origin": j.get("source_origin"),
        "signals": j.get("signals"), "rule_version": j.get("rule_version"),
        "numeric_rules": j.get("numeric_rules"),
        # 같은 run_id 로 재투입하면 work/ 가 덮인다 → 그 시도가 본 바이트를 원장에 남긴다.
        "source_fingerprint": {
            "n_files": (j.get("source_fingerprint_before") or {}).get("n_files"),
            "digest": _fp_digest(j.get("source_fingerprint_before"))},
        "lanes": [{k: l.get(k) for k in ("adapter", "role", "produces")} for l in (j.get("lanes") or [])],
        "rejected": [{k: x.get(k) for k in ("adapter", "role", "reason")} for x in (j.get("rejected") or [])],
        "probe_report": j.get("probe_report"),
        "n_files": len(j.get("files") or []), "n_unreadable": len(j.get("unreadable") or []),
        "outcome": j.get("outcome")}),
    ("추출_결과.json", "stage", "EXTRACT", lambda j: {
        "stage": "추출", "geom_hash": j.get("geom_hash"), "rounding": j.get("rounding"),
        "n_dxf": len((j.get("geometry") or {}).get("dxf") or []),
        "n_cst": len((j.get("declared") or {}).get("cst") or []),
        "n_annotations": len(j.get("annotations") or []),
        "n_unreadable": len(j.get("unreadable") or []),
        "주의": [w for c in ((j.get("declared") or {}).get("cst") or []) for w in (c.get("주의") or [])]}),
    ("해석_결과.json", "verify", "VERIFY", lambda j: {
        "items": j.get("items"), "geom_hash": j.get("geom_hash"), "cached": j.get("cached", False),
        "product": j.get("product"), "registry_version": j.get("registry_version"),
        "verdict": j.get("verdict"), "reason": j.get("reason"),
        "numeric_rules": j.get("numeric_rules")}),
    ("렌더_결과.json", "stage", "VERIFY", lambda j: {
        "stage": "렌더", "artifacts": j.get("artifacts"), "n_previews": len(j.get("previews") or []),
        "failures": j.get("failures"), "notes": j.get("notes"),
        "beam_pattern": {k: (v.get("status") if isinstance(v, dict) else v)
                         for k, v in (j.get("beam_pattern") or {}).items()}}),
    ("게이트_판정.json", "gate", "COMPOSE", lambda j: {
        "pass": j.get("pass"), "violations": j.get("violations")}),
]


def ingest_run(conn, run_id: str, new_attempt: bool = False, chosen_by: str = "rule",
               variation: dict | None = None) -> dict:
    wd = work_dir(run_id, create=False)
    ident_p = wd / "식별_결과.json"
    if not ident_p.exists():
        raise FileNotFoundError(f"식별_결과.json 이 없다: {wd} — 먼저 tools/route.py 를 돌려라")
    ident = read_json(ident_p)
    src = ident.get("source") or {}
    interp = wd / "해석_결과.json"
    start_run(conn, run_id, src.get("path"), source_kind=src.get("kind") or "folder",
              source_name=src.get("name"), level=1,
              rule_version=ident.get("rule_version"),
              registry_version=(read_json(interp).get("registry_version") if interp.exists() else None))
    # 중복 판정은 **내용**으로 한다. 같은 run_id 로 재투입하는 정책에서 (kind, stage) 만 보면
    # 2차 시도의 새 산출을 "이미 등재됨"으로 버린다 — 시도가 원장에서 사라진다.
    # 산출이 그대로면 아무 일도 일어나지 않은 것이고, 달라졌으면 새 시도다.
    from _common import canonical_hash
    seen = {(e["kind"], e["payload"].get("stage"), e["payload"].get("_digest"))
            for e in events(conn, run_id)}

    plan = []
    for fname, kind, state, pick in _INGEST:
        f = wd / fname
        if not f.exists():
            plan.append((fname, kind, state, None, None, "산출 없음 — 해당 단계를 돌리지 않았다"))
            continue
        payload = pick(read_json(f))
        digest = canonical_hash(payload)          # attempt·_source_file 을 넣기 전의 내용만
        if (kind, payload.get("stage"), digest) in seen:
            plan.append((fname, kind, state, payload, digest,
                         "산출이 이전 등재와 동일 — 새 일이 일어나지 않았다"))
        else:
            plan.append((fname, kind, state, payload, digest, None))

    # 시도 회차는 **선언**받는다. --new-attempt 없이는 현재 회차에 이어 붙인다.
    prev = last_attempt(conn, run_id)
    attempt = (prev + 1) if new_attempt else max(1, prev)
    if new_attempt:
        # 재투입은 산출이 같아도 시도로 남아야 한다 — 내용 중복 판정을 이 회차에는 적용하지 않는다.
        plan = [(f, k, s, pl, dg, None if pl is not None else sk)
                for f, k, s, pl, dg, sk in plan]

    added, skipped = [], []
    for fname, kind, state, payload, digest, skip in plan:
        if skip is not None:
            skipped.append({"file": fname, "reason": skip})
            continue
        eid = append(conn, run_id, kind,
                     dict(payload, _digest=digest, _source_file=fname),
                     state=state, attempt=attempt, chosen_by=chosen_by,
                     variation=variation if kind == "routing" else None)
        added.append({"file": fname, "kind": kind, "state": state, "event_id": eid})
        seen.add((kind, payload.get("stage"), digest))
    return {"run_id": run_id, "attempt": attempt, "added": added, "skipped": skipped,
            "state": current_state(conn, run_id)}


# ── 자기 시험 ────────────────────────────────────────────────────────────────
def self_test(tmp_path=None) -> dict:
    """append-only 가 **실제로** 막히는지 시도해 본다. 주장 대신 시도가 근거다."""
    import tempfile
    checks, tmp = [], tmp_path or Path(tempfile.mkdtemp()) / "ledger_selftest.sqlite"

    def chk(name, fn, want_abort=False):
        try:
            fn(); ok = not want_abort; err = None
        except Exception as e:
            ok = want_abort; err = f"{type(e).__name__}: {e}"
        checks.append({"check": name, "pass": ok, "detail": err})

    import os
    os.environ["ORCH_LEDGER_DB"] = str(tmp)      # W-1 통과를 위해 원장 경로로 지정
    conn = open_ledger(tmp)

    checks.append({"check": f"저널 모드 협상 → {journal_mode(conn)}",
                   "pass": journal_mode(conn) in JOURNAL_PREFERENCE,
                   "detail": f"선호 {JOURNAL_PREFERENCE[0]} · 실제 {journal_mode(conn)}"})
    chk("스키마 생성 · runs/events/뷰/트리거", lambda: conn.execute(
        "SELECT name FROM sqlite_master WHERE name IN "
        "('runs','events','run_state','events_no_update','events_no_delete')").fetchall())
    checks.append({"check": "runs 에 state 열이 없다(구조가 불변식을 지킨다)",
                   "pass": "state" not in [r["name"] for r in
                                           conn.execute("PRAGMA table_info(runs)").fetchall()],
                   "detail": None})
    chk("run 등재", lambda: start_run(conn, "T1", "/src/x", source_name="x"))
    chk("run 중복 등재는 무해", lambda: start_run(conn, "T1", "/src/x"))
    chk("이벤트 append (routing)", lambda: append(conn, "T1", "routing", {"adapter": "dxf"}, "ROUTE"))
    chk("같은 payload 재append → 새 행", lambda: append(conn, "T1", "routing", {"adapter": "dxf"}, "ROUTE"))
    chk("stage 이벤트 append(정본 등재 kind)", lambda: append(conn, "T1", "stage", {"stage": "추출"}, "EXTRACT"))
    checks.append({"check": "확장 후보 kind 없음 — 전부 계약 등재",
                   "pass": EXT_KINDS == (), "detail": f"EXT_KINDS={EXT_KINDS}"})
    chk("어휘 밖 kind 거부", lambda: append(conn, "T1", "없는kind", {}), want_abort=True)
    chk("어휘 밖 state 거부", lambda: append(conn, "T1", "routing", {}, "없는상태"), want_abort=True)
    chk("없는 run_id 의 이벤트 거부(FK)", lambda: append(conn, "NOPE", "routing", {}), want_abort=True)
    chk("events UPDATE 거부(W-3)", lambda: conn.execute(
        "UPDATE events SET kind='gate' WHERE event_id=1"), want_abort=True)
    chk("events DELETE 거부(W-3)", lambda: conn.execute(
        "DELETE FROM events WHERE event_id=1"), want_abort=True)
    chk("runs UPDATE 거부(W-3)", lambda: conn.execute(
        "UPDATE runs SET state='DONE' WHERE run_id='T1'"), want_abort=True)
    chk("runs DELETE 거부(W-3)", lambda: conn.execute(
        "DELETE FROM runs WHERE run_id='T1'"), want_abort=True)

    # 이벤트가 없는 run 의 파생 상태는 WAIT (어휘의 시작값)
    start_run(conn, "T0", "/src/none", source_name="none")
    r0 = conn.execute("SELECT state, n_events FROM run_state WHERE run_id='T0'").fetchone()
    checks.append({"check": "이벤트 0건인 run 의 파생 상태 = WAIT",
                   "pass": r0["state"] == "WAIT" and r0["n_events"] == 0, "detail": None})

    append(conn, "T1", "failure", {"type": "permanent"}, "HOLD")
    # 재투입 — 산출이 같아도 회차는 오른다(선언받기 때문). 이것이 추론 방식과의 차이다.
    a2 = last_attempt(conn, "T1") + 1
    append(conn, "T1", "routing", {"adapter": "dxf"}, "ROUTE", attempt=a2)
    append(conn, "T1", "human", {"action": "approve", "decided_by": "self-test"}, "DONE", attempt=a2)
    r = conn.execute("SELECT state, n_events, n_attempts, n_hold FROM run_state"
                     " WHERE run_id='T1'").fetchone()
    checks.append({"check": "현재 상태는 마지막 이벤트에서 파생",
                   "pass": r["state"] == "DONE", "detail": f"state={r['state']} · n={r['n_events']}"})
    checks.append({"check": "재투입 시도 회차가 선언으로 오른다(같은 산출에도)",
                   "pass": r["n_attempts"] == 2 and r["n_hold"] == 1,
                   "detail": f"n_attempts={r['n_attempts']} · n_hold={r['n_hold']}"})
    chk("attempt 0 이하 거부", lambda: append(conn, "T1", "routing", {}, attempt=0), want_abort=True)
    chk("chosen_by 어휘 밖 거부", lambda: append(conn, "T1", "routing", {}, chosen_by="oracle"),
        want_abort=True)
    chk("variation.kind 어휘 밖 거부(재량의 닫힌 집합)",
        lambda: append(conn, "T1", "routing", {}, attempt=2, chosen_by="llm",
                       variation={"kind": "new_parser", "changed": ["x"]}), want_abort=True)
    chk("variation.changed 빈 값 거부(무엇을 바꿨는지 없으면 되풀이한다)",
        lambda: append(conn, "T1", "routing", {}, attempt=2, chosen_by="llm",
                       variation={"kind": "tool_args", "changed": []}), want_abort=True)
    # 예산 미지정이면 재량은 열리지 않는다 — 근거 없는 숫자를 넣지 않으면 기능이 닫힌다.
    _c0 = attempt_context(conn, "T1", budgets={"discretion_after_holds": None,
                                               "max_attempts": None})
    checks.append({"check": "예산 미지정 → 재량 닫힘(근거 없으면 기능이 닫힌다)",
                   "pass": not _c0["discretion_open"], "detail": _c0["사유"]})
    _bb = budget_basis(conn)
    checks.append({"check": "budget_basis 가 손입력을 표본에서 제외한다",
                   "pass": _bb["표본"]["events(실행 이력)"] == 0
                           and _bb["표본"]["events(손입력·시연)"] > 0,
                   "detail": f"실행 {_bb['표본']['events(실행 이력)']} · "
                             f"손입력 {_bb['표본']['events(손입력·시연)']}"})
    checks.append({"check": "표본 0이면 관측 예산을 '정할 수 없다'고 답한다",
                   "pass": all(x["정할_수_있는가"] is False for x in _bb["예산별_근거"]
                               if x["산지"] == "[관측]"),
                   "detail": None})
    chk("재량 시도 기록(llm + variation)",
        lambda: append(conn, "T1", "routing", {"adapter": "dxf"}, "ROUTE", attempt=2,
                       chosen_by="llm",
                       variation={"kind": "tool_args", "changed": ["min_len 5.0→3.0"],
                                  "reason": "패치 0건 — 세그먼트 하한이 높았을 가능성",
                                  "prev_attempt": 1}))
    _ctx = attempt_context(conn, "T1", budgets={"discretion_after_holds": 3, "max_attempts": 6})
    checks.append({"check": "attempt_context 가 '이미 해 본 것' 을 되짚는다",
                   "pass": any(x["variation"] for x in _ctx["prior"]) and
                           any("llm" in x["chosen_by"] for x in _ctx["prior"]),
                   "detail": f"prior {len(_ctx['prior'])}회차 · "
                             f"주체 {[x['chosen_by'] for x in _ctx['prior']]}"})
    _c2 = attempt_context(conn, "T1", budgets={"discretion_after_holds": 1, "max_attempts": 6})
    checks.append({"check": "재량 개시는 registry 예산이 정한다(코드 하드코딩 아님)",
                   "pass": _c2["discretion_open"] and not _ctx["discretion_open"],
                   "detail": f"after=3 → {_ctx['discretion_open']} · after=1 → {_c2['discretion_open']}"})
    _c3 = attempt_context(conn, "T1", budgets={"discretion_after_holds": 1, "max_attempts": 1})
    checks.append({"check": "시도 상한 도달 시 재량이 닫힌다",
                   "pass": not _c3["discretion_open"], "detail": _c3["사유"]})
    checks.append({"check": "events 에 attempt 열이 있다(json_extract 의존 없음)",
                   "pass": "attempt" in [x["name"] for x in
                                         conn.execute("PRAGMA table_info(events)").fetchall()],
                   "detail": None})
    checks.append({"check": "W-1 — 원장 밖 경로 쓰기 거부",
                   "pass": _rejects_outside(), "detail": None})

    # 구버전(v1) 원장을 만들어 거부되는지, migrate 로 넘어가는지 확인
    v1 = tmp.parent / "v1.sqlite"
    c1 = sqlite3.connect(str(v1))
    c1.executescript("""CREATE TABLE runs(run_id TEXT PRIMARY KEY, source_path TEXT, source_kind TEXT,
        source_name TEXT, level INTEGER, state TEXT, rule_version TEXT, registry_version TEXT,
        created_at TEXT);
        CREATE TABLE events(event_id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, ts TEXT,
        kind TEXT, state TEXT, payload TEXT);""")
    c1.execute("INSERT INTO runs VALUES('V1','/s','folder','s',1,'WAIT','rv','gv','2026-07-30T00:00:00+09:00')")
    c1.execute("INSERT INTO events VALUES(7,'V1','2026-07-30T00:00:01+09:00','routing','ROUTE',"
               "'{\"attempt\": 4}')")
    c1.commit(); c1.close()
    import os as _os
    _os.environ["ORCH_LEDGER_DB"] = str(v1)
    chk("구버전(v1) 원장 열기 거부", lambda: open_ledger(v1), want_abort=True)
    v2 = tmp.parent / "v1.sqlite.v2"
    _os.environ["ORCH_LEDGER_DB"] = str(v2)
    mig = {}
    chk("migrate 구버전 → 현재 스키마 (원본 보존)", lambda: mig.update(migrate(v1, v2)))
    checks.append({"check": "W-1 — 원장 폴더 밖 롤오버 대상 거부",
                   "pass": _rejects_ledger_outside(), "detail": None})
    _r2 = sqlite3.connect(str(v2)).execute("SELECT event_id, attempt FROM events").fetchone() \
          if v2.exists() else (None, None)
    checks.append({"check": "migrate 가 event_id 를 보존한다(원장 참조가 이벤트 번호로 이뤄진다)",
                   "pass": v1.exists() and v2.exists() and _r2[0] == 7,
                   "detail": f"runs={mig.get('runs')} · events={mig.get('events')}"})
    checks.append({"check": "migrate 가 payload 의 attempt 를 열로 옮긴다",
                   "pass": _r2[1] == 4, "detail": f"attempt={_r2[1]} (payload 에 4 였다)"})
    _os.environ["ORCH_LEDGER_DB"] = str(tmp)
    conn.close()
    n_fail = sum(1 for c in checks if not c["pass"])
    return {"db": str(tmp), "checks": checks, "n": len(checks), "n_fail": n_fail,
            "verdict": "PASS" if n_fail == 0 else "FAIL"}


def _rejects_ledger_outside() -> bool:
    import tempfile
    try:
        assert_writable(Path(tempfile.gettempdir()) / "elsewhere" / "x.sqlite", as_ledger=True)
        return False
    except PermissionError:
        return True


def _rejects_outside() -> bool:
    from _common import REPO
    try:
        assert_writable(REPO / "registry" / "products.yaml")
        return False
    except PermissionError:
        return True


# ── CLI ─────────────────────────────────────────────────────────────────────
def main(argv=None):
    ap = argparse.ArgumentParser(description="실행 원장 — append-only")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="스키마 생성")
    sub.add_parser("self-test", help="append-only 강제와 어휘 검사를 실제로 시도")
    sub.add_parser("runs", help="run 목록과 파생 현재 상태")
    g = sub.add_parser("ingest", help="work/<run_id>/*.json 을 이벤트로 등재")
    g.add_argument("--run-id", required=True)
    g.add_argument("--new-attempt", action="store_true",
                   help="재투입 — 산출이 같아도 새 시도 회차로 등재한다(회차는 선언받는다)")
    g.add_argument("--chosen-by", default="rule", choices=list(CHOSEN_BY),
                   help="이 시도의 방법을 누가 골랐는가(값을 만든 주체가 아니다)")
    g.add_argument("--variation", default=None,
                   help='재량으로 바꾼 것 JSON — {"kind":"tool_args","changed":[...],"reason":"..."}')
    m = sub.add_parser("migrate", help="구버전 원장을 새 파일로 옮긴다(원본 보존)")
    m.add_argument("--from", dest="src", required=True)
    m.add_argument("--to", dest="dst", required=True)
    sub.add_parser("budget-basis", help="예산을 관측으로 정할 근거가 있는가 — 있는지 없는지만 말한다")
    w = sub.add_parser("why", help="다음 시도를 정하기 위한 맥락 — 이미 해 본 것")
    w.add_argument("--run-id", required=True)
    w.add_argument("--product", default=None, help="예산을 읽을 registry 제품군")
    s = sub.add_parser("show", help="한 run 의 이벤트")
    s.add_argument("--run-id", required=True)
    s.add_argument("--full", action="store_true", help="payload 전문")
    a = ap.parse_args(argv)

    if a.cmd == "self-test":
        r = self_test()
        for c in r["checks"]:
            print(f"  {'○' if c['pass'] else '×'} {c['check']}"
                  + (f"  — {c['detail']}" if c["detail"] and not c["pass"] else ""))
        print(f"자기 시험: {r['n'] - r['n_fail']}/{r['n']} → {r['verdict']}  ({r['db']})")
        return 0 if r["n_fail"] == 0 else 1

    if a.cmd == "migrate":
        r = migrate(a.src, a.dst)
        print(f"옮김: {r['from']} → {r['to']}")
        print(f"  runs {r['runs']} · events {r['events']} (event_id 보존) → v{r['schema_version']}")
        print(f"  버린 것: {r['버린_것']} · 채운 것: {r['채운_것']}")
        print(f"  원본은 그대로 둔다 — 계약: 연 단위 롤오버, 삭제 없음")
        print(f"  이후: ORCH_LEDGER_DB 를 새 파일로 지정하라")
        return 0

    conn = open_ledger()
    if a.cmd == "init":
        print(f"원장 생성: {ledger_path()}")
        print(f"  저널 모드 {journal_mode(conn)} (선호 {JOURNAL_PREFERENCE[0]} — 저장소가 받아 준 값)")
        print(f"  kind 등재 {len(CONTRACT_KINDS)}종 · 확장 후보 {EXT_KINDS} · state {len(STATES)}종")
    elif a.cmd == "runs":
        rs = runs(conn)
        print(f"{'run_id':34} {'상태':8} {'이벤트':>5} {'시도':>4} {'HOLD':>5}  원천")
        for r in rs:
            print(f"{r['run_id']:34} {r['state']:8} {r['n_events']:>5} {r['n_attempts']:>4} "
                  f"{r['n_hold']:>5}  {r['source_name']}")
        print(f"총 {len(rs)} run")
    elif a.cmd == "ingest":
        r = ingest_run(conn, a.run_id, new_attempt=a.new_attempt, chosen_by=a.chosen_by,
                       variation=json.loads(a.variation) if a.variation else None)
        for x in r["added"]:
            print(f"  + #{x['event_id']:<4} {x['kind']:8} {x['state']:8} ← {x['file']}")
        for x in r["skipped"]:
            print(f"  · {x['file']:20} {x['reason']}")
        print(f"등재 {len(r['added'])}건 · 건너뜀 {len(r['skipped'])}건 · 시도 {r['attempt']}회차 "
              f"· 현재 상태 {r['state']}")
    elif a.cmd == "budget-basis":
        b = budget_basis(conn)
        print("표본:", " · ".join(f"{k} {v}" for k, v in b["표본"].items()))
        print("       (실행 이력 = 도구 산출에서 등재된 것. 손입력은 표본이 아니다)")
        print()
        for x in b["예산별_근거"]:
            can = x["정할_수_있는가"]
            mark = "—" if can == "해당 없음 — 정본이 정했다" else ("○" if can else "×")
            print(f"  {mark} {x['budget']:24} {x['산지']}")
            if x["필요한_관측"]:
                print(f"      필요한 관측: {x['필요한_관측']}")
                print(f"      현재:        {x['현재']}")
        print(f"\n{b['주의']}")
    elif a.cmd == "why":
        from _common import load_registry, resolve_product
        _, pdef = resolve_product(load_registry(), a.product)
        ctx = attempt_context(conn, a.run_id, budgets=pdef.get("budgets"))
        print(f"run {ctx['run_id']} · 마지막 시도 {ctx['last_attempt']}회차 · HOLD {ctx['n_hold']}회")
        print(f"재량: {'열림' if ctx['discretion_open'] else '닫힘'} — {ctx['사유']}")
        print(f"  허용 변경: {ctx['재량_범위']['허용']}")
        print(f"  금지: {ctx['재량_범위']['금지']}")
        print(f"\n이미 해 본 것:")
        for x in ctx["prior"]:
            print(f"  시도{x['attempt']} [{'/'.join(x['chosen_by']) or '-'}] 어댑터={x['adapter']} "
                  f"기여={x['contributing']} → {x['final_state']} ({x['outcome']})")
            for v in x["variation"]:
                print(f"        바꾼 것: {v.get('kind')} {v.get('changed')} — {v.get('reason')}")
    elif a.cmd == "show":
        for e in events(conn, a.run_id):
            body = (json.dumps(e["payload"], ensure_ascii=False, indent=2) if a.full
                    else json.dumps(e["payload"], ensure_ascii=False)[:130])
            print(f"#{e['event_id']:<4} {e['ts']}  시도{e['attempt']} {e['kind']:8} "
                  f"{str(e['state'] or '-'):8} {body}")
        print(f"현재 상태: {current_state(conn, a.run_id)}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
