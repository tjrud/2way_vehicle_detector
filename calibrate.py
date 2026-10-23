"""대표 영상 프레임에서 ROI 다각형과 통과선(접근로마다 1개)을 설정하는 도구.

조작
- 1단계(영역): 왼쪽 클릭으로 3점 이상 → Enter
- 2단계(통과선): 접근로마다 도로를 가로지르는 선의 양 끝을 클릭(2번 클릭 = 1개)
  화살표가 유입(교차로 안쪽) 방향이다. F = 마지막 통과선 방향 뒤집기,
  Z 또는 Backspace = 되돌리기, R = 현재 단계 초기화, Enter = 저장, Esc = 취소
"""

from __future__ import annotations

import sys
from pathlib import Path

from intersection_counter import Gate, default_inside_sides, gate_codes
from two_way_counter import INTERSECTION_GATE_COUNTS, draw_gates, load_config, save_config

ENTER_KEYS = (10, 13)
UNDO_KEYS = (8, ord("z"), ord("Z"))


def find_day_video(source_dir: Path) -> Path:
    """보정·시험용 09~12시 영상(04_*)을 우선 선택한다."""
    preferred = sorted(source_dir.glob("04_*.mp4"))
    videos = preferred or sorted(source_dir.glob("*.mp4"))
    if not videos:
        raise FileNotFoundError(f"MP4 파일이 없습니다: {source_dir}")
    return videos[0]


def preview_gates(
    roi: list[tuple[int, int]],
    lines: list[tuple[tuple[int, int], tuple[int, int]]],
    flips: list[bool],
    names: list[str],
    increasing_x_direction: str = "유출",
) -> list[Gate]:
    """지금까지 그린 선으로 기본 유입 방향(교차로 중심 쪽)과 사용자 뒤집기를 반영한 통과선."""
    if not lines:
        return []
    sides = default_inside_sides(
        [((float(a[0]), float(a[1])), (float(b[0]), float(b[1]))) for a, b in lines],
        roi if len(roi) >= 3 else None,
        increasing_x_direction,
    )
    codes = gate_codes(len(lines))
    gates = []
    for index, (a, b) in enumerate(lines):
        side = -sides[index] if flips[index] else sides[index]
        name = names[index] if index < len(names) and names[index] else codes[index]
        gates.append(Gate(codes[index], name, (float(a[0]), float(a[1])), (float(b[0]), float(b[1])), side))
    return gates


