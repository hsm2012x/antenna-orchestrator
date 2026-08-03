#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/gate.py — 참조 무결성 게이트 + 결정론 치환 (LLM 0콜)

Level 1 의 합격선. 계약 개정(2026-07-31)으로 성격이 바뀌었다.

    이전   문서의 모든 수치를 산출값 집합과 **전수 대조**한다.
    이후   문서는 수치를 쓰지 않는다. 참조 `{{v:키}}` 만 쓰고, 게이트가 카탈로그에서
           **치환**한다. 게이트는 대조가 아니라 **참조 무결성**을 본다.

왜 바꿨나 — 대조는 유효자리·단위 환산·절 번호/날짜 예외라는 경우의 수를 끝없이 만든다.
숫자를 쓰지 못하게 하면 그 경우의 수가 통째로 사라진다. 창작된 수치가 문서에 **들어갈 수
없으므로**, 게이트는 최후 방어선이 아니라 구조적 불가능이 된다(N-1 강화).

검사 5종
    undefined_key      카탈로그에 없는 키를 참조했다        → 값 창작 시도
    unknown_sigil      등재되지 않은 시길                   → 구문 오용
    malformed_ref      {{ }} 인데 참조 형태가 아니다        → 구문 오용
    empty_value        빈 값을 값으로 인용했다              → I-5 위반(빈 값은 담당 표기)
    bare_number        참조 밖 본문에 맨 숫자가 남았다      → 타이핑한 수치

닫히지 않는 것 — **키 오배치**. 실재하는 값을 틀린 문장에 붙이는 것은 참조가 유효하므로
게이트가 못 잡는다. 이것은 사람 검수의 몫이며, 그래서 치환본에 참조 키를 각주로 남긴다.

맨 숫자 예외는 **조용히 넘기지 않는다** — 적용된 예외를 전부 exempt_hits[] 에 남겨
사람 검수 화면에서 보이게 한다. 예외가 사각지대가 되는 것을 막는 유일한 방법이다.

CLI
    python tools/gate.py check <run_id> [초안.md]
    python tools/gate.py self-test
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _common as C          # noqa: E402
import catalog as CAT        # noqa: E402
import roles as R            # noqa: E402

DRAFT_NAME = "초안.md"                 # 프리즘 산출 — 참조본
SUBSTITUTED_NAME = "초안_치환.md"       # 게이트 산출 — 사람이 읽는 형태
VERDICT_NAME = "게이트_판정.json"

VIOLATION_KINDS = ("not_recorded", "template_modified", "undefined_key", "missing_figure",
                   "not_a_figure", "role_mismatch",
                   "role_unmapped", "unknown_sigil", "malformed_ref", "empty_value",
                   "bare_number", "unsubstituted_ref", "prose_unwritten")

# 서술 마커 — docspec 과 공유하는 형태. 마커 **사이**가 LLM 이 쓰는 유일한 자리다.
PROSE_RE = re.compile(r"<!--\s*PROSE:([^\s]+)[^>]*-->(.*?)<!--\s*/PROSE:\1\s*-->", re.S)
_SLOT_PAT = r"<!--\s*PROSE:[^>]*-->.*?<!--\s*/PROSE:[^>]*-->"

# ── 맨 숫자 예외 — 판정 규칙 (c). numeric_rules() 로 원장에 실린다 ─────────────
# 예외의 근거는 "값 주장이 아니다"뿐이다. 값을 주장하는 자리는 예외가 될 수 없다.
EXEMPTIONS = (
    ("md_heading_number", r"^(#{1,6}\s+)([0-9]+(?:\.[0-9]+)*)(?=[\s.])",
     "마크다운 제목의 절 번호 — 문서 구조이지 값이 아니다"),
    ("md_ordered_list", r"^(\s*)([0-9]+)(?=[.)]\s)",
     "순서 목록 표지 — 문서 구조이지 값이 아니다"),
    ("md_table_rule", r"^\s*\|?[\s:\|\-]+\|?\s*$",
     "표 구분행 — 문자 장식이지 값이 아니다"),
    ("inline_code", r"`[^`\n]*`",
     "인라인 코드 — 경로·명령·키의 축자 인용. 값 주장이 아니다(적용 위치를 전수 기록한다)"),
    ("fenced_code", r"^```.*?^```",
     "펜스 코드블록 — 축자 인용. 값 주장이 아니다(적용 위치를 전수 기록한다)"),
    ("html_comment", r"<!--.*?-->",
     "HTML 주석 — 서술 마커와 지침. 렌더되지 않으므로 독자에게 값 주장이 아니고, "
     "내용은 템플릿이 정하므로 template_modified 가 변경을 잡는다"),
)

# sentinel 에 숫자를 쓰지 않는다 — 맨 숫자 검사가 자기 자신을 잡는다.
_SENTINEL_RE = re.compile(r"\x00([A-J]+)\x01")


def _sentinel(i: int) -> str:
    return "\x00" + "".join(chr(ord("A") + int(d)) for d in str(i)) + "\x01"


def _unsentinel(s: str) -> int:
    return int("".join(str(ord(c) - ord("A")) for c in s))
