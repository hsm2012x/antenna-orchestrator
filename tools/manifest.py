#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/manifest.py — 입력 항목(선언) · 자동 채움 · 관측 대조 (LLM 0콜)

run 을 걸기 전에 사람이 아는 것을 **미리 준다**. 없어도 돌아가지만, 주면 두 가지가 생긴다.

    ① 편의  — `--project marine_radar` 하나로 대역·소자·제조사·담당자가 채워진다
    ② 대조  — **이쪽이 진짜 목적이다.** 선언과 관측이 어긋나면 그것이 발견이다

산지가 하나 늘어난다 — (e) 사람 선언
    지금까지 수치의 산지는 넷이었다: (a) 물리 정의값 (b) 외부 포맷 규약
    (c) 판정 규칙 (d) 모델 가정. 입력 항목은 다섯째다.
    **선언을 관측과 섞으면 안 된다.** "사람이 말한 X 대역"과 "도면에서 읽은 대역"은
    다른 것이고, 문서가 둘을 구별하지 못하면 "출처 없는 숫자를 쓰지 않는다"가 무너진다.
    그래서 카탈로그에 `선언.*` 로 따로 실리고 산지 문자열이 붙는다.

어긋남의 세 가지 뜻 — **판정하지 않고 드러낸다**
    ① 선언이 틀렸다      사람이 잘못 적었다
    ② 원천이 다르다      이 폴더가 그 프로젝트의 것이 아니다
    ③ **도구가 틀렸다**  ← 이것을 잡으려고 대조한다

CLI
    python tools/manifest.py template [--project marine_radar]   입력 항목 서식
    python tools/manifest.py resolve  <manifest.json>            자동 채움 결과
    python tools/manifest.py check    <run_id> [manifest.json]   선언 ↔ 관측 대조
    python tools/manifest.py bands · kinds · projects            어휘 보기
    python tools/manifest.py self-test
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402

MANIFEST_NAME = "입력항목.json"
DECL_SOURCE = "선언: 입력 항목(사람) — 관측이 아니다"

# (c) 판정 규칙 — 대조 임계. 원장에 실린다.
BAND_TOLERANCE = 0.10        # 대역 경계에서 이 비율까지는 경고하지 않는다
ER_TOLERANCE = 0.05          # 유전율 선언 대비 상대 오차


def numeric_rules() -> dict:
    return {"manifest_band_tolerance": BAND_TOLERANCE,
            "manifest_er_tolerance": ER_TOLERANCE,
            "규율": ("선언과 관측의 어긋남은 **경고**다. 판정이 아니다 — 셋 중 무엇인지"
                   "(선언 오류·원천 불일치·도구 결함) 사람이 정한다.")}


def _load_yaml(name: str) -> dict:
    import yaml
    p = Path(os.environ.get(f"ORCH_{name.upper()}_REGISTRY",
                            str(C.REPO / "registry" / f"{name}.yaml")))
    if not p.exists():
        raise FileNotFoundError(f"{name} 어휘 없음: {p}")
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def bands() -> dict:
    return _load_yaml("bands")


def kinds() -> dict:
    return _load_yaml("asset_kinds")


def projects() -> dict:
    return _load_yaml("projects")


# ── 양방향 색인 — 사람 ↔ 프로젝트 ↔ 대역 ───────────────────────────────────
# 사람을 고르면 그 사람의 프로젝트·대역이, 프로젝트를 고르면 담당자·대역이 뜬다.
# 한 방향만 두면 "이 사람이 뭘 하나"와 "이 프로젝트를 누가 하나"를 따로 관리하게 된다.

def index() -> dict:
    reg = projects()
    people = reg.get("people") or {}
    prjs = reg.get("projects") or {}
    ids = reg.get("identities") or {}
    teams = reg.get("teams") or {}

    by_person, by_project = {}, {}
    for pid, p in people.items():
        by_person[pid] = {"name": p.get("name"), "title": p.get("title"),
                          "team": p.get("team"),
                          "projects": list(p.get("projects") or []),
                          "bands": list(p.get("bands") or []),
                          "identities": [k for k, v in ids.items()
                                         if pid in (v.get("people") or [])]}
    for k, v in prjs.items():
        by_project[k] = {"label": v.get("label"), "team": v.get("team"),
                         "people": list(v.get("people") or []),
                         "bands": list(v.get("bands") or []),
                         "identities": [i for i, d in ids.items() if d.get("project") == k]}
    # 사람 → 프로젝트가 비어 있으면 팀에서 잇는다(한쪽만 적어도 돌아가게)
    for pid, p in by_person.items():
        if not p["projects"] and p["team"]:
            p["projects"] = list((teams.get(p["team"]) or {}).get("projects") or [])
    return {"people": by_person, "projects": by_project, "teams": teams,
            "identities": ids, "registrations": reg.get("registrations") or []}


