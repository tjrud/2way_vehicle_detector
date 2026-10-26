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
from dataclasses import dataclass, field
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


def class_order(config: dict, seen: Iterable[str] = ()) -> list[str]:
    """차종 열 순서: 설정한 분석 대상 차종 순서, 그 뒤에 실제로 나온 기타 차종."""
    names: list[str] = []
    for value in config.get("vehicle_classes", VEHICLE_CLASSES):
        name = CLASS_LABELS.get(int(value), "차량")
        if name not in names:
            names.append(name)
    for name in seen:
        if name not in names:
            names.append(name)
    return names


def write_class_summary(
    path: Path,
    class_counts: dict[int, dict[str, dict[str, int]]],
    names: Sequence[str],
) -> None:
    """15분 구간마다 차종별 유입·유출을 저장한다. 마지막 행은 합계."""
    totals = {name: {INBOUND: 0, OUTBOUND: 0} for name in names}
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        header = ["시간"]
        for name in names:
            header += [f"{name} 유입", f"{name} 유출"]
        writer.writerow(header + ["유입 합계", "유출 합계"])
        for slot in range(96):
            row: list[Any] = [slot_label(slot)]
            slot_in = slot_out = 0
            for name in names:
                value = class_counts[slot].get(name, {INBOUND: 0, OUTBOUND: 0})
                row += [value[INBOUND], value[OUTBOUND]]
                totals[name][INBOUND] += value[INBOUND]
                totals[name][OUTBOUND] += value[OUTBOUND]
                slot_in += value[INBOUND]
                slot_out += value[OUTBOUND]
            writer.writerow(row + [slot_in, slot_out])
        total_row: list[Any] = ["합계"]
        for name in names:
            total_row += [totals[name][INBOUND], totals[name][OUTBOUND]]
        writer.writerow(total_row + [sum(v[INBOUND] for v in totals.values()), sum(v[OUTBOUND] for v in totals.values())])


def rebuild_class_summary(output_dir: Path, config: dict | None = None) -> Path:
    """이미 저장된 차량별_통과기록.csv로 15분_차종별_집계.csv를 다시 만든다(재분석 불필요)."""
    class_counts: dict[int, dict[str, dict[str, int]]] = {slot: {} for slot in range(96)}
    labels = {slot_label(slot): slot for slot in range(96)}
    seen: list[str] = []
    with (output_dir / "차량별_통과기록.csv").open("r", newline="", encoding="utf-8-sig") as file:
        for row in csv.DictReader(file):
            slot = labels[row["15분구간"]]
            name = row["차종"] or "차량"
            if name not in seen:
                seen.append(name)
            bucket = class_counts[slot].setdefault(name, {INBOUND: 0, OUTBOUND: 0})
            bucket[row["방향"]] += 1
    path = output_dir / "15분_차종별_집계.csv"
    write_class_summary(path, class_counts, class_order(config or {}, seen))
    return path


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


@dataclass
class TrackedBox:
    """원본 영상 좌표로 복원한 추적 박스 하나."""

    track_id: int
    point: tuple[float, float]
    class_id: int
    confidence: float
    box: tuple[float, float, float, float]


