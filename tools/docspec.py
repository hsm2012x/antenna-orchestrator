#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/docspec.py — 문서 양식 정본 적재·검증 (LLM 0콜)

`registry/document_spec.yaml` 을 읽어 골격 생성기와 포장기가 함께 쓴다.
문서의 골조가 코드가 아니라 데이터에 있으므로, 양식을 바꾸는 데 코드를 고치지 않는다.

검증이 하는 일 — **오타로 절이 조용히 비는 것을 막는다**
    spec 의 role 이 `tools/roles.py` 어휘(또는 메타 키 형태)에 없으면 거부한다.
    I-A 가 경고한 실패 양상이 정확히 이것이다: 역할이 없으면 슬롯이 안 생기고,
    슬롯이 없으면 문서에 안 실리고, 안 실리면 게이트가 통과시킨다.
    **빠진 것은 위반으로 나타나지 않으므로** 적재 시점에 걸러야 한다.

서술 슬롯
    프리즘이 쓰는 유일한 자리. 골격에 마커로 박힌다.

        <!-- PROSE:기하.배열_구성 지침: … -->
        (여기서부터 작성)
        <!-- /PROSE:기하.배열_구성 -->

    마커 밖은 결정론 템플릿이고 게이트가 `template_modified` 로 불변을 지킨다.

CLI
    python tools/docspec.py show          절·역할·서술 슬롯 요약
    python tools/docspec.py check         검증만 (미등재 역할·미사용 역할 보고)
    python tools/docspec.py prose         프리즘에게 줄 서술 지침 블록
    python tools/docspec.py self-test
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C  # noqa: E402
import roles as R    # noqa: E402

DEFAULT_PATH = C.REPO / "registry" / "document_spec.yaml"

PROSE_OPEN = "<!-- PROSE:{slot} 지침: {guide} -->"
PROSE_CLOSE = "<!-- /PROSE:{slot} -->"
PROSE_PLACEHOLDER = "(여기서부터 작성)"

# 역할 어휘에 없어도 되는 이름 — 메타·식별·추출 키는 물리량이 아니라 키가 곧 역할이다.
_META_PREFIX = ("run.", "식별.", "추출.", "해석.", "verify.")

# 절이 취할 수 있는 모양.
#   table   항목 | 값 | 출처            — 값 하나에 한 줄
#   list    - 항목: 값                  — 짧은 머리말
#   figures 그림 블록                    — `figures:` 가 실린다
#   matrix  축 × 열                      — 같은 역할이 조건마다 되풀이되는 표
#           (주파수 × 성능, 성적 항목 × 규격/실측). 행은 카탈로그의 group 이 정한다.
#   gaps    빈 절 대장                  — 골격이 스스로 만든다(카탈로그에서 오지 않는다).
#           절마다 "내용 없음"을 찍는 대신 여기 한 줄로 모은다.
RENDERS = ("table", "list", "figures", "matrix", "gaps")

_FIGURE_PREFIX = "figure_"

# 공백의 종류 — 이것이 가르는 것은 **누가 무엇을 하면 채워지나**다.
#   선언  아는 사람이 말하면 된다. 값은 문서가 아니라 `absent.slot` 으로 들어간다.
#   반입  파일이 들어와야 한다. 말로는 못 채운다.
#   도구  도구가 아직 못 읽는다. 사람이 알아도 소용없다.
GAP_KINDS = ("선언", "반입", "도구")


class SpecError(ValueError):
    pass


def load(path=None) -> dict:
    import yaml
    p = Path(path or os.environ.get("ORCH_DOCUMENT_SPEC", str(DEFAULT_PATH)))
    if not p.exists():
        raise FileNotFoundError(f"문서 양식 정본 없음: {p} — 양식은 코드가 아니라 데이터다")
    spec = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    spec["_path"] = str(p)
    validate(spec)
    return spec


def known_role(name: str) -> bool:
    return name in R.ROLES or str(name).startswith(_META_PREFIX)


def is_figure_role(name: str) -> bool:
    return str(name).startswith(_FIGURE_PREFIX)


