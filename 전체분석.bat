@echo off
REM config.json의 전체 MP4를 독립 실행하고 진행 상태 및 CSV를 디스크에 저장한다.
cd /d "%~dp0"
set "YOLO_CONFIG_DIR=%~dp0"
if exist ".venv\Scripts\python.exe" goto run
echo Python environment not found. Run install.bat first.
pause
exit /b 1

:run
REM 진행 상황을 이 창에 표시하고 결과 폴더의 analysis.log에도 같은 내용을 남긴다.
chcp 65001 >nul
set "PYTHONUTF8=1"
".venv\Scripts\python.exe" run_analysis.py
set "EXIT_CODE=%ERRORLEVEL%"
echo.
if "%EXIT_CODE%"=="0" (echo Analysis finished. Check the result folder.) else (echo Analysis failed with exit code %EXIT_CODE%.)
pause
exit /b %EXIT_CODE%
