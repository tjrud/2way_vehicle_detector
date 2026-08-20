@echo off
REM 대표 주간 영상 5분만 독립 프로세스로 분석하고 test_results에 기록한다.
cd /d "%~dp0"
set "YOLO_CONFIG_DIR=%~dp0"
if exist ".venv\Scripts\python.exe" goto run
echo Python environment not found. Run install.bat first.
pause
exit /b 1

:run
REM 표준 출력과 오류를 하나의 로그로 남겨 창을 닫은 뒤에도 원인을 확인한다.
if not exist "test_results" mkdir "test_results"
".venv\Scripts\python.exe" run_analysis.py --test > "test_results\analysis.log" 2>&1
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" echo Analysis failed with exit code %EXIT_CODE%.
exit /b %EXIT_CODE%