@dataclass
class VideoClock:
    """영상 프레임 번호를 실제 시각(초)과 15분 구간으로 바꾼다."""

    fps: float
    start_seconds: float
    speed_factor: float

    def at(self, frame: int) -> tuple[float, float]:
        """(영상 내 초, 자정을 순환하는 실제 시각 초)."""
        video_seconds = frame / self.fps
        return video_seconds, (self.start_seconds + video_seconds * self.speed_factor) % 86400

    def slot(self, frame: int) -> int:
        """프레임이 속한 0~95 15분 구간."""
        return int(self.at(frame)[1] // 900)


class RoiWindow:
    """ROI 크롭 범위와 마스크. 추론은 잘라낸 좌표계로, 판정·기록은 원본 좌표로 한다."""

    def __init__(
        self,
        cv2: Any,
        np: Any,
        roi_points: list[list[int]],
        polygon: Any,
        frame_width: int,
        frame_height: int,
        crop_enabled: bool,
        padding_px: int,
    ) -> None:
        if crop_enabled:
            self.left, self.top, self.right, self.bottom = roi_crop_bounds(frame_width, frame_height, roi_points, padding_px)
        else:
            self.left, self.top, self.right, self.bottom = 0, 0, frame_width, frame_height
        shifted = polygon - np.array([self.left, self.top], dtype=np.int32)
        mask = np.zeros((self.bottom - self.top, self.right - self.left), dtype=np.uint8)
        cv2.fillPoly(mask, [shifted], 255)
        self.mask_bgr = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR)
        self.buffer = np.empty_like(self.mask_bgr)
        self._cv2 = cv2

    @property
    def size_text(self) -> str:
        return f"{self.right - self.left}x{self.bottom - self.top}"

    def masked(self, frame: Any) -> Any:
        """프레임을 ROI 범위로 자르고 ROI 밖을 검게 지운 재사용 버퍼를 반환한다."""
        cropped = frame[self.top:self.bottom, self.left:self.right]
        self._cv2.bitwise_and(cropped, self.mask_bgr, dst=self.buffer)
        return self.buffer


@dataclass
class DetectionFilter:
    """최소 크기, ROI 포함 여부, 앞차에 가려진 바닥점 기준으로 추적 박스를 거른다."""

    roi_points: list[list[int]]
    min_width: float
    min_height: float
    min_area: float
    occluded_limit: float

    @classmethod
    def from_config(cls, config: dict, scale: float) -> "DetectionFilter":
        return cls(
            roi_points=config["roi_polygon"],
            min_width=float(config.get("min_box_width_px", 35)) * scale,
            min_height=float(config.get("min_box_height_px", 35)) * scale,
            min_area=float(config.get("min_box_area_px", 2000)) * scale * scale,
            occluded_limit=float(config.get("occluded_bottom_ratio", 0.5) or 0),
        )

    def tracked_boxes(self, rows: Iterable[Sequence[float]], window: RoiWindow) -> list[TrackedBox]:
        """[x1, y1, x2, y2, id, cls, conf] 행을 원본 좌표 TrackedBox로 바꾸고 작은 박스·ROI 밖을 뺀다."""
        boxes: list[TrackedBox] = []
        for x1, y1, x2, y2, track_id, class_id, confidence in rows:
            x1, y1, x2, y2 = map(float, (x1, y1, x2, y2))
            if not box_meets_minimum_size((x1, y1, x2, y2), self.min_width, self.min_height, self.min_area):
                continue
            x1 += window.left
            x2 += window.left
            y1 += window.top
            y2 += window.top
            point = ((x1 + x2) / 2, y2)
            if not point_in_polygon(point, self.roi_points):
                continue
            boxes.append(TrackedBox(int(track_id), point, int(class_id), float(confidence), (x1, y1, x2, y2)))
        return boxes

    def judged(self, boxes: list[TrackedBox]) -> list[TrackedBox]:
        """통과 판정에 쓸 박스: 앞차가 아래 변을 가린 박스는 이 프레임에서 뺀다."""
        if self.occluded_limit <= 0 or len(boxes) <= 1:
            return boxes
        rects = [item.box for item in boxes]
        return [
            item for index, item in enumerate(boxes)
            if hidden_bottom_ratio(item.box, rects[:index] + rects[index + 1:]) < self.occluded_limit
        ]


