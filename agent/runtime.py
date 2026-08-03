#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent/runtime.py — 최소 상태 머신 실행기 (stdlib 전용 · LLM 0콜)

왜 있나
    설계 정본이 노드를 `(state) -> state` **순수 함수**로 못박았다. 그러면 실행기는
    갈아끼울 수 있는 부품이 된다 — 그래프의 뜻은 노드와 엣지에 있지 실행기에 있지 않다.
    이 파일은 LangGraph 가 없는 환경(튜너 클라우드 — 패키지 저장소 차단)에서
    **전이·분기·반려 고리·체크포인트 왕복·interrupt** 를 시험하기 위한 대역이다.

    `agent/graph.py` 는 langgraph 가 import 되면 langgraph 를, 아니면 이것을 쓴다.
    **노드와 엣지 정의는 한 벌뿐이다** — 둘에 같은 것을 먹인다.

여기서 통과한 것이 보증하는 것과 보증하지 않는 것
    보증한다   — 노드 함수의 정확성 · 분기 라벨 · 반려 상한 · 원장 기록 · 상태 어휘
    보증하지 않는다 — LangGraph 자체의 동작. **이 대역을 통과해도 langgraph 검증이 아니다.**
    Spark/본체에서 `python tools/check_env.py --state` 가 별도로 확인한다.

구현 범위 (LangGraph API 의 부분집합 — 쓰는 것만)
    add_node · add_edge · add_conditional_edges · START/END ·
    compile(checkpointer, interrupt_before) · invoke · get_state · update_state
    쓰지 않는 것(병렬 분기 fan-out · Send · 리듀서 애노테이션)은 **구현하지 않는다** —
    있는 척하면 langgraph 로 옮길 때 조용히 다르게 동작한다.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import _common as C  # noqa: E402

START = "__start__"
END = "__end__"

MAX_STEPS = 200          # 무한 고리 방지. 반려 상한(3)보다 훨씬 크게 두어 진짜 버그만 걸린다


class GraphError(RuntimeError):
    pass


