@echo off
chcp 65001 >nul
cd /d "%~dp0"
title IC Dashboard 로컬 AI 실행기
python local_api.py
if errorlevel 1 (
  echo.
  echo 실행에 실패했습니다. 먼저 설치.bat을 실행해 주세요.
  pause
)