class ResultBook:
    """모든 영상에 걸친 집계를 누적하고 결과 파일과 요약을 쓴다."""

    def __init__(
        self,
        config: dict,
        gates: Sequence[Gate],
        classifier: MovementClassifier,
        center: tuple[float, float] | None,
        output_dir: Path,
        trip_mode: bool,
    ) -> None:
        self.config = config
        self.gates = list(gates)
        self.classifier = classifier
        self.center = center
        self.trip_mode = trip_mode
        self.save_detailed = bool(config.get("save_detailed_records", True))
        self.counts = empty_counts()
        self.gate_counts = {slot: [{INBOUND: 0, OUTBOUND: 0} for _ in self.gates] for slot in range(96)}
        self.class_counts: dict[int, dict[str, dict[str, int]]] = {slot: {} for slot in range(96)}
        self.seen_classes: list[str] = []
        self.movement_counts: dict[int, dict[tuple[int | None, int | None], int]] = {slot: {} for slot in range(96)}
        self.movement_totals: dict[tuple[int | None, int | None], int] = {}
        self.events: list[CountEvent] = []
        self.trip_records: list[TripRecord] = []
        self.stats: dict[str, Any] = {
            "frames": 0, "recoveries": 0, "ignored": 0, "occluded": 0, "half_fallback": False, "videos": 0,
        }
        self.summary_path = output_dir / "15분_유입유출_집계.csv"
        self.events_path = output_dir / "차량별_통과기록.csv" if self.save_detailed else None
        self.gate_summary_path = output_dir / "15분_접근로별_집계.csv"
        self.class_summary_path = output_dir / "15분_차종별_집계.csv"
        self.movement_path = output_dir / "15분_방향별_집계.csv"
        self.od_path = output_dir / "OD_행렬.csv"
        self.trips_path = output_dir / "차량별_이동경로.csv"
        self.report_path = output_dir / "분석요약.txt"

    # ------------------------------------------------------------------ 누적
    def add_crossing(self, event: CountEvent, gate_index: int) -> None:
        """통과 한 건을 전체·통과선별·차종별 15분 집계와 상세 기록에 더한다."""
        slot, direction = event.slot, event.direction
        self.counts[slot][direction] += 1
        self.gate_counts[slot][gate_index][direction] += 1
        if event.class_name not in self.seen_classes:
            self.seen_classes.append(event.class_name)
        self.class_counts[slot].setdefault(event.class_name, {INBOUND: 0, OUTBOUND: 0})[direction] += 1
        if self.save_detailed:
            self.events.append(event)

    def add_trip(self, video_name: str, trip: Trip, clock: VideoClock) -> None:
        """마감된 이동 한 건을 진입 시각(없으면 진출 시각) 구간의 방향별 집계에 더한다."""
        slot = clock.slot(max(0, trip.reference_frame))
        key = (trip.entry_gate, trip.exit_gate)
        self.movement_counts[slot][key] = self.movement_counts[slot].get(key, 0) + 1
        self.movement_totals[key] = self.movement_totals.get(key, 0) + 1
        if self.save_detailed:
            self.trip_records.append(TripRecord(
                video=video_name,
                trip=trip,
                movement=self.classifier.label(trip.entry_gate, trip.exit_gate),
                class_name=CLASS_LABELS.get(trip.class_id, "차량") if trip.class_id is not None else "차량",
                slot=slot,
                entry_seconds=clock.at(trip.entry.frame)[1] if trip.entry is not None else None,
                exit_seconds=clock.at(trip.exit.frame)[1] if trip.exit is not None else None,
            ))

    def add_counter_stats(self, counter: IntersectionCounter) -> None:
        """판정기 하나가 끝날 때 재연결·무시 횟수를 누적한다."""
        self.stats["recoveries"] += counter.recovery_count
        self.stats["ignored"] += counter.ignored_crossings

    # ------------------------------------------------------------------ 합계
    def totals(self) -> tuple[int, int]:
        return (
            sum(item[INBOUND] for item in self.counts.values()),
            sum(item[OUTBOUND] for item in self.counts.values()),
        )

    def gate_totals(self) -> list[tuple[int, int]]:
        return [
            (
                sum(self.gate_counts[slot][index][INBOUND] for slot in range(96)),
                sum(self.gate_counts[slot][index][OUTBOUND] for slot in range(96)),
            )
            for index in range(len(self.gates))
        ]

    def class_names(self) -> list[str]:
        return class_order(self.config, self.seen_classes)

    def class_totals(self) -> dict[str, tuple[int, int]]:
        return {
            name: (
                sum(self.class_counts[slot].get(name, {}).get(INBOUND, 0) for slot in range(96)),
                sum(self.class_counts[slot].get(name, {}).get(OUTBOUND, 0) for slot in range(96)),
            )
            for name in self.class_names()
        }

    # ------------------------------------------------------------------ 저장
    def write_all(self, elapsed_seconds: float, device: str, half: bool, limit_seconds: float | None) -> dict[str, str]:
        """지금까지의 전체 결과 파일과 요약을 쓰고 {종류: 경로}를 반환한다."""
        write_summary(self.summary_path, self.counts)
        outputs = {"summary": str(self.summary_path)}
        if self.events_path is not None:
            write_events(self.events_path, self.events)
            outputs["events"] = str(self.events_path)
        write_class_summary(self.class_summary_path, self.class_counts, self.class_names())
        outputs["classes"] = str(self.class_summary_path)
        if len(self.gates) >= 2:
            write_gate_summary(self.gate_summary_path, self.gate_counts, self.gates)
            outputs["gates"] = str(self.gate_summary_path)
        if self.trip_mode:
            write_movement_summary(self.movement_path, self.movement_counts, self.gates, self.classifier)
            write_od_matrix(self.od_path, self.movement_totals, self.gates)
            outputs.update({"movements": str(self.movement_path), "od": str(self.od_path)})
            if self.save_detailed:
                write_trips(self.trips_path, self.trip_records, self.gates)
                outputs["trips"] = str(self.trips_path)
        report = build_report(self, elapsed_seconds, device, half, limit_seconds)
        self.report_path.write_text(report, encoding="utf-8-sig")
        outputs["report"] = str(self.report_path)
        return outputs


