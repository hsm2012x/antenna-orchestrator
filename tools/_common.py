#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/_common.py — 도구 공통 기반. 경로 규약 · 원자적 쓰기 · vendor_srs 진입 · 어휘.

이 파일은 판단하지 않는다. 판단은 각 도구가, 확정은 사람이 한다(AGENTS.md A-1).
쓰기 범위는 work/<run_id>/ · out/<원천명>/ 뿐이다(W-1) — 그 밖의 경로를 넘기면 거부한다.
"""
from __future__ import annotations
import json, os, sys, hashlib, tempfile
from pathlib import Path

# Windows 콘솔 기본 코덱(cp949)에서 한글·전각 문장부호가 터진다 → 표준출력 UTF-8 고정.
for _s in (sys.stdout, sys.stderr):
    try: _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass

# `python tools/x.py | head` 로 잘릴 때 BrokenPipeError 로 죽지 않게 한다(노트북·파이프 사용 편의).
try:
    import signal as _sig
    _sig.signal(_sig.SIGPIPE, _sig.SIG_DFL)   # Windows 에는 SIGPIPE 가 없다 → except 로 흘린다
except Exception:
    pass

REPO = Path(__file__).resolve().parent.parent

# ── 수치 상수 — 산지별로 분류한다 ────────────────────────────────────────────
# "하드코딩 금지"의 뜻은 **출처 없는 숫자를 쓰지 않는다**다. 숫자마다 산지가 넷 중 하나여야 한다.
#   (a) 물리·수학 정의값 — 바뀌지 않는다. 코드에 두는 것이 맞다.
#   (b) 외부 포맷 규약 — 그 규격이 정한 값. 우리 판단이 아니다.
#   (c) 판정 규칙 — 우리가 정한 값. rule_version 에 묶여 원장에 기록되어야 재현된다.
#   (d) 모델 가정 — 제품군에 따라 달라진다. registry 주입이 정본이고 코드 값은 명시된 기본값이다.
# 표시용 숫자(figsize · dpi · 축 범위 · 자릿수 맞춤)는 값에 영향이 없어 이 분류 대상이 아니다.

import math as _math

# (a) 물리·수학 정의값
C_MM_GHZ = 299.792458          # 진공 광속 c = 299 792 458 m/s → mm·GHz (1983년 정의값)
FOUR_PI = 4.0 * _math.pi
HALF_POWER_DB = 10.0 * _math.log10(0.5)   # = −3.0103 dB. 흔히 쓰는 "−3 dB"는 이 값의 통칭이다
DEG_PER_RAD = 180.0 / _math.pi            # math.degrees() 를 쓰고 손으로 적지 않는다

# (b) 외부 포맷 규약
ACAD_COLOR_BYLAYER = "256"     # AutoCAD ACI: 256=BYLAYER · 7=흑백 · 0=BYBLOCK → 특이 표기 아님
ACAD_COLOR_WHITE = "7"
ACAD_COLOR_BYBLOCK = "0"
ACAD_DEFAULT_COLORS = {ACAD_COLOR_BYLAYER, ACAD_COLOR_WHITE, ACAD_COLOR_BYBLOCK}
CST_SUBVOLUME_COORDS = 6       # CST SetSubvolume 은 (xmin,xmax,ymin,ymax,zmin,zmax) 6값
BOOST_ARCHIVE_MARK = "serialization::archive"   # boost 직렬화 아카이브 서명

# (c) 판정 규칙 — rule_version 과 함께 원장에 기록한다
GEOM_ROUND_DIGITS = 4          # mm 수치 라운딩 자리수. **추출·해석·게이트가 공유하는 단일 출처**
PARAM_ROUND_DIGITS = 6         # 선언 파라미터(심볼 값) 라운딩 — 격자 계산의 누적 오차를 막는다
HASH_HEX_LEN = 16              # sha256 앞 16자리 — 충돌 확률 대비 기록 길이의 타협
HASH_CHUNK_BYTES = 1 << 20     # 파일 해시 IO 버퍼(값에 영향 없음, 성능용)
TEXT_PROBE_BYTES = 4096        # 텍스트/바이너리 판정에 읽는 머리 바이트 수
TEXT_PRINTABLE_MIN = 0.90      # 이 비율 이상이면 ASCII 로 판독한다(NUL 이 하나라도 있으면 무조건 바이너리)
AF_THETA_SAMPLES = 3601        # 배열인자 θ 표본 수 — −90…+90° 를 0.05° 간격으로 (HPBW 정밀도를 정한다)
AF_POWER_FLOOR = 1e-12         # 로그 하한 가드
AF_DB_FLOOR = -120.0           # 하한 가드에 대응하는 dB 값
ARRAY_Y_SPLIT_MM = 2.0         # vendor extract_array 짝짓기 규약 — 상/하 세그먼트 구분 y 경계
ARRAY_MIN_SEG_MM = 5.0         # vendor extract_array — 패치로 볼 최소 세그먼트 길이

# (d) 모델 가정 — registry 로 덮어쓸 수 있다. 코드 값은 "명시된 기본값"이며 근거를 함께 적는다
HBW_FACTOR_UNIFORM = 0.886     # 균일 조명 개구의 −3 dB 빔폭 계수. 테이퍼가 있으면 커진다(≈1.0~1.3)
                               # → registry thresholds.hbw_uniform_factor 로 교체 가능


# numeric_rules() 와 조정 수단은 RULE_VERSION 정의 뒤에 있다 — 「조정값」 절 참조.

# ── 어휘 ────────────────────────────────────────────────────────────────────
# 판독수준(readability): 무엇을 실제로 읽을 수 있는가. 이 4개 밖의 값을 쓰지 않는다.
#   full         형상까지 판독 (ASCII DXF)
#   declared-only 형상은 못 읽고 선언값만 판독 (CST — ModelHistory.json·Parameters.json)
#   preview-only  내장 프리뷰만 (DWG — 본문 압축)
#   unreadable    아무것도 못 읽음 (사유 필수)
READABILITY = ("full", "declared-only", "preview-only", "unreadable")

# 어댑터 우선순위 — ARCHITECTURE_LEVEL1 3.1. 낮은 순위 원천도 버리지 않고 보존한다.
ADAPTER_PRIORITY = ("cst", "gerber", "dwg", "dxf")

RULE_VERSION = os.environ.get("ORCH_RULE_VERSION", "level1-2026-07-30")


# ── 조정값 (tunables) — (c)·(d) 상수를 바꾸는 **유일한** 수단 ────────────────
# 왜 필요한가: (c) 판정 규칙과 (d) 모델 가정은 개발 중·제품군에 따라 바뀌는 값이다.
#              그렇다고 코드를 고쳐 가며 쓰면 "어떤 값으로 돌린 run 인가"가 사라진다.
# 규율 셋:
#   1. (a) 물리·수학 정의값과 (b) 외부 포맷 규약은 **바꿀 수 없다** — 우리가 정한 값이 아니다.
#   2. 모든 변경에는 **사유(why)** 와 **주체(by)** 가 붙는다. 사유 없는 변경은 거부한다.
#   3. 변경이 하나라도 있으면 rule_version 에 `+tuned.<해시>` 가 붙는다 →
#      원장·산출 JSON 만 봐도 기본값 run 과 조정 run 이 절대 섞이지 않는다.
#
# 넣는 곳(뒤가 이긴다):
#   ① 코드 기본값        위 (c)(d) 블록
#   ② registry           products.<제품군>.thresholds — (d) 만. 제품군별 정본이다(W-2 읽기 전용)
#   ③ ORCH_OVERRIDE_FILE  json/yaml 파일 경로 — 팀·장비별 고정 조정
#   ④ ORCH_OVERRIDE       인라인 JSON — 1회성 실험
#   ⑤ set_override()      노트북·시험 코드에서 프로그램으로
#
# 형식(③④⑤ 공통): {"<이름>": {"value": <값>, "why": "<사유>", "by": "<주체>"}}
#   축약형 {"<이름>": <값>} 은 사유가 없으므로 거부한다.
#
# 범위는 취향이 아니라 **정의역**이다 — 비율은 [0,1], 표본 수는 2 이상, 계수는 양수.
#   (None = 그 방향으로 한계가 없다)
TUNABLES = {
    # 이름                 산지  형   하한   상한   뜻
    "geom_round_digits":   ("c", int,   0,    15,  "mm 수치 라운딩 자리수 (float64 십진 유효자리 15)"),
    "param_round_digits":  ("c", int,   0,    15,  "선언 파라미터 라운딩 자리수"),
    "hash_hex_len":        ("c", int,   1,    64,  "기록용 sha256 앞자리 수 (sha256=64 hex)"),
    "text_probe_bytes":    ("c", int,   1,  None,  "텍스트/바이너리 판정에 읽는 머리 바이트"),
    "text_printable_min":  ("c", float, 0.0, 1.0,  "ASCII 판독 최소 비율 (비율의 정의역)"),
    "af_theta_samples":    ("c", int,   2,  None,  "배열인자 θ 표본 수 (2 이상이라야 구간이 생긴다)"),
    "array_y_split_mm":    ("c", float, 0.0, None, "vendor extract_array 상/하 y 경계"),
    "array_min_seg_mm":    ("c", float, 0.0, None, "vendor extract_array 최소 세그먼트 길이"),
    "hbw_uniform_factor":  ("d", float, 0.0, None, "개구 빔폭 계수 k (균일 조명 0.886, 테이퍼 시 증가)"),
}
# 조정값 이름 → 모듈 전역 이름
_TUNABLE_GLOBAL = {
    "geom_round_digits": "GEOM_ROUND_DIGITS", "param_round_digits": "PARAM_ROUND_DIGITS",
    "hash_hex_len": "HASH_HEX_LEN", "text_probe_bytes": "TEXT_PROBE_BYTES",
    "text_printable_min": "TEXT_PRINTABLE_MIN", "af_theta_samples": "AF_THETA_SAMPLES",
    "array_y_split_mm": "ARRAY_Y_SPLIT_MM", "array_min_seg_mm": "ARRAY_MIN_SEG_MM",
    "hbw_uniform_factor": "HBW_FACTOR_UNIFORM",
}
# 바꿀 수 없는 값 — 이름이 들어오면 "왜 안 되는지"를 말하고 거부한다
_IMMUTABLE = {
    "c_mm_ghz": "(a) 진공 광속은 1983년 정의값이다",
    "four_pi": "(a) 수학 정의값이다",
    "half_power_db": "(a) 10·log10(0.5) 은 정의값이다 — '−3 dB' 는 통칭일 뿐이다",
    "deg_per_rad": "(a) 수학 정의값이다",
    "acad_color_bylayer": "(b) AutoCAD ACI 규약이 정한 값이다",
    "cst_subvolume_coords": "(b) CST SetSubvolume 인자 개수는 포맷이 정한다",
    "boost_archive_mark": "(b) boost 직렬화 서명 문자열이다",
    "max_gate_rejects": "예산이다 — registry/products.yaml budgets 에서 정한다(B-1)",
    "max_retries": "예산이다 — registry/products.yaml budgets 에서 정한다(B-1)",
    "compute_minutes": "예산이다 — registry/products.yaml budgets 에서 정한다(B-1)",
    "max_attempts": "예산이다 — registry/products.yaml budgets 에서 정한다([관측])",
    "discretion_after_holds": "예산이다 — registry/products.yaml budgets 에서 정한다([관측])",
}

_OVERRIDES: dict = {}     # 이름 → {"value","why","by","source"}


def _coerce(name: str, raw) -> dict:
    """조정 항목 하나를 검증한다. 실패는 예외로 즉시 드러낸다 — 조용히 무시하지 않는다."""
    if name in _IMMUTABLE:
        raise ValueError(f"조정 불가: {name} — {_IMMUTABLE[name]}")
    if name not in TUNABLES:
        raise ValueError(f"모르는 조정값: {name} — 가능한 이름: {', '.join(sorted(TUNABLES))}")
    if not isinstance(raw, dict) or "value" not in raw:
        raise ValueError(f"{name}: 형식은 {{'value':…, 'why':…, 'by':…}} 다 — 값만 주는 축약형은 거부한다")
    why = str(raw.get("why", "")).strip()
    if not why:
        raise ValueError(f"{name}: 사유(why)가 없다 — 사유 없는 변경은 재현할 수 없다")
    bucket, typ, lo, hi, _doc = TUNABLES[name]
    try:
        val = typ(raw["value"])
    except Exception as e:
        raise ValueError(f"{name}: {typ.__name__} 로 읽을 수 없다 ({raw['value']!r}) — {e}") from None
    if lo is not None and val < lo: raise ValueError(f"{name}: 정의역 하한 {lo} 미만 ({val})")
    if hi is not None and val > hi: raise ValueError(f"{name}: 정의역 상한 {hi} 초과 ({val})")
    return {"value": val, "why": why, "by": str(raw.get("by", "")).strip() or "미상",
            "bucket": bucket}


def _read_override_source(text: str, path: str = "") -> dict:
    try:
        return json.loads(text)
    except Exception:
        import yaml
        return yaml.safe_load(text) or {}


def set_override(name: str, value, *, why: str, by: str = "tuner", _source: str = "set_override()"):
    """프로그램에서 조정값을 바꾼다. 사유 없이는 바꿀 수 없다.

    주의: `from _common import GEOM_ROUND_DIGITS` 로 이미 값을 가져간 모듈은 갱신되지 않는다.
          바꾼 뒤 reload_dependents() 를 부르거나, 부르기 전에 조정하라.
    """
    ent = _coerce(name, {"value": value, "why": why, "by": by})
    ent["source"] = _source
    _OVERRIDES[name] = ent
    globals()[_TUNABLE_GLOBAL[name]] = ent["value"]
    return ent


def clear_overrides():
    """조정을 모두 되돌린다(시험용). 기본값은 _DEFAULTS 에 보관해 둔다."""
    for k in list(_OVERRIDES):
        globals()[_TUNABLE_GLOBAL[k]] = _DEFAULTS[k]
    _OVERRIDES.clear()


def reload_dependents(modules=("route", "extract", "verify_api", "render", "gate")):
    """조정 후 하위 도구 모듈을 다시 읽게 한다(노트북에서 값이 안 바뀌는 사고 방지)."""
    dropped = [m for m in modules if m in sys.modules]
    for m in dropped: del sys.modules[m]
    return dropped


def overrides() -> dict:
    """현재 적용된 조정 — 값·사유·주체·주입 경로까지 그대로 준다(원장 기록용)."""
    return {k: dict(v) for k, v in _OVERRIDES.items()}


def _load_overrides_from_env():
    src = []
    fp = os.environ.get("ORCH_OVERRIDE_FILE", "").strip()
    if fp:
        p = Path(fp)
        if not p.exists(): raise FileNotFoundError(f"ORCH_OVERRIDE_FILE 없음: {p}")
        src.append((f"ORCH_OVERRIDE_FILE={p}", _read_override_source(p.read_text(encoding="utf-8"))))
    inline = os.environ.get("ORCH_OVERRIDE", "").strip()
    if inline:
        src.append(("ORCH_OVERRIDE", _read_override_source(inline)))
    for label, obj in src:
        for name, raw in (obj or {}).items():
            ent = _coerce(name, raw)
            ent["source"] = label
            _OVERRIDES[name] = ent
            globals()[_TUNABLE_GLOBAL[name]] = ent["value"]


def apply_registry_tunables(product_def: dict, *, by: str = "registry"):
    """registry thresholds 의 (d) 값을 조정으로 반영한다. null 이면 손대지 않는다."""
    th = (product_def or {}).get("thresholds") or {}
    hit = []
    for name in TUNABLES:
        if TUNABLES[name][0] != "d": continue          # registry 가 정하는 것은 (d) 뿐이다
        v = th.get(name)
        if v is None: continue
        hit.append(set_override(name, v, why=f"registry thresholds.{name}", by=by,
                                _source="registry/products.yaml"))
    return hit


_DEFAULTS = {k: globals()[g] for k, g in _TUNABLE_GLOBAL.items()}
_load_overrides_from_env()


def effective_rule_version() -> str:
    """조정이 있으면 rule_version 을 갈라 놓는다 — 기본값 run 과 조정 run 은 다른 run 이다."""
    if not _OVERRIDES: return RULE_VERSION
    sig = json.dumps({k: _OVERRIDES[k]["value"] for k in sorted(_OVERRIDES)},
                     ensure_ascii=False, sort_keys=True)
    return f"{RULE_VERSION}+tuned.{hashlib.sha256(sig.encode()).hexdigest()[:8]}"


def numeric_rules() -> dict:
    """(c) 판정 규칙 상수를 한 벌로 반환한다 — 원장에 기록해 run 을 재현 가능하게 만든다.

    조정이 있으면 rule_version 이 달라지고, 무엇을 왜 바꿨는지가 함께 실린다.
    """
    r = {"rule_version": effective_rule_version(), "geom_round_digits": GEOM_ROUND_DIGITS,
         "param_round_digits": PARAM_ROUND_DIGITS, "hash_hex_len": HASH_HEX_LEN,
         "text_probe_bytes": TEXT_PROBE_BYTES, "text_printable_min": TEXT_PRINTABLE_MIN,
         "af_theta_samples": AF_THETA_SAMPLES,
         "array_y_split_mm": ARRAY_Y_SPLIT_MM, "array_min_seg_mm": ARRAY_MIN_SEG_MM}
    if _OVERRIDES:
        r["rule_version_base"] = RULE_VERSION
        r["overrides"] = {k: {"value": v["value"], "기본값": _DEFAULTS[k], "산지": v["bucket"],
                              "why": v["why"], "by": v["by"], "source": v["source"]}
                          for k, v in _OVERRIDES.items()}
    return r


# ── 경로 ────────────────────────────────────────────────────────────────────
def data_dir() -> Path:
    """작업·산출이 쌓이는 곳. 비어 있으면 저장소 루트다.

    ★ 빈 문자열도 미설정으로 본다 — 플러그인 설정에서 `${ORCH_DATA_DIR}` 이 풀리지 않으면
      빈 문자열이 들어오고, 그대로 쓰면 **현재 폴더**에 데이터가 쌓인다.
    """
    v = (os.environ.get("ORCH_DATA_DIR") or "").strip()
    return Path(v) if v else REPO


def data_dir_is_volatile() -> dict:
    """데이터가 **지워질 자리**에 쌓이려 하는가.

    플러그인으로 설치하면 저장소가 플러그인 폴더 안에 있다. 거기에 `work/` · `out/` ·
    원장을 쌓으면 **플러그인을 갱신하는 순간 전부 사라진다.** 사라진 뒤에 알면 늦다 —
    원장은 재현의 근거이고 재현되지 않는 실행은 하지 않은 것이다(B-3).
    """
    if (os.environ.get("ORCH_DATA_DIR") or "").strip():
        return {"volatile": False, "why": "ORCH_DATA_DIR 이 지정되어 있다"}
    d = str(data_dir()).replace("\\", "/")
    marks = [m for m in ("/plugins/", "/.claude/", "/marketplace") if m in d]
    if os.environ.get("CLAUDE_PLUGIN_ROOT") or marks:
        return {"volatile": True, "dir": str(data_dir()),
                "why": ("작업·산출이 **플러그인 폴더 안**에 쌓이려 한다. 플러그인을 갱신하면 "
                        "원장까지 함께 지워진다 — 재현할 수 없게 된다(B-3)."),
                "다음": ("`ORCH_DATA_DIR` 를 작업 폴더로 지정한다. "
                       "예: ORCH_DATA_DIR=C:\\Work\\_antenna_mcp")}
    return {"volatile": False, "why": "저장소 루트에 쌓인다 — 플러그인 설치가 아니다"}


def work_dir(run_id: str, create: bool = True) -> Path:
    d = data_dir() / "work" / run_id
    if create: d.mkdir(parents=True, exist_ok=True)
    return d


def out_dir(source_name: str, create: bool = True) -> Path:
    d = data_dir() / "out" / source_name
    if create: d.mkdir(parents=True, exist_ok=True)
    return d


class LedgerAmbiguous(RuntimeError):
    """원장 후보가 여럿인데 정본이 지정되지 않았다 — **조용히 고르지 않는다**(A-1)."""


def ledger_path() -> Path:
    """원장 경로. 여럿이면 **고르지 않고 멈춘다**(결함 F-11 의 수리).

    앞선 구현은 기본값으로 `ledger.sqlite` 를 조용히 가리켰다. 판이 올라가
    `ledger-v3.sqlite` 가 생긴 뒤에도 그대로여서, 게이트가 `not_recorded` 로 반려하고
    나서야 알 수 있었다 — **조용한 것이 문제였다.**

    이제 후보가 여럿이면 멈추고 현황을 보여준다. 정본이 무엇인지는 **조직의 사실**이지
    파일 시각의 함수가 아니다 — "가장 새것" 같은 규칙을 넣지 않는다(A-1).
    """
    env = os.environ.get("ORCH_LEDGER_DB", "").strip()
    if env:
        return Path(env)
    d = data_dir()
    default = d / "ledger.sqlite"
    others = sorted(p for p in d.glob("ledger*.sqlite")
                    if p.is_file() and not any(x in p.name for x in ("-wal", "-shm", "-journal")))
    if len(others) <= 1:
        return others[0] if others else default
    raise LedgerAmbiguous(
        "원장 후보가 여럿이다 — 도구가 고르지 않는다(A-1). "
        f"후보: {[p.name for p in others]}. "
        "`ORCH_LEDGER_DB` 로 정본을 지정한다. 현황: python tools/dbview.py ledger")


def checkpoint_path() -> Path:
    return Path(os.environ.get("ORCH_CHECKPOINT_DB", str(data_dir() / "checkpoints.sqlite")))


def assert_writable(path, *, as_ledger: bool = False) -> Path:
    """W-1 강제 — 쓸 수 있는 곳은 work/<run_id>/ · out/<원천명>/ · 원장 · 체크포인트뿐이다.

    수리 1: 앞선 구현은 work/·out/ 만 허용해 AGENTS.md W-1 이 명시한 `ledger.sqlite`(append)와
            `checkpoints.sqlite` 를 거부했다. 규칙 원문에 맞춘다(부속 파일 -wal·-shm·-journal 포함).
    수리 2: 계약이 원장의 **연 단위 롤오버**를 규정하므로 원장 파일은 하나가 아니다.
            `as_ledger=True` 는 "이것은 원장 파일이다"라는 호출자의 명시적 선언이며,
            그때만 **원장과 같은 폴더의 .sqlite** 를 허용한다(롤오버·마이그레이션 대상).
            폴더를 벗어나거나 확장자가 다르면 여전히 거부한다 — 선언이 만능 통과권은 아니다.
    """
    p = Path(path).resolve()
    roots = [(data_dir() / "work").resolve(), (data_dir() / "out").resolve()]
    if any(str(p).startswith(str(r)) for r in roots):
        return p
    for db in (ledger_path(), checkpoint_path()):
        db = db.resolve()
        if str(p) in (str(db), f"{db}-wal", f"{db}-shm", f"{db}-journal"):
            return p
    if as_ledger:
        home = ledger_path().resolve().parent
        if p.parent == home and p.suffix.lower() == ".sqlite":
            return p
        raise PermissionError(
            f"쓰기 범위 위반(W-1 · 원장 롤오버): {p} — 원장 폴더({home})의 *.sqlite 만 허용")
    raise PermissionError(
        f"쓰기 범위 위반(W-1): {p} — work/ · out/ · ledger.sqlite · checkpoints.sqlite 만 허용")


def new_run_id(prefix: str = "L1") -> str:
    """run_id = thread_id. 시각은 호출자 환경의 로컬 시각 문자열로만 쓰인다(정렬용)."""
    from datetime import datetime
    return f"{prefix}-{datetime.now().strftime('%Y%m%dT%H%M%S')}-{os.getpid():05d}"


# ── 입출력 ──────────────────────────────────────────────────────────────────
def write_json(path, obj) -> Path:
    """원자적 쓰기 — 중간에 죽어도 반쪽 JSON을 남기지 않는다. UTF-8 명시(Windows cp949 회피)."""
    p = assert_writable(path); p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=False)
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)
    return p


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha256_16(path, chunk: int = HASH_CHUNK_BYTES) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while (c := f.read(chunk)): h.update(c)
    return h.hexdigest()[:HASH_HEX_LEN]


def canonical_hash(obj) -> str:
    """geom_hash — 형상 딕셔너리의 정규 직렬화 해시. 부동소수는 호출부에서 이미 라운딩되어 있어야 한다."""
    s = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:HASH_HEX_LEN]


# ── vendor_srs 진입 (수정 금지 — 감싸서만 쓴다, T-5) ─────────────────────────
def vendor():
    v = str(REPO / "vendor_srs")
    if v not in sys.path: sys.path.insert(0, v)
    import cad_render  # noqa: E402
    return cad_render


# ── 원천 폴더 불변 검사 (W-2 · 완료 기준 6) ─────────────────────────────────
def source_fingerprint(source_path) -> dict:
    """원천 폴더의 파일별 (size, mtime_ns, sha256_16). run 전후 비교로 원본 불변을 증명한다."""
    root = Path(source_path)
    files = sorted([p for p in root.rglob("*") if p.is_file()], key=lambda p: str(p).lower())
    return {"root": str(root), "n_files": len(files),
            "files": {str(p.relative_to(root)).replace("\\", "/"):
                      {"size": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns,
                       "sha256_16": sha256_16(p)} for p in files}}


def fingerprint_diff(before: dict, after: dict) -> list:
    b, a = before.get("files", {}), after.get("files", {})
    d = []
    for k in sorted(set(b) | set(a)):
        if k not in a: d.append({"file": k, "change": "삭제됨"})
        elif k not in b: d.append({"file": k, "change": "추가됨"})
        elif b[k]["sha256_16"] != a[k]["sha256_16"]: d.append({"file": k, "change": "내용 변경"})
        elif b[k]["mtime_ns"] != a[k]["mtime_ns"]: d.append({"file": k, "change": "mtime 변경"})
    return d


# ── 레지스트리 (임계·예산의 유일한 주입원 — env·하드코딩 금지) ───────────────
DECLARED_DIR = "declared"          # registry/declared/<제품>.yaml — 사람이 말해서 채운 값


def _deep_merge(base: dict, over: dict) -> dict:
    """겹치면 선언(over)이 이긴다. **선언은 원본을 고치지 않는다** — 위에 얹을 뿐이다."""
    out = dict(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        elif v is not None:            # null 로 지우지 않는다 — 지우기는 사람이 파일에서
            out[k] = v
    return out


def load_registry(path=None) -> dict:
    """`registry/products.yaml` + `registry/declared/*.yaml` 덮어쓰기.

    왜 파일을 나누나 — `products.yaml` 은 **사람이 손으로 관리하는 정본**이다. 도구가
    거기에 쓰기 시작하면 주석과 손 편집이 갈려 나가고(T-5 의 정신), 무엇을 사람이 정했고
    무엇을 도구가 넣었는지 구분이 사라진다. 선언은 별도 파일에 쌓고 **위에 얹는다** —
    산지가 파일 경로로 남고, 되돌리려면 파일을 지우면 된다.
    """
    import yaml
    p = Path(path or os.environ.get("ORCH_PRODUCT_REGISTRY", str(REPO / "registry" / "products.yaml")))
    if not p.exists():
        raise FileNotFoundError(f"레지스트리 없음: {p} — 임계는 인자로만 받는다(하드코딩 금지)")
    reg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    dec_dir = p.parent / DECLARED_DIR
    if not dec_dir.is_dir():
        return reg
    prods = reg.setdefault("products", {})
    for f in sorted(dec_dir.glob("*.yaml")):
        d = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        name = d.get("product") or f.stem
        if name not in prods:
            continue                   # 없는 제품에 선언하지 않는다 — 제품 신설은 사람이
        prods[name] = _deep_merge(prods[name], d.get("values") or {})
        prods[name].setdefault("선언_출처", []).append(
            f"{f.name} · {d.get('by') or '미상'} · {d.get('at') or '시각 미상'}")
    return reg


def resolve_product(registry: dict, product: str | None) -> tuple[str, dict]:
    """제품군 미배정이면 광역 기본값(default)을 쓰고, 그 사실을 이름으로 드러낸다."""
    products = registry.get("products", {})
    if product and product in products:
        return product, products[product]
    return "default(제품군 미배정)", products.get("default", {})


# ── 점검 명령 (사람용) ──────────────────────────────────────────────────────
# 파이프라인은 이 명령을 부르지 않는다. "지금 어떤 값으로 도는가"를 사람이 눈으로 확인하는 수단이다.
#   python tools/_common.py tunables            현재 유효값·기본값·조정 내역
#   python tools/_common.py rules               원장에 실릴 numeric_rules() 그대로
if __name__ == "__main__":
    import argparse as _ap
    _p = _ap.ArgumentParser(description="조정값 점검 (사람용 · 파이프라인 비호출)")
    _p.add_argument("cmd", choices=["tunables", "rules", "self-test"])
    _a = _p.parse_args()
    if _a.cmd == "self-test":
        _ck = []
        def _t(name, fn):
            try: ok, det = fn()
            except Exception as e: ok, det = False, f"예외 {type(e).__name__}: {e}"
            _ck.append((name, ok, det)); print(f"  {'○' if ok else '×'} {name}  {det}")
        def _raises(fn, frag):
            try: fn()
            except ValueError as e: return frag in str(e), str(e)[:70]
            return False, "거부하지 않았다"
        _t("(a)(b) 상수는 조정 불가",
           lambda: _raises(lambda: _coerce("half_power_db", {"value": -3, "why": "x"}), "조정 불가"))
        _t("예산은 조정값이 아니다(registry 로 보낸다)",
           lambda: _raises(lambda: _coerce("max_attempts", {"value": 6, "why": "x"}), "예산이다"))
        _t("사유 없는 변경 거부",
           lambda: _raises(lambda: _coerce("geom_round_digits", {"value": 6}), "사유"))
        _t("축약형(값만) 거부",
           lambda: _raises(lambda: _coerce("geom_round_digits", 6), "축약형"))
        _t("정의역 밖 거부",
           lambda: _raises(lambda: _coerce("text_printable_min", {"value": 1.5, "why": "x"}), "상한"))
        _t("모르는 이름 거부",
           lambda: _raises(lambda: _coerce("nope", {"value": 1, "why": "x"}), "모르는 조정값"))
        def _rv():
            base = effective_rule_version()
            set_override("geom_round_digits", 6, why="자기 시험", by="self-test")
            tuned = effective_rule_version()
            clear_overrides()
            back = effective_rule_version()
            return (base == back == RULE_VERSION and tuned.startswith(RULE_VERSION + "+tuned.")
                    and GEOM_ROUND_DIGITS == _DEFAULTS["geom_round_digits"]), tuned
        _t("조정하면 rule_version 이 갈린다 · 되돌리면 복귀", _rv)
        def _nr():
            set_override("hbw_uniform_factor", 1.15, why="자기 시험", by="self-test")
            r = numeric_rules(); clear_overrides()
            o = r.get("overrides", {}).get("hbw_uniform_factor", {})
            return (o.get("why") == "자기 시험" and o.get("기본값") == 0.886
                    and r.get("rule_version_base") == RULE_VERSION), str(o)[:70]
        _t("numeric_rules 가 조정 사유·기본값을 함께 싣는다", _nr)
        def _rd():
            # __main__ 으로 돌 때 이 파일은 `_common` 이 아니다 → 하위 도구가 보는 그 모듈로 시험한다
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import importlib; _c = importlib.import_module("_common")
            import verify_api as _v0
            before = _v0.AF_THETA_SAMPLES
            _c.set_override("af_theta_samples", 7201, why="자기 시험", by="self-test")
            dropped = _c.reload_dependents(("verify_api",))
            import verify_api as _v
            ok = _v.AF_THETA_SAMPLES == 7201 and before == _DEFAULTS["af_theta_samples"]
            _c.clear_overrides(); _c.reload_dependents(("verify_api",))
            import verify_api as _v2
            return ok and _v2.AF_THETA_SAMPLES == before, f"{before} → 7201 → {_v2.AF_THETA_SAMPLES} · reload {dropped}"
        _t("reload_dependents 로 하위 도구까지 값이 바뀐다", _rd)
        _n = sum(1 for _, ok, _ in _ck if ok)
        print(f"자기 시험: {_n}/{len(_ck)} → {'PASS' if _n == len(_ck) else 'FAIL'}")
        sys.exit(0 if _n == len(_ck) else 1)
    if _a.cmd == "rules":
        print(json.dumps(numeric_rules(), ensure_ascii=False, indent=2))
    else:
        print(f"rule_version : {effective_rule_version()}")
        if _OVERRIDES: print(f"  (기본 {RULE_VERSION} · 조정 {len(_OVERRIDES)}건 → 다른 run 으로 기록된다)")
        print(f"{'이름':<22}{'산지':<5}{'유효값':>12}{'기본값':>12}  정의역 / 뜻")
        print("-" * 100)
        for _n, (_b, _t, _lo, _hi, _d) in TUNABLES.items():
            _cur = globals()[_TUNABLE_GLOBAL[_n]]
            _mark = " *" if _n in _OVERRIDES else "  "
            _dom = f"[{'-∞' if _lo is None else _lo}, {'∞' if _hi is None else _hi}]"
            print(f"{_n:<22}({_b}) {_cur!s:>11}{_DEFAULTS[_n]!s:>12}{_mark}{_dom:<14}{_d}")
        if _OVERRIDES:
            print("\n조정 내역")
            for _n, _e in _OVERRIDES.items():
                print(f"  * {_n} = {_e['value']} (기본 {_DEFAULTS[_n]})")
                print(f"      사유 {_e['why']} · 주체 {_e['by']} · 경로 {_e['source']}")
        else:
            print("\n조정 없음 — 전부 코드 기본값이다.")
        print("\n바꿀 수 없는 값:")
        for _n, _why in _IMMUTABLE.items(): print(f"  × {_n:<24}{_why}")
