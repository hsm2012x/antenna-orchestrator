#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent/rescue.py — 구제 경로 (AMD-2026-07-31-2)

**결정론이 실패한 자리에서만** LLM 이 개입해 파악하고, 사람에게 승인을 요청한다.
성공 경로에는 LLM 이 없다 — 그것이 A-3 의 재현성 취지를 지키는 방법이다.

개입 조건 다섯 (계약이 정한 닫힌 집합 — 이 밖에서는 부르지 않는다)
    F1  어댑터 후보 0 · outcome != ROUTE_OK
    F2  컨테이너 안 경계 신호가 엇갈린다(split.verdict == "ambiguous")
    F3  unassigned 파일이 남는다
    F4  역할 어휘 미등재 항목이 있다
    F5  판독 불가가 우세해 주도 레인을 못 정한다

    끄는 법: ORCH_RESCUE=off → 개정 전 동작(HOLD)으로 복귀.
    비상 차단이 없는 기제는 되돌릴 수 없다.

울타리 다섯
    ① 성공 경로 불가침 — 결정론이 판정하면 부르지 않는다
    ② 제안일 뿐이다 — 사람 승인 전에는 **어떤 값도 그 판단으로 추출되지 않는다**(A-1)
    ③ 선언을 이길 수 없다 — derived 처럼 선언 근거가 있는 판정을 뒤집지 못한다
    ④ 기록 의무 — chosen_by="llm" + variation{kind, changed[], reason, prev_attempt}
    ⑤ 되돌릴 수 있다 — 반려하면 분기로 되감고 사유를 context 로 주입한다

두 구현이 같은 계약을 갖는다
    propose(trigger, evidence, prior_attempts) -> {proposal, reason, confidence, changed[]}

    RuleRescue   결정론 대역. LLM 0콜 — 상태 머신을 LLM 없이 시험하기 위한 것이다.
                 **판단하지 않는다**: 증거를 정리해 "사람이 정해야 한다"로 넘긴다.
    LlmRescue    vLLM 경유. 프롬프트는 파일에서 온다(코드 하드코딩 금지).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent / "tools"))

# 개입 조건 — 계약이 정한 닫힌 집합
TRIGGERS = {
    "F1": "어댑터 후보 0 · 식별 실패",
    "F2": "컨테이너 안 경계 신호가 엇갈린다",
    "F3": "unassigned 파일이 남는다",
    "F4": "역할 어휘 미등재 항목이 있다",
    "F5": "판독 불가가 우세해 주도 레인을 못 정한다",
}

# 제안이 바꿀 수 있는 것 — 이 밖은 구제가 아니라 규칙 위반이다(A-2·N-1·N-2)
ALLOWED_CHANGES = ("boundaries", "adapter", "lane_order", "role_map", "entry_relation")


def enabled() -> bool:
    return os.environ.get("ORCH_RESCUE", "on").lower() not in ("off", "0", "false")


def detect(state: dict, *, split_result: dict | None = None,
           catalog: dict | None = None, discover_result: dict | None = None) -> list[dict]:
    """결정론으로 **실패를 판정한다.** 실패 판정을 LLM 이 하면 개입 범위가 스스로 넓어진다."""
    out = []
    r = state.get("routing") or {}
    if r and (r.get("outcome") != "ROUTE_OK" or not r.get("adapter")):
        out.append({"code": "F1", "why": TRIGGERS["F1"],
                    "evidence": {"outcome": r.get("outcome"),
                                 "candidates": r.get("candidates"),
                                 "signals": (r.get("signals") or [])[:8]}})
    if split_result and split_result.get("verdict") == "ambiguous":
        out.append({"code": "F2", "why": TRIGGERS["F2"],
                    "evidence": {"split_why": split_result.get("why"),
                                 "signals": split_result.get("signals")}})
    if discover_result and discover_result.get("unassigned"):
        out.append({"code": "F3", "why": TRIGGERS["F3"],
                    "evidence": {"unassigned": discover_result["unassigned"][:20]}})
    if catalog and catalog.get("unmapped_keys"):
        out.append({"code": "F4", "why": TRIGGERS["F4"],
                    "evidence": {"unmapped_keys": catalog["unmapped_keys"][:20]}})
    ex = state.get("extracted") or {}
    if ex.get("n_unreadable") and not (ex.get("lanes") or []):
        out.append({"code": "F5", "why": TRIGGERS["F5"],
                    "evidence": {"n_unreadable": ex["n_unreadable"], "lanes": ex.get("lanes")}})
    return out


def _validate(p: dict) -> dict:
    """울타리 ③④ 강제 — 제안이 허용 범위를 벗어나면 거부한다."""
    changed = [c for c in (p.get("changed") or []) if c]
    bad = [c for c in changed if c not in ALLOWED_CHANGES]
    if bad:
        raise ValueError(f"구제 제안이 허용 범위를 벗어났다: {bad} — 허용 {ALLOWED_CHANGES}")
    if not changed:
        raise ValueError("changed 가 비었다 — 무엇을 바꾸자는 것인지 적지 않으면 "
                         "다음 시도가 같은 것을 되풀이한다")
    p["changed"] = changed
    return p


