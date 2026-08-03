#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mcp_server/api.py — MCP 도구의 **알맹이**. SDK 를 import 하지 않는다.

왜 SDK 와 떼어 두나
    MCP 프로토콜은 아직 움직인다. 도구의 뜻(무엇을 받아 무엇을 주나)이 프로토콜 라이브러리에
    묶이면, SDK 가 판을 올릴 때마다 도구를 다시 짜야 한다. 여기에는 **순수 함수만** 두고
    `server.py` 가 배선만 한다 — SDK 를 갈아끼워도, stdlib 로 직접 짜도 이 파일은 그대로다.

왜 이 서버가 LLM 을 부르지 않나
    이 파이프라인의 LLM 이음매는 **딱 하나**다 — 문서의 서술 슬롯(그리고 식별 실패 시
    구제 경로). 나머지 전부가 LLM 0콜이다. 그 경계를 그대로 MCP 경계로 쓴다:

        서버  = 결정론 전부 (값 · 그림 · 골격 · 게이트 · 선언)
        호스트 LLM = `PROSE` 마커 사이만

    그래서 **모델을 갈아도 문서의 수치가 흔들리지 않는다.** 이것이 이 서버의 존재 이유다.

반환 규약
    모든 도구는 `dict` 를 돌려준다. 실패도 예외가 아니라 `{"ok": False, "why": ...}` 다 —
    MCP 클라이언트에게 스택트레이스는 쓸모가 없고, **무엇을 하면 되는지**가 필요하다.
    긴 산출(골격 · 치환본)은 문자열로 그대로 싣는다. 파일 경로만 주면 원격에서 못 읽는다.

쓰기 범위 (W-1)
    work/ · out/ · 원장 · 체크포인트 · registry/declared/ 뿐이다.
    원천 폴더와 `registry/products.yaml` 은 **읽기만** 한다.
