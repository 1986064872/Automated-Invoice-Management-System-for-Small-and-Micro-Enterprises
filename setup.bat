@echo off
setlocal
chcp 65001 >nul

set "ROOT=%~dp0"
cd /d "%ROOT%"

if not exist "%ROOT%backend\requirements.txt" (
  echo [ERROR] backend\requirements.txt was not found.
  pause
  exit /b 1
)

if not exist "%ROOT%frontend\dist\index.html" (
  echo [ERROR] frontend\dist\index.html was not found.
  echo This trial package requires a pre-built frontend.
  pause
  exit /b 1
)

if not exist "%ROOT%.venv\Scripts\python.exe" (
  set "PY_CMD="
  where py >nul 2>nul && set "PY_CMD=py -3"
  if not defined PY_CMD (
    where python >nul 2>nul && set "PY_CMD=python"
  )
  if not defined PY_CMD (
    echo [ERROR] Python 3.11 or newer was not found.
    echo Install Python, then run setup.bat again.
    pause
    exit /b 1
  )

  echo Creating local Python environment...
  %PY_CMD% -m venv .venv
  if errorlevel 1 goto failed
)

echo Updating pip...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto failed

echo Installing backend dependencies...
".venv\Scripts\python.exe" -m pip install -r backend\requirements.txt
if errorlevel 1 goto failed

echo.
choice /C YN /N /M "Install optional RapidOCR for JPG/PNG tickets? [Y/N] "
if errorlevel 2 goto complete

echo Installing RapidOCR...
".venv\Scripts\python.exe" -m pip install rapidocr-onnxruntime
if errorlevel 1 (
  echo [WARN] RapidOCR installation failed. PDF recognition still works.
)

:complete
echo.
echo Setup complete. Double-click start.bat to launch the application.
pause
exit /b 0

:failed
echo.
echo [ERROR] Setup failed. Check the messages above.
pause
exit /b 1
