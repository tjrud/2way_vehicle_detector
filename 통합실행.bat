@echo off
REM 로컬 분석 API와 Next.js 대시보드를 독립 프로세스로 함께 시작한다.
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto run
echo Python environment not found. Run install.bat first.
pause
exit /b 1

:run
REM Python 실행기는 PID와 로그를 저장한 뒤 기본 브라우저를 연다.
".venv\Scripts\python.exe" integrated_launcher.py
if errorlevel 1 pause
