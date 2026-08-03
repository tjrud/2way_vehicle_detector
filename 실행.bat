@echo off
chcp 65001 > nul
cd /d "%~dp0"
python app.py
if errorlevel 1 (
  echo.
  echo 실행 중 오류가 발생했습니다. 먼저 설치.bat을 실행해 주세요.
  pause
)
