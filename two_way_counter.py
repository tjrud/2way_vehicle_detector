"""YOLO 추적 결과를 통과선별 유입·유출, 방향별(OD) 이동과 15분 단위 CSV로 변환한다.

이 모듈은 UI에 의존하지 않는 분석 계층이다. ROI 크롭, 프레임 선행 디코딩,
최소 박스 필터, 결과 저장을 담당하고, 통과 판정과 ID 재연결은
:mod:`intersection_counter`의 :class:`IntersectionCounter`에 맡긴다.

통과선이 1개면 기존 2방향 도로 분석과 같고, 2개 이상이면 T자·십자·오거리·
회전교차로처럼 접근로마다 통과선을 두고 출발→도착 이동까지 집계한다.
"""

from __future__ import annotations

import csv
import json
import queue
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Sequence

from intersection_counter import (  # noqa: F401  (기존 import 경로 호환용 재노출 포함)
    INBOUND,
    OUTBOUND,
    TRIP_COMPLETE,
    TRIP_NO_ENTRY,
    TRIP_NO_EXIT,
    UNKNOWN,
    DirectionCounter,
    Gate,
    GateCrossing,
    IntersectionCounter,
    MovementClassifier,
    TrackState,
    Trip,
    build_gates,
    gates_center,
    screen_direction_text,
    side_of,
)


VEHICLE_CLASSES = [2, 3, 5, 7]  # COCO: car, motorcycle, bus, truck
CLASS_LABELS = {1: "자전거", 2: "승용차", 3: "오토바이", 5: "버스", 7: "트럭"}
VIDEO_PATTERN = re.compile(r"_(\d{2})(\d{2})-(\d{2})(\d{2})_")

INTERSECTION_TYPES = {
    "2way": "2방향 도로 (통과선 1개)",
    "3way": "T자·Y자 교차로 (3갈래)",
    "4way": "십자 교차로 (4갈래)",
    "5way": "오거리 (5갈래)",
    "roundabout": "회전교차로",
    "custom": "직접 지정",
}
INTERSECTION_GATE_COUNTS = {"2way": 1, "3way": 3, "4way": 4, "5way": 5}


class FramePrefetcher:
    """Decode frames on a background thread while inference handles the current frame."""

    _END = object()

    def __init__(
        self,
        capture: Any,
        max_frames: int,
        buffer_size: int = 8,
        stop_event: threading.Event | None = None,
    ) -> None:
        """캡처 객체와 최대 프레임 수 및 제한된 버퍼를 저장한다."""
        self.capture = capture
        self.max_frames = max(0, int(max_frames))
        self.stop_event = stop_event
        self.frames: queue.Queue[Any] = queue.Queue(maxsize=max(1, int(buffer_size)))
        self.closed = threading.Event()
        self.worker = threading.Thread(target=self._decode, name="video-frame-prefetch", daemon=True)

    def _put(self, item: Any) -> bool:
        """중지 여부를 확인하며 버퍼에 프레임 또는 종료 표식을 넣는다."""
        while not self.closed.is_set():
            try:
                self.frames.put(item, timeout=0.1)
                return True
            except queue.Full:
                continue
        return False

    def _decode(self) -> None:
        """백그라운드에서 순서대로 디코딩하고 항상 캡처를 해제한다."""
        try:
            for _ in range(self.max_frames):
                if self.closed.is_set() or (self.stop_event is not None and self.stop_event.is_set()):
                    break
                ok, frame = self.capture.read()
                if not ok or not self._put(frame):
                    break
        finally:
            self.capture.release()
            self._put(self._END)

    def __iter__(self) -> Iterator[Any]:
        """생산 스레드를 시작하고 원래 영상 순서대로 프레임을 제공한다."""
        self.worker.start()
        try:
            while True:
                try:
                    item = self.frames.get(timeout=0.1)
                except queue.Empty:
                    if not self.worker.is_alive():
                        break
                    continue
                if item is self._END:
                    break
                yield item
        finally:
            self.close()

    def close(self) -> None:
        """디코더에 종료를 알리고 짧게 합류해 리소스 누수를 막는다."""
        self.closed.set()
        if self.worker.is_alive():
            self.worker.join(timeout=5.0)


@dataclass
class CountEvent:
    """CSV 및 검수 이미지에 기록할 한 번의 차량 통과 사건."""

    video: str
    frame: int
    video_seconds: float
    real_seconds: float
    slot: int
    direction: str
    track_id: int
    class_id: int
    class_name: str
    confidence: float
    point_x: float
    point_y: float
    gate_code: str = "A"
    gate_name: str = "통과선"

    @property
    def real_time(self) -> str:
        """자정을 순환하는 실제 영상 시각을 HH:MM:SS로 표시한다."""
        return format_clock(self.real_seconds)


@dataclass
class TripRecord:
    """CSV에 기록할 한 차량의 진입→진출 이동."""

    video: str
    trip: Trip
    movement: str
    class_name: str
    slot: int
    entry_seconds: float | None
    exit_seconds: float | None


