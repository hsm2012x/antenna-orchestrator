#!/usr/bin/env python3
"""llm_pool.py — 무상태 chat() 동시 실행 풀 (WO-AUTO-ONBOARD §3b 병렬화).

왜 필요한가: vLLM은 **continuous batching**이라 동시에 도착한 요청들을 GPU 한 배치로
섞어 처리한다. 세션·상태 개념이 없으므로 우리 전 경로의 무상태 `chat()` 호출을 **동시
N개**로 걸어두기만 하면 처리량이 2~4배가 된다(verifier의 병렬 검증 5종 WP-V와 같은 패턴).
이 모듈이 제공하는 것은 **동시성·재시도·타임아웃·집계뿐**이며, 프롬프트와 판정 로직은
호출자(tools/onboard_summarize.py 등)의 몫이다.

제공
  LLMPool(parallel=4)      세마포어로 동시 수 하드 바운드(상한 ONBOARD_PARALLEL_MAX=16)
    .chat(...)             단발 호출 — 성공=문자열, 최종 실패=PoolError
    .chat_result(...)      단발 호출 — 예외 없음 {ok,text,error,attempts,tokens,wall_s}
    .map_chat(jobs)        프롬프트 목록 병렬(입력 순서 보존)
    .run(tasks)            임의 callable 목록 병렬 — 코드 재사용 경로
                           (예: codelink.spec_code_audit.summarize_code를 워커에서 호출)
    .stats                 {calls, ok, failed, retries, tokens, wall_s, max_inflight, …}

재시도: 요청별 타임아웃(기본 180s) + 지수 백오프 2회(2s·4s) → 총 시도 3회. 소진하면
예외를 삼키고 실패로 기록한다 — 실패 파일은 호출자가 report.skipped 에 남긴다(침묵 금지).

세마포어 재진입: 같은 스레드가 이미 슬롯을 쥐고 있으면 추가 획득하지 않는다. 덕분에
`run()`의 callable 안에서 같은 풀의 `chat()`을 불러도 교착이 없고, **슬롯 1개 = 파일 1개**
라는 병렬 입자(WO §3b: 프로젝트 내 파일 단위 병렬)가 유지된다.

토큰 집계: 응답 usage를 쓰고, 없으면 문자수/4로 **추정**한다(stats.tokens_estimated 로
추정분 콜 수를 따로 남긴다 — 보고서에서 실측/추정을 섞어 말하지 않기 위해).

캘리브레이션(파일럿 1회):
    python -m pipeline.llm_pool --calibrate [--levels 1,2,4,8] [--calls 20]
N을 올리며 같은 샘플을 돌려 aggregate tokens/s 표를 낸다. **무릎 지점** = 직전 N 대비
tokens/s 증가율이 임계(기본 15%) 아래로 꺾이는 지점 = max_num_seqs·KV 캐시 한계.
그 값을 `--parallel` 기본으로 쓰되, 주간에는 한 단계 낮춘다(Ask·리뷰와 엔진 공유).

규약: stdlib만 · **import 부수효과 0**(엔드포인트·모델은 첫 호출 때 지연 해석) ·
pipeline→retrieval import 금지 계약 준수.
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

DEFAULT_PARALLEL = int(os.getenv("ONBOARD_PARALLEL", "4") or "4")
MAX_PARALLEL = int(os.getenv("ONBOARD_PARALLEL_MAX", "16") or "16")   # verifier SWARM_CONCURRENCY 상한과 동조
DEFAULT_TIMEOUT = float(os.getenv("ONBOARD_LLM_TIMEOUT", "180"))
DEFAULT_RETRIES = int(os.getenv("ONBOARD_LLM_RETRIES", "2"))
DEFAULT_BACKOFF = float(os.getenv("ONBOARD_LLM_BACKOFF", "2.0"))
_MODEL_FALLBACK = os.getenv("MAIN_LLM_MODEL", "") or "qwen3-35b-fp8"   # pipeline.config와 같은 폴백


class PoolError(RuntimeError):
    """재시도까지 소진한 최종 실패. 호출자는 report.skipped 에 사유와 함께 남긴다."""


# ─── HTTP (주입 가능 — 테스트는 post=/get= 로 갈아끼운다) ─────────────────────
def _post_json(url: str, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _get_json(url: str, timeout: float) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def resolve_base_url(explicit: str = "") -> str:
    """엔진 base URL — 인자 > env(ONBOARD_CHAT_BASE·MAIN_LLM_BASE_URL·CHAT_BASE) > pipeline.config.

    pipeline.config import는 **호출 시점**에만 한다(모듈 import 부수효과 0 유지)."""
    if explicit:
        return explicit.rstrip("/")
    for k in ("ONBOARD_CHAT_BASE", "MAIN_LLM_BASE_URL", "CHAT_BASE"):
        v = os.getenv(k)
        if v:
            return v.rstrip("/")
    try:
        from pipeline import config          # noqa: PLC0415 — 지연 import(부수효과=.env 로딩)
        return str(config.MAIN_LLM_BASE_URL).rstrip("/")
    except Exception:                        # noqa: BLE001 — 단독 실행/미설치
        return "http://localhost:8000/v1"


def estimate_tokens(text: str) -> int:
    """usage 미제공 엔진용 보수적 추정(문자/4). 실측과 섞지 않도록 호출 수를 따로 센다."""
    return max(1, len(text or "") // 4)


# ─── 풀 ──────────────────────────────────────────────────────────────────────
class LLMPool:
    """무상태 chat 동시 실행 풀. 인스턴스 1개 = 동시 수 1벌(프로젝트 1개 처리 단위)."""

    def __init__(self, parallel: int | None = None, *, timeout: float | None = None,
                 retries: int | None = None, backoff: float | None = None,
                 base_url: str = "", model: str = "", post=None, get=None, sleep=None):
        want = int(parallel or DEFAULT_PARALLEL)
        self.parallel = max(1, min(want, MAX_PARALLEL))
        self.clamped = self.parallel != want
        self.timeout = float(DEFAULT_TIMEOUT if timeout is None else timeout)
        self.retries = max(0, int(DEFAULT_RETRIES if retries is None else retries))
        self.backoff = float(DEFAULT_BACKOFF if backoff is None else backoff)
        self._base = base_url
        self._model = model or os.getenv("ONBOARD_MODEL", "")
        self._post = post or _post_json
        self._get = get or _get_json
        self._sleep = sleep or time.sleep
        self._sem = threading.BoundedSemaphore(self.parallel)
        self._local = threading.local()
        self._lock = threading.Lock()
        self._inflight = 0
        self.stats = {"calls": 0, "ok": 0, "failed": 0, "retries": 0,
                      "prompt_tokens": 0, "completion_tokens": 0, "tokens": 0,
                      "tokens_estimated": 0, "wall_s": 0.0, "max_inflight": 0}

    # -- 엔드포인트/모델 (지연 해석) --------------------------------------
    @property
    def base_url(self) -> str:
        return resolve_base_url(self._base)

    @property
    def chat_url(self) -> str:
        return self.base_url + "/chat/completions"

    def model(self) -> str:
        if not self._model:
            try:
                self._model = self._get(self.base_url + "/models", 10.0)["data"][0]["id"]
            except Exception:                # noqa: BLE001 — 엔진 미기동: 폴백(캐시 안 함)
                return _MODEL_FALLBACK
        return self._model

    # -- 세마포어 (스레드 재진입 허용) ------------------------------------
    @contextmanager
    def _slot(self):
        held = getattr(self._local, "held", 0)
        if held:                                     # 이미 이 스레드가 슬롯 보유 → 이중 획득 금지
            self._local.held = held + 1
            try:
                yield
            finally:
                self._local.held -= 1
            return
        self._sem.acquire()
        self._local.held = 1
        with self._lock:
            self._inflight += 1
            self.stats["max_inflight"] = max(self.stats["max_inflight"], self._inflight)
        try:
            yield
        finally:
            with self._lock:
                self._inflight -= 1
            self._local.held = 0
            self._sem.release()

    # -- 집계 --------------------------------------------------------------
    def _account(self, usage: dict, prompt: str, text: str) -> int:
        pt = int((usage or {}).get("prompt_tokens") or 0)
        ct = int((usage or {}).get("completion_tokens") or 0)
        tt = int((usage or {}).get("total_tokens") or 0) or (pt + ct)
        estimated = False
        if not tt:                                   # mock·구버전 엔진: usage 없음 → 추정
            pt, ct = estimate_tokens(prompt), estimate_tokens(text)
            tt, estimated = pt + ct, True
        with self._lock:
            self.stats["prompt_tokens"] += pt
            self.stats["completion_tokens"] += ct
            self.stats["tokens"] += tt
            if estimated:
                self.stats["tokens_estimated"] += 1
        return tt

    # -- 1콜 ---------------------------------------------------------------
    def _call_once(self, prompt: str, system: str, max_tokens: int, temperature: float):
        msgs = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": "user", "content": prompt}]
        body = {"model": self.model(), "messages": msgs, "max_tokens": int(max_tokens),
                "temperature": temperature,
                "chat_template_kwargs": {"enable_thinking": False}}   # codelink/pipeline과 동일 규약
        out = self._post(self.chat_url, body, self.timeout)
        text = out["choices"][0]["message"]["content"]
        return text, (out.get("usage") or {})

    def chat_result(self, prompt: str, *, system: str = "", max_tokens: int = 900,
                    temperature: float = 0.0, key=None) -> dict:
        """예외를 던지지 않는 단발 호출. 실패해도 run을 죽이지 않는다(호출자가 skipped 기록)."""
        t0 = time.time()
        attempts, last = 0, ""
        while True:
            attempts += 1
            try:
                with self._slot():
                    text, usage = self._call_once(prompt, system, max_tokens, temperature)
                toks = self._account(usage, prompt, text)
                with self._lock:
                    self.stats["calls"] += 1
                    self.stats["ok"] += 1
                    self.stats["wall_s"] = round(self.stats["wall_s"] + (time.time() - t0), 3)
                return {"key": key, "ok": True, "text": text, "error": "", "attempts": attempts,
                        "tokens": toks, "wall_s": round(time.time() - t0, 3)}
            except Exception as e:               # noqa: BLE001 — 타임아웃·5xx·연결 전부 재시도 대상
                last = f"{type(e).__name__}: {e}"[:300]
                if attempts > self.retries:
                    with self._lock:
                        self.stats["calls"] += 1
                        self.stats["failed"] += 1
                        self.stats["wall_s"] = round(self.stats["wall_s"] + (time.time() - t0), 3)
                    return {"key": key, "ok": False, "text": "", "error": last,
                            "attempts": attempts, "tokens": 0, "wall_s": round(time.time() - t0, 3)}
                with self._lock:
                    self.stats["retries"] += 1
                self._sleep(self.backoff ** attempts)     # 지수 백오프: 2s → 4s

    def chat(self, prompt: str, *, system: str = "", max_tokens: int = 900,
             temperature: float = 0.0) -> str:
        r = self.chat_result(prompt, system=system, max_tokens=max_tokens, temperature=temperature)
        if not r["ok"]:
            raise PoolError(r["error"])
        return r["text"]

    # -- 병렬 --------------------------------------------------------------
    def map_chat(self, jobs) -> list:
        """jobs: [{key?, prompt, system?, max_tokens?}] → 결과 리스트(입력 순서 보존)."""
        jobs = list(jobs)
        if not jobs:
            return []
        with ThreadPoolExecutor(max_workers=self.parallel) as ex:
            futs = [ex.submit(self.chat_result, j["prompt"], system=j.get("system", ""),
                              max_tokens=int(j.get("max_tokens", 900)),
                              key=j.get("key", i)) for i, j in enumerate(jobs)]
            return [f.result() for f in futs]

    def run(self, tasks) -> list:
        """tasks: [(key, callable)] — 임의 작업(파일 1개 처리 등)을 슬롯 1개씩 점유해 병렬 실행.

        callable 내부에서 같은 풀의 chat()을 여러 번 불러도 된다(세마포어 재진입 허용 —
        맵-리듀스 요약이 한 슬롯 안에서 직렬로 도는 것이 의도한 입자다).
        callable이 예외를 던지면 재시도(지수 백오프) → 소진 시 ok=False. **제어 흐름용
        분기(예산 초과 등)는 예외 말고 반환값으로 표현할 것**(재시도 낭비 방지)."""
        tasks = list(tasks)
        if not tasks:
            return []

        def _one(key, fn):
            t0, attempts, last = time.time(), 0, ""
            while True:
                attempts += 1
                try:
                    with self._slot():
                        val = fn()
                    return {"key": key, "ok": True, "value": val, "error": "",
                            "attempts": attempts, "wall_s": round(time.time() - t0, 3)}
                except Exception as e:           # noqa: BLE001
                    last = f"{type(e).__name__}: {e}"[:300]
                    if attempts > self.retries:
                        return {"key": key, "ok": False, "value": None, "error": last,
                                "attempts": attempts, "wall_s": round(time.time() - t0, 3)}
                    with self._lock:
                        self.stats["retries"] += 1
                    self._sleep(self.backoff ** attempts)

        with ThreadPoolExecutor(max_workers=self.parallel) as ex:
            futs = [ex.submit(_one, k, fn) for k, fn in tasks]
            return [f.result() for f in futs]


# ─── 캘리브레이션 (파일럿 1회 — 무릎 지점 확정) ──────────────────────────────
CALIBRATION_PROMPT = (
    "다음 C 함수가 하는 일을 3줄 이내로 요약하라.\n\n```c\n"
    "static uint16_t crc16_ccitt(const uint8_t *buf, size_t len) {\n"
    "    uint16_t crc = 0xFFFF;\n"
    "    for (size_t i = 0; i < len; i++) {\n"
    "        crc ^= (uint16_t)buf[i] << 8;\n"
    "        for (int b = 0; b < 8; b++)\n"
    "            crc = (crc & 0x8000) ? (uint16_t)((crc << 1) ^ 0x1021) : (uint16_t)(crc << 1);\n"
    "    }\n    return crc;\n}\n```")
KNEE_GAIN = float(os.getenv("ONBOARD_CALIBRATE_KNEE_GAIN", "0.15"))   # 15% 미만 증가 = 꺾임


def calibrate(levels=(1, 2, 4, 8), calls: int = 20, prompt: str = "", *,
              system: str = "", max_tokens: int = 160, pool_kwargs: dict | None = None,
              progress=None) -> list:
    """N=levels 각각으로 같은 샘플 calls개를 돌려 aggregate tokens/s 측정. 풀은 N마다 새로."""
    rows = []
    for n in levels:
        pool = LLMPool(n, **(pool_kwargs or {}))
        jobs = [{"key": i, "prompt": prompt or CALIBRATION_PROMPT, "system": system,
                 "max_tokens": max_tokens} for i in range(calls)]
        t0 = time.time()
        res = pool.map_chat(jobs)
        wall = max(1e-6, time.time() - t0)
        ok = sum(1 for r in res if r["ok"])
        rows.append({"parallel": n, "calls": calls, "ok": ok, "failed": calls - ok,
                     "wall_s": round(wall, 2), "tokens": pool.stats["tokens"],
                     "tokens_per_s": round(pool.stats["tokens"] / wall, 1),
                     "calls_per_s": round(ok / wall, 2),
                     "max_inflight": pool.stats["max_inflight"],
                     "estimated": bool(pool.stats["tokens_estimated"])})
        if progress:
            progress(rows[-1])
    return rows


def knee_point(rows) -> int:
    """무릎 지점 = 직전 N 대비 tokens/s 증가율이 KNEE_GAIN 미만으로 처음 꺾이기 **직전** N."""
    best = rows[0]["parallel"] if rows else 0
    for prev, cur in zip(rows, rows[1:]):
        gain = (cur["tokens_per_s"] - prev["tokens_per_s"]) / max(1e-6, prev["tokens_per_s"])
        if gain < KNEE_GAIN:
            return prev["parallel"]
        best = cur["parallel"]
    return best


def format_calibration_table(rows) -> str:
    est = any(r.get("estimated") for r in rows)
    L = ["| --parallel N | 콜 수 | 성공 | 벽시계(s) | 토큰 | aggregate tokens/s | calls/s | 실제 동시 |",
         "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['parallel']} | {r['calls']} | {r['ok']} | {r['wall_s']} | {r['tokens']} | "
                 f"{r['tokens_per_s']} | {r['calls_per_s']} | {r['max_inflight']} |")
    L.append("")
    L.append(f"- 무릎 지점 추정: **N={knee_point(rows)}** (직전 N 대비 tokens/s 증가율 "
             f"{int(KNEE_GAIN * 100)}% 미만으로 꺾이기 직전)")
    if est:
        L.append("- ⚠ 토큰은 **추정치**(엔진이 usage를 주지 않음 — 문자/4). 실엔진 실측으로 대체할 것.")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="LLM 병렬 풀 — 캘리브레이션 CLI")
    ap.add_argument("--calibrate", action="store_true", help="N=levels 캘리브레이션 실행")
    ap.add_argument("--levels", default="1,2,4,8")
    ap.add_argument("--calls", type=int, default=20)
    ap.add_argument("--max-tokens", type=int, default=160)
    ap.add_argument("--prompt-file", default="", help="샘플 프롬프트 파일(기본: 내장 CRC 샘플)")
    ap.add_argument("--out", default="", help="표를 이 md 파일로도 저장")
    a = ap.parse_args(argv)
    if not a.calibrate:
        ap.error("--calibrate 만 지원한다(풀은 라이브러리로 import해 쓴다).")
    levels = tuple(int(x) for x in a.levels.split(",") if x.strip())
    prompt = ""
    if a.prompt_file:
        from pathlib import Path
        prompt = Path(a.prompt_file).read_text(encoding="utf-8")
    probe = LLMPool(1)
    print(f"[calibrate] engine={probe.chat_url} model={probe.model()} "
          f"levels={levels} calls/level={a.calls}", flush=True)
    rows = calibrate(levels, a.calls, prompt, max_tokens=a.max_tokens,
                     progress=lambda r: print(f"  N={r['parallel']:>2} → {r['wall_s']}s "
                                              f"{r['tokens_per_s']} tok/s (성공 {r['ok']}/{r['calls']})",
                                              flush=True))
    table = format_calibration_table(rows)
    print("\n" + table)
    if a.out:
        from pathlib import Path
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text("# LLM 병렬 캘리브레이션 (aggregate tokens/s)\n\n" + table + "\n",
                               encoding="utf-8")
        print(f"\n[calibrate] 표 저장 → {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