class RuleRescue:
    """결정론 대역 — **판단하지 않는다.** 증거를 정리해 사람에게 넘긴다."""

    name = "rule"
    llm_calls = 0

    def propose(self, trigger: dict, evidence: dict, prior_attempts=()) -> dict:
        code = trigger["code"]
        opts = {
            "F1": ["원천이 지원 포맷이 아니다", "판독 도구가 없다(반입 필요)", "경로가 잘못됐다"],
            "F2": ["안테나 하나다 — 틈은 칩·블록 경계다", "안테나 여럿이다 — 틈이 경계다",
                   "판올림 관계다"],
            "F3": ["분류 규칙을 늘려야 한다", "이 파일들은 안테나와 무관하다"],
            "F4": ["역할 어휘에 등재한다", "이 항목은 문서에 싣지 않는다"],
            "F5": ["변환 도구를 반입한다", "이 원천은 프리뷰만으로 둔다"],
        }.get(code, ["사람이 정한다"])
        return _validate({
            "proposal": None,                       # 대역은 고르지 않는다
            "options": opts,
            "reason": (f"{trigger['why']} — 결정론 대역은 판단하지 않는다. "
                       f"증거를 정리했으니 사람이 고른다(A-1)."),
            "confidence": 0.0,
            "changed": {"F1": ["adapter"], "F2": ["boundaries"], "F3": ["lane_order"],
                        "F4": ["role_map"], "F5": ["lane_order"]}.get(code, ["boundaries"]),
            "composer": self.name,
        })


class LlmRescue:
    """vLLM 경유 구제. 프리즘과 같은 규율 — 프롬프트는 파일에서, 프록시는 끊고."""

    name = "llm"
    DEFAULT_BASE = "http://localhost:8000/v1"

    def __init__(self, base=None, model=None, prompt_path=None, timeout=None,
                 temperature=None, max_tokens=None):
        self.base = (base or os.environ.get("CHAT_BASE") or self.DEFAULT_BASE).rstrip("/")
        self.model = model or os.environ.get("SERVED_NAME")
        self.prompt_path = prompt_path or os.environ.get("RESCUE_PROMPT_PATH")
        self.timeout = float(timeout or os.environ.get("PRISM_TIMEOUT", 120))
        self.temperature = float(temperature if temperature is not None
                                 else os.environ.get("RESCUE_TEMPERATURE", 0.1))
        self.max_tokens = int(max_tokens or os.environ.get("RESCUE_MAX_TOKENS", 700))
        self.calls = 0

    @staticmethod
    def _opener():
        import urllib.request
        return urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _prompt(self) -> str:
        p = Path(self.prompt_path) if self.prompt_path else \
            _HERE.parent / "registry" / "rescue_prompt.md"
        if not p.exists():
            raise FileNotFoundError(f"구제 프롬프트 없음: {p} — 프롬프트를 코드에 넣지 않는다")
        return p.read_text(encoding="utf-8")

    def _model(self) -> str:
        if self.model:
            return self.model
        import json
        with self._opener().open(self.base + "/models", timeout=self.timeout) as r:
            data = json.loads(r.read().decode("utf-8")).get("data") or []
        if not data:
            raise RuntimeError(f"{self.base}/models 가 비었다")
        self.model = data[0]["id"]
        return self.model

    def propose(self, trigger: dict, evidence: dict, prior_attempts=()) -> dict:
        import json
        import urllib.request
        body = {
            "model": self._model(), "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "messages": [
                {"role": "system", "content": self._prompt()},
                {"role": "user", "content": json.dumps({
                    "trigger": trigger["code"], "why": trigger["why"],
                    "evidence": evidence,
                    "이미_시도한_것": list(prior_attempts),
                    "바꿀_수_있는_것": list(ALLOWED_CHANGES),
                }, ensure_ascii=False)[:24000]},
            ]}
        req = urllib.request.Request(
            self.base + "/chat/completions", method="POST",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        key = os.environ.get("CHAT_API_KEY")
        if key:
            req.add_header("Authorization", f"Bearer {key}")
        with self._opener().open(req, timeout=self.timeout) as r:
            out = json.loads(r.read().decode("utf-8"))
        self.calls += 1
        txt = (out["choices"][0]["message"]["content"] or "").strip()
        try:
            m = txt[txt.index("{"):txt.rindex("}") + 1]
            p = json.loads(m)
        except Exception as exc:
            raise ValueError(f"구제 제안을 JSON 으로 읽지 못했다: {exc} · 원문 {txt[:200]}")
        p["composer"] = self.name
        return _validate(p)


def get_rescue(kind: str | None = None, **kw):
    kind = kind or os.environ.get("ORCH_RESCUE_AGENT", "rule")
    if kind == "rule":
        return RuleRescue()
    if kind == "llm":
        return LlmRescue(**kw)
    raise ValueError(f"알 수 없는 구제 에이전트: {kind} — rule|llm")