def format_clock(seconds: float | None) -> str:
    """초를 자정 순환 HH:MM:SS 문자열로 바꾼다."""
    if seconds is None:
        return ""
    total = int(seconds) % 86400
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def default_config(source_dir: str) -> dict:
    """정확도와 장시간 실행을 고려한 기본 분석 설정을 반환한다."""
    return {
        "source_dir": source_dir,
        "output_dir": "results",
        "model": "yolo26l.pt",
        "tracker": "botsort_reid.yaml",
        "device": "0",
        "half_precision": True,
        "confidence": 0.22,
        "iou": 0.65,
        "image_size": 960,
        "roi_crop_enabled": True,
        "roi_crop_padding_px": 64,
        "frame_prefetch_buffer": 8,
        "min_box_width_px": 35,
        "min_box_height_px": 35,
        "min_box_area_px": 2000,
        "pixel_scale": "auto",
        "speed_factor": 3.0,
        "vehicle_classes": VEHICLE_CLASSES,
        "intersection_type": "2way",
        "roi_polygon": [[410, 470], [1260, 430], [1270, 900], [650, 870], [400, 670]],
        "counting_line": [[790, 790], [1035, 545]],
        "increasing_x_direction": "유출",
        "gates": [],
        "count_mode": "auto",
        "movement_labels": {},
        "deadband_px": 8,
        "min_stable_frames": 3,
        "min_crossing_motion_px": 12,
        "line_margin_px": 40,
        "uturn_min_depth_px": 60,
        "velocity_smoothing": 0.5,
        "occluded_bottom_ratio": 0.5,
        "id_stitch_enabled": True,
        "id_stitch_in_transit": True,
        "id_stitch_max_gap": 90,
        "id_stitch_max_distance_px": 140,
        "id_stitch_line_distance_px": 160,
        "id_stitch_direction_tolerance_px": 12,
        "fast_crossing_enabled": True,
        "fast_crossing_min_normal_speed_px": 15,
        "fast_crossing_stable_frames": 2,
        "save_detailed_records": True,
        "save_event_images": True,
        "save_review_video": False,
        "review_width": 640,
        "review_stride": 3,
    }


def load_config(path: Path) -> dict:
    """UTF-8 JSON 설정 파일을 읽는다. PowerShell이 붙인 BOM도 허용한다."""
    with path.open("r", encoding="utf-8-sig") as file:
        return json.load(file)


def save_config(path: Path, config: dict) -> None:
    """한국어를 이스케이프하지 않고 사람이 읽기 좋은 JSON으로 저장한다."""
    with path.open("w", encoding="utf-8") as file:
        json.dump(config, file, ensure_ascii=False, indent=2)


def video_start_seconds(filename: str) -> int:
    """파일명의 _HHMM-HHMM_ 구간에서 시작 시각을 초 단위로 추출한다."""
    match = VIDEO_PATTERN.search(filename)
    if not match:
        raise ValueError(f"파일명에서 시작 시간을 찾을 수 없습니다: {filename}")
    start_hour, start_minute = int(match.group(1)), int(match.group(2))
    return start_hour * 3600 + start_minute * 60


def slot_label(slot: int) -> str:
    """0~95 슬롯을 15분 시간 구간 문자열로 변환한다."""
    start = slot * 15
    end = (start + 15) % (24 * 60)
    return f"{start // 60:02d}:{start % 60:02d}~{end // 60:02d}:{end % 60:02d}"


def point_in_polygon(point: tuple[float, float], polygon: list[list[int]]) -> bool:
    """외부 의존성 없이 광선 교차법으로 점의 ROI 포함 여부를 판정한다."""
    x, y = point
    inside = False
    previous = polygon[-1]
    for current in polygon:
        x1, y1 = previous
        x2, y2 = current
        intersects = (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-9) + x1
        if intersects:
            inside = not inside
        previous = current
    return inside


def hidden_bottom_ratio(
    box: tuple[float, float, float, float],
    others: Iterable[tuple[float, float, float, float]],
) -> float:
    """박스 아래 변이 앞쪽(화면 아래쪽) 차량 박스에 가려진 가로 비율을 계산한다.

    가려진 차량의 박스는 실제 바퀴가 아니라 앞차 경계에서 끝나므로 하단 중앙점이
    통과선을 넘은 것처럼 튈 수 있다. 이 비율이 크면 그 프레임은 판정에서 뺀다.
    """
    x1, y1, x2, y2 = box
    width = max(1e-6, x2 - x1)
    height = max(1e-6, y2 - y1)
    hidden = 0.0
    for ox1, oy1, ox2, oy2 in others:
        if oy2 <= y2 + 0.05 * height or oy1 >= y2:
            continue
        hidden = max(hidden, max(0.0, min(x2, ox2) - max(x1, ox1)) / width)
    return hidden


def box_meets_minimum_size(
    xyxy: tuple[float, float, float, float],
    min_width_px: float,
    min_height_px: float,
    min_area_px: float,
) -> bool:
    """Reject tiny detections before they enter the direction counter."""
    x1, y1, x2, y2 = xyxy
    width = max(0.0, x2 - x1)
    height = max(0.0, y2 - y1)
    return width >= min_width_px and height >= min_height_px and width * height >= min_area_px


def roi_crop_bounds(
    frame_width: int,
    frame_height: int,
    polygon: list[list[int]],
    padding_px: int,
) -> tuple[int, int, int, int]:
    """Return clamped, exclusive ROI crop bounds with context padding."""
    if not polygon:
        raise ValueError("roi_polygon must contain at least one point")
    padding = max(0, int(padding_px))
    xs = [int(point[0]) for point in polygon]
    ys = [int(point[1]) for point in polygon]
    left = max(0, min(xs) - padding)
    top = max(0, min(ys) - padding)
    right = min(frame_width, max(xs) + padding + 1)
    bottom = min(frame_height, max(ys) + padding + 1)
    if right <= left or bottom <= top:
        raise ValueError("roi_polygon does not overlap the video frame")
    return left, top, right, bottom


def resolve_pixel_scale(config: dict, frame_height: int) -> float:
    """px 단위 기준값을 영상 해상도에 맞춘다. 기본값은 높이 960px(기준 카메라)을 1.0으로 본다."""
    value = config.get("pixel_scale", "auto")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        return float(value)
    reference = float(config.get("pixel_scale_reference_height", 960) or 960)
    if frame_height <= 0:
        return 1.0
    return max(0.4, min(2.5, frame_height / reference))