def _validate_matrix(sec: dict, sid: str, bad: list) -> None:
    """행렬 절 — 축과 열이 둘 다 있어야 한다.

    축이 없으면 행을 무엇으로 가르는지 모르고, 열이 없으면 실을 것이 없다.
    축 역할을 **여럿 받는** 이유: 같은 축이 원천마다 다른 단위로 온다(MHz · GHz).
    환산하지 않는 것이 규율(N-1)이므로 축은 어느 쪽이든 받는다.
    """
    rend = sec.get("render")
    if rend == "gaps":
        # 대장은 축이 없다 — 행이 카탈로그가 아니라 **빈 절 자체**에서 나온다.
        if sec.get("axis"):
            raise SpecError(f"{sid}: gaps 절에는 axis 가 없다 — 행은 빈 절이 만든다")
        if sec.get("required"):
            raise SpecError(f"{sid}: gaps 절은 required 가 될 수 없다 — "
                            "빈 절이 없으면 이 절도 없는 것이 옳다")
        cols = sec.get("columns") or []
        if not cols:
            raise SpecError(f"{sid}: gaps 절에 columns 가 없다")
        for col in cols:
            if not known_role(col.get("role") or ""):
                bad.append((f"{sid}(gap)", col.get("role")))
            if not (col.get("label") or "").strip():
                raise SpecError(f"{sid}: {col.get('role')} 열에 label 이 없다")
        return
    is_matrix = rend == "matrix"
    has_parts = bool(sec.get("axis") or sec.get("columns"))
    if not is_matrix:
        if has_parts:
            raise SpecError(f"{sid}: axis·columns 는 render: matrix|gaps 에서만 쓴다")
        return
    ax = sec.get("axis") or {}
    ax_roles = ax.get("roles") or []
    if not ax_roles:
        raise SpecError(f"{sid}: matrix 에 axis.roles 가 없다 — 행을 무엇으로 가르는지 모른다")
    if not (ax.get("label") or "").strip():
        raise SpecError(f"{sid}: matrix 에 axis.label 이 없다 — 첫 열 머리글이 빈다")
    for role in ax_roles:
        if not known_role(role):
            bad.append((f"{sid}(axis)", role))
    cols = sec.get("columns") or []
    if not cols:
        raise SpecError(f"{sid}: matrix 에 columns 가 없다 — 실을 것이 없다")
    seen = set()
    for col in cols:
        role, label = col.get("role"), (col.get("label") or "").strip()
        if not role:
            raise SpecError(f"{sid}: 역할 없는 열")
        if not label:
            raise SpecError(f"{sid}: {role} 열에 label 이 없다 — 머리글이 빈다")
        if role in seen:
            raise SpecError(f"{sid}: 열 역할 중복: {role} — 한 행에 같은 역할이 두 칸이면 "
                            "게이트의 역할 대조가 어느 칸을 가리키는지 알 수 없다")
        seen.add(role)
        if not known_role(role):
            bad.append((f"{sid}(col)", role))
        if is_figure_role(role):
            raise SpecError(f"{sid}: 그림 역할은 열이 될 수 없다 — {role}")


def _validate_required(sec: dict, sid: str) -> None:
    """필수 절은 **비어도 사라지지 않는다.** 그러려면 무엇을 찍을지 미리 정해 둬야 한다.

    사유와 담당이 없으면 "없음"만 남고, 그것은 I-5 가 금지한 모양이다 —
    빈 값은 빈 채로 두되 **왜 비었고 누가 채우는지**가 함께 있어야 한다.
    """
    if not sec.get("required"):
        if sec.get("absent"):
            raise SpecError(f"{sid}: absent 는 required: true 에서만 쓴다 — "
                            "필수가 아니면 빈 절은 그냥 사라진다")
        return
    ab = sec.get("absent") or {}
    for k, what in (("why", "왜 비었는지"), ("owner", "누가 채우는지"),
                    ("slot", "어디에 넣으면 채워지는지")):
        if not (ab.get(k) or "").strip():
            raise SpecError(f"{sid}: required 인데 absent.{k} 가 없다 — {what}를 "
                            "적지 않으면 '없음'만 남는다(I-5)")
    if ab.get("kind") not in GAP_KINDS:
        raise SpecError(
            f"{sid}: absent.kind 는 {'|'.join(GAP_KINDS)} 중 하나여야 한다 — {ab.get('kind')!r}. "
            "사람이 말하면 되는 공백과 파일을 기다려야 하는 공백은 다른 종류다")


