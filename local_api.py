from __future__ import annotations

import csv
import os
import secrets
import threading
import uuid
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from calibrate import find_day_video
from two_way_counter import BatchAnalyzer, load_config


APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"
TOKEN_PATH = APP_DIR / ".local_api_token"
VERSION = "1.0.0"


def load_or_create_token() -> str:
    configured = os.getenv("IC_LOCAL_TOKEN", "").strip()
    if configured:
        return configured
    if TOKEN_PATH.exists():
        saved = TOKEN_PATH.read_text(encoding="utf-8").strip()
        if saved:
            return saved
    token = secrets.token_urlsafe(18)
    TOKEN_PATH.write_text(token, encoding="utf-8")
    return token


LOCAL_TOKEN = load_or_create_token()
allowed_origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://intersection-vehicle-counter-dashbo.vercel.app",
]
allowed_origins.extend(origin.strip() for origin in os.getenv("IC_DASHBOARD_ORIGINS", "").split(",") if origin.strip())

app = FastAPI(title="IC Dashboard Local AI", version=VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-IC-Token", "Access-Control-Request-Private-Network"],
)


@app.middleware("http")
async def private_network_access(request: Request, call_next):
    response = await call_next(request)
    if request.headers.get("access-control-request-private-network") == "true":
        response.headers["Access-Control-Allow-Private-Network"] = "true"
    return response


def require_token(x_ic_token: str = Header(default="")) -> None:
    if not secrets.compare_digest(x_ic_token, LOCAL_TOKEN):
        raise HTTPException(status_code=401, detail="연결 토큰이 올바르지 않습니다.")


job_lock = threading.Lock()
stop_event: threading.Event | None = None
current_job: dict[str, Any] | None = None


def now_text() -> str:
    return datetime.now().strftime("%H:%M:%S")


def job_snapshot() -> dict[str, Any] | None:
    with job_lock:
        return deepcopy(current_job)


def update_job(**values: Any) -> None:
    global current_job
    with job_lock:
        if current_job is not None:
            current_job.update(values)


def add_log(message: str) -> None:
    global current_job
    clean = str(message).strip()
    if not clean:
        return
    with job_lock:
        if current_job is None:
            return
        logs = current_job.setdefault("logs", [])
        logs.append(f"[{now_text()}] {clean}")
        current_job["message"] = clean
        if len(logs) > 200:
            del logs[:-200]


