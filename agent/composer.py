#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent/composer.py — 문서 조립(프리즘)의 자리

프리즘은 **Level 1 에서 유일한 LLM 자리**다(게이트·도구는 전부 결정론). 여기서 나오는 것은
`초안.md` 하나이고, 그마저 게이트를 통과해야 산출 영역에 닿는다.

두 구현이 같은 계약을 갖는다
    compose(run_id, catalog, skeleton, violations=None, attempt=1) -> str(참조본)

    SkeletonComposer   결정론 대역 — 골격의 `<키>` 를 역할에 맞는 키로 채운다. LLM 0콜.
                       상태 머신을 LLM 없이 시험하기 위한 것이다. **문서의 서술 품질은 없다** —
                       표를 채울 뿐이다. 이것이 최종 산출물인 척하지 않는다.
    PrismComposer      vLLM 경유 실제 프리즘(5단계). 투입 프롬프트는 PROMPT_LEVEL1.md 2절을
                       파일로 분리해 로드한다 — 코드에 하드코딩하지 않는다.

프리즘은 **숫자를 쓰지 않는다.** 골격이 정한 역할은 그대로 두고 `<키>` 만 고른다.
역할과 키의 출처가 달라야 키 오배치가 게이트에서 드러난다.

게이트 반려를 받으면 `violations` 가 들어온다. 위반 항목만 고치고 나머지는 건드리지 않는다.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

SLOT_RE = re.compile(r"\{\{([a-z]):<키>\|([^}|]+)\}\}")
FILLED_RE = re.compile(r"\{\{([a-z]):([^}|]+)\|([^}|]+)\}\}")
# 서술 마커 — gate.PROSE_RE 와 같은 형태. 여는 마커에 지침이 실려 있다.
PROSE_RE = re.compile(r"(<!--\s*PROSE:([^\s]+)([^>]*)-->)(.*?)(<!--\s*/PROSE:\2\s*-->)", re.S)


def prose_slots_of(skeleton: str) -> list[dict]:
    """골격에서 서술 슬롯과 지침을 뽑는다. 프리즘 프롬프트가 이것으로 만들어진다."""
    out = []
    for m in PROSE_RE.finditer(skeleton):
        guide = m.group(3).strip()
        if guide.startswith("지침:"):
            guide = guide[3:].strip()
        out.append({"slot": m.group(2), "guide": guide})
    return out


def fill_prose(doc: str, writer) -> str:
    """마커 **사이만** 바꾼다. 마커 자체와 그 밖은 건드리지 않는다(template_modified)."""
    def rep(m):
        guide = m.group(3).strip()
        if guide.startswith("지침:"):
            guide = guide[3:].strip()
        body = (writer(m.group(2), guide) or "").strip()
        return f"{m.group(1)}\n{body}\n{m.group(5)}"
    return PROSE_RE.sub(rep, doc)


