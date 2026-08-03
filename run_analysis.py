from __future__ import annotations

import argparse
import threading
from pathlib import Path

from calibrate import find_day_video
from two_way_counter import BatchAnalyzer, load_config


def main() -> None:
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
    analyzer = BatchAnalyzer(config, app_dir, stop_event=threading.Event())
    summary, events = analyzer.run(limit_seconds=limit, video_paths=videos)
    print(f"\n집계표: {summary}\n통과 기록: {events}")


if __name__ == "__main__":
    main()