def options_for(*, person: str | None = None, project: str | None = None) -> dict:
    """고른 것에 따라 **다음에 고를 수 있는 것**을 돌려준다."""
    ix = index()
    if person:
        p = ix["people"].get(person)
        if not p:
            raise ValueError(f"등재되지 않은 사람: {person} — 있는 것 {sorted(ix['people'])}")
        return {"person": person, "name": p["name"], "team": p["team"],
                "projects": p["projects"], "bands": p["bands"],
                "identities": p["identities"],
                "note": "대역 목록에는 아직 등록된 자산이 없는 것도 있다(선택지로만)"}
    if project:
        q = ix["projects"].get(project)
        if not q:
            raise ValueError(f"등재되지 않은 프로젝트: {project} — 있는 것 {sorted(ix['projects'])}")
        return {"project": project, "label": q["label"], "team": q["team"],
                "people": [{"id": x, "name": ix["people"].get(x, {}).get("name")}
                           for x in q["people"]],
                "bands": q["bands"], "identities": q["identities"]}
    return {"people": sorted(ix["people"]), "projects": sorted(ix["projects"]),
            "identities": sorted(ix["identities"])}


def next_version(identity: str) -> int:
    """같은 정체성의 **다음 등록 순번**. 설계 판올림 번호가 아니다."""
    regs = [r for r in (projects().get("registrations") or [])
            if r.get("identity") == identity]
    return max([int(r.get("version") or 0) for r in regs] or [0]) + 1


# ── 서식 ────────────────────────────────────────────────────────────────────

def template(project: str | None = None) -> dict:
    """입력 항목 서식. **전부 선택이다** — 비우면 그 항목은 대조하지 않는다."""
    m = {
        "_설명": "run 전에 아는 것을 적는다. 비워도 된다 — 적은 것만 대조한다.",
        "person": None,
        "project": project,
        "identity": None,
        "asset_kind": None,
        "asset_id": None,
        "version": None,
        "band": None,
        "band_profile": None,
        "operating_ghz": None,
        "substrate": {"name": None, "er": None, "er_kind": None, "manufacturer": None},
        "담당": None,
        "note": None,
        "_어휘": {
            "person": sorted(projects().get("people", {})),
            "project": sorted(projects().get("projects", {})),
            "identity": sorted(projects().get("identities", {})),
            "asset_kind": sorted(kinds().get("kinds", {})),
            "band": sorted(bands().get("bands", {})),
            "er_kind": ["datasheet", "fitted"],
        },
    }
    if project:
        m.update(resolve(m)["manifest"])
    return m


# ── 자동 채움 ────────────────────────────────────────────────────────────────

