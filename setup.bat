@echo off
setlocal EnableExtensions
chcp 65001 >nul

set "ROOT=%~dp0"
set "SETUP_VERSION=2026.09.30-4"
cd /d "%ROOT%"

set "SKIP_OCR="
if /I "%~1"=="--no-ocr" set "SKIP_OCR=1"

echo Invoice Assistant setup %SETUP_VERSION%
echo.

if not exist "%ROOT%backend\requirements.txt" goto missing_requirement
if not exist "%ROOT%frontend\dist\index.html" goto missing_frontend

set "VENV_PY=%ROOT%.venv\Scripts\python.exe"
if not exist "%ROOT%.venv" goto find_python
if not exist "%VENV_PY%" goto recreate_environment

"%VENV_PY%" -c "import sys" >nul 2>nul
if not errorlevel 1 goto install_dependencies

echo Existing .venv is invalid or was created on another computer.
echo Recreating the local Python environment...

:recreate_environment
rmdir /s /q "%ROOT%.venv"
if exist "%ROOT%.venv" goto failed_remove_environment

:find_python
where py >nul 2>nul
if not errorlevel 1 goto check_py313

where python >nul 2>nul
if not errorlevel 1 goto check_python

goto missing_python

:check_py313
py -3.13 -c "import sys,venv; assert sys.version_info >= (3,11)" >nul 2>nul
if not errorlevel 1 goto create_with_py313

:check_py312
py -3.12 -c "import sys,venv; assert sys.version_info >= (3,11)" >nul 2>nul
if not errorlevel 1 goto create_with_py312

:check_py311
py -3.11 -c "import sys,venv; assert sys.version_info >= (3,11)" >nul 2>nul
if not errorlevel 1 goto create_with_py311

goto check_python

:check_python
python -c "import sys,venv; assert sys.version_info >= (3,11)" >nul 2>nul
if not errorlevel 1 goto create_with_python

echo [ERROR] A usable Python 3.11+ with the venv module was not found.
echo Detected python command:
python --version
where python
goto missing_python

:create_with_py313
echo Creating local Python environment with Python 3.13...
py -3.13 -m venv .venv
if errorlevel 1 goto failed_create_venv
goto install_dependencies

:create_with_py312
echo Creating local Python environment with Python 3.12...
py -3.12 -m venv .venv
if errorlevel 1 goto failed_create_venv
goto install_dependencies

:create_with_py311
echo Creating local Python environment with Python 3.11...
py -3.11 -m venv .venv
if errorlevel 1 goto failed_create_venv
goto install_dependencies

:create_with_python
echo Creating local Python environment with python...
python -m venv .venv
if errorlevel 1 goto failed_create_venv

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
echo [ERROR] Install 64-bit Python 3.11 or newer from python.org.
echo During installation, enable "Add python.exe to PATH".
echo Do not use the Microsoft Store Python alias for this trial package.
pause
exit /b 1

:failed_create_venv
echo.
echo [ERROR] Python exists, but creating .venv failed with exit code %ERRORLEVEL%.
echo Common causes:
echo   1. Microsoft Store Python is incomplete or points to an App Execution Alias.
echo   2. Python is older than 3.11 or the venv module is missing.
echo   3. The package is in a protected or non-writable directory.
echo.
echo Recommended fix:
echo   Install 64-bit Python 3.11+ from python.org, select "Add python.exe to PATH",
echo   restart Explorer or the computer, then extract this package to a simple path such as:
echo   C:\InvoiceAssistantTrial
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
