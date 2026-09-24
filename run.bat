@echo off
REM SOPHIA Input Generator - Windows local launcher
REM Uses the python.org "py" launcher first so that MSYS2's python on PATH
REM does not shadow the real Python 3.12 installation.
setlocal
cd /d "%~dp0"

set "PYCMD="
where py >nul 2>nul
if not errorlevel 1 set "PYCMD=py -3"
if defined PYCMD goto found
where python >nul 2>nul
if not errorlevel 1 set "PYCMD=python"
if defined PYCMD goto found
echo [ERROR] Python not found. Install Python 3.12 from python.org.
pause
exit /b 1

:found
echo Using: %PYCMD%
if not exist ".env" (
  echo [WARN] .env not found. Copy .env.example to .env and set ANTHROPIC_API_KEY.
)
%PYCMD% -m pip install -q -r requirements.txt
if errorlevel 1 goto pipfail
start "" http://127.0.0.1:5000
%PYCMD% app.py
goto end

:pipfail
echo [ERROR] pip install failed.
pause
exit /b 1

:end
endlocal