def resolve(manifest: dict) -> dict:
    """프로젝트 → 대역·소자·제조사·담당자를 채운다. **채운 자리를 기록한다.**

    무엇이 사람이 적은 것이고 무엇이 자동으로 온 것인지 구별되지 않으면,
    나중에 "이 값은 누가 말한 건가"를 답할 수 없다.
    """
    m = json.loads(json.dumps(manifest, ensure_ascii=False))
    filled, warn = [], []
    # 사람을 골랐으면 프로젝트를 채운다(양방향 색인)
    if m.get("person") and not m.get("project"):
        o = options_for(person=m["person"])
        if len(o["projects"]) == 1:
            m["project"] = o["projects"][0]
            filled.append({"field": "project", "value": m["project"],
                           "from": f"person.{m['person']}"})
        elif o["projects"]:
            warn.append({"code": "project_ambiguous", "options": o["projects"],
                         "why": f"{o['name']} 의 프로젝트가 여럿이다 — 고른다"})

    pj = (projects().get("projects") or {}).get(m.get("project") or "")
    if m.get("project") and not pj:
        raise ValueError(f"등재되지 않은 프로젝트: {m['project']} — "
                         f"있는 것 {sorted((projects().get('projects') or {}))}")

    if pj:
        d = pj.get("defaults") or {}
        for key, val in (("band", (pj.get("bands") or [None])[0]),
                         ("band_profile", pj.get("band_profile"))):
            if val and not m.get(key):
                m[key] = val
                filled.append({"field": key, "value": val, "from": f"projects.{m['project']}"})
        sub = m.setdefault("substrate", {}) or {}
        for k in ("name", "er", "manufacturer"):
            v = (d.get("substrate") or {}).get(k)
            if v is not None and sub.get(k) is None:
                sub[k] = v
                filled.append({"field": f"substrate.{k}", "value": v,
                               "from": f"projects.{m['project']}"})
        m["substrate"] = sub
        # 담당자 — 프로젝트에서 채운다(요구 6: 프로젝트를 고르면 사람이 자동)
        if not m.get("담당") and pj.get("people"):
            ppl = projects().get("people") or {}
            m["담당"] = [{"id": x, "name": (ppl.get(x) or {}).get("name"),
                        "title": (ppl.get(x) or {}).get("title")} for x in pj["people"]]
            filled.append({"field": "담당", "value": m["담당"],
                           "from": f"projects.{m['project']}"})
        if not m.get("person") and len(pj.get("people") or []) == 1:
            m["person"] = pj["people"][0]
            filled.append({"field": "person", "value": m["person"],
                           "from": f"projects.{m['project']}"})

        # 정체성 — 어느 안테나인가. 등록 순번은 여기서 나온다.
        ident = m.get("identity")
        if not ident:
            cands = [i for i, d in (projects().get("identities") or {}).items()
                     if d.get("project") == m["project"]
                     and (not m.get("asset_kind") or d.get("kind") == m["asset_kind"])]
            if len(cands) == 1:
                ident = m["identity"] = cands[0]
                filled.append({"field": "identity", "value": ident, "from": "프로젝트 유일 후보"})
            elif len(cands) > 1:
                warn.append({"code": "identity_ambiguous", "options": cands,
                             "why": "이 프로젝트에 정체성이 여럿이다 — 어느 안테나인지 고른다"})
        if ident:
            d2 = (projects().get("identities") or {}).get(ident) or {}
            for k in ("band", "band_profile"):
                if d2.get(k) and not m.get(k):
                    m[k] = d2[k]
                    filled.append({"field": k, "value": d2[k], "from": f"identity.{ident}"})
            if d2.get("kind") and not m.get("asset_kind"):
                m["asset_kind"] = d2["kind"]
                filled.append({"field": "asset_kind", "value": d2["kind"],
                               "from": f"identity.{ident}"})
            if not m.get("version"):
                m["version"] = next_version(ident)
                filled.append({"field": "version", "value": m["version"],
                               "from": "등록 순번(다음 번호)"})

    # 어휘 검증 — 오타를 조용히 넘기지 않는다
    if m.get("band") and m["band"] not in (bands().get("bands") or {}):
        raise ValueError(f"어휘 밖 대역: {m['band']} — 있는 것 {sorted(bands()['bands'])}")
    if m.get("asset_kind") and m["asset_kind"] not in (kinds().get("kinds") or {}):
        raise ValueError(f"어휘 밖 자산 종류: {m['asset_kind']} — "
                         f"있는 것 {sorted(kinds()['kinds'])}")

    # 유전율의 성격 — fitted 인지 datasheet 인지 밝히지 않으면 나중에 오독된다
    sub = m.get("substrate") or {}
    if sub.get("er") is not None and not sub.get("er_kind"):
        warn.append({"code": "er_kind_unset",
                     "why": ("유전율의 성격을 적지 않았다. `fitted`(실측 정합 보정값)와 "
                             "`datasheet`(재질 스펙)는 다른 값이다 — 정합값을 다른 자산에 "
                             "옮겨 쓰면 그 계산이 틀어진다")})
    return {"manifest": m, "filled": filled, "warnings": warn}


