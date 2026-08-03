from __future__ import annotations

import sys
from pathlib import Path

from two_way_counter import load_config, save_config


def find_day_video(source_dir: Path) -> Path:
    preferred = sorted(source_dir.glob("04_*.mp4"))
    videos = preferred or sorted(source_dir.glob("*.mp4"))
    if not videos:
        raise FileNotFoundError(f"MP4 파일이 없습니다: {source_dir}")
    return videos[0]


def draw_overlay(cv2, frame, roi, line, phase, scale):
    image = frame.copy()
    scaled_roi = [(round(x * scale), round(y * scale)) for x, y in roi]
    scaled_line = [(round(x * scale), round(y * scale)) for x, y in line]
    for index, point in enumerate(scaled_roi):
        cv2.circle(image, point, 6, (0, 220, 255), -1)
        if index:
            cv2.line(image, scaled_roi[index - 1], point, (0, 220, 255), 2)
    if phase == "line" and len(scaled_roi) >= 3:
        cv2.line(image, scaled_roi[-1], scaled_roi[0], (0, 220, 255), 2)
    for index, point in enumerate(scaled_line):
        cv2.circle(image, point, 7, (255, 0, 255), -1)
        if index:
            cv2.line(image, scaled_line[index - 1], point, (255, 0, 255), 3)
    instruction = "ROI: LEFT CLICK 3+ POINTS, ENTER=NEXT, R=RESET, ESC=CANCEL"
    if phase == "line":
        instruction = "COUNT LINE: LEFT CLICK 2 POINTS, ENTER=SAVE, R=RESET, ESC=CANCEL"
    cv2.rectangle(image, (0, 0), (image.shape[1], 38), (0, 0, 0), -1)
    cv2.putText(image, instruction, (12, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
    return image


def calibrate(config_path: Path) -> Path | None:
    import cv2

    config = load_config(config_path)
    video = find_day_video(Path(config["source_dir"]))
    cap = cv2.VideoCapture(str(video))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    duration = (cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) / fps
    cap.set(cv2.CAP_PROP_POS_MSEC, min(1800.0, duration / 2) * 1000)
    ok, original = cap.read()
    cap.release()
    if not ok:
        raise RuntimeError(f"대표 프레임을 읽지 못했습니다: {video.name}")

    max_width, max_height = 1200, 820
    scale = min(max_width / original.shape[1], max_height / original.shape[0], 1.0)
    display = cv2.resize(original, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    roi: list[tuple[int, int]] = []
    line: list[tuple[int, int]] = []
    phase = "roi"
    window = "2way calibration"

    def on_mouse(event, x, y, flags, param):
        del flags, param
        if event != cv2.EVENT_LBUTTONDOWN:
            return
        target = roi if phase == "roi" else line
        if phase == "line" and len(target) >= 2:
            target.clear()
        target.append((round(x / scale), round(y / scale)))

    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(window, on_mouse)
    saved = False
    while True:
        cv2.imshow(window, draw_overlay(cv2, display, roi, line, phase, scale))
        key = cv2.waitKey(30) & 0xFF
        if key == 27:
            break
        if key in (ord("r"), ord("R")):
            (roi if phase == "roi" else line).clear()
        if key in (10, 13):
            if phase == "roi" and len(roi) >= 3:
                phase = "line"
            elif phase == "line" and len(line) == 2:
                config["roi_polygon"] = [[x, y] for x, y in roi]
                config["counting_line"] = [[x, y] for x, y in line]
                save_config(config_path, config)
                saved = True
                break
    cv2.destroyAllWindows()
    if not saved:
        return None

    preview = original.copy()
    cv2.polylines(preview, [__import__("numpy").array(roi, dtype="int32")], True, (0, 220, 255), 3)
    cv2.line(preview, line[0], line[1], (255, 0, 255), 4)
    preview_path = config_path.parent / "영역설정_미리보기.jpg"
    cv2.imwrite(str(preview_path), preview)
    return preview_path


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("config.json")
    result = calibrate(target)
    print(f"저장 완료: {result}" if result else "취소했습니다.")

