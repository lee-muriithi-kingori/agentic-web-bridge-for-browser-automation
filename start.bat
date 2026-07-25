@echo off
REM Start the WebBridge server.
setlocal
cd /d "%~dp0"
start "WebBridge" /MIN python server.py 9876
echo WebBridge v3.2.0 started from %~dp0
endlocal