class SkeletonComposer:
    """결정론 대역. 역할별 후보 키를 **정해진 순서**로 배정한다 — 같은 입력에 같은 문서."""

    name = "skeleton"
    llm_calls = 0

    def __init__(self, fault: str | None = None):
        # fault 는 **시험용**이다. 게이트 반려 고리를 실물로 돌리려면 위반본이 필요한데,
        # 위반본을 손으로 써 두면 골격이 바뀔 때 같이 썩는다. 골격에서 만들어 낸다.
        self.fault = fault

    def compose(self, run_id, catalog, skeleton, violations=None, attempt=1) -> str:
        by_role: dict[str, list[str]] = {}
        for k, e in catalog["entries"].items():
            by_role.setdefault(e.get("role") or "", []).append(k)
        used: set[tuple] = set()

        def pick(m):
            sig, role = m.group(1), m.group(2)
            cands = by_role.get(role) or []
            for k in cands:
                if (k, sig) not in used:
                    used.add((k, sig))
                    return "{{%s:%s|%s}}" % (sig, k, role)
            return "{{%s:%s|%s}}" % (sig, cands[0] if cands else "없는키", role)

        doc = fill_prose(SLOT_RE.sub(pick, skeleton), self._prose)

        # 반려 고리 시험용 결함 주입 — 첫 시도에만 넣고, 반려를 받으면 고친다.
        # always_bad 만 예외로 매번 넣는다(반려 상한 시험용).
        if self.fault == self.ALWAYS_BAD:
            return self._inject(doc, by_role)
        if self.fault and attempt == 1 and not violations:
            doc = self._inject(doc, by_role)
        return doc

    @staticmethod
    def _prose(slot: str, guide: str) -> str:
        # 대역은 서술을 **쓰지 않는다**. 쓴 척하는 문장이 최종 문서에 남는 것이 가장 나쁘다.
        # 숫자가 없으므로 게이트를 통과하고, 읽는 사람은 대역임을 바로 안다.
        return f"(결정론 대역이 비워 둔 자리 — 서술은 프리즘이 쓴다. 슬롯 {slot})"

    ALWAYS_BAD = "always_bad"

    def _inject(self, doc: str, by_role) -> str:
        """결함을 **서술 마커 안**에 넣는다.

        마커 밖에 붙이면 template_modified 가 함께 터져 무엇을 시험하는지 흐려진다.
        실제 프리즘도 마커 안에만 쓰므로, 마커 안 주입이 실물에 가깝다.
        (template_modified 는 마커 밖을 건드리는 별도 결함으로 따로 시험한다.)
        """
        if self.fault == self.ALWAYS_BAD:
            return "# 시험\n\n소자 수는 28 개다.\n"      # 맨 숫자 — 매 시도 반려된다

        def first_slot(text: str) -> str:
            if self.fault == "bare_number":
                return "\uCD1D \uC18C\uC790\uB294 28 \uAC1C\uB85C \uCD94\uC815\uB41C\uB2E4."
            if self.fault == "undefined_key":
                return "\uC608\uC0C1 \uC774\uB4DD\uC740 {{v:\uD574\uC11D.\uC788\uC9C0\uB3C4_\uC54A\uC740_\uC774\uB4DD|gain_max_dbi}} \uC774\uB2E4."
            if self.fault == "role_mismatch":
                src = (by_role.get("af_grating_deg") or by_role.get("af_sll_db")
                       or by_role.get("af_sll_angle_deg") or [])
                if src:
                    return ("\uBC18\uC804\uB825 \uBE54\uD3ED\uC740 {{v:%s|af_hpbw_deg}} \uC774\uB2E4." % src[0])
            return text

        if self.fault == "template_modified":
            lines = doc.splitlines()
            for i, ln in enumerate(lines):
                if ln.startswith("| {{l:"):
                    del lines[i]
                    break
            return "\n".join(lines)

        if self.fault == "prose_unwritten":
            done = {"n": 0}

            def blank(m):
                if done["n"]:
                    return m.group(0)
                done["n"] = 1
                return f"{m.group(1)}\n(\uC5EC\uAE30\uC11C\uBD80\uD130 \uC791\uC131)\n{m.group(5)}"
            return PROSE_RE.sub(blank, doc)

        done = {"n": 0}

        def rep(m):
            if done["n"]:
                return m.group(0)
            done["n"] = 1
            return f"{m.group(1)}\n{first_slot(m.group(4).strip())}\n{m.group(5)}"
        return PROSE_RE.sub(rep, doc)


