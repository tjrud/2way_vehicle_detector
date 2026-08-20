@echo off
REM config.json의 전체 MP4를 독립 실행하고 진행 상태 및 CSV를 디스크에 저장한다.
cd /d "%~dp0"
set "YOLO_CONFIG_DIR=%~dp0"
if exist ".venv\Scripts\python.exe" goto run
echo Python environment not found. Run install.bat first.
pause
exit /b 1

:run
REM 장시간 실행 중 콘솔 출력이 쌓이지 않도록 analysis.log로 보낸다.
".venv\Scripts\python.exe" run_analysis.py > "analysis.log" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" echo Analysis failed with exit code %EXIT_CODE%.
exit /b %EXIT_CODE%
