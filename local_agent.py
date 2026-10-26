"""웹 대시보드와 로컬 YOLO 분석기를 연결하는 경량 HTTP API.

표준 라이브러리 HTTP 서버만 사용하며, 분석 작업은 별도 비데몬 스레드에서
실행한다. 따라서 브라우저를 닫아도 Python 프로세스가 살아 있는 동안 분석이
계속되고 상태 JSON과 영상별 CSV가 갱신된다.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import secrets
import threading
import traceback
import uuid
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from calibrate import find_day_video
from two_way_counter import BatchAnalyzer, load_config


APP_DIR = Path(__file__).resolve().parent
VERSION = "1.0.0"
TRUSTED_ORIGINS = {
    # 토큰 없는 통합 모드는 아래 대시보드 Origin에서 온 요청만 허용한다.
    "http://127.0.0.1:3000",
    "http://localhost:3000",
    "https://intersection-vehicle-counter.kskssk.chatgpt.site",
}


def now_iso() -> str:
    """상태 파일과 API 응답에 사용할 현지 시간대 ISO 문자열을 만든다."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    """모니터가 반쪽짜리 JSON을 읽지 않도록 임시 파일을 원자 교체한다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def resolve_path(value: str, fallback: str) -> Path:
    """빈 값에는 기본 경로를 적용하고 상대 경로는 프로젝트 기준으로 푼다."""
    path = Path(value.strip() or fallback)
    return path if path.is_absolute() else APP_DIR / path


def read_summary(path: Path) -> list[dict[str, Any]]:
    """한국어 집계 CSV를 대시보드가 쓰는 영문 키 배열로 변환한다."""
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", newline="", encoding="utf-8-sig") as file:
        for row in csv.DictReader(file):
            inbound = int(row.get("유입", 0) or 0)
            outbound = int(row.get("유출", 0) or 0)
            rows.append({"time": row.get("시간", ""), "inbound": inbound, "outbound": outbound, "total": inbound + outbound})
    return rows


