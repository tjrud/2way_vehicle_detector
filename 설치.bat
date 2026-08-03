@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo 필요한 차량 추적 라이브러리를 설치합니다.
python -m pip install --upgrade ultralytics opencv-python numpy pillow
echo.
echo 설치가 끝났습니다. 실행.bat을 더블클릭하세요.
pause
