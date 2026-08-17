@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo 필요한 차량 추적 라이브러리를 설치합니다.
python -m pip install --upgrade -r requirements.txt
echo.
echo 설치가 끝났습니다.
echo 기존 프로그램은 실행.bat, 웹 대시보드 연결은 대시보드연결.bat을 실행하세요.
pause