class JobManager:
    """동시에 하나의 분석 작업만 생성·중지·조회하도록 상태를 관리한다."""

    def __init__(self) -> None:
        """작업 상태와 중지 이벤트를 보호하는 재진입 잠금을 준비한다."""
        self.lock = threading.RLock()
        self.job: dict[str, Any] | None = None
        self.stop_event: threading.Event | None = None

    def snapshot(self) -> dict[str, Any] | None:
        """호출자가 내부 상태를 바꾸지 못하도록 현재 작업의 복사본을 반환한다."""
        with self.lock:
            return dict(self.job) if self.job is not None else None

    def _update(self, **values: Any) -> None:
        """잠금 안에서 현재 작업의 일부 필드만 갱신한다."""
        with self.lock:
            if self.job is not None:
                self.job.update(values)

    def _log(self, message: str) -> None:
        """메모리 증가를 막기 위해 대시보드용 최근 로그 200줄만 유지한다."""
        clean = str(message).strip()
        if not clean:
            return
        with self.lock:
            if self.job is None:
                return
            self.job["logs"] = [*self.job.get("logs", []), clean][-200:]
            self.job["message"] = clean

    def start(self, request: dict[str, Any]) -> dict[str, Any]:
        """새 작업을 만들고 독립 분석 스레드를 시작한다."""
        with self.lock:
            if self.job and self.job.get("status") in {"queued", "running", "stopping"}:
                raise RuntimeError("이미 실행 중인 분석이 있습니다.")
            job_id = uuid.uuid4().hex[:12]
            self.stop_event = threading.Event()
            self.job = {
                "id": job_id, "status": "queued", "progress": 0.0,
                "message": "분석을 준비하고 있습니다.", "logs": [], "summary": [],
                "summaryPath": None, "eventsPath": None, "createdAt": now_iso(),
            }
        # 비데몬 스레드이므로 HTTP 요청이 끝나거나 브라우저가 닫혀도 분석은 유지된다.
        threading.Thread(target=self._run, args=(job_id, request), daemon=False).start()
        return self.snapshot() or {}

    def stop(self, job_id: str) -> dict[str, Any]:
        """해당 작업에 협력적 중지 이벤트를 전달한다."""
        with self.lock:
            if not self.job or self.job.get("id") != job_id:
                raise KeyError("분석 작업을 찾을 수 없습니다.")
            if self.job.get("status") not in {"queued", "running", "stopping"}:
                return dict(self.job)
            self.job.update(status="stopping", message="분석 중지를 요청했습니다.")
            if self.stop_event is not None:
                self.stop_event.set()
            return dict(self.job)

    def _run(self, job_id: str, request: dict[str, Any]) -> None:
        """요청값을 설정에 반영해 분석하고 상태·오류 파일을 끝까지 기록한다."""
        output_dir = resolve_path(str(request.get("outputDir", "results")), "results")
        status_path = output_dir / "analysis_status.json"
        error_path = output_dir / "analysis_error.log"
        try:
            config = load_config(APP_DIR / "config.json")
            source_dir = resolve_path(str(request.get("sourceDir", "")), str(config.get("source_dir", "")))
            if not source_dir.is_dir():
                raise FileNotFoundError(f"영상 폴더를 찾을 수 없습니다: {source_dir}")
            output_dir.mkdir(parents=True, exist_ok=True)
            config.update({
                "source_dir": str(source_dir), "output_dir": str(output_dir),
                "model": str(request.get("model") or config.get("model", "yolo26l.pt")),
                "device": str(request.get("device") or config.get("device", "0")),
                "confidence": float(request.get("confidence", config.get("confidence", 0.22))),
                "save_event_images": bool(request.get("saveEventImages", True)),
                "save_review_video": bool(request.get("saveReviewVideo", False)),
            })
            test_mode = bool(request.get("testMode", False))
            videos = [find_day_video(source_dir)] if test_mode else None
            limit_seconds = 300 if test_mode else None
            self._update(status="running", message="분석을 시작했습니다.")
            atomic_json(status_path, {"updated_at": now_iso(), "status": "running", "progress": 0.0, "job_id": job_id, "test": test_mode})

            def progress(value: float) -> None:
                # 메모리 상태와 디스크 상태를 함께 갱신해 UI와 외부 모니터를 일치시킨다.
                percent = round(max(0.0, min(1.0, value)) * 100, 3)
                self._update(progress=percent)
                atomic_json(status_path, {"updated_at": now_iso(), "status": "running", "progress": value, "job_id": job_id, "test": test_mode})

            analyzer = BatchAnalyzer(config, APP_DIR, log=self._log, progress=progress, stop_event=self.stop_event)
            summary_path, events_path = analyzer.run(limit_seconds=limit_seconds, video_paths=videos)
            stopped = bool(self.stop_event and self.stop_event.is_set())
            status = "stopped" if stopped else "completed"
            progress_value = (self.snapshot() or {}).get("progress", 0.0) if stopped else 100.0
            self._update(
                status=status, progress=progress_value,
                message="분석이 중지되었습니다." if stopped else "분석이 완료되었습니다.",
                summary=read_summary(summary_path), summaryPath=str(summary_path),
                eventsPath=str(events_path) if events_path is not None else None, completedAt=now_iso(),
                reportPath=str(analyzer.report_path) if analyzer.report_path else None,
                outputs=analyzer.outputs,
            )
            atomic_json(status_path, {
                "updated_at": now_iso(), "status": status, "progress": progress_value / 100,
                "job_id": job_id, "test": test_mode, "summary_csv": str(summary_path),
                "events_csv": str(events_path) if events_path is not None else None,
            })
        except BaseException:
            error_path.parent.mkdir(parents=True, exist_ok=True)
            error_path.write_text(traceback.format_exc(), encoding="utf-8")
            self._update(status="failed", message="분석 중 오류가 발생했습니다.", error=str(error_path), completedAt=now_iso())
            atomic_json(status_path, {"updated_at": now_iso(), "status": "failed", "progress": 0.0, "job_id": job_id, "error_log": str(error_path)})


