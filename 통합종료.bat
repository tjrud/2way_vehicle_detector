@echo off
REM integrated_processes.json에 기록된 API·대시보드 프로세스만 종료한다.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" exit /b 0
".venv\Scripts\python.exe" integrated_launcher.py --stop
pause