def validate(spec: dict) -> dict:
    if not spec.get("sections"):
        raise SpecError("sections 가 비었다 — 절이 없으면 문서가 없다")
    seen_id, seen_slot, bad = set(), set(), []
    for sec in spec["sections"]:
        sid = sec.get("id")
        if not sid:
            raise SpecError(f"id 없는 절: {sec.get('title')!r}")
        if sid in seen_id:
            raise SpecError(f"절 id 중복: {sid}")
        seen_id.add(sid)
        if sec.get("render") not in RENDERS:
            raise SpecError(
                f"{sid}: render 는 {'|'.join(RENDERS)} 여야 한다 — {sec.get('render')!r}")
        for role in sec.get("roles") or []:
            if not known_role(role):
                bad.append((sid, role))
            elif is_figure_role(role):
                # 그림은 `figures:` 로 붙인다. `roles:` 에 섞으면 값 표에 그림이 끼거나
                # 반대가 된다 — 골격이 둘을 다른 문법으로 펴기 때문이다.
                raise SpecError(f"{sid}: 그림 역할은 roles 가 아니라 figures 에 — {role}")
        for role in sec.get("figures") or []:
            if not known_role(role):
                bad.append((sid, role))
            elif not is_figure_role(role):
                raise SpecError(f"{sid}: figures 에 그림 아닌 역할 — {role}")
        _validate_matrix(sec, sid, bad)
        _validate_required(sec, sid)
        if not any(sec.get(k) for k in ("roles", "columns", "figures")):
            raise SpecError(f"{sid}: 실을 것이 없다 — roles·columns·figures 가 모두 비었다")
        if sec.get("reuse") and sec.get("render") == "gaps":
            raise SpecError(f"{sid}: gaps 절에 reuse 는 뜻이 없다")
        for pr in sec.get("prose") or []:
            slot = pr.get("slot")
            if not slot:
                raise SpecError(f"{sid}: slot 이름 없는 서술")
            key = f"{sid}.{slot}"
            if key in seen_slot:
                raise SpecError(f"서술 슬롯 중복: {key}")
            seen_slot.add(key)
            if not (pr.get("guide") or "").strip():
                raise SpecError(f"{key}: guide 가 비었다 — 무엇을 쓸지 말하지 않으면 지어낸다")
    gaps = [s for s in spec["sections"] if s.get("render") == "gaps"]
    if len(gaps) > 1:
        raise SpecError(f"대장 절이 {len(gaps)}개다 — 공백을 두 곳에 모으면 모은 것이 아니다")
    if gaps and spec["sections"][-1] is not gaps[0]:
        raise SpecError(f"{gaps[0]['id']}: 대장은 **마지막 절**이어야 한다 — "
                        "앞에 두면 아직 읽지도 않은 절의 공백을 먼저 보게 된다")
    if not gaps and any(s.get("required") for s in spec["sections"]):
        raise SpecError("required 절이 있는데 대장(render: gaps)이 없다 — "
                        "빈 절이 아무 데도 나타나지 않고 조용히 사라진다(I-5)")
    for d in spec.get("deferred_sections") or []:
        for role in d.get("roles") or []:
            if not known_role(role):
                bad.append((f"(보류 {d.get('id')})", role))
    tr = spec.get("title_role")
    if tr and not known_role(tr):
        bad.append(("(title)", tr))
    # ── 절의 공백이 아닌 선언 자리 (D-59) ───────────────────────────────────
    # 형상 대표 렌더러처럼 **어느 절이 비어서가 아니라 취향이라서** 물어야 하는 것이
    # 있다. absent.fields 는 빈 절에만 달리므로 여기 따로 둔다. 자리를 정하는 것은
    # 여전히 양식 정본이다 — 코드에 경로를 박지 않는다.
    for i, f in enumerate(spec.get("declarable_extra") or []):
        where = f"(declarable_extra[{i}])"
        for k in ("path", "label", "role"):
            if not f.get(k):
                raise SpecError(f"{where}: {k} 가 없다 — 자리를 알 수 없는 선언은 받지 않는다")
        if not known_role(f["role"]):
            bad.append((where, f["role"]))
        if f.get("type") == "enum" and not f.get("values"):
            raise SpecError(f"{where}: type 이 enum 인데 values 가 없다 — "
                            "고를 것이 없으면 고르라고 할 수 없다")
    if bad:
        raise SpecError(
            "역할 어휘에 없는 이름이 spec 에 있다 — 오타면 그 절이 조용히 빈다(I-A):\n  " +
            "\n  ".join(f"{s} → {r}" for s, r in bad) +
            "\n  등재: tools/roles.py ROLE_RULES · 확인: python tools/roles.py list")
    return spec