def draw_overlay(cv2, frame, roi, gates, pending, phase, scale, expected):
    """현재 클릭한 좌표와 단계별 조작 안내를 미리보기 위에 그린다."""
    image = frame.copy()
    scaled_roi = [(round(x * scale), round(y * scale)) for x, y in roi]
    for index, point in enumerate(scaled_roi):
        cv2.circle(image, point, 6, (0, 220, 255), -1)
        if index:
            cv2.line(image, scaled_roi[index - 1], point, (0, 220, 255), 2)
    if phase == "gates" and len(scaled_roi) >= 3:
        cv2.line(image, scaled_roi[-1], scaled_roi[0], (0, 220, 255), 2)
    draw_gates(cv2, image, gates, highlight=len(gates) - 1 if gates else None, scale=scale)
    if pending is not None:
        cv2.circle(image, (round(pending[0] * scale), round(pending[1] * scale)), 7, (255, 0, 255), -1)
    if phase == "roi":
        lines = [
            "STEP 1  AREA: LEFT CLICK 3+ POINTS (include road beyond every line)",
            "ENTER=NEXT  Z=UNDO  R=RESET  ESC=CANCEL",
        ]
    else:
        target = f"/{expected}" if expected else ""
        lines = [
            f"STEP 2  LINES {len(gates)}{target}: CLICK 2 POINTS ACROSS EACH ROAD. ARROW = INBOUND",
            "F=FLIP LAST ARROW  Z=UNDO  R=RESET LINES  ENTER=SAVE  ESC=CANCEL",
        ]
    cv2.rectangle(image, (0, 0), (image.shape[1], 58), (0, 0, 0), -1)
    for row, text in enumerate(lines):
        cv2.putText(image, text, (12, 23 + row * 24), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
    return image


def calibrate(
    config_path: Path,
    intersection_type: str | None = None,
    gate_names: list[str] | None = None,
) -> Path | None:
    """마우스로 ROI와 통과선(1개 이상)을 받아 설정 및 미리보기 이미지를 저장한다."""
    import cv2
    import numpy as np

    config = load_config(config_path)
    intersection_type = intersection_type or str(config.get("intersection_type", "2way"))
    expected = INTERSECTION_GATE_COUNTS.get(intersection_type)
    names = [name.strip() for name in (gate_names if gate_names is not None else config.get("approach_names", []))]
    increasing = str(config.get("increasing_x_direction", "유출"))
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
    lines: list[tuple[tuple[int, int], tuple[int, int]]] = []
    flips: list[bool] = []
    pending: list[tuple[int, int] | None] = [None]
    phase = ["roi"]
    window = "intersection calibration"

    def on_mouse(event, x, y, flags, param):
        # 화면은 축소될 수 있으므로 클릭 좌표를 원본 해상도로 되돌려 저장한다.
        del flags, param
        if event != cv2.EVENT_LBUTTONDOWN:
            return
        point = (round(x / scale), round(y / scale))
        if phase[0] == "roi":
            roi.append(point)
            return
        if pending[0] is None:
            pending[0] = point
            return
        if abs(point[0] - pending[0][0]) + abs(point[1] - pending[0][1]) < 10:
            return
        if expected == 1 and lines:
            # 2방향 도로는 통과선 1개: 다시 그리면 기존 선을 바꾼다.
            lines.clear()
            flips.clear()
        lines.append((pending[0], point))
        flips.append(False)
        pending[0] = None

    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    cv2.setMouseCallback(window, on_mouse)
    saved = False
    gates: list[Gate] = []
    while True:
        gates = preview_gates(roi, lines, flips, names, increasing)
        cv2.imshow(window, draw_overlay(cv2, display, roi, gates, pending[0], phase[0], scale, expected))
        key = cv2.waitKey(30) & 0xFF
        if key == 27:
            break
        if key in (ord("r"), ord("R")):
            if phase[0] == "roi":
                roi.clear()
            else:
                lines.clear()
                flips.clear()
                pending[0] = None
        elif key in UNDO_KEYS:
            if phase[0] == "roi":
                if roi:
                    roi.pop()
            elif pending[0] is not None:
                pending[0] = None
            elif lines:
                lines.pop()
                flips.pop()
            else:
                phase[0] = "roi"
        elif key in (ord("f"), ord("F")) and flips:
            flips[-1] = not flips[-1]
        elif key in ENTER_KEYS:
            if phase[0] == "roi" and len(roi) >= 3:
                phase[0] = "gates"
            elif phase[0] == "gates" and lines:
                config["roi_polygon"] = [[x, y] for x, y in roi]
                config["gates"] = [gate.to_config() for gate in gates]
                config["counting_line"] = config["gates"][0]["line"]
                config["intersection_type"] = intersection_type
                if len(gates) == 1:
                    # 기존 2방향 화면 문구·버튼과 일치하도록 방향 값도 함께 맞춘다.
                    nx = gates[0].inward_normal()[0]
                    config["increasing_x_direction"] = "유입" if nx > 0 else "유출"
                save_config(config_path, config)
                saved = True
                break
    cv2.destroyAllWindows()
    if not saved:
        return None

    preview = original.copy()
    cv2.polylines(preview, [np.array(roi, dtype="int32")], True, (0, 220, 255), 3)
    draw_gates(cv2, preview, gates)
    preview_path = config_path.parent / "영역설정_미리보기.jpg"
    cv2.imwrite(str(preview_path), preview)
    return preview_path


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name("config.json")
    result = calibrate(target)
    print(f"저장 완료: {result}" if result else "취소했습니다.")