def build_counter(config: dict, gates: Sequence[Gate], scale: float = 1.0) -> IntersectionCounter:
    """설정값과 해상도 배율로 통과 판정기를 만든다."""
    motion = config.get("min_crossing_motion_px", config.get("min_horizontal_motion_px", 12))
    return IntersectionCounter(
        gates,
        deadband_px=float(config.get("deadband_px", 8)) * scale,
        min_stable_frames=int(config.get("min_stable_frames", 3)),
        min_crossing_motion_px=float(motion) * scale,
        line_margin_px=float(config.get("line_margin_px", 40)) * scale,
        id_stitch_enabled=bool(config.get("id_stitch_enabled", True)),
        id_stitch_max_gap=int(config.get("id_stitch_max_gap", 90)),
        id_stitch_max_distance_px=float(config.get("id_stitch_max_distance_px", 140)) * scale,
        id_stitch_line_distance_px=float(config.get("id_stitch_line_distance_px", 160)) * scale,
        id_stitch_direction_tolerance_px=float(config.get("id_stitch_direction_tolerance_px", 12)) * scale,
        fast_crossing_enabled=bool(config.get("fast_crossing_enabled", True)),
        fast_crossing_min_normal_speed_px=float(config.get("fast_crossing_min_normal_speed_px", 15)) * scale,
        fast_crossing_stable_frames=int(config.get("fast_crossing_stable_frames", 2)),
        count_mode=str(config.get("count_mode", "auto") or "auto"),
        stitch_in_transit=bool(config.get("id_stitch_in_transit", True)),
        uturn_min_depth_px=float(config.get("uturn_min_depth_px", 60)) * scale,
        velocity_smoothing=float(config.get("velocity_smoothing", 0.5)),
    )


def empty_counts() -> dict[int, dict[str, int]]:
    """96개 15분 구간의 유입·유출 0 집계."""
    return {slot: {INBOUND: 0, OUTBOUND: 0} for slot in range(96)}


def write_summary(path: Path, counts: dict[int, dict[str, int]]) -> None:
    """Excel 호환 UTF-8 BOM을 사용해 96개 15분 집계 행을 저장한다."""
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(["시간", "유입", "유출", "합계"])
        for slot in range(96):
            incoming = counts[slot][INBOUND]
            outgoing = counts[slot][OUTBOUND]
            writer.writerow([slot_label(slot), incoming, outgoing, incoming + outgoing])


def write_events(path: Path, events: Iterable[CountEvent]) -> None:
    """차량별 통과 사건을 상세 CSV로 저장한다. 통과선 열은 호환을 위해 마지막에 둔다."""
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        fieldnames = [
            "실제시간", "15분구간", "방향", "차종", "추적ID", "신뢰도",
            "원본영상", "영상초", "프레임", "좌표X", "좌표Y", "통과선",
        ]
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for event in events:
            writer.writerow({
                "실제시간": event.real_time,
                "15분구간": slot_label(event.slot),
                "방향": event.direction,
                "차종": event.class_name,
                "추적ID": event.track_id,
                "신뢰도": f"{event.confidence:.3f}",
                "원본영상": event.video,
                "영상초": f"{event.video_seconds:.3f}",
                "프레임": event.frame,
                "좌표X": f"{event.point_x:.1f}",
                "좌표Y": f"{event.point_y:.1f}",
                "통과선": event.gate_name,
            })


def write_video_results(
    output_dir: Path,
    video: Path,
    counts: dict[int, dict[str, int]],
    events: Iterable[CountEvent],
    save_detailed_records: bool = True,
) -> tuple[Path, Path | None]:
    """Write one video's summary and optionally its detailed crossing records."""
    summary_path = output_dir / f"{video.stem}_15분_유입유출_집계.csv"
    write_summary(summary_path, counts)
    events_path = None
    if save_detailed_records:
        events_path = output_dir / f"{video.stem}_차량별_통과기록.csv"
        write_events(events_path, events)
    return summary_path, events_path


def write_gate_summary(
    path: Path,
    gate_counts: dict[int, list[dict[str, int]]],
    gates: Sequence[Gate],
) -> None:
    """15분 구간마다 통과선(접근로)별 유입·유출을 저장한다. 마지막 행은 합계."""
    totals = [{INBOUND: 0, OUTBOUND: 0} for _ in gates]
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        header = ["시간"]
        for gate in gates:
            header += [f"{gate.name} 유입", f"{gate.name} 유출"]
        writer.writerow(header + ["유입 합계", "유출 합계"])
        for slot in range(96):
            row: list[Any] = [slot_label(slot)]
            slot_in = slot_out = 0
            for index, _ in enumerate(gates):
                value = gate_counts[slot][index]
                row += [value[INBOUND], value[OUTBOUND]]
                totals[index][INBOUND] += value[INBOUND]
                totals[index][OUTBOUND] += value[OUTBOUND]
                slot_in += value[INBOUND]
                slot_out += value[OUTBOUND]
            writer.writerow(row + [slot_in, slot_out])
        total_row: list[Any] = ["합계"]
        for value in totals:
            total_row += [value[INBOUND], value[OUTBOUND]]
        writer.writerow(total_row + [sum(v[INBOUND] for v in totals), sum(v[OUTBOUND] for v in totals)])


def movement_columns(gates: Sequence[Gate], classifier: MovementClassifier) -> list[tuple[int | None, int | None, str]]:
    """방향별 집계 열: 모든 출발→도착 쌍, 도착 미확인, 출발 미확인 순서."""
    columns: list[tuple[int | None, int | None, str]] = []
    for entry, entry_gate in enumerate(gates):
        for exit_, exit_gate in enumerate(gates):
            label = classifier.label(entry, exit_)
            columns.append((entry, exit_, f"{entry_gate.name}→{exit_gate.name}({label})"))
    for entry, gate in enumerate(gates):
        columns.append((entry, None, f"{gate.name}→{UNKNOWN}"))
    for exit_, gate in enumerate(gates):
        columns.append((None, exit_, f"{UNKNOWN}→{gate.name}"))
    return columns


