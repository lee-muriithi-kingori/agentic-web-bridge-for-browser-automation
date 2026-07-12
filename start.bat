@echo off
REM Start the bridge server from the env/bridge folder.
setlocal
cd /d "%~dp0"
start "WebBridge" /MIN "C:\Users\wahit\AppData\Local\Python\pythoncore-3.14-64\python.exe" "%~dp0server.py" 9876
echo WebBridge v3.1 started from %~dp0
endlocal
