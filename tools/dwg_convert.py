# dwg_convert.py — DWG → DXF 변환 훅 (ODA File Converter 래퍼)
#
# 반입 정책(결정 D-22): 외부 변환기의 사내 반입은 허용, 금지는 런타임 네트워크 호출뿐.
# 이 래퍼는 로컬에 설치된 ODA File Converter를 호출만 한다 — 네트워크 0.
# 변환기 미설치 시 예외 대신 {"pending": True} 를 돌려준다(연계점 규약) —
# 파이프라인은 그 파일을 "판독 불가(변환기 미반입)"로 기록하고 진행한다.
#
# 사용:
#   from tools.dwg_convert import dwg_to_dxf, converter_status
#   r = dwg_to_dxf("holes_drill_20260227.dwg", out_dir="work/r1/dxf")
#   r["ok"] → 변환된 DXF 경로 r["dxf"], 아니면 r["pending"]=True + r["reason"]
#
# env:
#   ODA_CONVERTER — ODAFileConverter 실행 파일 경로
#     (예: C:\Program Files\ODA\ODAFileConverter\ODAFileConverter.exe)
#   미설정이면 아래 기본 후보를 탐색한다.
#
# ODA File Converter CLI 규약(공식):
#   ODAFileConverter <입력폴더> <출력폴더> <출력버전> <출력타입> <재귀0|1> <감사0|1> [필터]
#   출력버전은 ACAD12(R12 ASCII DXF — 기존 dxf_read 파서와 호환)를 쓴다.

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

_CANDIDATES = [
    os.environ.get("ODA_CONVERTER", ""),
    r"C:\Program Files\ODA\ODAFileConverter\ODAFileConverter.exe",
    "/usr/bin/ODAFileConverter",
    "/opt/oda/ODAFileConverter",
]
_OUT_VERSION = "ACAD12"   # R12 ASCII — vendor dxf_read 규약과 호환
_OUT_TYPE = "DXF"
_TIMEOUT_S = 120


def _find_converter():
    for p in _CANDIDATES:
        if p and Path(p).exists():
            return str(p)
        if p and shutil.which(p):
            return shutil.which(p)
    return None


def converter_status():
    """{"installed": bool, "path": str|None, "searched": [...]} — 00 환경 점검 셀용."""
    p = _find_converter()
    return {"installed": p is not None, "path": p,
            "searched": [c for c in _CANDIDATES if c]}


def dwg_to_dxf(dwg_path, out_dir):
    """DWG 1건 → R12 ASCII DXF. 원본 불변 — 임시 폴더에 복사 후 변환한다.

    반환: {"ok": True, "dxf": 경로, "tool": ..., "version": "ACAD12"}
      또는 {"ok": False, "pending": True, "reason": ...}   (변환기 부재·실패)
    """
    src = Path(dwg_path)
    if not src.exists():
        return {"ok": False, "pending": True, "reason": f"입력 없음: {src}"}
    conv = _find_converter()
    if conv is None:
        return {"ok": False, "pending": True,
                "reason": "ODA File Converter 미반입 — ODA_CONVERTER env 설정 필요 (EXT-1)"}
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp_in:
        shutil.copy2(src, Path(tmp_in) / src.name)          # 원본 불변(I-2)
        cmd = [conv, tmp_in, str(out_dir), _OUT_VERSION, _OUT_TYPE, "0", "1", src.name]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            return {"ok": False, "pending": True, "reason": f"변환 타임아웃 {_TIMEOUT_S}s"}
        except OSError as e:
            return {"ok": False, "pending": True, "reason": f"실행 실패: {e}"}
    dxf = out_dir / (src.stem + ".dxf")
    if not dxf.exists():
        err = (r.stderr or r.stdout or "").strip()[:300]
        return {"ok": False, "pending": True,
                "reason": f"변환 결과 없음 (exit {r.returncode}) {err}"}
    return {"ok": True, "dxf": str(dxf), "tool": conv, "version": _OUT_VERSION}


if __name__ == "__main__":
    import argparse, json
    ap = argparse.ArgumentParser(description="DWG → R12 DXF (ODA 래퍼 · 런타임 네트워크 0)")
    ap.add_argument("dwg", nargs="?")
    ap.add_argument("--out", default="work/dxf")
    ap.add_argument("--status", action="store_true", help="변환기 설치 상태만 출력")
    a = ap.parse_args()
    if a.status or not a.dwg:
        print(json.dumps(converter_status(), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(dwg_to_dxf(a.dwg, a.out), ensure_ascii=False, indent=2))