def write_movement_summary(
    path: Path,
    movement_counts: dict[int, dict[tuple[int | None, int | None], int]],
    gates: Sequence[Gate],
    classifier: MovementClassifier,
) -> None:
    """15분 구간마다 출발→도착(회전) 대수를 저장한다. 시간은 진입 시각 기준."""
    columns = movement_columns(gates, classifier)
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(["시간"] + [name for _, _, name in columns] + ["완료 이동", "전체 이동"])
        totals = [0] * len(columns)
        for slot in range(96):
            values = [movement_counts[slot].get((entry, exit_), 0) for entry, exit_, _ in columns]
            totals = [a + b for a, b in zip(totals, values)]
            complete = sum(v for (entry, exit_, _), v in zip(columns, values) if entry is not None and exit_ is not None)
            writer.writerow([slot_label(slot)] + values + [complete, sum(values)])
        complete_total = sum(v for (entry, exit_, _), v in zip(columns, totals) if entry is not None and exit_ is not None)
        writer.writerow(["합계"] + totals + [complete_total, sum(totals)])


def write_od_matrix(
    path: Path,
    movement_totals: dict[tuple[int | None, int | None], int],
    gates: Sequence[Gate],
) -> None:
    """전체 시간의 출발(행)→도착(열) OD 행렬을 저장한다."""
    origins: list[int | None] = [*range(len(gates)), None]
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(["출발＼도착"] + [gate.name for gate in gates] + [UNKNOWN, "합계"])
        for origin in origins:
            values = [movement_totals.get((origin, destination), 0) for destination in origins]
            name = gates[origin].name if origin is not None else UNKNOWN
            writer.writerow([name] + values + [sum(values)])
        column_totals = [sum(movement_totals.get((origin, destination), 0) for origin in origins) for destination in origins]
        writer.writerow(["합계"] + column_totals + [sum(column_totals)])


def write_trips(path: Path, records: Iterable[TripRecord], gates: Sequence[Gate]) -> None:
    """차량별 진입→진출 이동 기록을 저장한다."""
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        fieldnames = [
            "진입시각", "진출시각", "15분구간", "출발", "도착", "회전", "상태", "차종",
            "추적ID", "최종ID", "원본영상", "진입프레임", "진출프레임",
        ]
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            trip = record.trip
            writer.writerow({
                "진입시각": format_clock(record.entry_seconds),
                "진출시각": format_clock(record.exit_seconds),
                "15분구간": slot_label(record.slot),
                "출발": gates[trip.entry_gate].name if trip.entry_gate is not None else UNKNOWN,
                "도착": gates[trip.exit_gate].name if trip.exit_gate is not None else UNKNOWN,
                "회전": record.movement,
                "상태": trip.status,
                "차종": record.class_name,
                "추적ID": trip.track_id,
                "최종ID": trip.last_track_id,
                "원본영상": record.video,
                "진입프레임": trip.entry.frame if trip.entry is not None else "",
                "진출프레임": trip.exit.frame if trip.exit is not None else "",
            })


def reset_model_tracking_state(model: Any) -> bool:
    """Reset trackers between videos without re-registering YOLO callbacks."""
    predictor = getattr(model, "predictor", None)
    trackers = getattr(predictor, "trackers", None) if predictor is not None else None
    if not trackers:
        return False
    for tracker in trackers:
        tracker.reset()
    predictor.vid_path = [None] * len(trackers)
    if hasattr(predictor, "_feats"):
        predictor._feats = None
    return True


def extract_box_rows(boxes: Any, torch_module: Any = None) -> Any:
    """추적 박스를 [x1, y1, x2, y2, id, cls, conf] 행렬로 한 번에 CPU로 옮긴다."""
    if boxes is None or getattr(boxes, "id", None) is None:
        return []
    if torch_module is not None and isinstance(boxes.xyxy, torch_module.Tensor):
        return torch_module.cat(
            (boxes.xyxy, boxes.id[:, None], boxes.cls[:, None], boxes.conf[:, None]),
            dim=1,
        ).cpu().numpy()
    import numpy as np

    return np.concatenate(
        (
            np.asarray(boxes.xyxy, dtype=float),
            np.asarray(boxes.id, dtype=float)[:, None],
            np.asarray(boxes.cls, dtype=float)[:, None],
            np.asarray(boxes.conf, dtype=float)[:, None],
        ),
        axis=1,
    )


def draw_gates(cv2: Any, image: Any, gates: Sequence[Gate], highlight: int | None = None, scale: float = 1.0) -> None:
    """통과선, 코드 문자, 유입(안쪽) 방향 화살표를 그린다. 좌표는 scale배로 그린다."""
    thickness = max(1, round(3 * scale))
    for index, gate in enumerate(gates):
        color = (0, 255, 255) if index == highlight else (255, 0, 255)
        a = (round(gate.a[0] * scale), round(gate.a[1] * scale))
        b = (round(gate.b[0] * scale), round(gate.b[1] * scale))
        cv2.line(image, a, b, color, thickness + (2 if index == highlight else 0))
        mx, my = gate.midpoint
        nx, ny = gate.inward_normal()
        arrow = max(18.0, min(60.0, gate.length * 0.35))
        start = (round(mx * scale), round(my * scale))
        end = (round((mx + nx * arrow) * scale), round((my + ny * arrow) * scale))
        cv2.arrowedLine(image, start, end, color, max(1, thickness - 1), tipLength=0.35)
        label_at = (round((mx - nx * 22) * scale) - 8, round((my - ny * 22) * scale) + 8)
        cv2.putText(image, gate.code, label_at, cv2.FONT_HERSHEY_SIMPLEX, max(0.5, 0.9 * scale), color, max(1, thickness - 1))


