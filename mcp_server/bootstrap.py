#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mcp_server/bootstrap.py — 플러그인 런타임 자립 (표준 라이브러리만)

플러그인으로 설치하면 호스트가 이 파일을 **시스템 파이썬**으로 띄운다. 그 파이썬에는
`mcp` · `pyyaml` 이 없을 수 있다(대개 없다). Claude Code 는 플러그인의 requirements 를
자동으로 깔아 주지 않는다 — 그래서 여기서 스스로 갖춘다.

하는 일
    1. `${CLAUDE_PLUGIN_DATA}/venv` 를 만든다(플러그인을 갱신해도 데이터 폴더는 살아 있다).
    2. **기동 임계** 의존성(requirements-runtime.txt: mcp · pyyaml)을 그 venv 에 **동기로**
       설치한다 — 매니페스트가 바뀌었을 때만 다시 설치한다(내용 대조. 파일 존재만으로 판정 안 함).
    3. **그림용** matplotlib(requirements-figures.txt)은 **백그라운드로** 깐다 — 지연 로드라
       기동을 막지 않는다. 서버는 먼저 붙고 matplotlib 는 뒤에서 준비된다.
    4. 그 venv 의 파이썬으로 `server.py` 를 **넘겨** 실행한다(argv · stdio 그대로).

★ stdio 규율: 이 프로세스는 **표준출력에 한 글자도 쓰지 않는다.** 표준출력은 MCP 의
  JSON-RPC 통로다 — 로그도 pip 출력도 전부 표준오류로 보낸다. 서버가 그 통로를 물려받는다.

환경
    CLAUDE_PLUGIN_DATA   갱신에도 살아남는 영속 폴더. 플러그인 밖에서 실행하면(개발) 없다 —
                         그때는 저장소 안 `.plugin-data/` 로 떨어진다(.gitignore 처리).
    CLAUDE_PLUGIN_ROOT   플러그인 루트. server.py 가 ORCH_REPO 로 받는다(plugin.json 이 지정).