def operating_range(m: dict) -> list | None:
    """대조에 쓸 주파수 범위. `operating` 이 있으면 그것이 이긴다(문자 대역은 너무 넓다)."""
    if m.get("operating_ghz"):
        return list(m["operating_ghz"])
    b = (bands().get("bands") or {}).get(m.get("band") or "")
    if not b:
        return None
    prof = (b.get("operating") or {}).get(m.get("band_profile") or "")
    return list((prof or {}).get("range_ghz") or b.get("range_ghz") or []) or None


# ── 대조 ────────────────────────────────────────────────────────────────────

def check(run_id: str, manifest: dict | None = None, work: Path | None = None) -> dict:
    """선언 ↔ 관측. **판정하지 않는다 — 드러낸다.**"""
    import catalog as CAT
    work = Path(work) if work else C.work_dir(run_id, create=False)
    if manifest is None:
        p = work / MANIFEST_NAME
        manifest = C.read_json(p) if p.exists() else {}
    if not manifest:
        return {"run_id": run_id, "declared": False, "mismatches": [],
                "note": "입력 항목이 없다 — 대조하지 않는다(없어도 된다)"}

    m = resolve(manifest)["manifest"]
    cat = CAT.load(run_id, work)
    E = cat["entries"]

    def val(role):
        e = next((x for x in E.values() if x.get("role") == role), None)
        return (e or {}).get("value"), (e or {}).get("key")

    out = []
    rng = operating_range(m)
    cmp_ = (bands().get("비교") or {})
    must = tuple(cmp_.get("must_match") or ())
    should = tuple(cmp_.get("should_match") or ())
    skip = tuple(cmp_.get("do_not_compare") or ())
    if rng:
        lo, hi = rng
        pad = (hi - lo) * BAND_TOLERANCE
        for role in must + should:
            if role in skip:
                continue
            v, key = val(role)
            if not isinstance(v, (int, float)):
                continue
            if lo - pad <= v <= hi + pad:
                continue
            warn = role in must
            out.append({
                "kind": "manifest_mismatch" if warn else "manifest_note",
                "severity": "warn" if warn else "info",
                "field": "band", "role": role,
                "declared": f"{m.get('band')} {rng} GHz", "observed": v, "key": key,
                "why": (("원천이 **스스로 선언한** 동작 대역이 목표와 다르다. 셋 중 하나다 — "
                         "① 선언이 틀렸다 ② 이 원천이 그 프로젝트 것이 아니다 "
                         "③ **도구가 틀렸다**") if warn else
                        (cmp_.get("why_should") or "목표 근처가 아니다 — 정보로만 남긴다")),
            })

    sub = m.get("substrate") or {}
    if isinstance(sub.get("er"), (int, float)):
        for e in E.values():
            if e.get("role") == "material_er" and isinstance(e.get("value"), (int, float)):
                if e["value"] <= 1.0:
                    continue                      # 공기·도체 — 기판 유전율이 아니다
                d = abs(e["value"] - sub["er"]) / max(sub["er"], 1e-9)
                if d > ER_TOLERANCE:
                    out.append({"kind": "manifest_mismatch", "field": "substrate.er",
                                "role": "material_er", "declared": sub["er"],
                                "observed": e["value"], "key": e["key"],
                                "er_kind": sub.get("er_kind"),
                                "why": ("선언 유전율과 원천 선언값이 다르다. "
                                        "정합값(fitted)과 스펙값(datasheet)을 섞지 않았는지 본다")})

    kd = (kinds().get("kinds") or {}).get(m.get("asset_kind") or "")
    missing = []
    if kd:
        have = {e.get("role") for e in E.values()}
        missing = [r for r in (kd.get("expected_roles") or []) if r not in have]

    return {"run_id": run_id, "declared": True, "manifest": m,
            "operating_ghz": rng, "mismatches": out, "n_mismatch": len(out),
            "asset_kind": m.get("asset_kind"),
            "expected_missing": missing,
            "expected_note": ("이 종류에서 **기대되는데 없는** 역할이다. 종류가 다르면 "
                              "없는 것이 정상이므로, 종류를 밝혀야 공백과 정상을 가른다."),
            "numeric_rules": numeric_rules()}


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

    print("[manifest.py 자기 시험]")
    chk("대역 어휘 적재", len(bands()["bands"]) >= 8)
    chk("자산 종류 어휘 적재", len(kinds()["kinds"]) >= 6)
    chk("프로젝트 어휘 적재", "marine_radar" in projects()["projects"])

    # 문자 대역은 너무 넓다 — operating 이 이긴다
    wide = operating_range({"band": "X"})
    narrow = operating_range({"band": "X", "band_profile": "marine_radar"})
    chk("X 대역 기본 8~12 GHz", wide == [8.0, 12.0], str(wide))
    chk("운용 범위가 이긴다 9.3~9.5", narrow == [9.3, 9.5], str(narrow))

    # 자동 채움
    r = resolve({"project": "marine_radar"})
    m = r["manifest"]
    chk("프로젝트 → 대역·종류 자동 채움",
        m["band"] == "X" and m["asset_kind"] == "antenna", str(m)[:120])
    chk("채운 자리를 기록한다", any(f["field"] == "band" for f in r["filled"]),
        str(r["filled"]))
    chk("담당자도 채워진다", bool(m.get("담당")), str(m.get("담당")))

    # 양방향 색인 — 사람 ↔ 프로젝트 (요구 6)
    op = options_for(person="example_owner")
    chk("사람 → 프로젝트·대역이 뜬다",
        op["projects"] == ["marine_radar"] and set(op["bands"]) >= {"X", "Ku"}, str(op))
    oj = options_for(project="marine_radar")
    chk("프로젝트 → 담당자가 뜬다",
        any(x["id"] == "example_owner" for x in oj["people"]), str(oj["people"]))
    r5 = resolve({"person": "example_owner"})
    chk("사람만 골라도 프로젝트·대역·종류가 채워진다",
        r5["manifest"]["project"] == "marine_radar" and r5["manifest"]["band"] == "X"
        and r5["manifest"]["asset_kind"] == "antenna", str(r5["manifest"])[:150])
    chk("프로젝트를 고르면 담당자가 채워진다",
        any(x.get("id") == "example_owner" for x in (r5["manifest"].get("담당") or [])),
        str(r5["manifest"].get("담당")))

    # 정체성과 등록 순번 — version 은 **등록 순번**이지 설계 판올림이 아니다
    chk("정체성이 채워진다",
        r5["manifest"]["identity"] == "marine_radar_antenna_x",
        str(r5["manifest"].get("identity")))
    chk("등록 순번이 다음 번호로 채워진다",
        r5["manifest"]["version"] == next_version("marine_radar_antenna_x"),
        str(r5["manifest"].get("version")))
    chk("등록 순번은 기존 등록 수 + 1", next_version("marine_radar_antenna_x") == 3,
        str(next_version("marine_radar_antenna_x")))

    # 유전율 성격 경고
    r4 = resolve({"substrate": {"er": 3.0}})
    chk("유전율 성격(fitted/datasheet) 미기재 경고",
        any(x["code"] == "er_kind_unset" for x in r4["warnings"]), str(r4["warnings"]))

    # 어휘 밖은 거부 — 오타를 조용히 넘기지 않는다
    for bad, field in (({"band": "Z"}, "band"), ({"asset_kind": "안테나"}, "asset_kind"),
                       ({"project": "없는프로젝트"}, "project")):
        try:
            resolve(bad)
            chk(f"어휘 밖 {field} 거부", False, "통과해 버렸다")
        except ValueError:
            chk(f"어휘 밖 {field} 거부", True)

    # ── 대역 대조 등급 (2026-07-31 정정) ──────────────────────────────────
    # 목표 밖 주파수는 필터가 거른다. 그리고 소자 λ/2 공진은 배열 동작 주파수가 아니다 —
    # 기하 공진이 대역 밖인 것은 **결함이 아니다**. 경고로 띄우면 진짜 신호가 묻힌다.
    cmp_ = bands().get("비교") or {}
    chk("공진 역할은 대역과 비교하지 않는다",
        {"resonance_free_min_ghz", "resonance_free_max_ghz", "param_resonance_ghz"}
        <= set(cmp_.get("do_not_compare") or []), str(cmp_.get("do_not_compare")))
    chk("선언 동작 대역만 경고 대상",
        set(cmp_.get("must_match") or []) == {"band_lo_ghz", "band_hi_ghz"},
        str(cmp_.get("must_match")))
    chk("비교 제외 이유를 적는다", "필터가 거른다" in (cmp_.get("why_not") or ""))

    import glob
    eco = next((Path(p).parent.name for p in glob.glob("work/*/값_카탈로그.json")
                if "Antenna_CAD_ECO" in p), None)
    if eco:
        c = check(eco, {"project": "marine_radar", "asset_kind": "antenna"})
        chk("기하 공진은 어긋남으로 잡지 않는다",
            not any(x["role"].startswith("resonance") for x in c["mismatches"]),
            json.dumps(c["mismatches"], ensure_ascii=False)[:200])
    else:
        chk("실물 대조", True, "(카탈로그 없음 — 건너뜀)")

    # 선언 동작 대역이 다르면 **경고**
    t2 = next((Path(p).parent.name for p in glob.glob("work/*/값_카탈로그.json")
               if p.endswith("-test2/값_카탈로그.json")), None)
    if t2:
        c2 = check(t2, {"project": "marine_radar", "asset_kind": "antenna"})
        w = [x for x in c2["mismatches"] if x.get("severity") == "warn"]
        chk(f"선언 대역 50~70 GHz vs 목표 9.3~9.5 → 경고 ({len(w)}건)", len(w) >= 1,
            json.dumps(c2["mismatches"], ensure_ascii=False)[:200])
        chk("어긋남의 세 가지 뜻을 함께 적는다",
            all("도구가 틀렸다" in x["why"] for x in w))
    else:
        chk("선언 대역 경고", True, "(표본 없음 — 건너뜀)")

    # 입력 항목이 없어도 돈다
    import tempfile
    c0 = check("없는run", {}, Path(tempfile.mkdtemp()))
    chk("입력 항목 없으면 대조하지 않는다", c0["declared"] is False and not c0["mismatches"])

    chk("판정 규칙이 산지와 함께 실린다",
        {"manifest_band_tolerance", "manifest_er_tolerance"} <= set(numeric_rules()))
    chk("어긋남은 판정이 아니라고 명시", "판정이 아니다" in numeric_rules()["규율"])

    print(f"\n결과: {ok}/{ok + fail} PASS")
    return 0 if fail == 0 else 1


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd = argv[1]
    if cmd == "self-test":
        return self_test()
    if cmd == "template":
        pj = argv[3] if len(argv) > 3 and argv[2] == "--project" else None
        print(json.dumps(template(pj), ensure_ascii=False, indent=2))
        return 0
    if cmd == "resolve":
        print(json.dumps(resolve(C.read_json(argv[2])), ensure_ascii=False, indent=2))
        return 0
    if cmd == "check":
        m = C.read_json(argv[3]) if len(argv) > 3 else None
        r = check(argv[2], m)
        if not r["declared"]:
            print(r["note"])
            return 0
        print(f"선언 대역 {r['operating_ghz']} GHz · 종류 {r['asset_kind']}")
        print(f"어긋남 {r['n_mismatch']}건")
        for x in r["mismatches"]:
            print(f"  [{x['field']}] {x['role']}: 선언 {x['declared']} ↔ 관측 {x['observed']}")
            print(f"      {x['why']}")
        if r["expected_missing"]:
            print(f"\n이 종류에서 기대되는데 없는 역할 {len(r['expected_missing'])}종: "
                  f"{r['expected_missing']}")
        return 0
    if cmd in ("bands", "kinds", "projects"):
        d = {"bands": bands, "kinds": kinds, "projects": projects}[cmd]()
        key = {"bands": "bands", "kinds": "kinds", "projects": "projects"}[cmd]
        for k, v in (d.get(key) or {}).items():
            extra = ""
            if cmd == "bands":
                extra = f"  {v.get('range_ghz')} GHz  [{v.get('kind')}]"
                for pn, pv in (v.get("operating") or {}).items():
                    extra += f"\n      운용 {pn}: {pv.get('range_ghz')} GHz"
            elif cmd == "kinds":
                extra = f"  기대 역할 {len(v.get('expected_roles') or [])}종" + \
                        ("  [확정 대기]" if v.get("확정_대기") else "")
            else:
                extra = f"  팀 {v.get('team')} · 대역 {v.get('band')} · 단계 {v.get('stage')}"
            print(f"  {k:<16} {v.get('label', '')}{extra}")
        return 0
    print(f"알 수 없는 명령: {cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