class BatchAnalyzer:
    """여러 MP4를 순차 분석하고 영상별·전체 결과를 지속적으로 저장한다."""

    def __init__(
        self,
        config: dict,
        config_dir: Path,
        log: Callable[[str], None] | None = None,
        progress: Callable[[float], None] | None = None,
        stop_event: threading.Event | None = None,
        model_loader: Callable[[str], Any] | None = None,
    ) -> None:
        """설정과 로그·진행률·중지 콜백을 주입받아 UI와 분리한다."""
        self.config = config
        self.config_dir = config_dir
        self.log = log or print
        self.progress = progress or (lambda value: None)
        self.stop_event = stop_event or threading.Event()
        self.model_loader = model_loader
        self.report_path: Path | None = None
        self.outputs: dict[str, str] = {}

    def _load_model(self, model_path: str) -> Any:
        """YOLO 모델을 불러온다. 테스트에서는 model_loader로 대체한다."""
        if self.model_loader is not None:
            return self.model_loader(model_path)
        from ultralytics import YOLO

        return YOLO(model_path)

    def run(
        self,
        limit_seconds: float | None = None,
        video_paths: list[Path] | None = None,
    ) -> tuple[Path, Path | None]:
        """대상 영상을 분석하고 전체 집계 및 선택적 상세 CSV 경로를 반환한다."""
        import cv2
        import numpy as np

        try:
            import torch
        except ImportError:  # 가짜 모델로 테스트할 때만 해당
            torch = None

        started_at = time.monotonic()
        source_dir = Path(self.config["source_dir"])
        output_dir = Path(self.config.get("output_dir", "results"))
        if not output_dir.is_absolute():
            output_dir = self.config_dir / output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        event_image_dir = output_dir / "event_images"
        if self.config.get("save_event_images", True):
            event_image_dir.mkdir(parents=True, exist_ok=True)

        videos = video_paths if video_paths is not None else sorted(source_dir.glob("*.mp4"))
        if not videos:
            raise FileNotFoundError(f"분석할 MP4 파일이 없습니다: {source_dir}")

        gates = build_gates(self.config)
        roi_points = self.config["roi_polygon"]
        center = gates_center(gates, roi_points, self.config.get("center_point"))
        classifier = MovementClassifier(gates, center, self.config.get("movement_labels") or {})
        trip_mode = build_counter(self.config, gates).count_mode == "trip"
        self.log(
            f"통과선 {len(gates)}개: "
            + ", ".join(f"{gate.code}={gate.name}" for gate in gates)
            + (" · 출발→도착 집계 사용" if trip_mode else "")
        )

        requested_device = str(self.config.get("device", "0"))
        cuda_ready = torch is not None and torch.cuda.is_available()
        device = requested_device if requested_device == "cpu" or cuda_ready else "cpu"
        if device != requested_device:
            self.log("GPU를 찾지 못해 CPU로 분석합니다. 매우 느릴 수 있습니다.")
        half = bool(self.config.get("half_precision", True)) and device != "cpu"
        model_path = str(self.config["model"])
        self.log(f"모델 불러오는 중: {model_path} / 장치: {device}{' / FP16' if half else ''}")
        model = self._load_model(model_path)
        tracker_config = str(self.config.get("tracker", "botsort_reid.yaml"))
        tracker_path = Path(tracker_config)
        if not tracker_path.is_absolute() and (self.config_dir / tracker_path).exists():
            tracker_config = str(self.config_dir / tracker_path)
        self.log(f"추적기: {tracker_config}")

        counts = empty_counts()
        gate_counts = {slot: [{INBOUND: 0, OUTBOUND: 0} for _ in gates] for slot in range(96)}
        movement_counts: dict[int, dict[tuple[int | None, int | None], int]] = {slot: {} for slot in range(96)}
        movement_totals: dict[tuple[int | None, int | None], int] = {}
        events: list[CountEvent] = []
        trip_records: list[TripRecord] = []
        save_detailed_records = bool(self.config.get("save_detailed_records", True))
        summary_path = output_dir / "15분_유입유출_집계.csv"
        events_path = output_dir / "차량별_통과기록.csv" if save_detailed_records else None
        gate_summary_path = output_dir / "15분_접근로별_집계.csv"
        movement_path = output_dir / "15분_방향별_집계.csv"
        od_path = output_dir / "OD_행렬.csv"
        trips_path = output_dir / "차량별_이동경로.csv"
        self.report_path = output_dir / "분석요약.txt"
        stats = {"frames": 0, "recoveries": 0, "ignored": 0, "occluded": 0, "half_fallback": False, "videos": 0}
        occluded_limit = float(self.config.get("occluded_bottom_ratio", 0.5) or 0)

        # 먼저 전체 프레임 수를 계산해야 모든 영상에 걸친 진행률을 낼 수 있다.
        metadata: list[tuple[Path, int, float]] = []
        total_frames = 0
        for video in videos:
            cap = cv2.VideoCapture(str(video))
            frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
            cap.release()
            if limit_seconds is not None:
                frames = min(frames, int(limit_seconds * fps))
            metadata.append((video, frames, fps))
            total_frames += frames

        processed_frames = 0
        polygon = np.array(roi_points, dtype=np.int32)
        classes = [int(value) for value in self.config.get("vehicle_classes", VEHICLE_CLASSES)]
        speed_factor = float(self.config.get("speed_factor", 3.0))
        roi_crop_enabled = bool(self.config.get("roi_crop_enabled", True))
        frame_prefetch_buffer = int(self.config.get("frame_prefetch_buffer", 8))
        track_options = {
            "persist": True,
            "tracker": tracker_config,
            "conf": float(self.config.get("confidence", 0.22)),
            "iou": float(self.config.get("iou", 0.65)),
            "imgsz": int(self.config.get("image_size", 960)),
            "classes": classes,
            "device": device,
            "half": half,
            "verbose": False,
        }

        def write_all_outputs() -> None:
            """완료된 영상까지의 전체 결과 파일을 갱신한다."""
            write_summary(summary_path, counts)
            if events_path is not None:
                write_events(events_path, events)
            self.outputs = {"summary": str(summary_path)}
            if events_path is not None:
                self.outputs["events"] = str(events_path)
            if len(gates) >= 2:
                write_gate_summary(gate_summary_path, gate_counts, gates)
                self.outputs["gates"] = str(gate_summary_path)
            if trip_mode:
                write_movement_summary(movement_path, movement_counts, gates, classifier)
                write_od_matrix(od_path, movement_totals, gates)
                self.outputs.update({"movements": str(movement_path), "od": str(od_path)})
                if save_detailed_records:
                    write_trips(trips_path, trip_records, gates)
                    self.outputs["trips"] = str(trips_path)
            report = build_report(
                self.config, gates, classifier, center, counts, gate_counts, movement_totals,
                trip_mode, stats, time.monotonic() - started_at, device, track_options["half"],
                limit_seconds,
            )
            self.report_path.write_text(report, encoding="utf-8-sig")
            self.outputs["report"] = str(self.report_path)

        for video_index, (video, expected_frames, fps) in enumerate(metadata, start=1):
            if self.stop_event.is_set():
                break
            self.log(f"[{video_index}/{len(videos)}] {video.name} 분석 시작")
            if reset_model_tracking_state(model):
                self.log("  영상 전환: 추적기 상태 초기화")
            video_counts = empty_counts()
            video_events: list[CountEvent] = []
            start_seconds = video_start_seconds(video.name)
            cap = cv2.VideoCapture(str(video))
            frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            scale = resolve_pixel_scale(self.config, frame_height)
            counter = build_counter(self.config, gates, scale)
            min_box_width = float(self.config.get("min_box_width_px", 35)) * scale
            min_box_height = float(self.config.get("min_box_height_px", 35)) * scale
            min_box_area = float(self.config.get("min_box_area_px", 2000)) * scale * scale
            if roi_crop_enabled:
                crop_left, crop_top, crop_right, crop_bottom = roi_crop_bounds(
                    frame_width,
                    frame_height,
                    roi_points,
                    round(int(self.config.get("roi_crop_padding_px", 64)) * scale),
                )
            else:
                crop_left, crop_top, crop_right, crop_bottom = 0, 0, frame_width, frame_height
            # 추론에는 잘라낸 좌표계를 쓰되 통과 판정과 결과 기록은 원본 좌표로 복원한다.
            shifted_polygon = polygon - np.array([crop_left, crop_top], dtype=np.int32)
            roi_mask = np.zeros((crop_bottom - crop_top, crop_right - crop_left), dtype=np.uint8)
            cv2.fillPoly(roi_mask, [shifted_polygon], 255)
            roi_mask_bgr = cv2.cvtColor(roi_mask, cv2.COLOR_GRAY2BGR)
            masked_buffer = np.empty_like(roi_mask_bgr)
            self.log(
                f"  ROI 크롭: {crop_right - crop_left}x{crop_bottom - crop_top} "
                f"(원본 {frame_width}x{frame_height}, 화면 배율 {scale:.2f})"
            )
            writer = None
            review_size: tuple[int, int] | None = None
            review_scale = 1.0
            review_stride = max(1, int(self.config.get("review_stride", 3)))
            if self.config.get("save_review_video", False):
                review_width = min(frame_width, int(self.config.get("review_width", 640)))
                review_scale = review_width / frame_width
                review_size = (review_width, round(frame_height * review_scale))
                review_path = output_dir / f"검수_{video.stem}.mp4"
                writer = cv2.VideoWriter(str(review_path), cv2.VideoWriter_fourcc(*"mp4v"), fps / review_stride, review_size)

            def real_time_of(frame_number: int) -> tuple[float, float]:
                video_seconds = frame_number / fps
                return video_seconds, (start_seconds + video_seconds * speed_factor) % 86400

            def record_trips(trips: Iterable[Trip]) -> None:
                for trip in trips:
                    reference = trip.reference_frame
                    _, reference_seconds = real_time_of(max(0, reference))
                    slot = int(reference_seconds // 900)
                    key = (trip.entry_gate, trip.exit_gate)
                    movement_counts[slot][key] = movement_counts[slot].get(key, 0) + 1
                    movement_totals[key] = movement_totals.get(key, 0) + 1
                    if save_detailed_records:
                        trip_records.append(TripRecord(
                            video=video.name,
                            trip=trip,
                            movement=classifier.label(trip.entry_gate, trip.exit_gate),
                            class_name=CLASS_LABELS.get(trip.class_id, "차량") if trip.class_id is not None else "차량",
                            slot=slot,
                            entry_seconds=real_time_of(trip.entry.frame)[1] if trip.entry is not None else None,
                            exit_seconds=real_time_of(trip.exit.frame)[1] if trip.exit is not None else None,
                        ))

            frame_index = 0
            video_started = time.monotonic()
            prefetcher = FramePrefetcher(cap, expected_frames, frame_prefetch_buffer, self.stop_event)
            for frame in prefetcher:
                if self.stop_event.is_set():
                    break
                cropped = frame[crop_top:crop_bottom, crop_left:crop_right]
                cv2.bitwise_and(cropped, roi_mask_bgr, dst=masked_buffer)
                try:
                    result = model.track(masked_buffer, **track_options)[0]
                except Exception as error:
                    if not track_options["half"]:
                        raise
                    # FP16 문제가 나면 모델을 FP32로 다시 만들고 판정기도 새로 시작한다.
                    self.log(f"  FP16 추론 오류로 FP32로 전환합니다: {error}")
                    track_options["half"] = False
                    stats["half_fallback"] = True
                    model = self._load_model(model_path)
                    record_trips(counter.finish())
                    stats["recoveries"] += counter.recovery_count
                    stats["ignored"] += counter.ignored_crossings
                    counter = build_counter(self.config, gates, scale)
                    result = model.track(masked_buffer, **track_options)[0]

                track_details: list[tuple[int, tuple[float, float], int, float, tuple[float, float, float, float]]] = []
                for x1, y1, x2, y2, track_id_value, class_id_value, confidence_value in extract_box_rows(result.boxes, torch):
                    x1, y1, x2, y2 = map(float, (x1, y1, x2, y2))
                    if not box_meets_minimum_size((x1, y1, x2, y2), min_box_width, min_box_height, min_box_area):
                        continue
                    x1 += crop_left
                    x2 += crop_left
                    y1 += crop_top
                    y2 += crop_top
                    point = ((x1 + x2) / 2, y2)
                    if not point_in_polygon(point, roi_points):
                        continue
                    track_details.append((int(track_id_value), point, int(class_id_value), float(confidence_value), (x1, y1, x2, y2)))

                # 한 프레임의 모든 ID를 함께 전달해야 동시 가림을 일대일로 연결할 수 있다.
                # 차량이 없는 프레임도 전달해야 사라진 ID가 제때 정리된다.
                judged = track_details
                if occluded_limit > 0 and len(track_details) > 1:
                    boxes = [item[4] for item in track_details]
                    judged = [
                        item for index, item in enumerate(track_details)
                        if hidden_bottom_ratio(item[4], boxes[:index] + boxes[index + 1:]) < occluded_limit
                    ]
                    stats["occluded"] += len(track_details) - len(judged)
                crossings = counter.process_frame(
                    [(track_id, point, class_id, confidence) for track_id, point, class_id, confidence, _ in judged],
                    frame_index,
                )
                for new_id, old_id in counter.last_recoveries.items():
                    self.log(f"  가림 ID 연결: {old_id} → {new_id}")

                details_by_id = {item[0]: item for item in track_details}
                crossing_by_id: dict[int, GateCrossing] = {}
                for crossing in crossings:
                    crossing_by_id[crossing.track_id] = crossing
                    gate = gates[crossing.gate_index]
                    direction = crossing.direction
                    _, point, observed_class, confidence, (x1, y1, x2, y2) = details_by_id[crossing.track_id]
                    class_id = crossing.class_id if crossing.class_id is not None else observed_class
                    video_seconds, real_seconds = real_time_of(frame_index)
                    slot = int(real_seconds // 900)
                    counts[slot][direction] += 1
                    video_counts[slot][direction] += 1
                    gate_counts[slot][crossing.gate_index][direction] += 1
                    event = CountEvent(
                        video=video.name,
                        frame=frame_index,
                        video_seconds=video_seconds,
                        real_seconds=real_seconds,
                        slot=slot,
                        direction=direction,
                        track_id=crossing.track_id,
                        class_id=class_id,
                        class_name=CLASS_LABELS.get(class_id, "차량"),
                        confidence=confidence,
                        point_x=point[0],
                        point_y=point[1],
                        gate_code=gate.code,
                        gate_name=gate.name,
                    )
                    if save_detailed_records:
                        events.append(event)
                        video_events.append(event)
                    gate_text = f" [{gate.name}]" if len(gates) >= 2 else ""
                    self.log(f"  {event.real_time}{gate_text} {direction} {event.class_name} (ID {crossing.track_id})")
                    if self.config.get("save_event_images", True):
                        snapshot = frame.copy()
                        cv2.polylines(snapshot, [polygon], True, (0, 220, 255), 2)
                        draw_gates(cv2, snapshot, gates, highlight=crossing.gate_index)
                        color = (255, 160, 40) if direction == INBOUND else (40, 130, 255)
                        cv2.rectangle(snapshot, (int(x1), int(y1)), (int(x2), int(y2)), color, 3)
                        gate_part = f"_{gate.code}" if len(gates) >= 2 else ""
                        image_name = f"{event.real_time.replace(':', '')}{gate_part}_{direction}_ID{crossing.track_id}_{video.stem}.jpg"
                        cv2.imwrite(str(event_image_dir / image_name), snapshot)

                record_trips(counter.drain_trips())

                if writer is not None and frame_index % review_stride == 0 and review_size is not None:
                    small = cv2.resize(frame, review_size, interpolation=cv2.INTER_AREA)
                    cv2.polylines(small, [np.round(polygon * review_scale).astype(np.int32)], True, (0, 220, 255), 1)
                    draw_gates(cv2, small, gates, scale=review_scale)
                    for track_id, _, _, _, (x1, y1, x2, y2) in track_details:
                        crossing = crossing_by_id.get(track_id)
                        color = (200, 200, 200)
                        if crossing is not None:
                            color = (255, 160, 40) if crossing.direction == INBOUND else (40, 130, 255)
                        p1 = (round(x1 * review_scale), round(y1 * review_scale))
                        p2 = (round(x2 * review_scale), round(y2 * review_scale))
                        cv2.rectangle(small, p1, p2, color, 2 if crossing is not None else 1)
                        cv2.putText(small, str(track_id), (p1[0], max(12, p1[1] - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)
                    total_in = sum(item[INBOUND] for item in counts.values())
                    total_out = sum(item[OUTBOUND] for item in counts.values())
                    cv2.putText(small, f"IN {total_in} / OUT {total_out}", (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (30, 255, 30), 2)
                    if len(gates) >= 2:
                        for index, gate in enumerate(gates):
                            gate_in = sum(gate_counts[s][index][INBOUND] for s in range(96))
                            gate_out = sum(gate_counts[s][index][OUTBOUND] for s in range(96))
                            cv2.putText(small, f"{gate.code} in {gate_in} out {gate_out}", (10, 46 + index * 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (30, 255, 30), 1)
                    writer.write(small)

                frame_index += 1
                processed_frames += 1
                if frame_index % 120 == 0:
                    self.progress(processed_frames / max(total_frames, 1))

            prefetcher.close()
            record_trips(counter.finish())
            stats["recoveries"] += counter.recovery_count
            stats["ignored"] += counter.ignored_crossings
            stats["frames"] += frame_index
            stats["videos"] += 1
            if writer is not None:
                writer.release()
            video_summary_path, video_events_path = write_video_results(
                output_dir,
                video,
                video_counts,
                video_events,
                save_detailed_records=save_detailed_records,
            )
            # 영상 하나가 끝날 때마다 전체 결과 파일을 갱신한다.
            write_all_outputs()
            if video_events_path is not None:
                self.log(f"  영상별 CSV 저장: {video_summary_path.name}, {video_events_path.name}")
            else:
                self.log(f"  영상별 집계 CSV 저장: {video_summary_path.name}")
            elapsed = max(1e-6, time.monotonic() - video_started)
            self.log(f"[{video_index}/{len(videos)}] 완료: {frame_index:,} 프레임 · {frame_index / elapsed:.1f} fps")

        write_all_outputs()
        self.progress(1.0 if not self.stop_event.is_set() else processed_frames / max(total_frames, 1))
        self.log(
            f"완료: 유입 {sum(item[INBOUND] for item in counts.values()):,}대 / "
            f"유출 {sum(item[OUTBOUND] for item in counts.values()):,}대"
        )
        self.log(f"요약: {self.report_path}")
        return summary_path, events_path


def build_report(
    config: dict,
    gates: Sequence[Gate],
    classifier: MovementClassifier,
    center: tuple[float, float] | None,
    counts: dict[int, dict[str, int]],
    gate_counts: dict[int, list[dict[str, int]]],
    movement_totals: dict[tuple[int | None, int | None], int],
    trip_mode: bool,
    stats: dict[str, Any],
    elapsed_seconds: float,
    device: str,
    half: bool,
    limit_seconds: float | None,
) -> str:
    """사람과 Claude가 짧게 읽을 수 있는 분석 요약과 점검 항목을 만든다."""
    total_in = sum(item[INBOUND] for item in counts.values())
    total_out = sum(item[OUTBOUND] for item in counts.values())
    fps = stats["frames"] / elapsed_seconds if elapsed_seconds > 0 else 0.0
    minutes, seconds = divmod(int(elapsed_seconds), 60)
    lines = [
        f"분석 요약 · {datetime.now().strftime('%Y-%m-%d %H:%M')}" + (" · 시험 분석" if limit_seconds else ""),
        f"설정: {INTERSECTION_TYPES.get(str(config.get('intersection_type') or ('2way' if len(gates) == 1 else 'custom')), '사용자 지정')} · 통과선 {len(gates)}개 · "
        f"모델 {config.get('model')} · 장치 {device}{' FP16' if half else ''} · 입력 {config.get('image_size', 960)}px",
        f"처리: 영상 {stats['videos']}개 · {stats['frames']:,}프레임 · {fps:.1f} fps · {minutes}분 {seconds}초",
        f"합계: 유입 {total_in:,} / 유출 {total_out:,}",
    ]
    gate_totals = []
    for index, gate in enumerate(gates):
        gate_in = sum(gate_counts[slot][index][INBOUND] for slot in range(96))
        gate_out = sum(gate_counts[slot][index][OUTBOUND] for slot in range(96))
        gate_totals.append((gate_in, gate_out))
    if len(gates) >= 2:
        lines.append("통과선별: " + " | ".join(
            f"{gate.code}({gate.name}) 유입 {gate_in} 유출 {gate_out}"
            for gate, (gate_in, gate_out) in zip(gates, gate_totals)
        ))
    else:
        lines.append(f"유입 방향: {screen_direction_text(gates[0])}")
    complete = no_exit = no_entry = 0
    if trip_mode:
        ranked = []
        for (entry, exit_), value in movement_totals.items():
            if entry is not None and exit_ is not None:
                complete += value
                ranked.append((value, entry, exit_))
            elif entry is not None:
                no_exit += value
            else:
                no_entry += value
        ranked.sort(reverse=True)
        if ranked:
            lines.append(f"방향별 상위(완료 {complete}건): " + ", ".join(
                f"{gates[entry].name}→{gates[exit_].name} {classifier.label(entry, exit_)} {value}"
                for value, entry, exit_ in ranked[:8]
            ))
        all_trips = complete + no_exit + no_entry
        share = (no_exit + no_entry) / all_trips * 100 if all_trips else 0.0
        lines.append(f"미확인 이동: 도착 미확인 {no_exit} · 출발 미확인 {no_entry} ({share:.0f}%)")
    lines.append(f"ID 재연결 {stats['recoveries']}회 · 무시한 중복 통과 {stats['ignored']}회 · 가려진 바닥점 제외 {stats.get('occluded', 0):,}건"
                 + (" · FP16→FP32 전환됨" if stats.get("half_fallback") else ""))

    warnings: list[str] = []
    if total_in + total_out == 0:
        warnings.append("집계된 차량이 없습니다. 영상 폴더, ROI, 통과선 위치를 확인하세요.")
    if len(gates) == 1 and total_in + total_out >= 10 and min(total_in, total_out) == 0:
        warnings.append("한 방향만 집계되었습니다. 유입/유출 방향 설정과 통과선 위치를 확인하세요.")
    if len(gates) >= 2:
        busiest = max(gate_in + gate_out for gate_in, gate_out in gate_totals)
        for gate, (gate_in, gate_out) in zip(gates, gate_totals):
            if busiest >= 10 and gate_in + gate_out == 0:
                warnings.append(f"통과선 {gate.code}({gate.name})에서 집계가 없습니다. 위치·ROI 포함 여부를 확인하세요.")
            if center is not None and side_of(gate.a, gate.b, center) not in (0, gate.inside_side):
                warnings.append(f"통과선 {gate.code}({gate.name})의 유입 화살표가 교차로 중심 반대쪽입니다. 의도한 설정인지 확인하세요.")
    if trip_mode and complete + no_exit + no_entry >= 10:
        if (no_exit + no_entry) / (complete + no_exit + no_entry) > 0.3:
            warnings.append("미확인 이동이 30%를 넘습니다. ROI가 통과선 바깥 도로까지 충분히 덮는지, 통과선이 교차로 입구에 있는지 확인하세요.")
    if device != "cpu" and stats["frames"] >= 1000 and fps < 12:
        warnings.append("처리 속도가 낮습니다. GPU 사용, FP16, 입력 크기(image_size)를 확인하세요.")
    lines.append("점검: " + (" / ".join(warnings) if warnings else "특이사항 없음"))
    return "\n".join(lines) + "\n"
