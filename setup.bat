@echo off
setlocal EnableExtensions
chcp 65001 >nul

set "ROOT=%~dp0"
cd /d "%ROOT%"

set "SKIP_OCR="
if /I "%~1"=="--no-ocr" set "SKIP_OCR=1"

if not exist "%ROOT%backend\requirements.txt" goto missing_requirement
if not exist "%ROOT%frontend\dist\index.html" goto missing_frontend

set "VENV_PY=%ROOT%.venv\Scripts\python.exe"
if not exist "%ROOT%.venv" goto create_environment
if not exist "%VENV_PY%" goto recreate_environment

"%VENV_PY%" -c "import sys" >nul 2>nul
if not errorlevel 1 goto install_dependencies

echo Existing .venv is invalid or was created on another computer.
echo Recreating the local Python environment...

:recreate_environment
rmdir /s /q "%ROOT%.venv"
if exist "%ROOT%.venv" goto failed_remove_environment

set "PY_CMD="
where py >nul 2>nul
if not errorlevel 1 set "PY_CMD=py -3"
if defined PY_CMD goto create_environment

where python >nul 2>nul
if not errorlevel 1 set "PY_CMD=python"
if not defined PY_CMD goto missing_python

:create_environment
echo Creating local Python environment...
%PY_CMD% -m venv .venv
if errorlevel 1 goto failed

:install_dependencies
echo Updating pip...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto failed

echo Installing backend dependencies...
".venv\Scripts\python.exe" -m pip install -r backend\requirements.txt
if errorlevel 1 goto failed

if defined SKIP_OCR goto complete

echo.
choice /C YN /N /M "Install optional RapidOCR for JPG/PNG tickets? [Y/N] "
if errorlevel 2 goto complete

echo Installing RapidOCR...
".venv\Scripts\python.exe" -m pip install rapidocr-onnxruntime
if errorlevel 1 echo [WARN] RapidOCR installation failed. PDF recognition still works.

:complete
echo.
echo Setup complete. Double-click start.bat to launch the application.
pause
exit /b 0

:missing_requirement
echo [ERROR] backend\requirements.txt was not found.
pause
exit /b 1

:missing_frontend
echo [ERROR] frontend\dist\index.html was not found.
echo This trial package requires a pre-built frontend.
pause
exit /b 1

:missing_python
echo [ERROR] Python 3.11 or newer was not found.
echo Install Python, then run setup.bat again.
pause
exit /b 1

:failed_remove_environment
echo [ERROR] Could not remove the invalid .venv directory.
echo Close any running Python process and run setup.bat again.
pause
exit /b 1

:failed
echo.
echo [ERROR] Setup failed. Check the messages above.
pause
exit /b 1