def spec_roles(spec: dict) -> list[str]:
    """spec 이 배치한 역할 전부 — 값·축·열·그림. 하나라도 빠뜨리면
    `unused_roles` 가 배치된 역할을 '미배치'로 잘못 보고한다."""
    out = []
    for sec in spec["sections"]:
        out += list(sec.get("roles") or [])
        out += list(sec.get("figures") or [])
        out += list((sec.get("axis") or {}).get("roles") or [])
        out += [c.get("role") for c in (sec.get("columns") or []) if c.get("role")]
    return out


def section_of(spec: dict, role: str) -> str | None:
    """이 역할이 실리는 절 — 카탈로그가 새 역할을 냈을 때 어디로 가는지 확인용."""
    for sec in spec["sections"]:
        if role in spec_section_roles(sec):
            return sec["id"]
    return None


def spec_section_roles(sec: dict) -> list[str]:
    return (list(sec.get("roles") or []) + list(sec.get("figures") or [])
            + list((sec.get("axis") or {}).get("roles") or [])
            + [c.get("role") for c in (sec.get("columns") or []) if c.get("role")])


def deferred_roles(spec: dict) -> dict:
    """보류된 절이 데리고 있는 역할 — **빠뜨린 것이 아니라 미룬 것**이다.
    구분해 두지 않으면 다음 사람이 오타나 누락으로 읽고 다시 배치한다."""
    return {d["id"]: list(d.get("roles") or []) for d in (spec.get("deferred_sections") or [])}


def unused_roles(spec: dict) -> list[str]:
    """어휘에는 있는데 어느 절에도 배치되지 않은 역할 — 문서에서 통째로 빠지는 값들.
    보류 절의 역할은 제외한다(사유가 적혀 있으므로 '빠진 것'이 아니다)."""
    used = set(spec_roles(spec))
    for rs in deferred_roles(spec).values():
        used |= set(rs)
    return sorted(r for r in R.ROLES if r not in used)


def prose_slots(spec: dict) -> list[dict]:
    out = []
    for sec in spec["sections"]:
        for pr in sec.get("prose") or []:
            out.append({"section": sec["id"], "section_title": sec.get("title", sec["id"]),
                        "slot": pr["slot"], "key": f"{sec['id']}.{pr['slot']}",
                        "guide": " ".join((pr.get("guide") or "").split()),
                        "max_sentences": pr.get("max_sentences"),
                        "skip_if_all_empty": bool(pr.get("skip_if_all_empty"))})
    return out


def prose_block(spec: dict) -> str:
    """프리즘 프롬프트에 실을 서술 지침. 규율 + 슬롯별 지시."""
    lines = ["[서술 규율 — 이것을 어기면 게이트가 반려한다]"]
    for r in spec.get("prose_rules") or []:
        lines.append(f"  · {r}")
    lines += ["", "[쓸 자리 — 마커 사이만 채운다]"]
    for s in prose_slots(spec):
        cap = f" (최대 {s['max_sentences']}문장)" if s["max_sentences"] else ""
        lines.append(f"  {s['key']}{cap}\n      {s['guide']}")
    return "\n".join(lines)


# ── 자기 시험 ────────────────────────────────────────────────────────────────