_DIGIT_RE = re.compile(r"[0-9]")
_NUMBER_RE = re.compile(r"[-+−]?\d+(?:[.,]\d+)*(?:[eE][-+]?\d+)?")


def numeric_rules() -> dict:
    """게이트가 쓰는 판정 규칙의 산지 기록. 원장 gate 이벤트에 그대로 실린다."""
    r = dict(C.numeric_rules())
    r["gate_mode"] = "reference_binding"
    r["gate_render_rule"] = CAT.RENDER_RULE
    r["gate_ref_sigils"] = sorted(CAT.REF_SIGILS)
    r["gate_bare_number_exemptions"] = {
        **{name: why for name, _, why in EXEMPTIONS},
        TEMPLATE_LINE_EXEMPTION[0]: TEMPLATE_LINE_EXEMPTION[1],
    }
    r["gate_role_vocab_size"] = len(R.ROLES)
    r["gate_blind_spot"] = ("역할 미선언 참조 — 골격 슬롯 밖 자유 서술에서는 역할과 키를 "
                            "모두 프리즘이 정하므로 두 진술이 독립이 아니고, 키 오배치가 "
                            "대조로 드러나지 않는다. 치환본 산지 표가 이를 '역할 미선언'으로 "
                            "표시해 사람 검수에 넘긴다.")
    return r


# ── 예외 마스킹 ──────────────────────────────────────────────────────────────

TEMPLATE_LINE_EXEMPTION = (
    "template_line",
    "골격이 이미 담고 있던 줄 — 프리즘이 타이핑한 숫자가 아니다. 규칙 코드(T-3 · EXT-2)나 "
    "규격 문서명(IPC-4552)이 부재 사유·대장에 들어간다. 줄이 바뀌면 template_modified 가 "
    "먼저 잡으므로 이 예외 뒤에 값 주장이 숨을 수 없다")


def _mask_template_lines(text: str, skeleton: str) -> tuple[str, list[dict]]:
    """골격과 **글자까지 같은 줄**의 숫자를 가린다.

    맨 숫자 검사의 목적은 "LLM 이 값을 타이핑했나"다. 골격에 이미 있던 줄은 LLM 이 쓴 것이
    아니므로 검사 대상이 아니다. 이 예외가 뚫리지 않는 이유는 **골격이 결정론이기 때문**이다 —
    프리즘이 그 줄을 건드리면 글자가 달라져 예외에서 빠지고, 다른 곳으로 옮기면
    `template_modified` 가 구조 변경으로 잡는다.
    """
    keep = {ln for ln in skeleton.splitlines() if _DIGIT_RE.search(ln)}
    if not keep:
        return text, []
    hits, out = [], []
    for i, ln in enumerate(text.splitlines(keepends=True), 1):
        bare = ln.rstrip("\n")
        if bare in keep:
            hits.append({"exemption": TEMPLATE_LINE_EXEMPTION[0], "line": i,
                         "text": bare[:80], "why": TEMPLATE_LINE_EXEMPTION[1]})
            out.append(_DIGIT_RE.sub("•", bare) + ln[len(bare):])
        else:
            out.append(ln)
    return "".join(out), hits


def _mask_exemptions(text: str, skeleton: str | None = None) -> tuple[str, list[dict]]:
    """예외에 해당하는 구간의 숫자를 가린다. 무엇을 가렸는지 전부 기록한다."""
    hits: list[dict] = []
    masked = text
    if skeleton:
        masked, th = _mask_template_lines(masked, skeleton)
        hits += th
    for name, pat, why in EXEMPTIONS:
        flags = re.M | (re.S if name in ("fenced_code", "html_comment") else 0)
        out = []
        pos = 0
        for m in re.finditer(pat, masked, flags):
            seg = m.group(0)
            if not _DIGIT_RE.search(seg):
                continue
            # 제목·목록은 번호 부분만, 나머지는 구간 전체
            if name in ("md_heading_number", "md_ordered_list"):
                s, e = m.start(2), m.end(2)
            else:
                s, e = m.start(), m.end()
            line = masked.count("\n", 0, s) + 1
            hits.append({"exemption": name, "line": line,
                         "text": masked[s:e][:80], "why": why})
            out.append(masked[pos:s])
            out.append(_DIGIT_RE.sub("\u2022", masked[s:e]))
            pos = e
        out.append(masked[pos:])
        masked = "".join(out)
    return masked, hits


# ── 본체 ────────────────────────────────────────────────────────────────────

def _prose_regions(text: str) -> list[tuple[str, str]]:
    return [(m.group(1), m.group(2)) for m in PROSE_RE.finditer(text)]


def _strip_prose(text: str) -> str:
    """서술 내용을 지우고 마커만 남긴다 — 템플릿 부분만 비교하기 위해."""
    return PROSE_RE.sub(lambda m: f"<!--PROSE:{m.group(1)}--><!--/PROSE:{m.group(1)}-->", text)


PLACEHOLDER_KEY = "<키>"


