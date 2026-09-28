@echo off
setlocal
rem ============================================================
rem  Frontend dev server (Vite + HMR) on http://127.0.0.1:5173
rem  It proxies /api to the backend on :8000, so start
rem  dev_backend.bat first.
rem ============================================================
chcp 65001 >nul

set "ROOT=%~dp0"
cd /d "%ROOT%frontend"

if not exist "node_modules" (
  echo Installing frontend dependencies, please wait...
  call npm install
)

call npm run dev
pause