"""
from __future__ import annotations

import os
import sys
import traceback
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _resolve_repo() -> Path:
    """`tools/` 와 `agent/` 가 어디 있는지 찾는다. **못 찾으면 사유를 말하고 멈춘다.**

    왜 이렇게까지 하나 — 못 찾았을 때 파이썬이 내는 것은 `No module named '_common'` 이다.
    그 줄만 보고는 무엇을 해야 할지 알 수 없다(실제로 걸렸다). 여기서 **무엇이 없고
    무엇을 하면 되는지**까지 말한다. `ORCH_REPO` 로 직접 지정할 수도 있다.
    """
    cands = []
    env = os.environ.get("ORCH_REPO", "").strip()
    if env:
        cands.append(("ORCH_REPO", Path(env).expanduser().resolve()))
    cands += [("mcp_server 의 상위", _HERE.parent),
              ("현재 작업 폴더", Path.cwd().resolve()),
              ("현재 작업 폴더의 상위", Path.cwd().resolve().parent)]
    tried = []
    for why, root in cands:
        tried.append(f"    {why:<20} {root}")
        if (root / "tools" / "_common.py").is_file():
            return root
    raise RuntimeError(
        "저장소를 찾지 못했다 — `tools/_common.py` 가 있는 폴더가 있어야 한다.\n"
        "  이 서버는 `mcp_server/` 만으로는 돌지 않는다. 도구 본체(`tools/` · `agent/` ·\n"
        "  `registry/`)와 **같은 저장소 안에** 있어야 한다.\n\n"
        "  찾아본 곳:\n" + "\n".join(tried) + "\n\n"
        "  고치는 법 — 둘 중 하나\n"
        "    (a) 저장소 루트에 mcp_server 를 두어 <루트>/tools 와 <루트>/mcp_server 가 나란히 있게 한다\n"
        "    (b) 환경변수로 알려 준다:  set ORCH_REPO=C:\\path\\to\\repo   (Windows)\n"
        "                              export ORCH_REPO=/path/to/repo    (Linux · macOS)\n"
        "  확인:  python -c \"import pathlib,os;print(pathlib.Path(os.environ.get('ORCH_REPO','.'))/'tools'/'_common.py')\"")


_REPO = _resolve_repo()
for _p in (str(_REPO / "tools"), str(_REPO / "agent")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import _common as C      # noqa: E402
import guide as GUIDE_MOD  # noqa: E402  (같은 폴더)

MAX_TEXT = 200_000       # 한 응답에 싣는 문자열 상한 — 넘으면 자르고 그 사실을 밝힌다


def _err(why: str, **extra) -> dict:
    return {"ok": False, "why": why, **extra}


def _guard(fn):
    """예외를 **사유**로 바꾼다. LLM 은 예외를 못 고치지만 사유는 고칠 수 있다.

    ★ 원래 시그니처를 그대로 물려준다. 안 물려주면 `(*a, **kw)` 가 되고, SDK 가 그것으로
      도구 스키마를 만들어 **클라이언트 LLM 이 인자 이름을 모르게 된다** — 도구가 있는데
      못 부르는 상태다. 배선 점검(`server.py --list`)에서 이것이 먼저 드러난다.
    """
    import functools
    import inspect as _i

    @functools.wraps(fn)
    def wrapped(*a, **kw):
        try:
            r = fn(*a, **kw)
            if isinstance(r, dict):
                r.setdefault("ok", True)
            return r
        except FileNotFoundError as e:
            return _err(f"{e}", kind="not_found",
                        다음="run_pipeline 을 먼저 돌렸는지, run_id 가 맞는지 확인한다")
        except C.LedgerAmbiguous as e:
            return _err(f"{e}", kind="ledger_ambiguous",
                        다음="orch_status() 의 원장 후보를 보고 ORCH_LEDGER_DB 를 지정한다")
        except Exception as e:
            return _err(f"{type(e).__name__}: {e}", kind="error",
                        trace=traceback.format_exc(limit=3))
    wrapped.__signature__ = _i.signature(fn)
    return wrapped


def _clip(s: str) -> tuple[str, bool]:
    s = s or ""
    return (s[:MAX_TEXT], True) if len(s) > MAX_TEXT else (s, False)


# ══ 조회 ═══════════════════════════════════════════════════════════════════

@_guard
def orch_status() -> dict:
    """환경이 성립하는가 · 원장 정본이 하나인가 · 어떤 run 이 있는가.

    **첫 도구다.** 원장이 여럿이면 여기서 멈춘다 — 도구가 정본을 고르지 않는다(A-1).
    """
    import dbview as DV
    import docspec as DS
    import roles as R
    led = DV.ledger_status()
    spec = DS.load()
    runs = []
    wd = C.data_dir() / "work"
    if wd.is_dir():
        for p in sorted(wd.iterdir()):
            if not p.is_dir():
                continue
            runs.append({"run_id": p.name,
                         "has": sorted(f.name for f in p.glob("*.json"))[:8],
                         "골격": (p / "골격.md").exists(),
                         "초안": (p / "초안.md").exists()})
    vol = C.data_dir_is_volatile()
    return {
        "data_root": str(C.data_dir()),
        # ★ 플러그인으로 설치하면 데이터가 **지워질 자리**에 쌓일 수 있다. 먼저 말한다.
        "data_root_경고": vol if vol.get("volatile") else "",
        "rule_version": C.effective_rule_version(),
        "ledger": {"ok": led["ok"], "chosen": led["chosen"], "by": led["by"],
                   "n_candidates": led["n"], "why": led["why"],
                   "candidates": [c["path"] for c in led["candidates"]]},
        "document_spec": {"version": spec.get("spec_version"),
                          "n_sections": len(spec["sections"])},
        "role_vocab_size": len(R.ROLES),
        "n_runs": len(runs),
        "runs": runs[-40:],
        "llm": "이 서버는 LLM 을 부르지 않는다 — 서술은 호스트 모델이 쓴다",
        "ok": led["ok"] and not vol.get("volatile"),
    }


@_guard
def discover_sources(path: str) -> dict:
    """폴더 안에 안테나가 몇 개인가. **개수를 확정하지 않는다** — 후보를 낸다."""
    import discover as DISC
    d = DISC.scan(path)
    return {"source": d["source"], "n_entries": d["n_entries"],
            "entries": [{"entry_id": e["entry_id"], "kind": e["kind"], "path": e["path"],
                         "project_tag": e.get("project_tag"),
                         "derived_from": e.get("derived_from"),
                         "link_candidates": e.get("link_candidates") or []}
                        for e in d["entries"]],
            "unassigned": d["unassigned"],
            "규율": "신호는 경계 후보를 만들 뿐이다. 경계가 안테나 경계인지는 사람이 정한다(A-1)"}


def _stale(run_id: str) -> dict:
    """이 run 의 해석이 지금 레지스트리와 어긋나는가 — **선언 이후 안 돌린 상태**.

    호스트 LLM 은 도구를 자유로운 순서로 부른다. `declare_set` 으로 값을 넣고
    `run_pipeline` 없이 `document_brief` 를 부르면 **낡은 카탈로그**를 받는다.
    그러면 "말하면 채워진다"고 해놓고 안 채워지고, 사람은 자기가 말한 값이 왜 없는지 모른다.

    ★ 파일 시각으로 판정하지 않는다(D-28). 해석이 저장해 둔 요구 묶음과 지금 레지스트리에서
      다시 뽑은 요구 묶음을 **내용으로** 대조한다.
    """
    try:
        import verify_api as V
        return V.stale_requirements(run_id)
    except Exception as e:
        return {"stale": False, "changed": [], "why": f"낡음을 판정하지 못했다: {e}"}


@_guard
def run_report(run_id: str) -> dict:
    """한 run 이 무엇을 냈나 · **무엇이 비었나**. 문서를 쓰기 전에 읽는다."""
    import catalog as CT
    work = C.work_dir(run_id, create=False)
    if not work.is_dir():
        raise FileNotFoundError(f"run 이 없다: {run_id}")

    def _j(name):
        p = work / name
        return C.read_json(p) if p.exists() else {}

    ident, ext, ver = _j("식별_결과.json"), _j("추출_결과.json"), _j("해석_결과.json")
    figs = _j("그림_결과.json")
    cat = CT.load(run_id, work) if (work / CT.CATALOG_NAME).exists() else {}
    items = ver.get("items") or []
    return {
        "run_id": run_id,
        "source": (ident.get("source") or {}).get("name"),
        "adapter": ident.get("adapter"),
        "contributing_lanes": ident.get("contributing_lanes") or [],
        "n_files": len(ident.get("files") or []),
        "unreadable": [u.get("rel") or u.get("name") for u in (ident.get("unreadable") or [])],
        "product": ver.get("product"),
        "verdict": ver.get("verdict"),
        "n_checks": len(items),
        "n_mismatch": sum(1 for i in items if i.get("pass") is False),
        "n_unjudged": sum(1 for i in items if i.get("pass") is None),
        "unjudged": [{"check": i.get("check"), "reason": i.get("reason")}
                     for i in items if i.get("pass") is None][:20],
        "figures": [{"key": f.get("key"), "role": f.get("role"),
                     "path": (f.get("figure") or {}).get("path")}
                    for f in (figs.get("entries") or [])],
        "n_catalog_entries": cat.get("n_entries", 0),
        "unmapped_keys": (cat.get("unmapped_keys") or [])[:20],
        "artifacts": sorted(p.name for p in work.iterdir() if p.is_file()),
        "stale": _stale(run_id),
    }


@_guard
def catalog_lookup(run_id: str, query: str = "", role: str = "", limit: int = 60) -> dict:
    """문서에 쓸 수 있는 값을 찾는다. **여기 없는 값은 문서에 못 쓴다.**

    `query` 는 키·이름에 대한 부분 일치, `role` 은 역할 정확 일치.
    """
    import catalog as CT
    cat = CT.load(run_id)
    out = []
    for k, e in cat["entries"].items():
        if role and e.get("role") != role:
            continue
        if query and query not in k and query not in str(e.get("label", "")):
            continue
        out.append({"key": k, "value": e.get("render_with_unit"), "label": e.get("label"),
                    "role": e.get("role"), "source": e.get("source"),
                    "formula": e.get("formula"), "판정": e.get("판정"),
                    "empty": not e.get("render_with_unit"), "reason": e.get("reason", "")})
    return {"run_id": run_id, "n_total": len(cat["entries"]), "n_matched": len(out),
            "entries": out[:limit], "truncated": len(out) > limit,
            "ref_syntax": cat.get("ref_syntax")}


ASSET_VIEWS = ("status", "coverage", "anomalies", "compare", "links")


@_guard
def asset_query(view: str = "status", role: str = "", limit: int = 50) -> dict:
    """자산 DB 조회 — 자산화 현황 · 피복률 · 이상 징후 · 역할별 나란히 · 연결.

    ★ 자산 DB 는 **정본이 아니다**(work/ 에서 rebuild 가능한 파생물). 어긋나면 원장이 옳다.
      `compare` 는 여러 안테나의 **같은 역할** 값을 나란히 세운다 — 축이 뒤집힌 값이
      여기서 드러난다(한 안테나만 보면 둘 다 그럴듯한 숫자다).
    """
    import assets as A
    if view not in ASSET_VIEWS:
        return _err(f"모르는 조회: {view!r} — 쓸 수 있는 것 {ASSET_VIEWS}")
    conn = A.open_db(create=False)
    try:
        if view == "anomalies":
            return {"view": view, "result": A.anomalies(conn),
                    "규율": "이상 징후는 판정이 아니라 **사람이 볼 목록**이다"}
        if view == "coverage":
            return {"view": view, "result": A.coverage(conn)}
        if view == "links":
            return {"view": view, "rows": A.links(conn)[:limit]}
        if view == "compare":
            if not role:
                return _err("compare 에는 role 이 필요하다 — 어휘는 리소스 orch://roles")
            return {"view": view, "role": role, "rows": A.compare(conn, role)[:limit]}
        return {"view": "status", "rows": A.status(conn)[:limit],
                "규율": "자산 DB 는 파생물이다 — 정본은 원장과 work/"}
    finally:
        conn.close()


@_guard
def ledger_events(run_id: str = "", limit: int = 50) -> dict:
    """실행 기록. **기록 없는 실행은 하지 않은 것이다**(B-3)."""
    import ledger as L
    conn = L.open_ledger(C.ledger_path(), create=False)
    try:
        if run_id:
            evs = L.events(conn, run_id)
            return {"run_id": run_id, "n": len(evs), "events": evs[-limit:],
                    "state": L.current_state(conn, run_id)}
        rs = L.runs(conn)
        return {"n_runs": len(rs), "runs": rs[-limit:]}
    finally:
        conn.close()


# ══ 실행 ═══════════════════════════════════════════════════════════════════

@_guard
def run_pipeline(source: str, product: str = "", run_prefix: str = "",
                 entry: str = "", composer: str = "skeleton") -> dict:
    """원천 폴더 → 식별 · 추출 · 해석 · 렌더 · 그림 · 카탈로그 · 골격.

    `composer="skeleton"` 이 기본이다 — **서버는 문서를 쓰지 않는다.** 골격까지만 만들고
    나머지는 호스트 LLM 이 `document_brief` → `submit_document` 로 채운다.

    폴더에 안테나가 여럿이면 **각각 run 이 된다**(A-2). 하나만 돌리려면 `entry` 를 준다.
    """
    src = Path(source).expanduser()
    if not src.exists():
        return _err(f"원천 경로가 없다: {src}", kind="not_found")
    import graph as G
    r = G.run_source(str(src), prefix=run_prefix or None, product=product or None,
                     only=entry or None, composer=composer)
    return {"source": r["source"], "n_entries": r["n_entries"], "runs": r["runs"],
            "unassigned": r["unassigned"],
            "다음": "run_report(run_id) 로 무엇이 비었는지 본 뒤 document_brief(run_id)"}


@_guard
def compare_revisions(run_a: str, run_b: str) -> dict:
    """두 run 의 형상이 어떤 관계인가 — 배율 · 폭 튜닝 · 국부 수정 · 다른 배열.

    ★ **순서를 기하가 말하지 못한다**(I-M). 어느 쪽이 먼저인지는 판정하지 않는다.
    """
    import revision as RV
    return RV.compare(run_a, run_b)


@_guard
def crosscheck(run_id: str) -> dict:
    """**독립적으로 선언된** 두 값이 같은가. 같은 산지끼리 대조하면 아무것도 검증되지 않는다."""
    import crosscheck as CC
    return CC.check(run_id)


@_guard
def relate_entries(source: str) -> dict:
    """폴더 안 자산들이 서로 무슨 관계인가 — 판올림 · 동거 · 파생 · 무관.

    `derived` 만 자동 확정이다(근거가 CST 의 **선언**이다). 나머지는 제안이고,
    신호가 엇갈리면 `unknown` 으로 **질문**이 나온다.
    """
    import discover as DISC
    import relate as RL
    d = DISC.scan(source)
    es = d["entries"]
    out, questions = [], []
    for i in range(len(es)):
        for j in range(i + 1, len(es)):
            v = RL.classify(es[i], es[j])
            out.append({"a": es[i]["entry_id"], "b": es[j]["entry_id"], **v})
            if v.get("relation") == RL.UNKNOWN:
                questions.append(RL.question_for(es[i], es[j], v))
    return {"source": d["source"], "n_pairs": len(out), "relations": out,
            "questions": questions,
            "규율": "derived 만 자동 확정. 나머지는 제안이고 확정은 사람(A-1)"}


# ══ 문서 — 호스트 LLM 과의 이음매 ══════════════════════════════════════════

@_guard
def document_brief(run_id: str) -> dict:
    """**당신(호스트 LLM)이 문서를 쓰는 데 필요한 전부.**

    돌려주는 것 셋 —
      `skeleton`  절이 다 서 있고 값 자리가 `<키>` 로 비어 있는 골격
      `catalog`   쓸 수 있는 값의 전부(키 · 값 · 역할 · 산지)
      `rules`     서술 규율과 슬롯별 지침

    할 일 둘 — ① `<키>` 를 카탈로그의 키로 바꾼다 ② `PROSE` 마커 **사이**를 쓴다.
    `|역할` 과 마커 밖은 건드리지 않는다. 다 쓰면 `submit_document` 로 낸다.
    """
    import catalog as CT
    import docspec as DS
    work = C.work_dir(run_id, create=False)
    if not work.is_dir():
        raise FileNotFoundError(f"run 이 없다: {run_id}")
    cat = CT.load(run_id, work) if (work / CT.CATALOG_NAME).exists() else CT.build(run_id, work)
    spec = DS.load()
    # ★ 준 것을 그대로 박아 둔다. 게이트는 `골격.md` 와 대조하는데, brief 가 즉석에서
    #   만든 골격을 주고 디스크에는 옛 골격이 남아 있으면 **다른 것과 대조하게 된다** —
    #   호스트 LLM 은 시킨 대로 했는데 반려되고, 사유도 엉뚱해진다.
    skeleton = CT.skeleton(cat, spec)
    sk_path = work / "골격.md"
    if not sk_path.exists() or sk_path.read_text(encoding="utf-8") != skeleton:
        sk_path.write_text(skeleton, encoding="utf-8")
    sk, sk_cut = _clip(skeleton)
    prompt, p_cut = _clip(CT.prompt_block(cat))
    slots = DS.prose_slots(spec)
    stale = _stale(run_id)
    return {
        "run_id": run_id,
        # ★ 낡았으면 **먼저 말한다.** 낡은 카탈로그로 문서를 다 쓰고 나서 알면
        #   그 작업이 통째로 버려진다.
        "stale": stale,
        "경고": (f"이 run 은 선언 이후 다시 돌지 않았다 — 바뀐 것: {stale['changed']}. "
               "run_pipeline 을 다시 돌린 뒤 brief 를 새로 받는다"
               if stale.get("stale") else ""),
        "skeleton": sk, "skeleton_truncated": sk_cut,
        "catalog": prompt, "catalog_truncated": p_cut,
        "n_catalog_entries": cat["n_entries"],
        "prose_rules": spec.get("prose_rules") or [],
        "prose_slots": [{"key": s["key"], "guide": s["guide"],
                         "max_sentences": s["max_sentences"]} for s in slots],
        "ref_syntax": cat.get("ref_syntax"),
        "할 일": [
            "골격의 `<키>` 를 카탈로그의 키로 바꾼다",
            "`PROSE` 마커 **사이**에 소견을 쓴다",
            "`|역할` 을 바꾸지 않는다 — 키를 바꾼다",
            "마커 밖(표 · 제목 · 주석 · 대장)을 고치지 않는다",
            "이미 키가 박힌 행렬의 칸을 건드리지 않는다",
            "본문에 숫자를 타이핑하지 않는다",
        ],
        "다음": "submit_document(run_id, markdown) — 통과할 때까지 위반만 고쳐 다시 낸다",
    }


@_guard
def _gate_attempt(run_id: str) -> int:
    """이 run 이 게이트에 **몇 번째로** 걸리는가. 원장의 gate 사건 수 + 1.

    왜 세나 — 모델을 갈아 붙일 때 **몇 번 반려되고 통과하나**가 그 모델의 성적이다.
    호스트가 세어 주지 않으므로 서버가 센다. 세지 않으면 비교할 것이 없다(B-3).
    """
    try:
        import ledger as L
        conn = L.open_ledger(C.ledger_path(), create=False)
        try:
            return sum(1 for e in L.events(conn, run_id) if e.get("kind") == "gate") + 1
        finally:
            conn.close()
    except Exception:
        return 1


@_guard
def submit_document(run_id: str, markdown: str, attempt: int = 0) -> dict:
    """채운 문서를 **결정론 게이트**에 건다.

    통과하면 참조가 값으로 치환된 문서가 나온다. 안 되면 **무엇을 어디서 어겼는지**가
    온다 — 위반만 고쳐 다시 낸다. 게이트는 LLM 이 아니다: 문서를 쓴 쪽이 채점까지 하면
    심판과 선수가 같아진다.
    """
    import gate as GT
    if not (markdown or "").strip():
        return _err("빈 문서다", kind="empty")
    work = C.work_dir(run_id)
    if not work.is_dir():
        raise FileNotFoundError(f"run 이 없다: {run_id}")
    draft = work / GT.DRAFT_NAME
    draft.write_text(markdown, encoding="utf-8")
    attempt = attempt or _gate_attempt(run_id)
    v = GT.check_run(run_id, draft, attempt=attempt)
    sub = ""
    if v["pass"]:
        sp = work / GT.SUBSTITUTED_NAME
        sub = sp.read_text(encoding="utf-8") if sp.exists() else ""
    sub, cut = _clip(sub)
    from collections import Counter
    kinds = Counter(x["kind"] for x in v["violations"])
    return {
        "run_id": run_id, "pass": v["pass"], "attempt": attempt,
        "n_violations": len(v["violations"]),
        "violation_kinds": dict(kinds),
        "violations": v["violations"][:40],
        "n_refs": v["n_refs"],
        "exempt_hits": v.get("exempt_hits", [])[:10],
        "substituted": sub, "substituted_truncated": cut,
        "다음": ("통과했다 — package_run(run_id) 으로 묶는다" if v["pass"]
               else "violations 만 고쳐 다시 낸다. 종류별 대처는 리소스 orch://quickstart 참조"),
    }


@_guard
def package_run(run_id: str) -> dict:
    """통과한 문서를 산출 폴더로 묶고 자산 DB 에 등재한다."""
    import package as PK
    r = PK.build(run_id)
    return {"run_id": run_id, "result": r,
            "다음": "asset_query(view='status') 로 자산 DB 등재를 확인한다"}


# ══ 선언 — 사람이 아는 값이 들어오는 길 ════════════════════════════════════

@_guard
def declare_gaps(run_id: str) -> dict:
    """지금 **물어보면 채워지는 것**의 목록.

    문서의 빈 곳 중에는 파일을 기다려야 하는 것과 도구를 기다려야 하는 것 말고,
    **아는 사람이 말하면 그만인 것**이 있다. 여기 나오는 것이 그것이다 —
    사용자에게 물어보고, 답을 들으면 `declare_set` 에 넣는다.
    """
    import declare as D
    return D.gaps(run_id)


@_guard
def declare_set(path: str, value: str, product: str = "", by: str = "",
                why: str = "") -> dict:
    """사람이 말한 값을 **선언 자리**에 넣는다.

    ★ 값을 문서에 직접 적지 않는 이유 — 문서에 타이핑하면 게이트가 그것이 사람이 한 말인지
      모델이 지어낸 말인지 구별할 수 없다. 선언 자리로 들어가면 출처와 함께 실린다.

    `registry/products.yaml` 은 고치지 않는다. `registry/declared/<제품>.yaml` 에 쌓이고
    적재 시점에 얹힌다 — 되돌리려면 그 파일을 지운다.
    `by`(누가 말했나)가 없으면 거부한다(A-1).
    """
    import declare as D
    if not by.strip():
        return _err("누가 말했는지 없이 선언하지 않는다 — by 를 채운다(A-1)",
                    kind="who_missing")
    try:
        r = D.set_value(path, value, product=product or None, by=by, why=why)
    except D.DeclareError as e:
        return _err(str(e), kind="declare_rejected")
    # 넣은 것으로 끝이 아니다 — **다시 돌려야** 문서에 실린다. 그 말을 여기서 한다.
    r["다음"] = ("`run_pipeline` 을 다시 돌려야 이 값이 참조로 문서에 실린다. "
                "돌리기 전에 `document_brief` 를 부르면 낡은 카탈로그를 받는다")
    return r


# ══ 리소스 ═════════════════════════════════════════════════════════════════

def resource_guide() -> str:
    return GUIDE_MOD.GUIDE


def resource_quickstart() -> str:
    return GUIDE_MOD.QUICKSTART


def resource_roles() -> str:
    import roles as R
    lines = ["# 역할 어휘 — 값의 종류", "",
             "role = 자산 DB 컬럼 = 문서 골격 슬롯 = 게이트 타입. 셋을 한 어휘가 규율한다.",
             "어휘에 없는 값은 문서에 실리지 못한다 — 지어내지 않고 사람이 등재한다.", ""]
    for r in R.ROLES:
        lines.append(f"- `{r}` — {R.ROLE_DESC.get(r, '')}")
    return "\n".join(lines)


def resource_document_spec() -> str:
    import docspec as DS
    spec = DS.load()
    lines = [f"# 문서 양식 정본 — {spec.get('title')} ({spec.get('spec_version')})", "",
             "절 구성 · 역할 배치 · 그림 자리 · 서술 슬롯의 정본. 코드가 아니라 데이터다.", ""]
    for sec in spec["sections"]:
        mark = " **[필수]**" if sec.get("required") else ""
        lane = f" · 레인 {sec['lane']}" if sec.get("lane") else ""
        lines.append(f"## {sec['title']}  `{sec['render']}`{mark}{lane}")
        if sec.get("axis"):
            lines.append(f"- 축: {sec['axis']['label']} ← {', '.join(sec['axis']['roles'])}")
        for c in sec.get("columns") or []:
            lines.append(f"- 열: {c['label']} ← `{c['role']}`")
        if sec.get("roles"):
            lines.append("- 값: " + ", ".join(f"`{r}`" for r in sec["roles"]))
        if sec.get("figures"):
            lines.append("- 그림: " + ", ".join(f"`{r}`" for r in sec["figures"]))
        for pr in sec.get("prose") or []:
            lines.append(f"- ✎ `{sec['id']}.{pr['slot']}` — {' '.join(pr['guide'].split())}")
        if sec.get("required"):
            ab = sec["absent"]
            lines.append(f"- ∅ 비면: [{ab.get('kind')}] {' '.join(ab['why'].split())} "
                         f"→ {ab.get('owner')} · {ab.get('slot')}")
        lines.append("")
    return "\n".join(lines)


def resource_rules() -> str:
    import gate as GT
    import json as _j
    return ("# 판정 규칙의 산지\n\n"
            "여기 값들이 `rule_version` 에 묶여 원장에 기록된다. 규칙이 바뀌면 판본이 갈린다.\n\n"
            "```json\n" + _j.dumps(GT.numeric_rules(), ensure_ascii=False, indent=2) + "\n```\n")


RESOURCES = {
    "orch://guide": ("사용법 — 규칙과 표준 순서", "text/markdown", resource_guide),
    "orch://quickstart": ("빠른 시작 · 위반 대처표", "text/markdown", resource_quickstart),
    "orch://roles": ("역할 어휘 전체", "text/markdown", resource_roles),
    "orch://document-spec": ("문서 양식 정본", "text/markdown", resource_document_spec),
    "orch://rules": ("판정 규칙 · 게이트 예외의 산지", "text/markdown", resource_rules),
}

TOOLS = (
    orch_status, discover_sources, run_report, catalog_lookup, asset_query, ledger_events,
    run_pipeline, compare_revisions, crosscheck, relate_entries,
    document_brief, submit_document, package_run,
    declare_gaps, declare_set,
)


# ══ 자기 시험 ══════════════════════════════════════════════════════════════

def self_test() -> int:
    ok = fail = 0

    def chk(n, cond, d=""):
        nonlocal ok, fail
        if cond:
            ok += 1; print(f"  PASS  {n}")
        else:
            fail += 1; print(f"  FAIL  {n}  {d}")

    print("[mcp_server/api.py 자기 시험]")

    # 이 파일이 SDK 에 묶이지 않았는가 — 묶이면 SDK 판올림마다 도구를 다시 짠다
    src = Path(__file__).read_text(encoding="utf-8")
    import re as _re
    imports = _re.findall(r"^\s*(?:from|import)\s+([\w.]+)", src, _re.M)
    chk("SDK 를 import 하지 않는다 — 프로토콜이 바뀌어도 도구는 그대로다",
        not any(m.split(".")[0] in ("mcp", "fastmcp") for m in imports), str(imports))
    chk("도구가 전부 등재되어 있다", len(TOOLS) >= 14, str(len(TOOLS)))
    chk("도구 이름이 겹치지 않는다", len({t.__name__ for t in TOOLS}) == len(TOOLS))
    chk("도구마다 설명이 있다", all((t.__doc__ or "").strip() for t in TOOLS),
        str([t.__name__ for t in TOOLS if not (t.__doc__ or "").strip()]))

    st = orch_status()
    chk("현황을 낸다", "data_root" in st and "ledger" in st, str(list(st)[:6]))
    chk("서버가 LLM 을 부르지 않음을 밝힌다", "LLM" in str(st.get("llm")))
    chk("run 목록을 낸다", st["n_runs"] >= 1, str(st["n_runs"]))

    rid = next((r["run_id"] for r in st["runs"] if r["골격"]), None) or \
        next((r["run_id"] for r in st["runs"]), None)
    chk("시험에 쓸 run 이 있다", bool(rid), str(rid))

    if rid:
        rep = run_report(rid)
        chk("run 보고가 무엇이 비었는지 낸다",
            "n_unjudged" in rep and "unreadable" in rep, str(list(rep)[:8]))

        br = document_brief(rid)
        chk("brief 에 골격이 실린다", br["skeleton"].startswith("#"), br["skeleton"][:40])
        chk("brief 에 카탈로그가 실린다", "값 카탈로그" in br["catalog"])
        chk("brief 에 서술 규율이 실린다", len(br["prose_rules"]) >= 4)
        chk("brief 에 슬롯 지침이 실린다", len(br["prose_slots"]) >= 5)
        chk("brief 가 금지를 먼저 말한다",
            any("바꾸지 않는다" in x or "고치지 않는다" in x for x in br["할 일"]))

        # 게이트가 실제로 반려하는가 — 안 채운 골격은 통과하면 안 된다
        sub = submit_document(rid, br["skeleton"])
        chk("안 채운 골격은 반려된다", sub["pass"] is False, str(sub.get("violation_kinds")))
        chk("무엇을 어겼는지 종류로 말한다",
            "unsubstituted_ref" in sub["violation_kinds"] or
            "undefined_key" in sub["violation_kinds"], str(sub["violation_kinds"]))
        chk("빈 문서를 거부한다", submit_document(rid, "  ")["ok"] is False)

        cl = catalog_lookup(rid, role="식별.원천명")
        chk("역할로 값을 찾는다", cl["n_matched"] >= 1, str(cl["n_matched"]))
        cl2 = catalog_lookup(rid, query="없는키이름zzz")
        chk("없으면 없다고 한다", cl2["n_matched"] == 0)

        dg = declare_gaps(rid)
        chk("무엇을 물으면 되는지 낸다", "declarable" in dg, str(list(dg)[:5]))

    # 쓰기 거부 — 선언 자리가 아닌 곳
    bad = declare_set("products:example_x_band.hidden.x", "1",
                      product="example_x_band", by="시험")
    chk("선언 자리가 아니면 거부한다", bad["ok"] is False and "선언 자리" in bad["why"],
        str(bad)[:80])
    chk("누가 말했는지 없으면 거부한다",
        declare_set("products:example_x_band.use", "x", product="example_x_band",
                    by="")["ok"] is False)

    # 없는 run 은 예외가 아니라 사유로
    nf = run_report("없는run_zzz")
    chk("없는 run 은 사유로 답한다", nf["ok"] is False and nf["kind"] == "not_found",
        str(nf)[:80])
    chk("무엇을 하면 되는지 말한다", "run_pipeline" in str(nf.get("다음")))

    # ── 회귀 — 실제로 났던 결함 셋. 도구를 **정해진 순서로** 부르면 안 나온다 ──────
    #   호스트 LLM 은 순서를 지키지 않는다. 그래서 여기 못 박는다.
    import inspect as _ins
    bad_sig = [t.__name__ for t in TOOLS
               if any(pr.kind in (pr.VAR_POSITIONAL, pr.VAR_KEYWORD)
                      for pr in _ins.signature(t).parameters.values())]
    chk("회귀 ① 도구 시그니처가 지워지지 않는다 — 지워지면 인자 이름을 몰라 못 부른다",
        not bad_sig, str(bad_sig))
    chk("회귀 ① 인자에 형이 붙어 있다 — 스키마가 서려면 필요하다",
        all(pr.annotation is not _ins.Parameter.empty
            for t in TOOLS for pr in _ins.signature(t).parameters.values()))

    if rid:
        # 회귀 ② brief 가 준 골격이 **디스크에 박히는가**. 안 박히면 게이트가 다른 것과
        #        대조하고, 호스트는 시킨 대로 했는데 반려된다(F-36).
        work = C.work_dir(rid, create=False)
        (work / "골격.md").write_text("# 낡은 골격\n", encoding="utf-8")
        br2 = document_brief(rid)
        chk("회귀 ② brief 가 준 골격이 디스크에 박힌다",
            (work / "골격.md").read_text(encoding="utf-8") == br2["skeleton"])
        tv = submit_document(rid, br2["skeleton"].replace(
            next(l for l in br2["skeleton"].splitlines() if l.startswith("## ")),
            "## 내가 바꾼 제목", 1))
        chk("회귀 ② 그래서 마커 밖 변경이 잡힌다",
            "template_modified" in tv["violation_kinds"], str(tv["violation_kinds"]))

        # 회귀 ③ 선언 이후 안 돌린 run 을 **먼저** 알려주는가
        chk("회귀 ③ brief 가 낡음을 실어 보낸다", "stale" in br2 and "경고" in br2)
        chk("회귀 ③ run_report 도 낡음을 싣는다", "stale" in run_report(rid))
        st3 = _stale(rid)
        chk("회귀 ③ 낡음을 파일 시각이 아니라 내용으로 판정한다",
            "changed" in st3 and isinstance(st3["changed"], list))

    # 리소스
    chk(f"리소스 {len(RESOURCES)}종", len(RESOURCES) >= 5)
    for uri, (_t, _m, fn) in RESOURCES.items():
        body = fn()
        chk(f"리소스 {uri} 가 내용을 낸다", len(body) > 200, f"{len(body)}자")
    chk("사용법이 금지를 먼저 말한다", "절대 규칙" in resource_guide())
    chk("빠른 시작에 위반 대처표가 있다",
        "role_mismatch" in resource_quickstart() and "bare_number" in resource_quickstart())

    print(f"\n결과: {ok}/{ok + fail} PASS")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(self_test())