def _norm_template(text: str, pinned: set | None = None) -> str:
    """키가 채워진 참조와 골격의 `<키>` 를 같은 것으로 본다 — 키 선택은 프리즘의 일이다.

    ★ 골격이 **키를 이미 박아 둔 자리**는 예외다(행렬의 행).
      행렬은 행마다 키가 정해져 있고 그 짝이 곧 뜻이다 — 9300 행의 이득은 9300 의
      이득이어야 한다. 여기서까지 키를 별표로 뭉개면 프리즘이 행을 바꿔치기해도
      역할은 그대로라 아무 검사에도 걸리지 않는다. 역할 대조는 칸의 **종류**를 지키지
      행의 **짝**을 지키지 못하기 때문에, 짝은 템플릿 불변이 지켜야 한다.
    """
    t = _strip_prose(text)
    t = CAT.REF_RE.sub(
        lambda m: "{{%s:%s|%s}}" % (
            m.group(1),
            m.group(2) if (pinned and m.group(2) in pinned) else "*",
            (m.group(3) or "")),
        t)
    return "\n".join(ln.rstrip() for ln in t.strip().splitlines() if ln.strip())


def pinned_keys(skeleton: str) -> set:
    """골격이 **직접 박은** 키 — 슬롯(`<키>`)이 아닌 것 전부.

    행렬의 행이 여기 해당한다. 이 키들은 프리즘의 선택지가 아니라 템플릿의 일부다.
    """
    return {m.group(2) for m in CAT.REF_RE.finditer(_strip_prose(skeleton))
            if m.group(2) != PLACEHOLDER_KEY}


def check_template(document: str, skeleton: str) -> list[dict]:
    """템플릿 불변 검사.

    템플릿은 결정론으로 재생성되므로 **LLM 이 무엇을 바꿨는지 기계적으로 알 수 있다.**
    프리즘에게 허용된 변경은 둘뿐이다 — 슬롯 `<키>` 를 키로 바꾸는 것, 마커 사이를 쓰는 것.
    그 밖의 변경(표 삭제·행 추가·제목 수정·역할 바꾸기)은 전부 위반이다.
    """
    import difflib
    pin = pinned_keys(skeleton)
    a = _norm_template(skeleton, pin).splitlines()
    b = _norm_template(document, pin).splitlines()
    if a == b:
        return []
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes():
        if tag == "equal":
            continue
        out.append({
            "kind": "template_modified", "location": f"template line {i1 + 1}",
            "op": tag,
            "expected": " / ".join(a[i1:i2])[:160],
            "got": " / ".join(b[j1:j2])[:160],
            "why": ("템플릿을 고쳤다 — 프리즘에게 허용된 변경은 슬롯 <키> 채우기와 "
                    "PROSE 마커 사이 서술뿐이다. 표·제목·역할은 결정론 산출이다."),
        })
    return out[:20]


