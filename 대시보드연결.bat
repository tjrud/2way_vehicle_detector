@echo off
REM 구형 단독 모드: 현재 창에서 로컬 분석 API만 실행한다.
REM 일반 사용자는 분석 API와 웹 화면을 함께 여는 통합실행.bat을 사용한다.
cd /d "%~dp0"
set "YOLO_CONFIG_DIR=%~dp0"
if exist ".venv\Scripts\python.exe" goto run
echo Python environment not found. Run install.bat first.
pause
exit /b 1

:run
REM 종료 원인을 확인할 수 있도록 이 모드는 콘솔 창과 종료 코드를 유지한다.
title IC Local AI Dashboard Agent
".venv\Scripts\python.exe" local_agent.py
set "EXIT_CODE=%ERRORLEVEL%"
if not "%EXIT_CODE%"=="0" echo Local agent failed with exit code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%