class PrismComposer:
    """vLLM 경유 실제 프리즘.

    **LLM 은 서술 슬롯만 쓴다.** 표·제목·참조는 결정론 템플릿이 이미 채웠고, LLM 이 그것을
    건드리면 게이트가 `template_modified` 로 반려한다. 그래서 프리즘의 실패 양상은
    "틀린 수치"가 아니라 "밋밋한 서술"이 된다 — 후자는 문서를 오염시키지 않는다.

    슬롯마다 **한 번씩** 부른다. 문서 전체를 한 번에 생성시키지 않는 이유:
      · 전체 생성은 템플릿을 다시 쓰게 만들고, 그러면 template_modified 가 매번 터진다.
      · 슬롯별 호출은 실패를 슬롯 단위로 격리한다 — 한 슬롯이 망가져도 나머지는 산다.
      · 지침이 슬롯마다 다르므로 프롬프트가 짧고 구체적이다.

    이름 규약 — `setup/00_common.sh` 를 따른다. 새 이름을 여기서 만들지 않는다.
      CHAT_BASE      chat vLLM 엔드포인트(기본 http://localhost:8000/v1)
      SERVED_NAME    서빙 모델 id. 없으면 `/models` 에 물어본다(detect_served_name 과 같은 방식)
    새로 쓰는 `PRISM_*` 넷은 **아직 어디에도 등재되지 않은 이름**이다 —
    `_system/interfaces.yaml#env_contract` 에 등재해야 정본이 된다(사람 몫).

    호출은 OpenAI 호환 `/chat/completions` 를 **stdlib urllib** 로 친다 — 새 pip 의존 0건.
    프록시는 **끊고** 부른다: setup 전 스크립트의 공통 규약이 "localhost 호출이 SOCKS 로
    새는 사고 방지"다. urllib 는 http_proxy 를 자동으로 따르므로 여기서 명시적으로 막는다.
    """

    name = "prism"
    DEFAULT_BASE = "http://localhost:8000/v1"

    def __init__(self, base=None, model=None, prompt_path=None, timeout=None,
                 temperature=None, max_tokens=None):
        self.base = (base or os.environ.get("CHAT_BASE") or self.DEFAULT_BASE).rstrip("/")
        self.model = model or os.environ.get("SERVED_NAME") or os.environ.get("CHAT_MODEL")
        self.prompt_path = prompt_path or os.environ.get("PRISM_PROMPT_PATH")
        self.timeout = float(timeout or os.environ.get("PRISM_TIMEOUT", 120))
        self.temperature = float(temperature if temperature is not None
                                 else os.environ.get("PRISM_TEMPERATURE", 0.2))
        self.max_tokens = int(max_tokens or os.environ.get("PRISM_MAX_TOKENS", 512))
        self.calls = 0

    @staticmethod
    def _opener():
        """프록시를 **쓰지 않는** opener.

        setup/00_common.sh 가 모든 스크립트에서 http_proxy 계열을 unset 하는 이유와 같다 —
        localhost 호출이 SOCKS 로 새면 붙지 않거나, 더 나쁘게는 사내 데이터가 밖으로 나간다
        (POLICY-4: 우리 데이터의 외부 송신 금지). 파이썬 urllib 는 env 프록시를 자동으로
        따르므로 여기서 끊어야 한다.
        """
        import urllib.request
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _post(self, path: str, body: dict) -> dict:
        import json
        import urllib.request
        req = urllib.request.Request(
            self.base + path, method="POST",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        key = os.environ.get("CHAT_API_KEY")
        if key:
            req.add_header("Authorization", f"Bearer {key}")
        with self._opener().open(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def resolve_model(self) -> str:
        """모델 이름을 **서버에 물어본다**. 코드에 박으면 서빙이 바뀔 때 조용히 어긋난다."""
        if self.model:
            return self.model
        import json
        with self._opener().open(self.base + "/models", timeout=self.timeout) as r:
            data = json.loads(r.read().decode("utf-8")).get("data") or []
        if not data:
            raise RuntimeError(f"{self.base}/models 가 비었다 — 서빙 중인 모델이 없다")
        self.model = data[0]["id"]
        return self.model

    def system_prompt(self) -> str:
        """투입 프롬프트는 **파일에서 온다**. 코드에 하드코딩하지 않는다(PROMPT_IMPL)."""
        if self.prompt_path and Path(self.prompt_path).exists():
            return Path(self.prompt_path).read_text(encoding="utf-8")
        default = Path(__file__).resolve().parent.parent / "registry" / "prism_prompt.md"
        if default.exists():
            return default.read_text(encoding="utf-8")
        raise FileNotFoundError(
            f"프리즘 프롬프트 없음 — PRISM_PROMPT_PATH 또는 {default}. "
            "프롬프트를 코드에 넣지 않는다.")

    def compose(self, run_id, catalog, skeleton, violations=None, attempt=1) -> str:
        sysmsg = self.system_prompt()
        model = self.resolve_model()
        section_of = _section_index(skeleton)
        rejected = {v.get("slot") for v in (violations or []) if v.get("slot")}

        def write(slot: str, guide: str) -> str:
            # 반려본을 다시 받을 때, 문제 없던 슬롯은 다시 부르지 않는다(예산 B-1).
            if violations and rejected and slot not in rejected:
                keep = _existing_prose(skeleton, slot)
                if keep:
                    return keep
            user = _slot_prompt(slot, guide, section_of.get(slot, ""), violations)
            out = self._post("/chat/completions", {
                "model": model, "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "messages": [{"role": "system", "content": sysmsg},
                             {"role": "user", "content": user}]})
            self.calls += 1
            return _sanitize((out["choices"][0]["message"]["content"] or "").strip())

        return fill_prose(skeleton, write)


_NUM_RE = re.compile(r"[-+\u2212]?\d[\d.,]*")


def _sanitize(text: str) -> str:
    """모델이 마커·표·숫자를 뱉으면 여기서 자른다.

    게이트가 어차피 잡지만 **반려 왕복을 줄이는 것이 예산이다**(B-1 compute_minutes).
    잘라낸 것을 숨기지 않는다 — 숫자가 든 문장은 통째로 버린다(표에 이미 있는 값이다).
    """
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    text = "\n".join(ln for ln in text.splitlines()
                     if not ln.lstrip().startswith(("|", "#", "```")))
    kept = [s for s in re.split(r"(?<=[.!?\uB2E4])\s+", text.strip())
            if s and not _NUM_RE.search(s)]
    return " ".join(kept).strip()


def _section_index(skeleton: str) -> dict:
    """슬롯이 속한 절의 표를 함께 보여 준다 — 서술이 표를 보고 쓰게 하는 장치."""
    out, buf = {}, []
    for ln in skeleton.splitlines():
        if ln.startswith("## "):
            buf = []
        m = re.match(r"<!--\s*PROSE:([^\s]+)", ln)
        if m:
            out[m.group(1)] = "\n".join(buf[-40:])
        buf.append(ln)
    return out


def _existing_prose(doc: str, slot: str) -> str:
    for m in PROSE_RE.finditer(doc):
        if m.group(2) == slot:
            body = m.group(4).strip()
            return "" if body in ("", "(\uC5EC\uAE30\uC11C\uBD80\uD130 \uC791\uC131)") else body
    return ""


def _slot_prompt(slot, guide, section_text, violations) -> str:
    parts = [
        f"[\uC2AC\uB86F] {slot}",
        f"[\uC9C0\uCE68] {guide}",
        "",
        "[\uC774 \uC808\uC758 \uD45C \u2014 \uAC12\uC740 \uC5EC\uAE30 \uC774\uBBF8 \uC788\uB2E4. \uB418\uD480\uC774\uD558\uC9C0 \uB9D0\uACE0 \uD45C\uAC00 \uB9D0\uD558\uC9C0 \uBABB\uD558\uB294 \uAC83\uC744 \uC368\uB77C]",
        section_text or "(\uD45C \uC5C6\uC74C)",
        "",
        "[\uCD9C\uB825 \uADDC\uCE59]",
        "  \u00B7 \uC22B\uC790\uB97C \uC4F0\uC9C0 \uB9C8\uB77C. \uC22B\uC790\uAC00 \uB4E0 \uBB38\uC7A5\uC740 \uBC84\uB824\uC9C4\uB2E4.",
        "  \u00B7 \uB9C8\uCEE4\u00B7\uD45C\u00B7\uC81C\uBAA9\u00B7\uCF54\uB4DC\uBE14\uB85D\uC744 \uCD9C\uB825\uD558\uC9C0 \uB9C8\uB77C. \uBB38\uC7A5\uB9CC \uCD9C\uB825\uD55C\uB2E4.",
        "  \u00B7 \uD655\uC778\uB418\uC9C0 \uC54A\uC740 \uAC83\uC744 \uB2E8\uC815\uD558\uC9C0 \uB9C8\uB77C.",
    ]
    if violations:
        vs = [v for v in violations if v.get("slot") == slot or not v.get("slot")]
        if vs:
            parts += ["", "[\uC9C1\uC804 \uBC18\uB824 \uC0AC\uC720 \u2014 \uC774\uAC83\uB9CC \uACE0\uCC98\uB77C]"]
            parts += [f"  \u00B7 {v.get('kind')}: {v.get('why', '')}" for v in vs[:6]]
    return "\n".join(parts)


def get_composer(kind: str | None = None, **kw):
    kind = kind or os.environ.get("ORCH_COMPOSER", "skeleton")
    if kind == "skeleton":
        return SkeletonComposer(**kw)
    if kind == "prism":
        return PrismComposer(**kw)
    raise ValueError(f"알 수 없는 조립기: {kind} — skeleton|prism")
