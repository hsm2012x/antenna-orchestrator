#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tools/dwgmeta.py — DWG 헤더 결정론 판독 (LLM 0콜)

출처
    승격  `reference_code/08_antenna_cad_em.ipynb` **셀 10** — `dwg_meta` · `dwg_preview`
          · `_sha16`. 원본과 스냅샷은 수정하지 않았다. 여기 사본은 경로 처리만 이 저장소
          규약(`Path` 인자·예외 무전파)에 맞췄고 판정 로직은 그대로다.
    신규  `dwg_dates` 는 **셀 10 에 없다.** README 는 셀 10 을 "근거 등급의 원재료"로
          적었지만, 실제로 셀 10 이 뽑는 것은 포맷·저장 프로그램·폰트·외부 경로뿐이고
          **시각은 하나도 뽑지 않는다.** 그래서 시각 판독기는 여기서 새로 쓴다.
          셀 10 에서 물려받은 것은 코드가 아니라 **자세**다 — "헤더에서 결정론으로
          읽히는 것만 읽고, 압축 구간은 읽지 않는다."

★ 왜 전수 스캔이 아니라 앵커 판독인가
    앞서 파일 전체에서 (율리우스일, ms) 꼴을 훑어봤더니 2031-01-30 · 2034-02-22 같은
    가짜가 섞였다. 압축된 본문의 임의 바이트가 우연히 조건을 만족한 것이다.
    → 파일 헤더가 **주소로 가리키는** `AcDb:SummaryInfo` 안에서만, **정해진 자리**를
      읽는다. 스캔이 아니라 구조 판독이므로 가짜가 나올 자리가 없다.

AcDb:SummaryInfo 배치 (R2004+ = AC1018 이상, 실물 2종으로 확인)
    파일 헤더 0x20 (LE u32)  →  섹션 시작 주소
      TV × 8   Title · Subject · Author · Keywords · Comments · LastSavedBy
               · RevisionNumber · HyperlinkBase
               TV = RS 문자수(끝 NUL 포함) + 문자수×2 바이트 UTF-16LE. 빈 값은 01 00 00 00
      RS       사용자 정의 속성 개수 (그 수만큼 TV 이름/값 쌍이 이어진다)
      6 바이트 미상
      BL,BL    생성 (율리우스일, 자정 이후 ms)   — 없으면 0/0
      BL,BL    수정 (율리우스일, 자정 이후 ms)
    확인: `antenna reflector.dwg`(AC1024) 는 문자열이 2바이트 길고, `holes_drill_20260227.dwg`
    (AC1032) 는 짧다. 두 파일에서 자리가 **정확히 그만큼 어긋나며** 같은 규칙으로 맞는다.
    길이가 다른 두 실물이 같은 규칙을 만족하는 것이 이 배치의 근거다.

표준시 — **한국(KST, UTC+9). 확정 2026-07-31 사람**
    SummaryInfo 값이 현지인지 세계시인지 파일에 적혀 있지 않다. 다만 두 실물 모두 파일
    안에 **정확히 +9시간 어긋난 쌍둥이 값**이 또 있다. 기계가 한국이라면 그 쌍둥이가
    현지(KST)이고 SummaryInfo 쪽이 **세계시**라는 뜻이다.
    → 그래서 판독값에 +9h 를 더해 `modified`(현지 KST)로 싣고, 세계시는 `modified_utc`
      로 함께 남긴다. 쌍둥이가 정확히 +9 인지를 매번 확인해 `tz_check` 에 적는다 —
      다른 시간대 기계에서 저장된 파일이 섞이면 그 자리에서 드러난다.

읽지 않는 것 (T-1)
    R2000 이하(AC1015 이하)에는 이 섹션이 없다 — **없다고 말하고 만다.** 본문 섹션은
    압축이므로 레이어명·치수는 변환기 없이 읽지 않는다(셀 10 의 `note` 그대로).

CLI
    python tools/dwgmeta.py read <파일.dwg>
    python tools/dwgmeta.py scan <폴더>
    python tools/dwgmeta.py self-test