def self_test() -> int:
    import copy
    ok = fail = 0

    def chk(n, cond, d=""):
        nonlocal ok, fail
        if cond:
            ok += 1
            print(f"  PASS  {n}")
        else:
            fail += 1
            print(f"  FAIL  {n}  {d}")

    print("[docspec.py 자기 시험]")
    spec = load()
    chk(f"양식 적재 (절 {len(spec['sections'])}개)", len(spec["sections"]) >= 6)
    chk("서술 슬롯 존재", len(prose_slots(spec)) >= 5, str(len(prose_slots(spec))))
    chk("모든 역할이 어휘에 있다", all(known_role(r) for r in spec_roles(spec)))
    chk("서술 규율이 프롬프트 블록에 실린다",
        "숫자를 쓰지 않는다" in prose_block(spec) and "마커" in prose_block(spec))

    # 보고서 골조 — 실제 보고서가 담던 것이 절로 서 있는가
    ids = [s["id"] for s in spec["sections"]]
    chk("요구·시뮬·시험·대조가 각각 절이다",
        {"요구명세", "시뮬결과", "시험결과", "요구대조"} <= set(ids), str(ids))
    chk("업데이트 필요 대장이 마지막 절이다",
        spec["sections"][-1]["render"] == "gaps", str(ids[-1]))
    chk("요약이 첫 절이다", ids[0] == "요약", str(ids[0]))
    chk("시제품 · 도면이 앞쪽에 있다", ids.index("시제품") <= 2, str(ids.index("시제품")))
    # 공백의 종류가 갈려 있는가 — "말하면 되는 것"과 "기다려야 하는 것"
    kinds = {(s.get("absent") or {}).get("kind") for s in spec["sections"] if s.get("required")}
    chk("공백 종류가 셋 다 쓰인다", kinds == set(GAP_KINDS), str(kinds))
    decl = [s for s in spec["sections"] if (s.get("absent") or {}).get("kind") == "선언"]
    chk("선언 공백은 기계가 쓸 자리를 갖는다",
        all(s["absent"].get("fields") for s in decl), str([s["id"] for s in decl]))
    chk("선언 자리의 역할·키가 어휘에 있다",
        all(known_role(f["role"]) for s in decl for f in s["absent"]["fields"]
            if f.get("role")))
    mats = [s for s in spec["sections"] if s["render"] == "matrix"]
    chk(f"행렬 절 {len(mats)}개", len(mats) >= 4, str([s["id"] for s in mats]))
    reqd = [s for s in spec["sections"] if s.get("required")]
    chk(f"필수 절 {len(reqd)}개 전부 사유·담당을 갖는다",
        all((s.get("absent") or {}).get("why") and (s["absent"]).get("owner") for s in reqd))

    # 그림이 한 절에 몰려 있지 않은가 — 보고서는 사진을 본문 사이에 둔다
    figsec = [s["id"] for s in spec["sections"] if s.get("figures")]
    chk(f"그림이 {len(figsec)}개 절에 나뉘어 붙는다", len(figsec) >= 4, str(figsec))
    allfigs = [r for s in spec["sections"] for r in (s.get("figures") or [])]
    chk("그림 역할 중복 배치 없음", len(allfigs) == len(set(allfigs)), str(allfigs))
    chk("모든 그림 역할이 figure_ 로 시작", all(is_figure_role(r) for r in allfigs))

    # 오타 방어 — I-A 가 경고한 실패를 적재 시점에 잡는가
    #   절 번호로 짚지 않는다 — 양식이 바뀌면 시험이 먼저 깨진다(실제로 깨졌다).
    def _tbl(s):
        return next(x for x in s["sections"] if x.get("roles") and x["render"] == "table")

    bad = copy.deepcopy(spec)
    _tbl(bad)["roles"].append("af_hpbw_degg")
    try:
        validate(bad)
        chk("오타 역할 거부", False, "통과해 버렸다")
    except SpecError as e:
        chk("오타 역할 거부", "af_hpbw_degg" in str(e))

    def _mat(s):
        return next(x for x in s["sections"] if x["render"] == "matrix")

    def _req(s):
        return next(x for x in s["sections"] if x.get("required"))

    for mut, name in (
        (lambda s: s["sections"][0].pop("id"), "id 없는 절 거부"),
        (lambda s: s["sections"][0].update(render="카드"), "알 수 없는 render 거부"),
        (lambda s: _tbl(s)["prose"][0].update(guide="  "), "빈 guide 거부"),
        # 행렬 — 축이나 열이 빠지면 표가 성립하지 않는다
        (lambda s: _mat(s).pop("axis"), "축 없는 행렬 거부"),
        (lambda s: _mat(s).update(columns=[]), "열 없는 행렬 거부"),
        (lambda s: _mat(s)["axis"].update(label=""), "축 머리글 없는 행렬 거부"),
        (lambda s: _mat(s)["columns"].append(dict(_mat(s)["columns"][0])),
         "열 역할 중복 거부"),
        (lambda s: _mat(s)["columns"].append({"role": "figure_3d_iso", "label": "그림"}),
         "그림을 열로 쓰는 것 거부"),
        # 그림과 값의 자리를 섞지 않는다
        (lambda s: _tbl(s)["roles"].append("figure_3d_top"),
         "roles 에 그림 역할 거부"),
        (lambda s: _tbl(s).update(figures=["band_lo_ghz"]),
         "figures 에 값 역할 거부"),
        # 필수 절 — 비었을 때 무엇을 찍을지 없으면 '없음'만 남는다
        (lambda s: _req(s)["absent"].update(owner=""), "담당 없는 필수 절 거부"),
        (lambda s: _req(s)["absent"].update(why=" "), "사유 없는 필수 절 거부"),
        (lambda s: _req(s).pop("absent"), "absent 없는 필수 절 거부"),
        # 필수가 아닌데 absent 를 단 절 — 사라질 절에 사유를 적어 두면 아무도 못 본다
        (lambda s: next(x for x in s["sections"]
                        if not x.get("required") and x["render"] != "gaps")
         .update(absent={"why": "x", "owner": "y", "kind": "선언", "slot": "z"}),
         "필수 아닌 절의 absent 거부"),
        (lambda s: _req(s)["absent"].update(kind="아무거나"), "모르는 공백 종류 거부"),
        (lambda s: _req(s)["absent"].update(slot=""), "적을 자리 없는 필수 절 거부"),
        (lambda s: [x for x in s["sections"] if x["render"] == "gaps"][0]
         .update(required=True), "대장을 필수로 만드는 것 거부"),
        (lambda s: s["sections"].insert(0, s["sections"].pop()), "대장이 마지막이 아니면 거부"),
        # 행렬 아닌 절에 축·열
        (lambda s: _tbl(s).update(axis={"label": "x", "roles": ["open_item"]}),
         "표 절의 axis 거부"),
    ):
        b = copy.deepcopy(spec)
        try:
            mut(b)
            validate(b)
            chk(name, False, "통과해 버렸다")
        except (SpecError, KeyError):
            chk(name, True)

    b = copy.deepcopy(spec)
    b["sections"].append(dict(b["sections"][0]))
    try:
        validate(b)
        chk("절 id 중복 거부", False)
    except SpecError:
        chk("절 id 중복 거부", True)

    # 미배치 역할 — 거부는 아니지만 보이게 한다
    uu = unused_roles(spec)
    chk(f"미배치 역할 보고 ({len(uu)}종)", isinstance(uu, list),
        ", ".join(uu[:8]))

    # HTML 배치도 역할 이름을 쓴다
    tiles = [t["role"] for t in (spec.get("html") or {}).get("tiles") or []]
    chk("HTML 타일 역할이 어휘에 있다", all(known_role(r) for r in tiles), str(tiles))

    print(f"\n결과: {ok}/{ok + fail} PASS")
    return 0 if fail == 0 else 1


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    if argv[1] == "self-test":
        return self_test()
    spec = load(argv[2] if len(argv) > 2 else None)
    if argv[1] == "show":
        print(f"{spec['title']}  · {spec['spec_version']}  ({spec['_path']})")
        for sec in spec["sections"]:
            mark = " ★필수" if sec.get("required") else ""
            lane = f"  ·{sec['lane']}" if sec.get("lane") else ""
            print(f"\n  {sec['title']}   [{sec['render']}]{mark}{lane}")
            ax = sec.get("axis") or {}
            if ax:
                print(f"      ┏ 축 {ax.get('label')} ← {' | '.join(ax.get('roles') or [])}")
            for col in sec.get("columns") or []:
                print(f"      ┃ 열 {col['label']:<12} {col['role']}")
            for r in sec.get("roles") or []:
                print(f"      {r}")
            for r in sec.get("figures") or []:
                print(f"      ▣ {r}")
            for pr in sec.get("prose") or []:
                print(f"      ✎ {pr['slot']}")
            if sec.get("required"):
                ab = sec.get("absent") or {}
                print(f"      ∅ 비면: {' '.join((ab.get('why') or '').split())[:60]}…"
                      f"  담당 {ab.get('owner')}")
        return 0
    if argv[1] == "check":
        uu = unused_roles(spec)
        print(f"검증 통과 — 절 {len(spec['sections'])} · 역할 {len(spec_roles(spec))} "
              f"· 서술 슬롯 {len(prose_slots(spec))}")
        print(f"\n어느 절에도 배치되지 않은 역할 {len(uu)}종 (문서에서 빠진다):")
        for r in uu:
            print(f"  {r:<30} {R.ROLE_DESC.get(r, '')}")
        return 0
    if argv[1] == "prose":
        print(prose_block(spec))
        return 0
    print(f"알 수 없는 명령: {argv[1]}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