def gate(document: str, catalog: dict, skeleton: str | None = None,
         fig_root: Path | None = None) -> dict:
    """참조본 + 값 카탈로그 → 판정 + 치환본.

    `fig_root` 를 주면 그림 참조(`{{g:키}}`)의 **파일 실재**까지 본다. 그림은 문서 밖
    파일이라 카탈로그만으로는 실재가 보증되지 않는다 — 없는 그림을 가리키는 문서는
    통과할 수 없다(`missing_figure`).

    반환 {pass, violations[{kind, ref|value, location, key?, nearest_keys?}],
          substituted, refs_used[], exempt_hits[]}
    """
    entries = catalog.get("entries", {})
    violations: list[dict] = []
    refs_used: list[dict] = []
    repl: list[str] = []

    def line_of(idx: int) -> int:
        return document.count("\n", 0, idx) + 1

    def _nearest(key: str, n: int = 3) -> list[str]:
        tail = key.rsplit(".", 1)[-1].lower()
        head = key.split(".", 1)[0].lower()
        scored = []
        for k in entries:
            kl = k.lower()
            s = (2 if kl.startswith(head) else 0) + (3 if tail and tail in kl else 0)
            if s:
                scored.append((s, k))
        scored.sort(key=lambda t: (-t[0], len(t[1])))
        return [k for _, k in scored[:n]]

    # 1) 참조 치환 — 자리를 sentinel 로 남겨 맨 숫자 검사에서 제외한다
    def _sub(m: re.Match) -> str:
        raw = m.group(0)
        loc = line_of(m.start())
        mm = CAT.REF_RE.fullmatch(raw)
        if not mm:
            violations.append({"kind": "malformed_ref", "ref": raw[:120], "location": f"line {loc}",
                               "why": "참조 형태가 아니다 — {{시길:키}} 로 쓴다"})
            return _keep(raw)
        sigil, key, declared_role = mm.group(1), mm.group(2), mm.group(3)
        if sigil not in CAT.REF_SIGILS:
            violations.append({"kind": "unknown_sigil", "ref": raw[:120], "location": f"line {loc}",
                               "sigil": sigil, "허용": sorted(CAT.REF_SIGILS)})
            return _keep(raw)
        e = entries.get(key)
        if e is None:
            violations.append({"kind": "undefined_key", "ref": raw[:120], "location": f"line {loc}",
                               "key": key, "nearest_keys": _nearest(key),
                               "why": "카탈로그에 없는 값이다 — 도구가 산출하지 않은 값은 쓸 수 없다(N-1)"})
            return _keep(raw)
        # 키 오배치 검사 — 역할은 골격(결정론)이, 키는 프리즘이 정한다. 두 진술이 어긋나면
        # 실재하는 값을 틀린 자리에 넣은 것이다. 이것이 '값은 맞는데 문장이 틀린' 경우를
        # 잡는 유일한 결정론 수단이다.
        if declared_role:
            actual = e.get("role")
            if actual is None:
                violations.append({"kind": "role_unmapped", "ref": raw[:120],
                                   "location": f"line {loc}", "key": key,
                                   "declared_role": declared_role,
                                   "why": "이 키의 역할이 어휘에 없다 — tools/roles.py 에 사람이 등재해야 한다"})
                return _keep(raw)
            if actual != declared_role:
                violations.append({"kind": "role_mismatch", "ref": raw[:120],
                                   "location": f"line {loc}", "key": key,
                                   "declared_role": declared_role, "actual_role": actual,
                                   "candidates": [k for k, x in entries.items()
                                                  if x.get("role") == declared_role][:5],
                                   "why": ("슬롯이 요구한 역할과 키의 역할이 다르다 — 키 오배치다. "
                                           "값은 실재하지만 자리가 틀렸다.")})
                return _keep(raw)
        if sigil == "g":
            fig = e.get("figure")
            if not fig:
                violations.append({"kind": "not_a_figure", "ref": raw[:120],
                                   "location": f"line {loc}", "key": key,
                                   "why": "그림 참조인데 그림 항목이 아니다"})
                return _keep(raw)
            # 그림 파일이 실제로 있는지 본다 — 없는 그림을 가리키는 문서는 통과할 수 없다.
            # 숫자와 달리 그림은 **문서 밖 파일**이라 카탈로그만으로는 실재를 보증하지 못한다.
            fs = fig.get("fs_path") or fig["path"]
            if fig_root is not None and not (Path(fig_root) / fs).exists():
                violations.append({"kind": "missing_figure", "ref": raw[:120],
                                   "location": f"line {loc}", "key": key,
                                   "path": fs,
                                   "why": ("카탈로그에는 있는데 파일이 없다 — 렌더 산출이 "
                                           "지워졌거나 경로가 어긋났다")})
                return _keep(raw)
            # 캡션은 **여기서** 만든다. 프리즘이 쓰는 자리가 아니다.
            text = (f"![{fig['alt']}]({fig['path']})\n\n*{fig['caption']}*")
        else:
            text = {"v": e.get("render_with_unit", ""), "n": e.get("render", ""),
                    "u": e.get("unit", ""), "f": e.get("formula", ""),
                    "s": e.get("source", ""), "l": e.get("label", ""),
                    "r": e.get("reason", ""),
                    # 판정 — 해석이 낸 것을 옮긴다. 대조가 없었으면 **없다고 적는다**.
                    # "판정하지 않은 것"과 "통과한 것"은 다르다(I-5).
                    "p": _verdict_text(e)}[sigil]
        # u(단위)는 무단위 값이 정상이므로 빈 것이 결함이 아니다. 나머지는 빈 채로 나갈 수 없다 —
        # 특히 s(출처)가 비면 "출처 없는 숫자"가 문서에 실린다.
        if sigil != "u" and (text is None or text == ""):
            violations.append({"kind": "empty_value", "ref": raw[:120], "location": f"line {loc}",
                               "key": key, "sigil": sigil, "reason": e.get("reason", ""),
                               "why": ("빈 값을 인용했다 — 빈 값은 빈 채로 두고 담당을 표기한다(I-5). "
                                       "출처가 비면 출처 없는 숫자가 된다.")})
            return _keep(raw)
        refs_used.append({"key": key, "sigil": sigil, "line": loc, "text": text,
                          "role": e.get("role"), "quantity": e.get("quantity"),
                          "role_declared": bool(declared_role)})
        repl.append(text)
        return _sentinel(len(repl) - 1)

    kept = []          # 치환하지 않고 원문대로 둔 참조. 아래 잔존 검사의 기준이 된다.

    def _keep(raw: str) -> str:
        kept.append(raw)
        repl.append(raw)
        return _sentinel(len(repl) - 1)

    # 0) 템플릿 불변 — 골격이 주어졌을 때만. 값 검사보다 먼저 본다(구조가 틀리면 나머지는 무의미).
    if skeleton:
        violations.extend(check_template(document, skeleton))

    # 0b) 서술 슬롯 — 자리표시자가 그대로면 쓰지 않은 것이다
    for slot, body in _prose_regions(document):
        if not body.strip() or CAT and body.strip() == "(여기서부터 작성)":
            violations.append({"kind": "prose_unwritten", "location": f"PROSE:{slot}",
                               "slot": slot,
                               "why": "서술 슬롯이 비어 있다 — 마커 사이는 프리즘이 채운다"})

    staged = CAT.REF_LOOSE_RE.sub(_sub, document)

    # 2) 맨 숫자 — 예외를 가린 뒤 남은 숫자만 본다
    masked, exempt_hits = _mask_exemptions(staged, skeleton)
    for m in _NUMBER_RE.finditer(masked):
        if "\x00" in m.group(0):
            continue
        violations.append({
            "kind": "bare_number", "value": m.group(0),
            "location": f"line {masked.count(chr(10), 0, m.start()) + 1}",
            "context": _ctx(masked, m.start(), m.end()),
            "why": "본문에 타이핑한 숫자다 — 값은 카탈로그 참조로만 쓴다(N-1)",
        })

    # 3) 치환 확정
    substituted = _SENTINEL_RE.sub(lambda m: repl[_unsentinel(m.group(1))], staged)
    # 잔존 참조 검사 — 수리: 앞선 구현은 "치환하지 않는 위반"의 종류를 손으로 나열해
    # 비교했다. role_mismatch·role_unmapped 를 나중에 추가하면서 목록이 낡아, 정상적으로
    # 반려된 문서에까지 "게이트 결함 의심"이 덧붙었다. 나열 대신 **실제로 남긴 것**을 센다.
    if CAT.REF_LOOSE_RE.search(substituted) and not kept:
        violations.append({"kind": "unsubstituted_ref", "location": "-",
                           "why": "치환 후에도 참조가 남았다 — 게이트 결함 의심"})

    order = {k: i for i, k in enumerate(VIOLATION_KINDS)}
    violations.sort(key=lambda v: (order.get(v["kind"], 99), v.get("location", "")))
    return {
        "pass": not violations,
        "violations": violations,
        "substituted": substituted,
        "refs_used": refs_used,
        "exempt_hits": exempt_hits,
        "n_refs": len(refs_used),
        "n_entries": len(entries),
    }


