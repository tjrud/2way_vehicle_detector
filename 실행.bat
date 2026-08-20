@echo off
REM ROI 설정과 시험·전체 분석을 제공하는 기존 Tkinter GUI를 실행한다.
cd /d "%~dp0"
set "YOLO_CONFIG_DIR=%~dp0"
if exist ".venv\Scripts\python.exe" goto run
echo Python environment not found. Run install.bat first.
pause
exit /b 1

:run
".venv\Scripts\python.exe" app.py
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" echo Program failed with exit code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%
