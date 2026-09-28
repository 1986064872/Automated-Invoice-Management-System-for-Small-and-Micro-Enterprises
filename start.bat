@echo off
setlocal

rem ============================================================
rem  企业智能票据记账助手 —— 一键启动
rem
rem  重要：这个黑窗口就是服务本体。窗口关掉 = 服务停止。
rem       启动完成后会自动打开 http://127.0.0.1:8000
rem
rem  【本文件的保存要求，别改错了】
rem  1. 编码必须存成 ANSI/GBK，不要存成 UTF-8。
rem     UTF-8 + chcp 65001 的组合下，cmd 会按缓冲区边界错位解析中文，
rem     把 rem 注释行当成命令去执行，双击后满屏 "is not recognized..."。
rem     GBK 与系统默认代码页 936 一致，命令行里不需要任何 chcp。
rem  2. 换行必须用 CRLF。LF 换行会让 cmd 解析多行 if(...) 块出错。
rem ============================================================

title 票据记账助手 - 运行中（关闭本窗口即停止服务）

set "ROOT=%~dp0"
set "PORT=8000"

if not exist "%ROOT%backend\app\main.py" (
  echo [错误] 没找到 backend\app\main.py
  echo        请把 start.bat 放在项目根目录，和 backend、frontend 同级。
  echo.
  pause
  exit /b 1
)

rem ---- 找 Python：项目内 .venv -> backend\.venv -> 用户目录下的默认环境 ----
rem 默认环境在用户目录下 %USERPROFILE%\.workbuddy\...
rem 不是项目上一级！以前写成 "%ROOT%..\.workbuddy\..."，
rem 算出来是 Desktop\.workbuddy\...（不存在），双击必然报找不到 Python。
set "PY=%USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe"
if exist "%ROOT%.venv\Scripts\python.exe" set "PY=%ROOT%.venv\Scripts\python.exe"
if exist "%ROOT%backend\.venv\Scripts\python.exe" set "PY=%ROOT%backend\.venv\Scripts\python.exe"

if not exist "%PY%" (
  echo [错误] 找不到可用的 Python 环境，以下路径都不存在：
  echo     %USERPROFILE%\.workbuddy\binaries\python\envs\default\Scripts\python.exe
  echo     %ROOT%.venv\Scripts\python.exe
  echo     %ROOT%backend\.venv\Scripts\python.exe
  echo.
  echo 可以自己建一个：
  echo     python -m venv .venv
  echo     .venv\Scripts\pip install -r backend\requirements.txt
  echo.
  pause
  exit /b 1
)

echo 使用 Python 环境：
echo     %PY%
echo.

rem ---- 端口已被占用？那服务多半已经在跑了，直接开浏览器 ----
"%PY%" -c "import socket,sys; s=socket.socket(); s.settimeout(0.6); sys.exit(0 if s.connect_ex(('127.0.0.1',%PORT%))==0 else 1)" >nul 2>&1
if errorlevel 1 goto launch

echo [提示] 端口 %PORT% 已经有程序在监听，服务可能已经在运行了。
echo        现在直接打开浏览器；想重启的话，先关掉那个正在跑的窗口。
echo.
start "" "http://127.0.0.1:%PORT%"
pause
exit /b 0

:launch
if not exist "%ROOT%backend\.env" (
  echo [提示] 没有 backend\.env，将用默认配置启动。
  echo        OCR 走本地文本层解析，不联网、不产生费用。
  echo.
)

cd /d "%ROOT%backend"

echo ============================================================
echo   网页界面：http://127.0.0.1:%PORT%
echo   接口文档：http://127.0.0.1:%PORT%/docs
echo   停止服务：在本窗口按 Ctrl+C，或者直接关掉本窗口
echo ============================================================
echo.
echo 正在启动，第一次要等几秒，请稍候……
echo.

rem 后台盯着健康检查接口，真通了再开浏览器，最多等 60 秒。
rem 以前写死 Start-Sleep 3 秒，服务还没起来浏览器就弹出来了，
rem 结果显示「无法访问此网站」，看起来就像启动失败。
start "" powershell -NoProfile -WindowStyle Hidden -Command "for($i=0;$i -lt 120;$i++){try{Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:%PORT%/api/v1/health' -TimeoutSec 1 | Out-Null; Start-Process 'http://127.0.0.1:%PORT%'; break}catch{Start-Sleep -Milliseconds 500}}"

"%PY%" -m uvicorn app.main:app --host 127.0.0.1 --port %PORT%

echo.
echo [服务已退出]
echo 如果上面有报错，把整段内容截图发我。
echo.
pause
