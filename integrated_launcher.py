"""로컬 분석 API와 Next.js 대시보드를 한 번에 시작·종료한다.

두 서비스는 현재 터미널과 분리된 Windows 프로세스로 실행한다. PID는
integrated_processes.json에 저장해 통합종료.bat에서 정확히 종료한다.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path


APP_DIR = Path(__file__).resolve().parent
STATE_PATH = APP_DIR / "integrated_processes.json"
RUNTIME_NODE = APP_DIR / ".runtime" / "node" / "node.exe"
DASHBOARD_DIR = APP_DIR / "dashboard"


def port_open(port: int) -> bool:
    """로컬 호스트의 지정 포트가 이미 연결을 받고 있는지 확인한다."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def wait_port(port: int, timeout: float = 40.0) -> None:
    """서비스가 준비될 때까지 짧게 폴링하고 제한 시간 후 실패시킨다."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if port_open(port):
            return
        time.sleep(0.25)
    raise RuntimeError(f"로컬 서비스 포트 {port}가 시작되지 않았습니다.")


def detached_flags() -> int:
    """콘솔 창 없이 부모 프로세스와 독립 실행하기 위한 Windows 플래그."""
    return (
        getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    )


def start_process(command: list[str], log_path: Path, env: dict[str, str]) -> subprocess.Popen[bytes]:
    """출력을 로그 파일로 보내면서 숨겨진 독립 프로세스를 시작한다."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("ab")
    try:
        return subprocess.Popen(
            command,
            cwd=APP_DIR,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=env,
            creationflags=detached_flags(),
            close_fds=True,
        )
    finally:
        log.close()


def start() -> None:
    """설치 상태와 포트를 확인한 뒤 API·대시보드를 실행하고 브라우저를 연다."""
    node = RUNTIME_NODE if RUNTIME_NODE.exists() else Path(shutil.which("node") or "")
    next_cli = DASHBOARD_DIR / "node_modules" / "next" / "dist" / "bin" / "next"
    if not node.is_file() or not next_cli.is_file() or not (DASHBOARD_DIR / ".next").is_dir():
        raise RuntimeError("통합 설치가 완료되지 않았습니다. 설치.bat을 먼저 실행하세요.")
    if port_open(8765) or port_open(3000):
        raise RuntimeError("기존 대시보드 또는 분석 실행기가 켜져 있습니다. 통합종료.bat을 먼저 실행하세요.")

    # 두 자식 프로세스가 같은 로컬 통합 모드 설정을 공유한다.
    env = os.environ.copy()
    env.update({
        "YOLO_CONFIG_DIR": str(APP_DIR),
        "LOCAL_DASHBOARD": "1",
        "AUTH_SECRET": "local-dashboard-only",
        "NODE_ENV": "production",
    })
    agent = start_process(
        [sys.executable, "-u", str(APP_DIR / "local_agent.py")],
        APP_DIR / "logs" / "local_agent.log",
        env,
    )
    dashboard = start_process(
        [str(node), str(next_cli), "start", str(DASHBOARD_DIR), "-H", "127.0.0.1", "-p", "3000"],
        APP_DIR / "logs" / "dashboard.log",
        env,
    )
    STATE_PATH.write_text(json.dumps({
        "agent_pid": agent.pid,
        "dashboard_pid": dashboard.pid,
        "started_at": time.time(),
    }, indent=2), encoding="utf-8")
    try:
        wait_port(8765)
        wait_port(3000)
    except BaseException:
        stop()
        raise
    webbrowser.open("http://127.0.0.1:3000")
    print("통합 대시보드를 시작했습니다: http://127.0.0.1:3000")


def stop_pid(pid: int) -> None:
    """지정 PID와 그 자식 프로세스 트리를 강제 종료한다."""
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
    )


def stop() -> None:
    """저장된 PID만 종료하고 다음 실행을 위해 상태 파일을 제거한다."""
    if not STATE_PATH.exists():
        print("저장된 통합 프로세스가 없습니다.")
        return
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        for key in ("agent_pid", "dashboard_pid"):
            pid = state.get(key)
            if isinstance(pid, int) and pid > 0:
                stop_pid(pid)
    finally:
        STATE_PATH.unlink(missing_ok=True)
    print("통합 대시보드를 종료했습니다.")


def main() -> None:
    """기본은 시작, --stop은 통합 서비스 종료를 수행한다."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--stop", action="store_true")
    args = parser.parse_args()
    stop() if args.stop else start()


if __name__ == "__main__":
    main()