class ReviewVideo:
    """검수용 축소 영상. stride 프레임마다 ROI·통과선·박스·누적 수량을 그려 저장한다."""

    def __init__(self, cv2: Any, np: Any, path: Path, fps: float, frame_width: int, frame_height: int, config: dict) -> None:
        width = min(frame_width, int(config.get("review_width", 640)))
        self.scale = width / frame_width
        self.size = (width, round(frame_height * self.scale))
        self.stride = max(1, int(config.get("review_stride", 3)))
        self.writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps / self.stride, self.size)
        self._cv2 = cv2
        self._np = np

    def write(
        self,
        frame_index: int,
        frame: Any,
        polygon: Any,
        boxes: Sequence[TrackedBox],
        crossing_by_id: dict[int, GateCrossing],
        book: ResultBook,
    ) -> None:
        if frame_index % self.stride != 0:
            return
        cv2, np = self._cv2, self._np
        small = cv2.resize(frame, self.size, interpolation=cv2.INTER_AREA)
        cv2.polylines(small, [np.round(polygon * self.scale).astype(np.int32)], True, (0, 220, 255), 1)
        draw_gates(cv2, small, book.gates, scale=self.scale)
        for item in boxes:
            crossing = crossing_by_id.get(item.track_id)
            color = (200, 200, 200)
            if crossing is not None:
                color = direction_color(crossing.direction)
            x1, y1, x2, y2 = item.box
            p1 = (round(x1 * self.scale), round(y1 * self.scale))
            p2 = (round(x2 * self.scale), round(y2 * self.scale))
            cv2.rectangle(small, p1, p2, color, 2 if crossing is not None else 1)
            cv2.putText(small, str(item.track_id), (p1[0], max(12, p1[1] - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.4, color, 1)
        total_in, total_out = book.totals()
        cv2.putText(small, f"IN {total_in} / OUT {total_out}", (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (30, 255, 30), 2)
        if len(book.gates) >= 2:
            for index, (gate, (gate_in, gate_out)) in enumerate(zip(book.gates, book.gate_totals())):
                cv2.putText(small, f"{gate.code} in {gate_in} out {gate_out}", (10, 46 + index * 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (30, 255, 30), 1)
        self.writer.write(small)

    def release(self) -> None:
        self.writer.release()


def direction_color(direction: str) -> tuple[int, int, int]:
    """유입은 파랑, 유출은 주황(BGR)."""
    return (255, 160, 40) if direction == INBOUND else (40, 130, 255)


@dataclass
class _RunContext:
    """한 번의 분석 실행 동안 공유하는 모델·설정·누적 결과."""

    cv2: Any
    np: Any
    torch: Any
    output_dir: Path
    event_image_dir: Path
    gates: list[Gate]
    polygon: Any
    book: ResultBook
    model: Any
    model_path: str
    device: str
    track_options: dict[str, Any]
    total_frames: int
    started_at: float
    limit_seconds: float | None
    processed_frames: int = 0


@dataclass
class _VideoState:
    """영상 하나를 분석하는 동안의 판정기·좌표 변환·영상별 결과."""

    video: Path
    clock: VideoClock
    scale: float
    counter: IntersectionCounter
    window: RoiWindow
    detections: DetectionFilter
    review: ReviewVideo | None
    counts: dict[int, dict[str, int]] = field(default_factory=empty_counts)
    events: list[CountEvent] = field(default_factory=list)


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

    # ------------------------------------------------------------------ 실행 준비
    def _output_dir(self) -> Path:
        output_dir = Path(self.config.get("output_dir", "results"))
        if not output_dir.is_absolute():
            output_dir = self.config_dir / output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        if self.config.get("save_event_images", True):
            (output_dir / "event_images").mkdir(parents=True, exist_ok=True)
        return output_dir

    def _videos(self, video_paths: list[Path] | None) -> list[Path]:
        source_dir = Path(self.config["source_dir"])
        videos = video_paths if video_paths is not None else sorted(source_dir.glob("*.mp4"))
        if not videos:
            raise FileNotFoundError(f"분석할 MP4 파일이 없습니다: {source_dir}")
        return videos

    def _device(self, torch: Any) -> tuple[str, bool]:
        """요청 장치가 GPU인데 CUDA가 없으면 CPU로 바꾸고 FP16 사용 여부를 정한다."""
        requested = str(self.config.get("device", "0"))
        cuda_ready = torch is not None and torch.cuda.is_available()
        device = requested if requested == "cpu" or cuda_ready else "cpu"
        if device != requested:
            self.log("GPU를 찾지 못해 CPU로 분석합니다. 매우 느릴 수 있습니다.")
        return device, bool(self.config.get("half_precision", True)) and device != "cpu"

    def _tracker_config(self) -> str:
        tracker_config = str(self.config.get("tracker", "botsort_reid.yaml"))
        tracker_path = Path(tracker_config)
        if not tracker_path.is_absolute() and (self.config_dir / tracker_path).exists():
            tracker_config = str(self.config_dir / tracker_path)
        return tracker_config

    @staticmethod
    def _scan_videos(cv2: Any, videos: list[Path], limit_seconds: float | None) -> list[tuple[Path, int, float]]:
        """전체 진행률 계산을 위해 영상별 처리 프레임 수와 fps를 먼저 읽는다."""
        metadata: list[tuple[Path, int, float]] = []
        for video in videos:
            capture = cv2.VideoCapture(str(video))
            frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
            fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
            capture.release()
            if limit_seconds is not None:
                frames = min(frames, int(limit_seconds * fps))
            metadata.append((video, frames, fps))
        return metadata

    # ------------------------------------------------------------------ 실행
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
        output_dir = self._output_dir()
        videos = self._videos(video_paths)

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

        device, half = self._device(torch)
        model_path = str(self.config["model"])
        self.log(f"모델 불러오는 중: {model_path} / 장치: {device}{' / FP16' if half else ''}")
        model = self._load_model(model_path)
        tracker_config = self._tracker_config()
        self.log(f"추적기: {tracker_config}")

        book = ResultBook(self.config, gates, classifier, center, output_dir, trip_mode)
        self.report_path = book.report_path
        metadata = self._scan_videos(cv2, videos, limit_seconds)
        run = _RunContext(
            cv2=cv2,
            np=np,
            torch=torch,
            output_dir=output_dir,
            event_image_dir=output_dir / "event_images",
            gates=gates,
            polygon=np.array(roi_points, dtype=np.int32),
            book=book,
            model=model,
            model_path=model_path,
            device=device,
            track_options={
                "persist": True,
                "tracker": tracker_config,
                "conf": float(self.config.get("confidence", 0.22)),
                "iou": float(self.config.get("iou", 0.65)),
                "imgsz": int(self.config.get("image_size", 960)),
                "classes": [int(value) for value in self.config.get("vehicle_classes", VEHICLE_CLASSES)],
                "device": device,
                "half": half,
                "verbose": False,
            },
            total_frames=sum(frames for _, frames, _ in metadata),
            started_at=started_at,
            limit_seconds=limit_seconds,
        )

        for video_index, (video, expected_frames, fps) in enumerate(metadata, start=1):
            if self.stop_event.is_set():
                break
            self._analyze_video(run, f"{video_index}/{len(videos)}", video, expected_frames, fps)

        self._write_outputs(run)
        self.progress(1.0 if not self.stop_event.is_set() else run.processed_frames / max(run.total_frames, 1))
        total_in, total_out = book.totals()
        self.log(f"완료: 유입 {total_in:,}대 / 유출 {total_out:,}대")
        self.log(f"요약: {self.report_path}")
        return book.summary_path, book.events_path

    def _write_outputs(self, run: _RunContext) -> None:
        """완료된 영상까지의 전체 결과 파일과 요약을 갱신한다."""
        self.outputs = run.book.write_all(
            time.monotonic() - run.started_at, run.device, run.track_options["half"], run.limit_seconds,
        )

    def _open_video(self, run: _RunContext, video: Path, fps: float) -> tuple[Any, _VideoState]:
        """영상을 열고 해상도에 맞춘 판정기·ROI 크롭·박스 필터·검수 영상을 준비한다."""
        cv2, np = run.cv2, run.np
        clock = VideoClock(fps, video_start_seconds(video.name), float(self.config.get("speed_factor", 3.0)))
        capture = cv2.VideoCapture(str(video))
        frame_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        frame_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        scale = resolve_pixel_scale(self.config, frame_height)
        counter = build_counter(self.config, run.gates, scale)
        window = RoiWindow(
            cv2, np, self.config["roi_polygon"], run.polygon, frame_width, frame_height,
            bool(self.config.get("roi_crop_enabled", True)),
            round(int(self.config.get("roi_crop_padding_px", 64)) * scale),
        )
        self.log(f"  ROI 크롭: {window.size_text} (원본 {frame_width}x{frame_height}, 화면 배율 {scale:.2f})")
        review = None
        if self.config.get("save_review_video", False):
            review = ReviewVideo(cv2, np, run.output_dir / f"검수_{video.stem}.mp4", fps, frame_width, frame_height, self.config)
        state = _VideoState(
            video=video,
            clock=clock,
            scale=scale,
            counter=counter,
            window=window,
            detections=DetectionFilter.from_config(self.config, scale),
            review=review,
        )
        return capture, state

    def _analyze_video(self, run: _RunContext, position: str, video: Path, expected_frames: int, fps: float) -> None:
        """영상 하나를 프레임 단위로 추적·판정하고 영상별·전체 결과를 저장한다."""
        self.log(f"[{position}] {video.name} 분석 시작")
        if reset_model_tracking_state(run.model):
            self.log("  영상 전환: 추적기 상태 초기화")
        capture, state = self._open_video(run, video, fps)

        frame_index = 0
        video_started = time.monotonic()
        prefetcher = FramePrefetcher(capture, expected_frames, int(self.config.get("frame_prefetch_buffer", 8)), self.stop_event)
        for frame in prefetcher:
            if self.stop_event.is_set():
                break
            self._process_frame(run, state, frame, frame_index)
            frame_index += 1
            run.processed_frames += 1
            if frame_index % 120 == 0:
                self.progress(run.processed_frames / max(run.total_frames, 1))

        prefetcher.close()
        for trip in state.counter.finish():
            run.book.add_trip(video.name, trip, state.clock)
        run.book.add_counter_stats(state.counter)
        run.book.stats["frames"] += frame_index
        run.book.stats["videos"] += 1
        if state.review is not None:
            state.review.release()
        video_summary_path, video_events_path = write_video_results(
            run.output_dir, video, state.counts, state.events, save_detailed_records=run.book.save_detailed,
        )
        # 영상 하나가 끝날 때마다 전체 결과 파일을 갱신한다.
        self._write_outputs(run)
        if video_events_path is not None:
            self.log(f"  영상별 CSV 저장: {video_summary_path.name}, {video_events_path.name}")
        else:
            self.log(f"  영상별 집계 CSV 저장: {video_summary_path.name}")
        elapsed = max(1e-6, time.monotonic() - video_started)
        self.log(f"[{position}] 완료: {frame_index:,} 프레임 · {frame_index / elapsed:.1f} fps")

    def _process_frame(self, run: _RunContext, state: _VideoState, frame: Any, frame_index: int) -> None:
        """한 프레임: 추적 → 박스 필터 → 통과 판정 → 기록·이미지 → 이동 마감 → 검수 영상."""
        result = self._track(run, state, state.window.masked(frame))
        boxes = state.detections.tracked_boxes(extract_box_rows(result.boxes, run.torch), state.window)
        judged = state.detections.judged(boxes)
        run.book.stats["occluded"] += len(boxes) - len(judged)
        # 한 프레임의 모든 ID를 함께 전달해야 동시 가림을 일대일로 연결할 수 있다.
        # 차량이 없는 프레임도 전달해야 사라진 ID가 제때 정리된다.
        crossings = state.counter.process_frame(
            [(item.track_id, item.point, item.class_id, item.confidence) for item in judged],
            frame_index,
        )
        for new_id, old_id in state.counter.last_recoveries.items():
            self.log(f"  가림 ID 연결: {old_id} → {new_id}")

        boxes_by_id = {item.track_id: item for item in boxes}
        crossing_by_id: dict[int, GateCrossing] = {}
        for crossing in crossings:
            crossing_by_id[crossing.track_id] = crossing
            self._record_crossing(run, state, frame, frame_index, crossing, boxes_by_id[crossing.track_id])
        for trip in state.counter.drain_trips():
            run.book.add_trip(state.video.name, trip, state.clock)
        if state.review is not None:
            state.review.write(frame_index, frame, run.polygon, boxes, crossing_by_id, run.book)

    def _track(self, run: _RunContext, state: _VideoState, image: Any) -> Any:
        """YOLO 추적을 실행한다. FP16 오류가 나면 FP32 모델과 새 판정기로 한 번 다시 시도한다."""
        try:
            return run.model.track(image, **run.track_options)[0]
        except Exception as error:
            if not run.track_options["half"]:
                raise
            self.log(f"  FP16 추론 오류로 FP32로 전환합니다: {error}")
            run.track_options["half"] = False
            run.book.stats["half_fallback"] = True
            run.model = self._load_model(run.model_path)
            for trip in state.counter.finish():
                run.book.add_trip(state.video.name, trip, state.clock)
            run.book.add_counter_stats(state.counter)
            state.counter = build_counter(self.config, run.gates, state.scale)
            return run.model.track(image, **run.track_options)[0]

    def _record_crossing(
        self,
        run: _RunContext,
        state: _VideoState,
        frame: Any,
        frame_index: int,
        crossing: GateCrossing,
        box: TrackedBox,
    ) -> None:
        """통과 한 건을 집계·로그·통과 순간 이미지로 남긴다."""
        gate = run.gates[crossing.gate_index]
        class_id = crossing.class_id if crossing.class_id is not None else box.class_id
        video_seconds, real_seconds = state.clock.at(frame_index)
        event = CountEvent(
            video=state.video.name,
            frame=frame_index,
            video_seconds=video_seconds,
            real_seconds=real_seconds,
            slot=int(real_seconds // 900),
            direction=crossing.direction,
            track_id=crossing.track_id,
            class_id=class_id,
            class_name=CLASS_LABELS.get(class_id, "차량"),
            confidence=box.confidence,
            point_x=box.point[0],
            point_y=box.point[1],
            gate_code=gate.code,
            gate_name=gate.name,
        )
        state.counts[event.slot][event.direction] += 1
        if run.book.save_detailed:
            state.events.append(event)
        run.book.add_crossing(event, crossing.gate_index)
        gate_text = f" [{gate.name}]" if len(run.gates) >= 2 else ""
        self.log(f"  {event.real_time}{gate_text} {event.direction} {event.class_name} (ID {crossing.track_id})")
        if self.config.get("save_event_images", True):
            self._save_snapshot(run, frame, event, crossing.gate_index, box)

    @staticmethod
    def _save_snapshot(run: _RunContext, frame: Any, event: CountEvent, gate_index: int, box: TrackedBox) -> None:
        """통과 순간 원본 프레임에 ROI·통과선·차량 박스를 그려 저장한다."""
        cv2 = run.cv2
        snapshot = frame.copy()
        cv2.polylines(snapshot, [run.polygon], True, (0, 220, 255), 2)
        draw_gates(cv2, snapshot, run.gates, highlight=gate_index)
        x1, y1, x2, y2 = box.box
        cv2.rectangle(snapshot, (int(x1), int(y1)), (int(x2), int(y2)), direction_color(event.direction), 3)
        gate_part = f"_{event.gate_code}" if len(run.gates) >= 2 else ""
        stem = Path(event.video).stem
        image_name = f"{event.real_time.replace(':', '')}{gate_part}_{event.direction}_ID{event.track_id}_{stem}.jpg"
        cv2.imwrite(str(run.event_image_dir / image_name), snapshot)


def build_report(
    book: ResultBook,
    elapsed_seconds: float,
    device: str,
    half: bool,
    limit_seconds: float | None,
) -> str:
    """사람과 Claude가 짧게 읽을 수 있는 분석 요약과 점검 항목을 만든다."""
    config, gates, stats = book.config, book.gates, book.stats
    total_in, total_out = book.totals()
    fps = stats["frames"] / elapsed_seconds if elapsed_seconds > 0 else 0.0
    minutes, seconds = divmod(int(elapsed_seconds), 60)
    shape = str(config.get("intersection_type") or ("2way" if len(gates) == 1 else "custom"))
    lines = [
        f"분석 요약 · {datetime.now().strftime('%Y-%m-%d %H:%M')}" + (" · 시험 분석" if limit_seconds else ""),
        f"설정: {INTERSECTION_TYPES.get(shape, '사용자 지정')} · 통과선 {len(gates)}개 · "
        f"모델 {config.get('model')} · 장치 {device}{' FP16' if half else ''} · 입력 {config.get('image_size', 960)}px",
        f"처리: 영상 {stats['videos']}개 · {stats['frames']:,}프레임 · {fps:.1f} fps · {minutes}분 {seconds}초",
        f"합계: 유입 {total_in:,} / 유출 {total_out:,}",
    ]
    gate_totals = book.gate_totals()
    if len(gates) >= 2:
        lines.append("통과선별: " + " | ".join(
            f"{gate.code}({gate.name}) 유입 {gate_in} 유출 {gate_out}"
            for gate, (gate_in, gate_out) in zip(gates, gate_totals)
        ))
    else:
        lines.append(f"유입 방향: {screen_direction_text(gates[0])}")
    class_totals = [(name, values) for name, values in book.class_totals().items() if sum(values)]
    if class_totals:
        lines.append("차종별: " + " | ".join(f"{name} 유입 {i} 유출 {o}" for name, (i, o) in class_totals))
    complete = no_exit = no_entry = 0
    if book.trip_mode:
        ranked = []
        for (entry, exit_), value in book.movement_totals.items():
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
                f"{gates[entry].name}→{gates[exit_].name} {book.classifier.label(entry, exit_)} {value}"
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
            if book.center is not None and side_of(gate.a, gate.b, book.center) not in (0, gate.inside_side):
                warnings.append(f"통과선 {gate.code}({gate.name})의 유입 화살표가 교차로 중심 반대쪽입니다. 의도한 설정인지 확인하세요.")
    if book.trip_mode and complete + no_exit + no_entry >= 10:
        if (no_exit + no_entry) / (complete + no_exit + no_entry) > 0.3:
            warnings.append("미확인 이동이 30%를 넘습니다. ROI가 통과선 바깥 도로까지 충분히 덮는지, 통과선이 교차로 입구에 있는지 확인하세요.")
    if device != "cpu" and stats["frames"] >= 1000 and fps < 12:
        warnings.append("처리 속도가 낮습니다. GPU 사용, FP16, 입력 크기(image_size)를 확인하세요.")
    lines.append("점검: " + (" / ".join(warnings) if warnings else "특이사항 없음"))
    return "\n".join(lines) + "\n"
