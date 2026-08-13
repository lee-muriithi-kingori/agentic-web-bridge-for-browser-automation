@echo off
REM Start the WebBridge server.
REM Usage: start.bat [PORT]
setlocal
cd /d "%~dp0"
set PORT=%1
if "%PORT%"=="" set PORT=9876
start "WebBridge" /MIN python server.py --port %PORT%
echo WebBridge v4.0.0 started on port %PORT% from %~dp0
endlocal