def _verdict_text(e: dict) -> str:
    """카탈로그 항목의 판정을 사람이 읽는 한 마디로.

    ★ 빈 문자열을 내지 않는다 — `empty_value` 로 반려되어 표가 통째로 막힌다.
      대조가 없었다는 것도 **정보**다: 이 값은 아무 요구와도 견주어지지 않았다는 뜻이다.
    """
    v = e.get("판정")
    if v and v != "대조 없음":
        return v
    if e.get("reason"):
        return "판정 안 함"
    return "대조 없음"


def _ctx(text: str, s: int, e: int, w: int = 28) -> str:
    return text[max(0, s - w):e + w].replace("\n", "⏎").replace("\u2022", "#")


# ── run 결합 ────────────────────────────────────────────────────────────────

def check_run(run_id: str, draft: Path | None = None, *, write_ledger: bool = True,
              attempt: int = 1, register_asset: bool = True) -> dict:
    work = C.work_dir(run_id)
    draft = Path(draft) if draft else work / DRAFT_NAME
    if not draft.exists():
        raise FileNotFoundError(f"참조본이 없다: {draft}")
    try:
        cat = CAT.load(run_id, work)
    except Exception:
        cat = CAT.build(run_id, work)
        C.write_json(work / CAT.CATALOG_NAME, cat)

    sk_path = work / "골격.md"
    skeleton = sk_path.read_text(encoding="utf-8") if sk_path.exists() else None
    r = gate(draft.read_text(encoding="utf-8"), cat, skeleton, fig_root=work)
    (work / SUBSTITUTED_NAME).write_text(_with_footnotes(r), encoding="utf-8")

    verdict = {
        "run_id": run_id, "rule_version": C.effective_rule_version(),
        "draft": str(draft), "pass": r["pass"],
        "violations": r["violations"], "n_refs": r["n_refs"],
        "n_entries": r["n_entries"], "exempt_hits": r["exempt_hits"],
        "refs_used": r["refs_used"],
        "substituted_path": str(work / SUBSTITUTED_NAME),
        "numeric_rules": numeric_rules(),
    }
    C.write_json(work / VERDICT_NAME, verdict)

    if write_ledger:
        try:
            import ledger as L
            conn = L.open_ledger()
            L.append(conn, run_id, "gate",
                     {"pass": r["pass"], "n_violations": len(r["violations"]),
                      "violation_kinds": sorted({v["kind"] for v in r["violations"]}),
                      "n_refs": r["n_refs"], "n_exempt": len(r["exempt_hits"]),
                      "numeric_rules": numeric_rules()},
                     state="COMPOSE" if not r["pass"] else "REVIEW",
                     attempt=attempt, chosen_by="rule")
            conn.close()
        except Exception as exc:
            # 수리: 앞선 구현은 원장 실패를 삼키고 "통과"를 선언했다. B-3 위반이다 —
            # 기록되지 않은 판정은 존재하지 않는 판정이고, 통과를 선언할 근거가 없다.
            verdict["pass"] = False
            verdict["recorded"] = False
            verdict["ledger_error"] = str(exc)
            verdict["violations"] = [{
                "kind": "not_recorded", "location": "-", "why":
                f"원장에 gate 이벤트를 남기지 못했다 — 기록 없는 실행은 하지 않은 것이다(B-3): {exc}"
            }] + verdict["violations"]
            C.write_json(work / VERDICT_NAME, verdict)
            return verdict
        verdict["recorded"] = True

    # 통과본만 자산 DB 에 등재한다 — "어느 안테나가 자산화되었나"의 근거가 된다.
    # 자산 DB 는 파생물이므로 실패해도 판정을 바꾸지 않는다(원장과 다른 지위다).
    if register_asset and verdict["pass"]:
        try:
            import assets as A
            conn = A.open_db()
            verdict["asset"] = A.ingest(conn, run_id, work)
            conn.close()
        except Exception as exc:
            verdict["asset_error"] = str(exc)
    C.write_json(work / VERDICT_NAME, verdict)
    return verdict


