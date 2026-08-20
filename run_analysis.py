"""배치 파일에서 호출하는 독립 영상 분석 진입점.

분석 프로세스가 대화나 GUI와 분리되어 실행되도록 하고, 외부 모니터가
읽을 수 있는 analysis_status.json 및 오류 로그를 결과 폴더에 남긴다.
"""

from __future__ import annotations

import argparse
import json
import threading
import traceback
from datetime import datetime
from pathlib import Path

from calibrate import find_day_video
from two_way_counter import BatchAnalyzer, load_config


def write_status(path: Path, **values: object) -> None:
    """부분 기록을 피하기 위해 임시 파일을 만든 뒤 상태 파일을 교체한다."""
    payload = {"updated_at": datetime.now().astimezone().isoformat(timespec="seconds"), **values}
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    """설정을 읽어 시험 또는 전체 분석을 실행하고 최종 상태를 기록한다."""
    parser = argparse.ArgumentParser(description="2way 차량 유입·유출 분석")
    parser.add_argument("--test", action="store_true", help="주간 영상 5분만 시험 분석")
    args = parser.parse_args()
    app_dir = Path(__file__).resolve().parent
    config = load_config(app_dir / "config.json")
    videos = None
    limit = None
    if args.test:
        videos = [find_day_video(Path(config["source_dir"]))]
        config = {**config, "output_dir": "test_results", "save_review_video": True}
        limit = 300
    output_dir = Path(config.get("output_dir", "results"))
    if not output_dir.is_absolute():
        output_dir = app_dir / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    status_path = output_dir / "analysis_status.json"
    error_path = output_dir / "analysis_error.log"

    def update_progress(value: float) -> None:
        # 모니터는 이 파일만 읽으므로 분석 콜백마다 최신 진행률을 원자적으로 기록한다.
        write_status(status_path, status="running", progress=round(value, 6), test=bool(args.test))

    write_status(status_path, status="running", progress=0.0, test=bool(args.test))
    try:
        analyzer = BatchAnalyzer(
            config,
            app_dir,
            progress=update_progress,
            stop_event=threading.Event(),
        )
        summary, events = analyzer.run(limit_seconds=limit, video_paths=videos)
        write_status(
            status_path,
            status="completed",
            progress=1.0,
            test=bool(args.test),
            summary_csv=str(summary),
            events_csv=str(events) if events is not None else None,
        )
        print(f"\n집계표: {summary}\n통과 기록: {events}")
    except BaseException:
        error_path.write_text(traceback.format_exc(), encoding="utf-8")
        write_status(
            status_path,
            status="failed",
            progress=0.0,
            test=bool(args.test),
            error_log=str(error_path),
        )
        raise


if __name__ == "__main__":
    main()