def read_summary(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            inbound = int(row.get("유입", 0) or 0)
            outbound = int(row.get("유출", 0) or 0)
            total = int(row.get("합계", inbound + outbound) or 0)
            if total:
                rows.append({"time": row.get("시간", ""), "inbound": inbound, "outbound": outbound, "total": total})
    return rows


def run_job(job_id: str, payload: dict[str, Any], worker_stop: threading.Event) -> None:
    try:
        config = load_config(CONFIG_PATH)
        source_dir = Path(str(payload.get("sourceDir", "")).strip()).expanduser()
        if not source_dir.is_dir():
            raise FileNotFoundError(f"영상 폴더를 찾을 수 없습니다: {source_dir}")
        output_value = str(payload.get("outputDir", "results")).strip() or "results"
        config.update({
            "source_dir": str(source_dir),
            "output_dir": output_value,
            "model": str(payload.get("model", "yolo26l.pt")),
            "device": str(payload.get("device", "0")),
            "confidence": float(payload.get("confidence", 0.22)),
            "save_event_images": bool(payload.get("saveEventImages", True)),
            "save_review_video": bool(payload.get("saveReviewVideo", False)),
        })
        test_mode = bool(payload.get("testMode", False))
        video_paths = None
        limit_seconds = None
        if test_mode:
            video_paths = [find_day_video(source_dir)]
            config["output_dir"] = "test_results" if output_value == "results" else output_value
            config["save_review_video"] = True
            limit_seconds = 300

        update_job(status="running", message="AI 모델과 영상을 준비하고 있습니다.")
        analyzer = BatchAnalyzer(
            config,
            APP_DIR,
            log=add_log,
            progress=lambda value: update_job(progress=round(max(0.0, min(1.0, value)) * 100, 1)),
            stop_event=worker_stop,
        )
        summary_path, events_path = analyzer.run(limit_seconds=limit_seconds, video_paths=video_paths)
        stopped = worker_stop.is_set()
        update_job(
            status="stopped" if stopped else "completed",
            progress=current_job.get("progress", 0) if stopped and current_job else 100,
            message="사용자 요청으로 분석을 중지했습니다." if stopped else "분석과 15분 집계가 완료되었습니다.",
            summary=read_summary(summary_path),
            summaryPath=str(summary_path),
            eventsPath=str(events_path),
        )
        add_log("분석 중지" if stopped else "모든 결과 파일 저장 완료")
    except Exception as error:
        update_job(status="failed", message=str(error))
        add_log(f"오류: {error}")


@app.get("/health")
def health(_: None = Depends(require_token)) -> dict[str, Any]:
    return {"status": "ok", "app": "IC Local AI", "version": VERSION, "device": "GPU/CPU 자동 감지"}


@app.get("/jobs/current")
def get_current_job(_: None = Depends(require_token)) -> dict[str, Any]:
    return {"job": job_snapshot()}


@app.get("/jobs/{job_id}")
def get_job(job_id: str, _: None = Depends(require_token)) -> dict[str, Any]:
    job = job_snapshot()
    if not job or job.get("id") != job_id:
        raise HTTPException(status_code=404, detail="분석 작업을 찾을 수 없습니다.")
    return {"job": job}


@app.post("/jobs")
def create_job(payload: dict[str, Any], _: None = Depends(require_token)) -> dict[str, Any]:
    global current_job, stop_event
    source_dir = str(payload.get("sourceDir", "")).strip()
    if not source_dir:
        raise HTTPException(status_code=400, detail="전처리 영상 폴더를 입력해 주세요.")
    confidence = float(payload.get("confidence", 0.22))
    if not 0.05 <= confidence <= 0.95:
        raise HTTPException(status_code=400, detail="검출 신뢰도는 0.05에서 0.95 사이여야 합니다.")
    with job_lock:
        if current_job and current_job.get("status") in {"queued", "running", "stopping"}:
            raise HTTPException(status_code=409, detail="이미 분석이 진행 중입니다.")
        job_id = uuid.uuid4().hex
        current_job = {"id": job_id, "status": "queued", "progress": 0, "message": "분석 작업을 준비하고 있습니다.", "logs": [], "summary": []}
        stop_event = threading.Event()
    worker = threading.Thread(target=run_job, args=(job_id, payload, stop_event), daemon=True, name=f"ic-ai-{job_id[:8]}")
    worker.start()
    return {"job": job_snapshot()}


@app.post("/jobs/{job_id}/stop")
def stop_job(job_id: str, _: None = Depends(require_token)) -> dict[str, Any]:
    job = job_snapshot()
    if not job or job.get("id") != job_id:
        raise HTTPException(status_code=404, detail="분석 작업을 찾을 수 없습니다.")
    if job.get("status") not in {"queued", "running", "stopping"}:
        return {"job": job}
    if stop_event:
        stop_event.set()
    update_job(status="stopping", message="현재 프레임 처리를 마친 뒤 중지합니다.")
    return {"job": job_snapshot()}


@app.post("/open-results")
def open_results(payload: dict[str, Any], _: None = Depends(require_token)) -> dict[str, bool]:
    output_dir = Path(str(payload.get("outputDir", "results")).strip() or "results").expanduser()
    if not output_dir.is_absolute():
        output_dir = APP_DIR / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        os.startfile(output_dir)  # type: ignore[attr-defined]
    else:
        raise HTTPException(status_code=501, detail="현재 버전은 Windows 결과 폴더 열기를 지원합니다.")
    return {"ok": True}


if __name__ == "__main__":
    print("\n" + "=" * 62)
    print(" IC Dashboard 로컬 AI 실행기")
    print(" 주소: http://127.0.0.1:8765")
    print(f" 연결 토큰: {LOCAL_TOKEN}")
    print(" 이 창을 닫으면 대시보드의 AI 분석도 중지됩니다.")
    print("=" * 62 + "\n")
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning")