class AgentHandler(BaseHTTPRequestHandler):
    """인증, CORS, JSON 직렬화를 공통 처리하는 REST 요청 핸들러."""

    server_version = "ICLocalAgent/1.0"

    @property
    def agent(self) -> "AgentServer":
        """타입 검사기가 사용자 정의 서버 속성을 알 수 있도록 좁힌다."""
        return self.server  # type: ignore[return-value]

    def log_message(self, format: str, *args: Any) -> None:
        """기본 HTTP 접근 로그를 분석기 로그 형식으로 출력한다."""
        print(f"[{self.log_date_time_string()}] {format % args}")

    def _cors(self) -> None:
        """허용된 대시보드가 로컬 API 응답을 읽는 데 필요한 헤더를 붙인다."""
        self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin", "*"))
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-IC-Token")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        """한국어를 보존한 UTF-8 JSON 응답을 전송한다."""
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        """신뢰 Origin의 무토큰 요청 또는 올바른 명시적 토큰을 허용한다."""
        origin = self.headers.get("Origin", "")
        if not self.agent.token and origin in TRUSTED_ORIGINS:
            return True
        if self.agent.token and secrets.compare_digest(self.headers.get("X-IC-Token", ""), self.agent.token):
            return True
        self._json(401, {"detail": "연결 토큰이 올바르지 않습니다."})
        return False

    def _body(self) -> dict[str, Any]:
        """크기를 제한한 요청 본문을 JSON 객체로 읽는다."""
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length > 1_000_000:
            raise ValueError("요청이 너무 큽니다.")
        value = json.loads(self.rfile.read(length).decode("utf-8") if length else "{}")
        if not isinstance(value, dict):
            raise ValueError("JSON 객체가 필요합니다.")
        return value

    def do_OPTIONS(self) -> None:
        """브라우저 CORS 사전 요청에 응답한다."""
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:
        """상태 확인과 현재·개별 작업 조회 API를 처리한다."""
        if not self._authorized():
            return
        path = urlparse(self.path).path
        if path == "/health":
            self._json(200, {"app": "IC Local AI", "version": VERSION, "device": "Windows GPU analyzer"})
        elif path == "/jobs/current":
            self._json(200, {"job": self.agent.manager.snapshot()})
        elif path.startswith("/jobs/"):
            job_id = path.removeprefix("/jobs/")
            job = self.agent.manager.snapshot()
            self._json(200, {"job": job}) if job and job.get("id") == job_id else self._json(404, {"detail": "분석 작업을 찾을 수 없습니다."})
        else:
            self._json(404, {"detail": "지원하지 않는 경로입니다."})

    def do_POST(self) -> None:
        """분석 시작·중지 및 결과 폴더 열기 요청을 처리한다."""
        if not self._authorized():
            return
        path = urlparse(self.path).path
        try:
            body = self._body()
            if path == "/jobs":
                self._json(202, {"job": self.agent.manager.start(body)})
            elif path.startswith("/jobs/") and path.endswith("/stop"):
                job_id = path[len("/jobs/"):-len("/stop")]
                self._json(200, {"job": self.agent.manager.stop(job_id)})
            elif path == "/open-results":
                output_dir = resolve_path(str(body.get("outputDir", "results")), "results")
                output_dir.mkdir(parents=True, exist_ok=True)
                os.startfile(output_dir)  # type: ignore[attr-defined]
                self._json(200, {"ok": True, "outputDir": str(output_dir)})
            else:
                self._json(404, {"detail": "지원하지 않는 경로입니다."})
        except KeyError as error:
            self._json(404, {"detail": str(error)})
        except (ValueError, FileNotFoundError, RuntimeError) as error:
            self._json(400, {"detail": str(error)})
        except BaseException as error:
            self._json(500, {"detail": f"로컬 실행기 오류: {error}"})


class AgentServer(ThreadingHTTPServer):
    """공유 JobManager와 선택적 토큰을 보유하는 다중 스레드 서버."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], token: str) -> None:
        """요청 처리기와 전역 작업 관리자를 결합한다."""
        super().__init__(address, AgentHandler)
        self.token = token
        self.manager = JobManager()


def main() -> None:
    """로컬 주소에만 API 서버를 열고 종료 시 소켓을 정리한다."""
    parser = argparse.ArgumentParser(description="교차로 대시보드용 로컬 AI 실행기")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--token", default=os.environ.get("IC_LOCAL_AGENT_TOKEN", ""))
    args = parser.parse_args()
    token = args.token.strip()
    server = AgentServer((args.host, args.port), token)
    print("=" * 64)
    print("IC Local AI dashboard agent")
    print(f"Address: http://{args.host}:{args.port}")
    print(f"Auth:    {'token' if token else 'trusted local dashboard'}")
    print("Keep this window open while using the dashboard.")
    print("=" * 64)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nLocal agent stopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