"""
from __future__ import annotations

import hashlib
import re
import struct
import sys
from datetime import datetime, timedelta
from pathlib import Path

# ── 셀 10 승격분 ────────────────────────────────────────────────────────────
# 출처: 08_antenna_cad_em.ipynb 셀 10 (원본 무수정)
_DWG_VER = {"AC1009": "R11/12", "AC1012": "R13", "AC1014": "R14", "AC1015": "AutoCAD 2000",
            "AC1018": "2004", "AC1021": "2007", "AC1024": "2010", "AC1027": "2013",
            "AC1032": "2018+"}
_SENTINEL = bytes([0x1F, 0x25, 0x6D, 0x07, 0xD4, 0x36, 0x28, 0x28,
                   0x9D, 0x57, 0xCA, 0x3F, 0x9D, 0x44, 0x10, 0x2B])

# SummaryInfo 가 있는 판 — R2004 이상
_R2004_PLUS = ("AC1018", "AC1021", "AC1024", "AC1027", "AC1032")
_SUMMARY_ADDR_OFF = 0x20        # 파일 헤더에서 섹션 주소가 놓인 자리
_UNKNOWN_GAP = 6                # 속성 개수 뒤 미상 바이트 (실물 2종 확인)
_JD_EPOCH = 2440588             # 율리우스일 2440588 = 1970-01-01
_JD_MIN, _JD_MAX = 2444240, 2465000     # 1980-01-01 ~ 2037 — 밖은 읽지 않는다
_MS_MAX = 86_400_000
_PROBE_MAX = 32                 # 자리가 어긋났을 때 앞으로 훑는 상한(바이트)

# 표준시 — 판정 규칙 (c). 확정: 사람 2026-07-31 "표준시 한국"
TZ_OFFSET_HOURS = 9
TZ_LABEL = "KST (UTC+9)"
TZ_산지 = ("확정 2026-07-31 사람 — 한국. SummaryInfo 는 세계시로 보고 +9h 를 더해 현지로 "
          "싣는다. 파일 안 +9h 쌍둥이가 이 판정을 매번 확인한다(tz_check)")

_FNAME_DATE = re.compile(r"(?<!\d)(20\d{2})[-_.]?(\d{2})[-_.]?(\d{2})(?!\d)")


def _sha16(p, n=1 << 20):
    """출처: 셀 10"""
    hsh = hashlib.sha256()
    with open(p, "rb") as f:
        while (chunk := f.read(n)):
            hsh.update(chunk)
    return hsh.hexdigest()[:16]


def dwg_preview(path, out_dir):
    """DWG 헤더의 이미지 시커에서 미리보기(BMP code=2 / PNG code=6)를 추출.
    변환기 불요 — 순수 파이썬. 실패해도 예외 없이 None.
    출처: 셀 10 (원본 무수정 — `Path` 인자만 명시)"""
    b = Path(path).read_bytes()
    try:
        off = struct.unpack_from("<I", b, 0x0D)[0]
        if not (0 < off < len(b)) or b[off:off + 16] != _SENTINEL:
            return None
        p = off + 16 + 4
        n = b[p]
        p += 1
        for _ in range(n):
            code = b[p]
            start = struct.unpack_from("<I", b, p + 1)[0]
            size = struct.unpack_from("<I", b, p + 5)[0]
            p += 9
            data = b[start:start + size]
            stem = Path(out_dir) / (Path(path).stem + "_preview")
            if code == 6 and data[:8] == b"\x89PNG\r\n\x1a\n":
                f = stem.with_suffix(".png")
                f.write_bytes(data)
                return str(f)
            if code == 2 and size > 40:            # DIB(파일헤더 없음) → BMP 로 복원
                hsz, w, h, _pl, bpp = struct.unpack_from("<IiiHH", data, 0)
                ncol = struct.unpack_from("<I", data, 32)[0] or (1 << bpp if bpp <= 8 else 0)
                f = stem.with_suffix(".bmp")
                f.write_bytes(b"BM" + struct.pack("<IHHI", 14 + size, 0, 0,
                                                  14 + hsz + ncol * 4) + data)
                return str(f)
    except Exception:
        return None
    return None


def dwg_meta(path):
    """DWG 헤더·비압축 구간에서 뽑히는 것만. 본문 섹션은 압축이라 문자열 스캔은 신뢰 못 함.
    출처: 셀 10 (원본 무수정)"""
    b = Path(path).read_bytes()
    ver = b[:6].decode("ascii", "replace")
    # DWG 는 ASCII/UTF-16LE 혼재 — 둘 다 훑는다(본문 섹션은 압축이라 헤더 주변만 유효)
    txt = b.decode("latin-1", "ignore") + "\n" + b.decode("utf-16-le", "ignore")
    app = re.search(r"(Teigha\(R\)[^\x00]{0,20}|ODA[^\x00]{0,10}|AutoCAD[^\x00]{0,20})", txt)
    fonts = sorted(set(re.findall(r"[A-Za-z0-9_\-]+\.(?:shx|ttf)", txt, re.I)))
    paths = sorted(set(re.findall(r"[A-Z]:\\[A-Za-z0-9_\\ \.\-]{4,60}", txt)))
    return {"format": f"DWG {ver} ({_DWG_VER.get(ver, '?')})",
            "saved_by": app.group(0).strip() if app else None,
            "fonts": fonts[:6], "ext_paths": paths[:4],
            "has_view_detail": "AcVar ViewDetailId" in txt,
            "note": "본문 섹션 압축 — 레이어명·치수는 변환기 없이 읽을 수 없음"}


# ── 신규 — 시각 판독 (셀 10 에 없음) ────────────────────────────────────────

def _u16(b, p):
    return int.from_bytes(b[p:p + 2], "little") if p + 2 <= len(b) else None


def _u32(b, p):
    return int.from_bytes(b[p:p + 4], "little") if p + 4 <= len(b) else None


def _tv(b, p):
    """SummaryInfo 문자열 하나. 반환 (문자열|None, 다음 위치|None)."""
    n = _u16(b, p)
    if n is None or n > 512:
        return None, None
    p += 2
    if n == 0:
        return "", p
    if p + 2 * n > len(b):
        return None, None
    s = b[p:p + 2 * n].decode("utf-16-le", "replace").rstrip("\x00")
    return s, p + 2 * n


def _slot(b, p):
    """(율리우스일, ms) 한 칸. 유효하면 datetime, 0/0 이면 None, 그 밖이면 False."""
    jd, ms = _u32(b, p), _u32(b, p + 4)
    if jd is None or ms is None:
        return False
    if jd == 0 and ms == 0:
        return None                      # 기록되지 않음 — 결함이 아니다
    if not (_JD_MIN <= jd <= _JD_MAX and 0 <= ms < _MS_MAX):
        return False
    return datetime(1970, 1, 1) + timedelta(days=jd - _JD_EPOCH, milliseconds=ms)


def dwg_dates(path) -> dict:
    """DWG 의 생성·수정 시각. **헤더가 가리키는 자리만** 읽는다(스캔 아님).

    반환 `readable` 값
        ok            수정 시각을 읽었다
        no-section    R2000 이하 — 섹션이 없다(결함 아님)
        bad-address   주소가 파일 밖 — 읽지 않는다
        unparsed      자리를 찾았으나 값이 시각이 아니다 — **지어내지 않는다**
    """
    p0 = Path(path)
    b = p0.read_bytes()
    ver = b[:6].decode("ascii", "replace")
    out = {"file": p0.name, "format": f"DWG {ver} ({_DWG_VER.get(ver, '?')})",
           "created": None, "modified": None, "modified_utc": None, "created_utc": None,
           "last_saved_by": None, "tz": TZ_LABEL, "tz_산지": TZ_산지,
           "산지": "AcDb:SummaryInfo — 파일 헤더 0x20 이 가리키는 자리를 결정론으로 읽음"}

    if ver not in _R2004_PLUS:
        return {**out, "readable": "no-section",
                "why": f"{ver} 에는 AcDb:SummaryInfo 가 없다 — 시각 없음이 정답이다"}

    addr = _u32(b, _SUMMARY_ADDR_OFF)
    if not addr or not (0 < addr < len(b)):
        return {**out, "readable": "bad-address", "why": f"섹션 주소 {addr} 가 파일 밖"}

    p = addr
    names = ("title", "subject", "author", "keywords", "comments",
             "last_saved_by", "revision", "hyperlink_base")
    fields = {}
    for nm in names:
        s, p = _tv(b, p)
        if p is None:
            return {**out, "readable": "unparsed", "why": f"속성 {nm} 판독 실패"}
        fields[nm] = s or None

    ncustom = _u16(b, p)
    if ncustom is None or ncustom > 64:
        return {**out, "readable": "unparsed", "why": f"사용자 속성 개수 이상: {ncustom}"}
    p += 2
    for _ in range(ncustom):
        _, p = _tv(b, p)
        if p is None:
            return {**out, "readable": "unparsed", "why": "사용자 속성 이름 판독 실패"}
        _, p = _tv(b, p)
        if p is None:
            return {**out, "readable": "unparsed", "why": "사용자 속성 값 판독 실패"}

    base = p + _UNKNOWN_GAP
    a, c = _slot(b, base), _slot(b, base + 8)
    probe = 0
    # 두 칸이 모두 시각이 아니면 자리가 어긋난 것이다 — **가정을 우기지 않고** 짧게 훑는다.
    # 훑는 범위는 앵커에서 32바이트뿐이고 결과에 이동량을 적는다(조용히 맞추지 않는다).
    if a is False and c is False:
        for d in range(2, _PROBE_MAX + 1, 2):
            a2, c2 = _slot(b, base + d), _slot(b, base + d + 8)
            if a2 or c2:
                a, c, probe = a2, c2, d
                break

    if a is False and c is False:
        return {**out, "readable": "unparsed", "last_saved_by": fields["last_saved_by"],
                "why": "정해진 자리의 값이 시각 범위를 벗어난다 — 지어내지 않는다"}

    created = a if isinstance(a, datetime) else None
    modified = c if isinstance(c, datetime) else None
    if modified is None and created is not None:
        # 한 칸만 살아 있으면 그것은 **수정 시각**이다(생성은 0 으로 비워지는 실물이 있다)
        created, modified = None, created

    twin = _tz_twin(b, modified) if modified else None
    _k = timedelta(hours=TZ_OFFSET_HOURS)
    return {**out, "readable": "ok" if modified else "unparsed",
            "created": (created + _k).isoformat(sep=" ") if created else None,
            "modified": (modified + _k).isoformat(sep=" ") if modified else None,
            "created_utc": created.isoformat(sep=" ") if created else None,
            "modified_utc": modified.isoformat(sep=" ") if modified else None,
            "tz_check": ("확인 — 파일 안 쌍둥이가 +9h 다" if twin and
                         twin["offset_hours"] == TZ_OFFSET_HOURS else
                         f"불일치 — 쌍둥이가 {twin['offset_hours']:+d}h 다. 한국 기계가 아닐 수 있다"
                         if twin else "쌍둥이 없음 — 확인하지 못했다"),
            "last_saved_by": fields["last_saved_by"],
            "properties": {k: v for k, v in fields.items() if v},
            "offset": hex(base + probe), "probe_shift": probe,
            "why": ("정상 판독" if not probe else
                    f"앵커에서 {probe}바이트 뒤에서 자리를 찾았다 — 배치가 실물과 다르다"),
            "tz_twin": twin}


def _tz_twin(b: bytes, ref: datetime) -> dict | None:
    """같은 시각의 **시차 쌍둥이**를 찾는다. 보강 근거일 뿐 판독 근거가 아니다.

    DWG 는 현지/세계 시각을 둘 다 적는다. 정확히 정시 단위로 어긋난 쌍이 있으면
    **기계의 시간대가 그만큼**이라는 뜻이다. 표준시는 사람이 한국으로 확정했으므로,
    이 쌍둥이는 이제 **그 확정을 확인하는 자리**다(`tz_check`). 값을 고치지는 않는다.
    """
    for k in list(range(1, 15)) + list(range(-14, 0)):
        t = ref + timedelta(hours=k)
        jd = (t - datetime(1970, 1, 1)).days + _JD_EPOCH
        ms = int(((t - datetime(1970, 1, 1)).seconds * 1000)
                 + (t - datetime(1970, 1, 1)).microseconds // 1000)
        pat = struct.pack("<II", jd, ms)
        if pat in b:
            return {"offset_hours": k, "at": t.isoformat(sep=" "),
                    "뜻": f"파일 안에 {k:+d}시간 어긋난 같은 시각이 또 있다 — 기계 시간대 후보",
                    "등급": "보강(휴리스틱) — 판독 근거로 쓰지 않는다"}
    return None


def filename_dates(path) -> list[dict]:
    """이름에 박힌 날짜. **사람이 붙인 선언**이다 — 저장 시각이 아니다."""
    out = []
    for part in (Path(path).name,):
        for y, mo, d in _FNAME_DATE.findall(part):
            try:
                t = datetime(int(y), int(mo), int(d))
            except ValueError:
                continue
            out.append({"ts": t.isoformat(sep=" "), "text": f"{y}{mo}{d}"})
    return out


def read(path) -> dict:
    """한 파일에 대해 셀 10 메타 + 시각을 함께."""
    p = Path(path)
    return {**dwg_meta(p), **dwg_dates(p),
            "sha256_16": _sha16(p), "size": p.stat().st_size,
            "filename_dates": filename_dates(p)}


def scan(root) -> list[dict]:
    return [read(p) for p in sorted(Path(root).rglob("*"))
            if p.is_file() and p.suffix.lower() == ".dwg"]


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

    print("[dwgmeta.py 자기 시험 — 실물]")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import _common as C
    base = C.data_dir() / "handoff" / "04_experiment_data" / "Antenna_CAD_ECO"
    ref, hol = base / "antenna reflector.dwg", base / "holes_drill_20260227.dwg"
    if not ref.exists():
        print("  건너뜀 — 실물 없음")
        return 2

    a, h = dwg_dates(ref), dwg_dates(hol)
    chk("AC1024 판독", a["readable"] == "ok", str(a))
    chk("AC1032 판독", h["readable"] == "ok", str(h))
    chk("문자열 길이가 다른 두 실물이 같은 규칙에 맞는다",
        a["probe_shift"] == 0 and h["probe_shift"] == 0,
        f"{a['probe_shift']} {h['probe_shift']}")
    chk("reflector 수정 2026-02-26 10:50 KST", str(a["modified"]).startswith("2026-02-26 10:50"),
        str(a["modified"]))
    chk("세계시를 함께 남긴다", str(a["modified_utc"]).startswith("2026-02-26 01:50"),
        str(a["modified_utc"]))
    chk("reflector 생성 2020-12-16", str(a["created"]).startswith("2020-12-16"),
        str(a["created"]))
    chk("holes 수정 2026-02-26 16:56 KST", str(h["modified"]).startswith("2026-02-26 16:56"),
        str(h["modified"]))
    chk("생성이 비어 있어도 결함이 아니다", h["created"] is None, str(h["created"]))
    chk("저장한 사람 문자열", (a["last_saved_by"], h["last_saved_by"]) == ("USER", "chk"),
        f"{a['last_saved_by']} {h['last_saved_by']}")

    # 가짜가 사라졌는가 — 전수 스캔이 만들던 2031·2034 가 나오면 안 된다
    txt = str(a) + str(h)
    chk("전수 스캔이 만들던 가짜(2031·2034)가 없다",
        "2031-01-30" not in txt and "2034-02-22" not in txt)

    # 표준시 확정(한국) — 쌍둥이가 그 확정을 확인한다
    tw = a.get("tz_twin")
    chk("시차 쌍둥이를 찾는다", tw and tw["offset_hours"] == TZ_OFFSET_HOURS, str(tw))
    chk("표준시는 한국으로 확정", a["tz"] == TZ_LABEL and "확정" in a["tz_산지"], a["tz"])
    chk("쌍둥이가 +9h 임을 확인한다", a["tz_check"].startswith("확인") and
        h["tz_check"].startswith("확인"), f"{a['tz_check']} / {h['tz_check']}")

    # 이름 날짜와 헤더가 **어긋난다** — 지어내서 맞추지 않는다(N-3)
    fn = filename_dates(hol)
    chk("이름에서 날짜를 읽는다", fn and fn[0]["text"] == "20260227", str(fn))
    d_hdr = datetime.fromisoformat(h["modified"]).date()
    d_fn = datetime.fromisoformat(fn[0]["ts"]).date()
    chk("KST 로 바꿔도 이름(02-27)과 헤더(02-26)는 하루 어긋난다", (d_fn - d_hdr).days == 1,
        f"{d_fn} {d_hdr}")

    # 셀 10 승격분이 그대로 동작하는가
    m = dwg_meta(ref)
    chk("셀 10 dwg_meta 포맷 판정", m["format"].startswith("DWG AC1024"), m["format"])
    chk("셀 10 note 유지", "변환기 없이 읽을 수 없음" in m["note"])

    # 없는 것은 없다고 말한다
    import tempfile
    f = Path(tempfile.mkdtemp()) / "x.dwg"
    f.write_bytes(b"AC1015" + b"\x00" * 300)
    r = dwg_dates(f)
    chk("R2000 이하는 섹션 없음", r["readable"] == "no-section", str(r["readable"]))
    chk("없음을 결함으로 말하지 않는다", "정답" in r["why"])

    f2 = Path(tempfile.mkdtemp()) / "y.dwg"
    f2.write_bytes(b"AC1032" + b"\x00" * 300)
    r2 = dwg_dates(f2)
    chk("주소가 0 이면 읽지 않는다", r2["readable"] == "bad-address", str(r2["readable"]))

    print(f"\n결과: {ok}/{ok + fail} PASS")
    return 0 if fail == 0 else 1


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    if argv[1] == "self-test":
        return self_test()
    if argv[1] == "read":
        r = read(argv[2])
        for k in ("file", "format", "readable", "created", "modified", "modified_utc",
                  "last_saved_by", "offset", "tz", "tz_check", "why"):
            print(f"  {k:14} {r.get(k)}")
        if r.get("tz_twin"):
            print(f"  {'tz_twin':14} {r['tz_twin']}")
        if r.get("filename_dates"):
            print(f"  {'이름 날짜':14} {[d['text'] for d in r['filename_dates']]}")
        return 0
    if argv[1] == "scan":
        for r in scan(argv[2]):
            print(f"{r['file']:34} {r['readable']:12} 수정 {r['modified']} KST  "
                  f"저장자 {r['last_saved_by']}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