"""
from __future__ import annotations

import os
import subprocess
import sys
import venv
from pathlib import Path

# 로그·pip 출력이 표준오류로 나갈 때 한글이 깨지지 않게 UTF-8 로 고정한다(콘솔 코드페이지 무관).
try:
    sys.stderr.reconfigure(encoding="utf-8")  # 3.7+
except Exception:
    pass

_HERE = Path(__file__).resolve().parent          # …/mcp_server
_REPO = _HERE.parent                             # 플러그인 루트
_SERVER = _HERE / "server.py"
_REQ_CORE = _HERE / "requirements-runtime.txt"   # 기동 임계 — 동기
_REQ_FIG = _HERE / "requirements-figures.txt"    # 그림 — 백그라운드


def _log(msg: str) -> None:
    """표준오류로만 알린다 — 표준출력은 MCP 통로다."""
    print(f"[antenna-orchestrator/bootstrap] {msg}", file=sys.stderr, flush=True)


def _data_dir() -> Path:
    d = os.environ.get("CLAUDE_PLUGIN_DATA", "").strip()
    if d:
        p = Path(d)
    else:
        # 플러그인 밖(개발·직접 실행) — 저장소 안에 떨어뜨린다.
        p = _REPO / ".plugin-data"
        _log("CLAUDE_PLUGIN_DATA 가 없다 — 개발 모드로 본다. venv 를 .plugin-data/ 에 둔다.")
    p.mkdir(parents=True, exist_ok=True)
    return p


def _venv_python(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _ensure_venv(venv_dir: Path) -> Path:
    vpy = _venv_python(venv_dir)
    if vpy.exists():
        return vpy
    _log(f"venv 를 만든다: {venv_dir}")
    venv.EnvBuilder(with_pip=True, clear=False).create(str(venv_dir))
    if not vpy.exists():
        raise RuntimeError(f"venv 를 만들었지만 파이썬이 없다: {vpy}")
    return vpy


def _install_core_if_stale(vpy: Path, data_dir: Path) -> None:
    """기동 임계 의존성을 동기로 설치한다 — 처음이거나 매니페스트가 바뀌었을 때만(내용 대조)."""
    lock = data_dir / "requirements-runtime.lock"
    want = _REQ_CORE.read_bytes() if _REQ_CORE.exists() else b""
    have = lock.read_bytes() if lock.exists() else None
    if have == want and want:
        return  # 이미 이 매니페스트로 설치했다 — 건너뛴다(빠른 경로).

    _log("기동 의존성을 설치한다(처음이거나 매니페스트가 바뀌었다). 이번 한 번뿐이다.")
    try:
        subprocess.run(
            [str(vpy), "-m", "pip", "install", "--disable-pip-version-check",
             "-q", "-r", str(_REQ_CORE)],
            stdout=sys.stderr,   # ★ pip 출력을 표준출력에 흘리지 않는다
            stderr=sys.stderr,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        # 락을 남기지 않는다 — 다음 세션이 다시 시도한다.
        _log(f"기동 의존성 설치 실패(exit {e.returncode}). 다음 세션에 다시 시도한다. "
             f"직접 깔려면: {vpy} -m pip install -r {_REQ_CORE}")
        return
    lock.write_bytes(want)
    _log("기동 의존성 준비 완료.")


def _install_figures_bg(vpy: Path, data_dir: Path) -> None:
    """matplotlib 을 **백그라운드**로 깐다 — 성공하면 표식을 남긴다. 기동을 막지 않는다."""
    if not _REQ_FIG.exists():
        return
    marker = data_dir / "figures.ok"
    # 표식이 이 매니페스트와 같으면 이미 깔린 것 — 건너뛴다.
    want = _REQ_FIG.read_bytes()
    if marker.exists() and marker.read_bytes() == want:
        return
    _log("그림 의존성(matplotlib)을 백그라운드로 준비한다 — 서버 기동을 막지 않는다.")
    # pip 성공 뒤 **폰트 캐시를 미리 데운다.** matplotlib 첫 렌더는 시스템 폰트를 훑어
    # 캐시(~/.matplotlib)를 만드는데, Windows 에선 이게 매우 느리고 여러 프로세스가 동시에
    # 만들려 하면 교착된다 — 그 비용을 run_pipeline 안(MCP 호출)에서 치르면 멈춘 것처럼 보인다.
    # 여기서 미리 한 번 그려 캐시를 만들어 두면 첫 그림이 즉시 나온다.
    # pip·캐시 warm 이 성공했을 때만 표식을 남긴다. 실패하면 다음 세션이 다시 시도한다.
    child = (
        "import subprocess,sys,pathlib\n"
        "r=subprocess.run([sys.executable,'-m','pip','install',"
        "'--disable-pip-version-check','-q','-r'," + repr(str(_REQ_FIG)) + "])\n"
        "if r.returncode==0:\n"
        "    try:\n"
        "        import matplotlib; matplotlib.use('Agg')\n"
        "        import matplotlib.pyplot as plt\n"
        "        plt.figure(); plt.plot([0,1],[0,1]); plt.close('all')\n"  # 폰트 캐시 빌드 유발
        "    except Exception:\n"
        "        pass\n"
        "    pathlib.Path(" + repr(str(marker)) + ").write_bytes(" + repr(want) + ")\n"
    )
    # 부모(이 부트스트랩) 수명과 무관하게 살아남도록 자식을 분리한다 —
    # 짧은 세션이라도 설치가 끊기지 않게. 실패하면 표식이 없어 다음 세션이 다시 시도한다.
    # ★ Windows: DETACHED_PROCESS 는 **검은 콘솔 창**을 새로 띄운다 — 쓰지 않는다.
    #   CREATE_NO_WINDOW 로 창 없이 돌리고, CREATE_NEW_PROCESS_GROUP 로 부모와 독립시킨다.
    detach = {}
    if os.name == "nt":
        # CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP
        detach["creationflags"] = 0x08000000 | 0x00000200
    else:
        detach["start_new_session"] = True
    try:
        subprocess.Popen(
            [str(vpy), "-c", child],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, **detach,
        )
    except Exception as e:
        _log(f"그림 의존성 백그라운드 설치를 띄우지 못했다({type(e).__name__}). "
             f"첫 그림 때 직접 필요하면: {vpy} -m pip install -r {_REQ_FIG}")


def main() -> int:
    data_dir = _data_dir()
    venv_dir = data_dir / "venv"
    try:
        vpy = _ensure_venv(venv_dir)
        _install_core_if_stale(vpy, data_dir)
        _install_figures_bg(vpy, data_dir)
    except Exception as e:  # venv 조차 못 만들면 — 시스템 파이썬으로라도 서버를 넘긴다
        _log(f"venv 준비 실패({type(e).__name__}: {e}). 시스템 파이썬으로 server.py 를 넘긴다.")
        vpy = Path(sys.executable)

    # server.py 로 넘긴다 — argv·stdin·stdout·stderr 를 그대로 물려준다.
    cmd = [str(vpy), str(_SERVER), *sys.argv[1:]]
    proc = subprocess.run(cmd)   # stdio 상속: MCP 통로가 서버로 그대로 이어진다
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
