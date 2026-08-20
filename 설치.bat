@echo off
REM Python 분석 환경, 휴대용 Node.js와 대시보드 빌드를 한 번에 준비한다.
cd /d "%~dp0"
set "YOLO_CONFIG_DIR=%~dp0"

REM 이미 만든 가상환경은 재사용하고 패키지만 최신 요구사항에 맞춘다.
if exist ".venv\Scripts\python.exe" goto packages

set "BOOTSTRAP_PY="
where python >nul 2>nul
if not errorlevel 1 set "BOOTSTRAP_PY=python"
if defined BOOTSTRAP_PY goto create_env
if exist "C:\Users\hello\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" set "BOOTSTRAP_PY=C:\Users\hello\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if defined BOOTSTRAP_PY goto create_env
echo Python was not found.
pause
exit /b 1

:create_env
REM 시스템 Python 또는 Codex 번들 Python으로 프로젝트 전용 가상환경을 만든다.
echo Creating the project Python environment...
"%BOOTSTRAP_PY%" -m venv .venv
if errorlevel 1 goto failed

:packages
REM 분석 패키지 후 CUDA용 PyTorch를 명시적으로 설치해 GPU 사용을 보장한다.
echo Installing analysis packages...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto failed
".venv\Scripts\python.exe" -m pip install --upgrade -r requirements.txt
if errorlevel 1 goto failed
echo Installing CUDA PyTorch...
".venv\Scripts\python.exe" -m pip install --upgrade torch==2.12.1 torchvision==0.27.1 --index-url https://download.pytorch.org/whl/cu130
if errorlevel 1 goto failed

REM 시스템 Node 설치 여부와 관계없이 프로젝트 안의 고정 버전을 사용한다.
if exist ".runtime\node\node.exe" goto dashboard_packages
echo Installing portable Node.js...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $ProgressPreference='SilentlyContinue'; New-Item -ItemType Directory -Force '.runtime' | Out-Null; Invoke-WebRequest 'https://nodejs.org/dist/v22.13.1/node-v22.13.1-win-x64.zip' -OutFile '.runtime\node.zip'; Expand-Archive '.runtime\node.zip' '.runtime' -Force; Move-Item '.runtime\node-v22.13.1-win-x64' '.runtime\node'"
if errorlevel 1 goto failed

:dashboard_packages
REM 하위 npm 설치 스크립트도 node.exe를 찾을 수 있도록 PATH 앞에 추가한다.
set "PATH=%~dp0.runtime\node;%PATH%"
echo Installing dashboard packages...
call ".runtime\node\npm.cmd" --cache ".runtime\npm-cache" --prefix dashboard ci
if errorlevel 1 goto failed
echo Building the integrated dashboard...
set "LOCAL_DASHBOARD=1"
set "AUTH_SECRET=local-dashboard-only"
call ".runtime\node\npm.cmd" --cache ".runtime\npm-cache" --prefix dashboard run build
if errorlevel 1 goto failed
echo.
echo Installation completed.
echo Run the integrated launcher batch file to open the dashboard.
pause
exit /b 0

:failed
echo.
echo Installation failed with exit code %ERRORLEVEL%.
pause
exit /b 1