class SqliteCheckpointer:
    """thread_id 별 상태 스냅샷 + **분기**.

    되돌아가기만 되고 갈라지기가 안 되면, 되감는 순간 이전 시도가 지워진다.
    무엇을 이미 해 봤는지 읽을 수 없으면 다음 시도가 같은 것을 되풀이한다 — 그래서 분기다.

        label       되돌아갈 자리의 이름. seq 번호로는 사람이 못 찾는다
        branch_id   되감으면 새 분기. 옛 분기는 그대로 남는다
        parent_seq  어디서 갈라졌는지
        context     분기 시작점에 주입하는 추가 지시(반려 사유·사람 힌트)

    지위는 그대로다 — **재개용 파생물이고 정본은 원장이다.**
    """

    MAIN = "main"

    def __init__(self, path=None):
        p = Path(path or C.checkpoint_path())
        p.parent.mkdir(parents=True, exist_ok=True)
        C.assert_writable(p)
        self.path = p
        self.conn = sqlite3.connect(str(p), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._migrate()

    # ── 스키마 ──────────────────────────────────────────────────────────────
    DDL = """
        CREATE TABLE IF NOT EXISTS checkpoints (
            thread_id  TEXT NOT NULL,
            branch_id  TEXT NOT NULL DEFAULT 'main',
            seq        INTEGER NOT NULL,
            parent_seq INTEGER,
            label      TEXT,
            ts         TEXT NOT NULL,
            node       TEXT,
            next_node  TEXT,
            state      TEXT NOT NULL,
            context    TEXT,
            PRIMARY KEY (thread_id, branch_id, seq));
    """

    def _migrate(self):
        """분기 스키마로 옮긴다.

        옛 표의 기본키는 `(thread_id, seq)` 라 분기가 들어갈 수 없다. ALTER TABLE 로는
        기본키를 못 바꾸므로 **새 표로 옮긴다** — 체크포인트는 재개용 파생물이지만
        진행 중인 run 이 있을 수 있어 **버리지 않고 복사**한다.
        """
        cur = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='checkpoints'").fetchone()
        if not cur:
            self.conn.executescript(self.DDL)
        else:
            info = list(self.conn.execute("PRAGMA table_info(checkpoints)"))
            cols = {r[1] for r in info}
            pk = [r[1] for r in sorted((r for r in info if r[5]), key=lambda r: r[5])]
            if pk != ["thread_id", "branch_id", "seq"]:
                self.conn.executescript("""
                    ALTER TABLE checkpoints RENAME TO checkpoints_old;
                """ + self.DDL)
                # 옛 표에 이미 있던 분기 열도 함께 옮긴다 — 빠뜨리면 savepoint 가 사라진다
                keep = [c for c in ("thread_id", "branch_id", "seq", "parent_seq", "label",
                                    "ts", "node", "next_node", "state", "context")
                        if c in cols]
                self.conn.execute(
                    f"INSERT OR IGNORE INTO checkpoints({','.join(keep)}) "
                    f"SELECT {','.join(keep)} FROM checkpoints_old")
                self.conn.execute("DROP TABLE checkpoints_old")
            else:
                for col, decl in (("parent_seq", "INTEGER"), ("label", "TEXT"),
                                  ("context", "TEXT")):
                    if col not in cols:
                        self.conn.execute(
                            f"ALTER TABLE checkpoints ADD COLUMN {col} {decl}")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_cp_label ON checkpoints(thread_id, label)")
        self.conn.commit()

    # ── 현재 분기 ───────────────────────────────────────────────────────────
    def current_branch(self, thread_id: str) -> str:
        """활성 분기 = **가장 나중에 쓴** 행의 분기.

        수리: 앞선 구현은 `ts DESC, seq DESC` 로 골랐다. ts 는 초 단위라 되감기 직후
        옛 분기(seq 가 큼)와 새 분기(seq 1)가 같은 초에 들어가면 옛 분기가 이겼다 —
        되감아도 새 분기로 넘어가지 않았다. 삽입 순서(rowid)가 사실이다.
        """
        r = self.conn.execute(
            "SELECT branch_id FROM checkpoints WHERE thread_id=? ORDER BY rowid DESC LIMIT 1",
            (thread_id,)).fetchone()
        return r["branch_id"] if r else self.MAIN

    def put(self, thread_id: str, node: str | None, next_node: str | None, state: dict,
            *, label: str | None = None, branch_id: str | None = None,
            parent_seq: int | None = None, context: str | None = None):
        from datetime import datetime, timezone
        branch_id = branch_id or self.current_branch(thread_id)
        seq = (self.conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 s FROM checkpoints WHERE thread_id=? AND branch_id=?",
            (thread_id, branch_id)).fetchone()["s"])
        self.conn.execute(
            """INSERT INTO checkpoints(thread_id, branch_id, seq, parent_seq, label, ts,
                                       node, next_node, state, context)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (thread_id, branch_id, seq, parent_seq, label,
             datetime.now(timezone.utc).isoformat(timespec="seconds"),
             node, next_node, json.dumps(state, ensure_ascii=False, default=str), context))
        self.conn.commit()

    def get(self, thread_id: str, branch_id: str | None = None):
        branch_id = branch_id or self.current_branch(thread_id)
        r = self.conn.execute(
            "SELECT * FROM checkpoints WHERE thread_id=? AND branch_id=? ORDER BY seq DESC LIMIT 1",
            (thread_id, branch_id)).fetchone()
        return dict(r) if r else None

    def history(self, thread_id: str, branch_id: str | None = None) -> list[dict]:
        if branch_id:
            return [dict(r) for r in self.conn.execute(
                "SELECT * FROM checkpoints WHERE thread_id=? AND branch_id=? ORDER BY seq",
                (thread_id, branch_id))]
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM checkpoints WHERE thread_id=? ORDER BY ts, seq", (thread_id,))]

    def branches(self, thread_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            """SELECT branch_id, COUNT(*) n, MIN(parent_seq) parent_seq,
                      MAX(ts) last_ts, MAX(context) context
               FROM checkpoints WHERE thread_id=? GROUP BY branch_id ORDER BY MIN(ts)""",
            (thread_id,))]

    def savepoints(self, thread_id: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute(
            """SELECT branch_id, seq, label, node, ts FROM checkpoints
               WHERE thread_id=? AND label IS NOT NULL ORDER BY ts""", (thread_id,))]

    def rewind(self, thread_id: str, *, label: str | None = None, seq: int | None = None,
               branch_id: str | None = None, context: str | None = None,
               patch: dict | None = None) -> dict:
        """savepoint 로 되감아 **새 분기**를 연다. 옛 분기는 지우지 않는다.

        `context` 는 새 분기의 시작점에 실린다 — 다음 시도가 이것을 읽고 달라진다.
        """
        src = branch_id or self.current_branch(thread_id)
        if label:
            r = self.conn.execute(
                """SELECT * FROM checkpoints WHERE thread_id=? AND label=?
                   ORDER BY ts DESC LIMIT 1""", (thread_id, label)).fetchone()
        elif seq is not None:
            r = self.conn.execute(
                "SELECT * FROM checkpoints WHERE thread_id=? AND branch_id=? AND seq=?",
                (thread_id, src, seq)).fetchone()
        else:
            raise GraphError("label 또는 seq 중 하나는 있어야 한다 — 어디로 되감는지 말하지 않았다")
        if not r:
            raise GraphError(f"되감을 자리가 없다: thread={thread_id} label={label} seq={seq}")

        base = dict(r)
        n = len({b["branch_id"] for b in self.branches(thread_id)})
        new_branch = f"b{n}"
        st = json.loads(base["state"])
        if patch:
            st.update(patch)

        # 같은 자리에서 이미 갈라져 나간 분기들의 사유를 **모두** 싣는다.
        # 마지막 사유만 주면 LLM 이 두 번째로 시도했던 것을 세 번째에 되풀이한다 —
        # "무엇을 이미 해 봤는지 읽을 수 있어야 다음 시도가 달라진다"가 이 절의 존재 이유다.
        prior = [x["context"] for x in self.conn.execute(
            """SELECT DISTINCT context FROM checkpoints
               WHERE thread_id=? AND parent_seq=? AND context IS NOT NULL
               ORDER BY rowid""", (thread_id, base["seq"]))]
        prior = (st.get("rewind_context") or []) + prior
        if context:
            prior = prior + [context]
        st["rewind_context"] = list(dict.fromkeys(prior))
        st["branch_id"] = new_branch
        self.put(thread_id, base["node"], base["next_node"], st,
                 label=f"{base['label'] or base['node']}→{new_branch}",
                 branch_id=new_branch, parent_seq=base["seq"], context=context)
        return {"thread_id": thread_id, "from_branch": base["branch_id"],
                "from_seq": base["seq"], "from_label": base["label"],
                "branch_id": new_branch, "next_node": base["next_node"],
                "context": context}

    def close(self):
        self.conn.close()


class Snapshot:
    __slots__ = ("values", "next", "seq", "ts", "branch_id", "label")

    def __init__(self, values, nxt, seq, ts, branch_id="main", label=None):
        self.values, self.next, self.seq, self.ts = values, nxt, seq, ts
        self.branch_id, self.label = branch_id, label

    def __repr__(self):
        return (f"Snapshot(branch={self.branch_id}, next={self.next}, "
                f"state={self.values.get('state')}, seq={self.seq})")


class StateGraph:
    def __init__(self, schema=None):
        self.schema = schema
        self.nodes: dict = {}
        self.edges: dict = {}            # name -> 다음 노드 이름 (고정 엣지)
        self.branches: dict = {}         # name -> (router, mapping)
        self.entry: str | None = None

    def add_node(self, name, fn):
        if name in (START, END):
            raise GraphError(f"예약된 이름: {name}")
        self.nodes[name] = fn

    def add_edge(self, a, b):
        if a == START:
            self.entry = b
            return
        self.edges[a] = b

    def add_conditional_edges(self, name, router, mapping: dict):
        self.branches[name] = (router, mapping)

    def compile(self, checkpointer=None, interrupt_before=(), savepoints_before=()):
        missing = [n for n in list(self.edges) + list(self.branches) if n not in self.nodes]
        if missing:
            raise GraphError(f"정의되지 않은 노드에 엣지가 걸렸다: {missing}")
        for n, (_, m) in self.branches.items():
            bad = [v for v in m.values() if v not in self.nodes and v != END]
            if bad:
                raise GraphError(f"{n} 분기가 없는 노드를 가리킨다: {bad}")
        if self.entry is None:
            raise GraphError("START 엣지가 없다")
        return CompiledGraph(self, checkpointer, tuple(interrupt_before),
                             tuple(savepoints_before))


class CompiledGraph:
    def __init__(self, g: StateGraph, checkpointer, interrupt_before, savepoints_before=()):
        self.g, self.cp, self.interrupt_before = g, checkpointer, interrupt_before
        self.savepoints_before = tuple(savepoints_before)

    # ── 내부 ────────────────────────────────────────────────────────────────
    def _thread(self, config) -> str:
        try:
            return config["configurable"]["thread_id"]
        except Exception:
            raise GraphError("config 에 configurable.thread_id 가 없다 (thread_id = run_id)")

    def _next_of(self, node: str, state: dict) -> str:
        if node in self.g.branches:
            router, mapping = self.g.branches[node]
            label = router(state)
            if not isinstance(label, str):
                raise GraphError(f"{node} 분기 함수는 문자열 라벨만 반환해야 한다: {label!r}")
            if label not in mapping:
                raise GraphError(f"{node} 분기 라벨 '{label}' 이 매핑에 없다: {sorted(mapping)}")
            return mapping[label]
        return self.g.edges.get(node, END)

    def _load(self, thread_id):
        rec = self.cp.get(thread_id) if self.cp else None
        if not rec:
            return None, None
        return json.loads(rec["state"]), rec["next_node"]

    # ── 공개 API ────────────────────────────────────────────────────────────
    def invoke(self, state: dict | None, config: dict) -> dict:
        """state=None 이면 체크포인트에서 **재개**한다(LangGraph 와 같은 규약)."""
        tid = self._thread(config)
        if state is None:
            state, nxt = self._load(tid)
            if state is None:
                raise GraphError(f"재개할 체크포인트가 없다: thread_id={tid}")
            if nxt in (None, END):
                return state
            node = nxt
            resuming = True
        else:
            state = dict(state)
            node = self.g.entry
            resuming = False
            if self.cp:
                self.cp.put(tid, None, node, state)

        steps = 0
        while node != END:
            if node in self.interrupt_before and not resuming:
                if self.cp:
                    self.cp.put(tid, None, node, state)
                return state                      # 사람 개입 대기 — 노드를 실행하지 않고 멈춘다
            resuming = False
            steps += 1
            if steps > MAX_STEPS:
                raise GraphError(f"{MAX_STEPS} 스텝 초과 — 고리가 닫히지 않는다 (마지막 노드 {node})")
            fn = self.g.nodes[node]
            if node in self.savepoints_before:
                self.cp and self.cp.put(tid, None, node, state, label=f"{node}_전")
            patch = fn(state)
            if patch:
                if not isinstance(patch, dict):
                    raise GraphError(f"노드 {node} 가 dict 가 아닌 것을 반환했다: {type(patch)}")
                state.update(patch)
            nxt = self._next_of(node, state)
            if self.cp:
                self.cp.put(tid, node, nxt, state)
            node = nxt
        return state

    def get_state(self, config) -> Snapshot | None:
        rec = self.cp.get(self._thread(config)) if self.cp else None
        if not rec:
            return None
        nxt = rec["next_node"]
        return Snapshot(json.loads(rec["state"]),
                        () if nxt in (None, END) else (nxt,), rec["seq"], rec["ts"],
                        rec.get("branch_id", "main"), rec.get("label"))

    def update_state(self, config, values: dict):
        """사람 개입 페이로드 주입. 다음 노드는 바꾸지 않는다 — 순서 변경 권한은 없다(A-2)."""
        tid = self._thread(config)
        rec = self.cp.get(tid)
        if not rec:
            raise GraphError(f"갱신할 체크포인트가 없다: {tid}")
        st = json.loads(rec["state"])
        st.update(values)
        self.cp.put(tid, rec["node"], rec["next_node"], st)
        return st