def _with_footnotes(r: dict) -> str:
    if not r["refs_used"]:
        return r["substituted"]
    n_undeclared = sum(1 for u in r["refs_used"] if not u.get("role_declared"))
    lines = [r["substituted"], "", "---", "",
             "## 값의 산지 (게이트 자동 생성 — 사람 검수용)", "",
             f"참조 {len(r['refs_used'])}건 중 **역할 미선언 {n_undeclared}건**. "
             "역할 미선언은 키 오배치가 대조로 걸러지지 않은 자리다 — 눈으로 확인한다.", "",
             "| 줄 | 키 | 역할 | 인용된 값 |", "| --- | --- | --- | --- |"]
    seen = set()
    for u in r["refs_used"]:
        sig = (u["line"], u["key"], u["sigil"])
        if sig in seen:
            continue
        seen.add(sig)
        role = u.get("role") or "-"
        mark = "" if u.get("role_declared") else " ⚠미선언"
        lines.append(f"| {u['line']} | `{u['key']}` | {role}{mark} | {u['text']} |")
    return "\n".join(lines) + "\n"


# ── 자기 시험 — 값은 전부 실물 산출에서 가져온다(합성 금지) ────────────────────

def self_test(run_id: str = "s7-test2") -> int:
    ok = fail = 0

    def chk(name, cond, detail=""):
        nonlocal ok, fail
        if cond:
            ok += 1
            print(f"  PASS  {name}")
        else:
            fail += 1
            print(f"  FAIL  {name}  {detail}")

    print(f"[gate.py 자기 시험 — 실물 run {run_id}]")
    try:
        cat = CAT.build(run_id)
    except Exception as exc:
        print(f"  건너뜀 — 실물 산출 없음: {exc}")
        return 2
    E = cat["entries"]
    kf = next(k for k in E if k.startswith("해석.선언_주파수_대역_하한"))
    kn = next(k for k in E if k.startswith("해석.소자_수"))
    kempty = next((k for k in E if E[k]["render"] == ""), None)

    # 1 정상 — 참조만 쓴 문서
    doc = (f"# 안테나 요약\n\n대역 하한은 {{{{v:{kf}}}}} 이고 소자는 {{{{n:{kn}}}}} 개다.\n"
           f"계산식: {{{{f:{kf}}}}} · 출처: {{{{s:{kf}}}}}\n")
    r = gate(doc, cat)
    chk("정상 참조본 통과", r["pass"], json.dumps(r["violations"], ensure_ascii=False)[:200])
    chk("치환값이 실물값과 일치", E[kf]["render_with_unit"] in r["substituted"])
    chk("참조 4건 기록", r["n_refs"] == 4, str(r["n_refs"]))

    # 2 맨 숫자
    r = gate("# 요약\n\n대역 하한은 50 GHz 이다.\n", cat)
    chk("맨 숫자 반려", not r["pass"] and r["violations"][0]["kind"] == "bare_number",
        json.dumps(r["violations"], ensure_ascii=False)[:200])

    # 3 없는 키
    r = gate("값은 {{v:해석.존재하지_않는_항목}} 이다.\n", cat)
    v = r["violations"][0]
    chk("없는 키 반려", not r["pass"] and v["kind"] == "undefined_key", str(v))
    chk("가까운 키 제안", isinstance(v.get("nearest_keys"), list))

    # 4 시길 오용 / 형태 오류
    r = gate("{{z:%s}}" % kf, cat)
    chk("미등록 시길 반려", r["violations"][0]["kind"] == "unknown_sigil")
    r = gate("{{해석.그냥키}}", cat)
    chk("형태 오류 반려", r["violations"][0]["kind"] == "malformed_ref")

    # 5 빈 값
    if kempty:
        r = gate("두께는 {{v:%s}} 이다." % kempty, cat)
        chk("빈 값 인용 반려", r["violations"][0]["kind"] == "empty_value", str(r["violations"][0]))
    else:
        chk("빈 값 인용 반려", True, "(빈 값 항목 없음 — 건너뜀)")

    # 6 예외 — 절 번호·목록·표 구분행·코드
    doc = ("## 3.6 게이트\n\n1. 첫째\n2. 둘째\n\n| a | b |\n| --- | --- |\n| x | y |\n"
           f"경로는 `Model/3D/KORIL_2.5_1.dxf` 이고 값은 {{{{v:{kn}}}}} 이다.\n")
    r = gate(doc, cat)
    chk("구조 숫자는 통과", r["pass"], json.dumps(r["violations"], ensure_ascii=False)[:300])
    names = {h["exemption"] for h in r["exempt_hits"]}
    chk("예외 적용을 전수 기록", {"md_heading_number", "md_ordered_list", "inline_code"} <= names,
        str(names))

    # 7 예외가 값을 숨기지 못한다는 확인 — 코드 밖 숫자는 여전히 잡힌다
    r = gate("`경로 2.5` 인데 이득은 25 dBi 다.\n", cat)
    chk("코드 밖 숫자는 잡힌다", not r["pass"] and
        any(v["kind"] == "bare_number" and "25" in v.get("value", "") for v in r["violations"]),
        json.dumps(r["violations"], ensure_ascii=False)[:200])

    # 8 키 오배치 — 역할이 선언되면 잡는다
    khp = next((k for k in E if E[k].get("role") == "af_hpbw_deg"), None)
    kgr = next((k for k in E if E[k].get("role") == "af_grating_deg"), None)
    ksll = next((k for k in E if E[k].get("role") == "af_sll_db"), None)
    r = gate(f"반전력 빔폭은 {{{{v:{khp}|af_hpbw_deg}}}} 이다.\n", cat)
    chk("역할 일치는 통과", r["pass"], json.dumps(r["violations"], ensure_ascii=False)[:200])
    wrong = kgr or ksll
    r = gate(f"반전력 빔폭은 {{{{v:{wrong}|af_hpbw_deg}}}} 이다.\n", cat)
    v = r["violations"][0] if r["violations"] else {}
    chk("키 오배치 반려(같은 단위·다른 역할)", not r["pass"] and v.get("kind") == "role_mismatch",
        json.dumps(r["violations"], ensure_ascii=False)[:250])
    chk("오배치 시 올바른 후보 제시", bool(v.get("candidates")), str(v.get("candidates")))

    # 8b 역할 미선언은 여전히 통과한다 — 남은 사각지대를 시험이 명시한다
    r = gate(f"반전력 빔폭은 {{{{v:{wrong}}}}} 이다.\n", cat)
    chk("역할 미선언은 통과한다(남은 사각지대)", r["pass"])
    chk("치환본이 미선언을 표시", "⚠미선언" in _with_footnotes(r))

    # 8c 골격은 모든 슬롯에 역할을 박는다
    sk = CAT.skeleton(cat)
    slots = re.findall(r"\{\{[a-z]:<키>\|([^}|]+)\}\}", sk)
    chk("골격 슬롯 전부 역할 선언", len(slots) > 10 and all(s.strip() for s in slots), str(len(slots)))

    # 8d 행렬 — 골격이 박아 둔 키는 템플릿의 일부다(행의 짝을 지키는 유일한 검사)
    #    역할이 같으므로 `role_mismatch` 는 안 걸린다. 여기서 안 잡으면 아무 데서도 안 잡힌다.
    sk_m = ("| 주파수 | 이득 |\n| --- | --- |\n"
            "| {{l:성능.A.freq|open_item}} | {{v:성능.A.gain|perf_gain_dbi}} |\n"
            "| {{l:성능.B.freq|open_item}} | {{v:성능.B.gain|perf_gain_dbi}} |\n")
    swapped = sk_m.replace("성능.A.gain", "\x00T\x01").replace(
        "성능.B.gain", "성능.A.gain").replace("\x00T\x01", "성능.B.gain")
    chk("행렬 행 바꿔치기를 템플릿 불변이 잡는다",
        any(v["kind"] == "template_modified" for v in check_template(swapped, sk_m)))
    chk("바꾸지 않으면 통과", check_template(sk_m, sk_m) == [])
    #    반대로 슬롯(`<키>`)은 여전히 프리즘이 고른다 — 그것까지 막으면 문서를 못 쓴다
    sk_s = "| {{l:<키>|bbox_x_mm}} | {{v:<키>|bbox_x_mm}} |\n"
    chk("슬롯 키 채우기는 템플릿 변경이 아니다",
        check_template("| {{l:추출.a|bbox_x_mm}} | {{v:추출.a|bbox_x_mm}} |\n", sk_s) == [])

    # 9 치환본에 산지 각주
    r = gate(f"값 {{{{v:{kf}}}}}\n", cat)
    chk("산지 각주 생성", "값의 산지" in _with_footnotes(r) and kf in _with_footnotes(r))

    # 10 결정론
    a = gate(doc, cat)
    b = gate(doc, cat)
    chk("같은 입력 같은 판정", json.dumps(a["violations"], ensure_ascii=False) ==
        json.dumps(b["violations"], ensure_ascii=False) and a["substituted"] == b["substituted"])

    # 11 규칙 기록
    nr = numeric_rules()
    chk("numeric_rules 에 게이트 모드·예외 등재",
        nr["gate_mode"] == "reference_binding"
        and len(nr["gate_bare_number_exemptions"]) == len(EXEMPTIONS) + 1
        and TEMPLATE_LINE_EXEMPTION[0] in nr["gate_bare_number_exemptions"])

    # 11b 템플릿 줄 예외 — 부재 대장의 규칙 코드(T-3)가 맨 숫자로 걸리면 안 된다
    sk_t = ("| 절 | 없는 것 |\n| --- | --- |\n"
            "| 시험 결과 | 측정 성적서는 사람이 반입한다(T-3) |\n")
    rt = gate(sk_t, cat, sk_t)
    chk("골격이 담은 규칙 코드는 맨 숫자가 아니다",
        not any(v["kind"] == "bare_number" for v in rt["violations"]),
        str([v for v in rt["violations"] if v["kind"] == "bare_number"]))
    chk("가린 것을 전부 기록한다",
        any(h["exemption"] == TEMPLATE_LINE_EXEMPTION[0] for h in rt["exempt_hits"]))
    #    ★ 예외가 뚫리지 않는다 — 골격에 없는 줄의 숫자는 그대로 걸린다
    rt2 = gate(sk_t + "| 이득 | 23 dBi 나왔다 |\n", cat, sk_t)
    chk("골격 밖에서 타이핑한 숫자는 여전히 걸린다",
        any(v["kind"] == "bare_number" for v in rt2["violations"]))

    # ── 그림 참조 — 값과 같은 검사를 물려받고, **파일 실재**를 하나 더 본다
    figcat = {"entries": {
        "그림.a": {"key": "그림.a", "role": "figure_2d_overview", "label": "2D 전체도",
                  "render": "figures/a.png", "render_with_unit": "figures/a.png",
                  "unit": "", "source": "render_page", "formula": "", "reason": "",
                  "figure": {"kind": "2d_overview", "path": "figures/a.png",
                             "fs_path": "figures/a.png", "alt": "2D 전체도",
                             "caption": "2D 전체도 · 축척 1:1.00 · 치수 정본",
                             "dimensional": True}},
        "값.x": {"key": "값.x", "role": "bbox_x_mm", "label": "가로", "render": "570",
                "render_with_unit": "570 mm", "unit": "mm", "source": "추출",
                "formula": "", "reason": ""}}}
    import tempfile as _tf
    root = Path(_tf.mkdtemp()); (root / "figures").mkdir()
    (root / "figures" / "a.png").write_bytes(b"\x89PNG")

    g1 = gate("# t\n\n{{g:그림.a|figure_2d_overview}}\n", figcat, fig_root=root)
    chk("그림 참조가 이미지 + 캡션으로 치환된다",
        "![2D 전체도](figures/a.png)" in g1["substituted"]
        and "치수 정본" in g1["substituted"], g1["substituted"][:120])
    chk("그림 참조에 위반이 없다", g1["pass"], str(g1["violations"]))

    g2 = gate("# t\n\n{{g:그림.a}}\n", figcat, fig_root=root / "없음")
    chk("없는 그림 파일을 잡는다",
        [v["kind"] for v in g2["violations"]] == ["missing_figure"], str(g2["violations"]))

    g3 = gate("# t\n\n{{g:값.x}}\n", figcat, fig_root=root)
    chk("그림이 아닌 것을 그림으로 인용하면 잡는다",
        [v["kind"] for v in g3["violations"]] == ["not_a_figure"], str(g3["violations"]))

    g4 = gate("# t\n\n{{g:그림.a|figure_3d_iso}}\n", figcat, fig_root=root)
    chk("그림도 역할 오배치를 잡는다",
        [v["kind"] for v in g4["violations"]] == ["role_mismatch"], str(g4["violations"]))

    g5 = gate("# t\n\n{{g:그림.없음}}\n", figcat, fig_root=root)
    chk("없는 그림 키를 잡는다",
        [v["kind"] for v in g5["violations"]] == ["undefined_key"], str(g5["violations"]))

    # 캡션은 게이트가 만든다 — 문서가 손으로 쓴 캡션은 맨 숫자로 걸린다
    g6 = gate("# t\n\n{{g:그림.a}}\n\n*축척 1:2.5 로 그린 도면*\n", figcat, fig_root=root)
    chk("손으로 쓴 캡션은 맨 숫자로 걸린다",
        any(v["kind"] == "bare_number" for v in g6["violations"]), str(g6["violations"]))

    print(f"\n결과: {ok}/{ok + fail} PASS")
    return 0 if fail == 0 else 1


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    if argv[1] == "self-test":
        return self_test(*argv[2:3])
    if argv[1] == "check":
        v = check_run(argv[2], argv[3] if len(argv) > 3 else None)
        print(f"{'통과' if v['pass'] else '반려'} — 참조 {v['n_refs']}건 · 위반 {len(v['violations'])}건 "
              f"· 예외 {len(v['exempt_hits'])}건")
        for x in v["violations"][:20]:
            print(f"  [{x['kind']}] {x.get('location','')} {x.get('ref') or x.get('value','')}")
        print(f"치환본: {v['substituted_path']}")
        return 0 if v["pass"] else 1
    print(f"알 수 없는 명령: {argv[1]}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
