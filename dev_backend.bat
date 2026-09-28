@echo off
setlocal
rem ============================================================
rem  Backend dev mode (auto reload). API only on :8000
rem  Use together with dev_frontend.bat for frontend work.
rem ============================================================
chcp 65001 >nul

set "ROOT=%~dp0"

rem 默认 venv 在**用户目录**下（%USERPROFILE%\.workbuddy\...），
rem 不是项目目录的上一级 —— 以前写错成 "%ROOT%..\.workbuddy\..." 会找不到 Python。
set "PY=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if exist "%ROOT%.venv\Scripts\python.exe" set "PY=%ROOT%.venv\Scripts\python.exe"
if exist "%ROOT%backend\.venv\Scripts\python.exe" set "PY=%ROOT%backend\.venv\Scripts\python.exe"

if not exist "%PY%" (
  echo [ERROR] 找不到 Python 环境。以下路径都不存在：
  echo     %USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe
  echo     %ROOT%.venv\Scripts\python.exe
  echo     %ROOT%backend\.venv\Scripts\python.exe
  echo.
  pause
  exit /b 1
)

cd /d "%ROOT%backend"
echo 后端开发模式（改代码自动重启）: http://127.0.0.1:8000/docs
echo 前端另开一个窗口跑 dev_frontend.bat
echo.
"%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
pause
