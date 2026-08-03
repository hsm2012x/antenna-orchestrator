@echo off
REM scripts\install_level1.bat — Level 1 4단계 의존 설치 (Windows 본체)
REM 규율: 1~3단계는 추가 설치가 필요 없다. 상태 머신용 최소 설치만 한다.
setlocal
cd /d "%~dp0.."

set PY=python
%PY% -V || (echo Python 을 찾지 못했다 & exit /b 1)

if not exist .venv (
  echo == 가상환경 생성 (.venv^)
  %PY% -m venv .venv
)
call .venv\Scripts\activate.bat
set PY=python

echo == 설치
%PY% -m pip install --upgrade pip
if defined WHEELHOUSE (
  %PY% -m pip install --no-index --find-links "%WHEELHOUSE%" -r requirements.txt
) else (
  %PY% -m pip install -r requirements.txt
)

echo == 점검
%PY% tools\check_env.py --state
endlocal
