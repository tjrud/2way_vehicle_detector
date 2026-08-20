"""YOLO 추적 결과를 유입·유출 이벤트와 15분 단위 CSV로 변환한다.

이 모듈은 UI에 의존하지 않는 핵심 분석 계층이다. ROI 크롭, 프레임 선행
디코딩, 최소 박스 필터, 통과선 판정, ID 조각 재연결과 결과 저장을 담당한다.
"""

from __future__ import annotations

import csv
import json
import math
import queue
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator


VEHICLE_CLASSES = [2, 3, 5, 7]  # COCO: car, motorcycle, bus, truck
CLASS_LABELS = {1: "자전거", 2: "승용차", 3: "오토바이", 5: "버스", 7: "트럭"}
VIDEO_PATTERN = re.compile(r"_(\d{2})(\d{2})-(\d{2})(\d{2})_")


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
class TrackState:
    """하나의 추적 ID에 대해 통과선 측면, 이동 속도와 집계 여부를 보존한다."""
    stable_side: int = 0
    candidate_side: int = 0
    candidate_frames: int = 0
    candidate_min_normal_speed: float = 0.0
    anchor_point: tuple[float, float] | None = None
    last_point: tuple[float, float] | None = None
    velocity: tuple[float, float] = (0.0, 0.0)
    last_frame: int = -1
    class_id: int | None = None
    counted: bool = False


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

    @property
    def real_time(self) -> str:
        """자정을 순환하는 실제 영상 시각을 HH:MM:SS로 표시한다."""
        total = int(self.real_seconds) % 86400
        return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


class DirectionCounter:
    """Track one crossing per ID through a finite line segment."""

    def __init__(
        self,
        line: list[list[int]] | list[tuple[int, int]],
        deadband_px: float = 8.0,
        min_stable_frames: int = 3,
        min_horizontal_motion_px: float = 12.0,
        line_margin_px: float = 35.0,
        increasing_x_direction: str = "유출",
        id_stitch_enabled: bool = True,
        id_stitch_max_gap: int = 90,
        id_stitch_max_distance_px: float = 140.0,
        id_stitch_line_distance_px: float = 160.0,
        id_stitch_direction_tolerance_px: float = 12.0,
        fast_crossing_enabled: bool = True,
        fast_crossing_min_normal_speed_px: float = 15.0,
        fast_crossing_stable_frames: int = 2,
    ) -> None:
        if len(line) != 2:
            raise ValueError("counting_line must contain exactly two points")
        self.a = (float(line[0][0]), float(line[0][1]))
        self.b = (float(line[1][0]), float(line[1][1]))
        self.length = math.dist(self.a, self.b)
        if self.length < 10:
            raise ValueError("counting_line is too short")
        self.deadband_px = deadband_px
        self.min_stable_frames = max(1, min_stable_frames)
        self.min_horizontal_motion_px = min_horizontal_motion_px
        self.line_margin_ratio = line_margin_px / self.length
        self.increasing_x_direction = increasing_x_direction
        self.id_stitch_enabled = id_stitch_enabled
        self.id_stitch_max_gap = max(1, id_stitch_max_gap)
        self.id_stitch_max_distance_px = max(1.0, id_stitch_max_distance_px)
        self.id_stitch_line_distance_px = max(1.0, id_stitch_line_distance_px)
        self.id_stitch_direction_tolerance_px = max(0.0, id_stitch_direction_tolerance_px)
        self.fast_crossing_enabled = fast_crossing_enabled
        self.fast_crossing_min_normal_speed_px = max(0.0, fast_crossing_min_normal_speed_px)
        self.fast_crossing_stable_frames = min(
            self.min_stable_frames,
            max(1, fast_crossing_stable_frames),
        )
        self.states: dict[int, TrackState] = {}
        # Keep discarded tracker IDs only during the reconnection guard window.
        # A permanent set grows for the entire duration of a long analysis.
        self.retired_ids: dict[int, int] = {}
        self.last_recoveries: dict[int, int] = {}

    def signed_distance(self, point: tuple[float, float]) -> float:
        """통과선 한쪽은 양수, 반대쪽은 음수가 되는 수직 거리를 계산한다."""
        ax, ay = self.a
        bx, by = self.b
        px, py = point
        return ((bx - ax) * (py - ay) - (by - ay) * (px - ax)) / self.length

    def side(self, point: tuple[float, float]) -> int:
        """데드밴드는 0, 통과선 양쪽은 각각 -1 또는 1로 분류한다."""
        distance = self.signed_distance(point)
        if abs(distance) <= self.deadband_px:
            return 0
        return 1 if distance > 0 else -1

    def projection_ratio(self, point: tuple[float, float]) -> float:
        """점이 유한 통과선의 시작 0과 끝 1 사이 어디에 투영되는지 계산한다."""
        ax, ay = self.a
        bx, by = self.b
        px, py = point
        dx, dy = bx - ax, by - ay
        return ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)

    def normal_velocity(self, state: TrackState) -> float:
        """Return signed motion perpendicular to the counting line in pixels per frame."""
        dx = self.b[0] - self.a[0]
        dy = self.b[1] - self.a[1]
        vx, vy = state.velocity
        return (dx * vy - dy * vx) / self.length

    def _remember_observation(
        self,
        state: TrackState,
        point: tuple[float, float],
        frame_index: int | None,
        class_id: int | None,
    ) -> None:
        """새 좌표로 프레임당 속도와 마지막 관측 메타데이터를 갱신한다."""
        if state.last_point is not None:
            frame_gap = 1
            if frame_index is not None and state.last_frame >= 0:
                frame_gap = max(1, frame_index - state.last_frame)
            state.velocity = (
                (point[0] - state.last_point[0]) / frame_gap,
                (point[1] - state.last_point[1]) / frame_gap,
            )
        state.last_point = point
        if frame_index is not None:
            state.last_frame = frame_index
        if class_id is not None:
            state.class_id = class_id

    def _stitch_score(
        self,
        state: TrackState,
        point: tuple[float, float],
        class_id: int,
        frame_index: int,
    ) -> float | None:
        """사라진 ID와 새 ID가 같은 차량일 가능성을 거리 점수로 평가한다."""
        if state.last_point is None or state.last_frame < 0:
            return None
        if state.counted:
            return None
        # A fragmented track often disappears before min_stable_frames is reached.
        # Keep that short pre-line fragment eligible as long as it has a known side.
        if state.stable_side == 0 and state.candidate_side == 0:
            return None
        gap = frame_index - state.last_frame
        if gap <= 0 or gap > self.id_stitch_max_gap:
            return None
        if abs(self.signed_distance(state.last_point)) > self.id_stitch_line_distance_px:
            return None
        if abs(self.signed_distance(point)) > self.id_stitch_line_distance_px:
            return None
        margin = self.line_margin_ratio * 1.5
        if not -margin <= self.projection_ratio(state.last_point) <= 1 + margin:
            return None
        if not -margin <= self.projection_ratio(point) <= 1 + margin:
            return None

        displacement_x = point[0] - state.last_point[0]
        velocity_x = state.velocity[0]
        tolerance = self.id_stitch_direction_tolerance_px
        if velocity_x > 0.5 and displacement_x < -tolerance:
            return None
        if velocity_x < -0.5 and displacement_x > tolerance:
            return None

        predicted = (
            state.last_point[0] + state.velocity[0] * gap,
            state.last_point[1] + state.velocity[1] * gap,
        )
        distance = math.dist(predicted, point)
        allowed_distance = self.id_stitch_max_distance_px + min(gap, 30) * 2.0
        if distance > allowed_distance:
            return None
        unstable_penalty = 12.0 if state.stable_side == 0 else 0.0
        return distance + gap * 0.35 + unstable_penalty

    @staticmethod
    def _promote_short_fragment(state: TrackState) -> None:
        """Preserve the known side when an ID changes before normal stabilization."""
        if state.stable_side != 0 or state.candidate_side == 0 or state.last_point is None:
            return
        state.stable_side = state.candidate_side
        state.anchor_point = state.last_point
        state.candidate_side = 0
        state.candidate_frames = 0
        state.candidate_min_normal_speed = 0.0

    def update_frame(
        self,
        observations: list[tuple[int, tuple[float, float], int]],
        frame_index: int,
    ) -> dict[int, str]:
        """Update a complete frame and reconnect IDs split by a short occlusion."""
        self.last_recoveries = {}
        expired_retired_ids = [
            track_id
            for track_id, retired_frame in self.retired_ids.items()
            if frame_index - retired_frame > self.id_stitch_max_gap
        ]
        for track_id in expired_retired_ids:
            self.retired_ids.pop(track_id, None)
        stale_ids = [
            track_id
            for track_id, state in self.states.items()
            if state.last_frame >= 0 and frame_index - state.last_frame > self.id_stitch_max_gap
        ]
        for track_id in stale_ids:
            self.states.pop(track_id, None)
            self.retired_ids[track_id] = frame_index
        active_ids = {track_id for track_id, _, _ in observations}
        if self.id_stitch_enabled:
            new_observations = [item for item in observations if item[0] not in self.states and item[0] not in self.retired_ids]
            candidates: list[tuple[float, int, int]] = []
            for new_id, point, class_id in new_observations:
                for old_id, state in self.states.items():
                    if old_id in active_ids:
                        continue
                    score = self._stitch_score(state, point, class_id, frame_index)
                    if score is not None:
                        candidates.append((score, old_id, new_id))

            matched_old: set[int] = set()
            matched_new: set[int] = set()
            for _, old_id, new_id in sorted(candidates):
                if old_id in matched_old or new_id in matched_new or old_id not in self.states:
                    continue
                recovered_state = self.states.pop(old_id)
                self._promote_short_fragment(recovered_state)
                self.states[new_id] = recovered_state
                self.retired_ids[old_id] = frame_index
                self.last_recoveries[new_id] = old_id
                matched_old.add(old_id)
                matched_new.add(new_id)

        directions: dict[int, str] = {}
        for track_id, point, class_id in observations:
            direction = self.update(track_id, point, frame_index=frame_index, class_id=class_id)
            if direction is not None:
                directions[track_id] = direction
        return directions

    def update(
        self,
        track_id: int,
        point: tuple[float, float],
        frame_index: int | None = None,
        class_id: int | None = None,
    ) -> str | None:
        """한 ID의 측면 변화를 누적해 확정된 유입·유출만 한 번 반환한다."""
        if track_id in self.retired_ids:
            return None
        state = self.states.setdefault(track_id, TrackState())
        self._remember_observation(state, point, frame_index, class_id)
        if state.counted:
            return None

        current_side = self.side(point)
        if current_side == 0:
            return None

        if state.stable_side == 0:
            if current_side == state.candidate_side:
                state.candidate_frames += 1
            else:
                state.candidate_side = current_side
                state.candidate_frames = 1
            if state.candidate_frames >= self.min_stable_frames:
                state.stable_side = current_side
                state.anchor_point = point
                state.candidate_side = 0
                state.candidate_frames = 0
                state.candidate_min_normal_speed = 0.0
            return None

        if current_side == state.stable_side:
            state.anchor_point = point
            state.candidate_side = 0
            state.candidate_frames = 0
            state.candidate_min_normal_speed = 0.0
            return None

        moving_away_speed = self.normal_velocity(state) * current_side
        if current_side == state.candidate_side:
            state.candidate_frames += 1
            state.candidate_min_normal_speed = min(state.candidate_min_normal_speed, moving_away_speed)
        else:
            state.candidate_side = current_side
            state.candidate_frames = 1
            state.candidate_min_normal_speed = moving_away_speed

        required_frames = self.min_stable_frames
        if (
            self.fast_crossing_enabled
            and state.candidate_min_normal_speed >= self.fast_crossing_min_normal_speed_px
        ):
            required_frames = self.fast_crossing_stable_frames
        if state.candidate_frames < required_frames or state.anchor_point is None:
            return None
        if not -self.line_margin_ratio <= self.projection_ratio(point) <= 1 + self.line_margin_ratio:
            return None

        horizontal_motion = point[0] - state.anchor_point[0]
        if abs(horizontal_motion) < self.min_horizontal_motion_px:
            return None

        state.counted = True
        if horizontal_motion > 0:
            return self.increasing_x_direction
        return "유출" if self.increasing_x_direction == "유입" else "유입"


def default_config(source_dir: str) -> dict:
    """정확도와 장시간 실행을 고려한 기본 분석 설정을 반환한다."""
    return {
        "source_dir": source_dir,
        "output_dir": "results",
        "model": "yolo26l.pt",
        "tracker": "botsort_reid.yaml",
        "device": "0",
        "confidence": 0.22,
        "iou": 0.65,
        "image_size": 960,
        "roi_crop_enabled": True,
        "roi_crop_padding_px": 64,
        "frame_prefetch_buffer": 8,
        "min_box_width_px": 35,
        "min_box_height_px": 35,
        "min_box_area_px": 2000,
        "speed_factor": 3.0,
        "vehicle_classes": VEHICLE_CLASSES,
        "roi_polygon": [[410, 470], [1260, 430], [1270, 900], [650, 870], [400, 670]],
        "counting_line": [[790, 790], [1035, 545]],
        "increasing_x_direction": "유출",
        "deadband_px": 8,
        "min_stable_frames": 3,
        "min_horizontal_motion_px": 12,
        "line_margin_px": 40,
        "id_stitch_enabled": True,
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
    """UTF-8 JSON 설정 파일을 읽는다."""
    with path.open("r", encoding="utf-8") as file:
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
    # Dependency-free ray casting, also used by unit tests.
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


def write_summary(path: Path, counts: dict[int, dict[str, int]]) -> None:
    """Excel 호환 UTF-8 BOM을 사용해 96개 15분 집계 행을 저장한다."""
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(["시간", "유입", "유출", "합계"])
        for slot in range(96):
            incoming = counts[slot]["유입"]
            outgoing = counts[slot]["유출"]
            writer.writerow([slot_label(slot), incoming, outgoing, incoming + outgoing])


def write_events(path: Path, events: Iterable[CountEvent]) -> None:
    """차량별 통과 사건을 상세 CSV로 저장한다."""
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        fieldnames = [
            "실제시간", "15분구간", "방향", "차종", "추적ID", "신뢰도",
            "원본영상", "영상초", "프레임", "좌표X", "좌표Y",
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


class BatchAnalyzer:
    """여러 MP4를 순차 분석하고 영상별·전체 결과를 지속적으로 저장한다."""

    def __init__(
        self,
        config: dict,
        config_dir: Path,
        log: Callable[[str], None] | None = None,
        progress: Callable[[float], None] | None = None,
        stop_event: threading.Event | None = None,
    ) -> None:
        """설정과 로그·진행률·중지 콜백을 주입받아 UI와 분리한다."""
        self.config = config
        self.config_dir = config_dir
        self.log = log or print
        self.progress = progress or (lambda value: None)
        self.stop_event = stop_event or threading.Event()

    def run(
        self,
        limit_seconds: float | None = None,
        video_paths: list[Path] | None = None,
    ) -> tuple[Path, Path | None]:
        """대상 영상을 분석하고 전체 집계 및 선택적 상세 CSV 경로를 반환한다."""
        import cv2
        import numpy as np
        import torch
        from ultralytics import YOLO

        # 무거운 영상·AI 의존성은 실제 분석이 시작될 때만 불러온다.
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

        requested_device = str(self.config.get("device", "0"))
        device = requested_device if requested_device == "cpu" or torch.cuda.is_available() else "cpu"
        self.log(f"모델 불러오는 중: {self.config['model']} / 장치: {device}")
        model = YOLO(self.config["model"])
        tracker_config = str(self.config.get("tracker", "botsort_reid.yaml"))
        tracker_path = Path(tracker_config)
        if not tracker_path.is_absolute() and (self.config_dir / tracker_path).exists():
            tracker_config = str(self.config_dir / tracker_path)
        self.log(f"추적기: {tracker_config}")
        counts = {slot: {"유입": 0, "유출": 0} for slot in range(96)}
        events: list[CountEvent] = []
        save_detailed_records = bool(self.config.get("save_detailed_records", True))
        summary_path = output_dir / "15분_유입유출_집계.csv"
        events_path = output_dir / "차량별_통과기록.csv" if save_detailed_records else None
        total_frames = 0
        # 먼저 전체 프레임 수를 계산해야 모든 영상에 걸친 진행률을 낼 수 있다.
        metadata: list[tuple[Path, int, float]] = []
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
        polygon = np.array(self.config["roi_polygon"], dtype=np.int32)
        classes = [int(value) for value in self.config.get("vehicle_classes", VEHICLE_CLASSES)]
        speed_factor = float(self.config.get("speed_factor", 3.0))
        roi_crop_enabled = bool(self.config.get("roi_crop_enabled", True))
        roi_crop_padding = int(self.config.get("roi_crop_padding_px", 64))
        frame_prefetch_buffer = int(self.config.get("frame_prefetch_buffer", 8))
        min_box_width = float(self.config.get("min_box_width_px", 35))
        min_box_height = float(self.config.get("min_box_height_px", 35))
        min_box_area = float(self.config.get("min_box_area_px", 2000))
        track_options = {
            "persist": True,
            "tracker": tracker_config,
            "conf": float(self.config.get("confidence", 0.22)),
            "iou": float(self.config.get("iou", 0.65)),
            "imgsz": int(self.config.get("image_size", 960)),
            "classes": classes,
            "device": device,
            "verbose": False,
        }

        for video_index, (video, expected_frames, fps) in enumerate(metadata, start=1):
            if self.stop_event.is_set():
                break
            self.log(f"[{video_index}/{len(videos)}] {video.name} 분석 시작")
            if reset_model_tracking_state(model):
                self.log("  영상 전환: 추적기 상태 초기화")
            direction_counter = DirectionCounter(
                self.config["counting_line"],
                deadband_px=float(self.config.get("deadband_px", 8)),
                min_stable_frames=int(self.config.get("min_stable_frames", 3)),
                min_horizontal_motion_px=float(self.config.get("min_horizontal_motion_px", 12)),
                line_margin_px=float(self.config.get("line_margin_px", 40)),
                increasing_x_direction=self.config.get("increasing_x_direction", "유출"),
                id_stitch_enabled=bool(self.config.get("id_stitch_enabled", True)),
                id_stitch_max_gap=int(self.config.get("id_stitch_max_gap", 90)),
                id_stitch_max_distance_px=float(self.config.get("id_stitch_max_distance_px", 140)),
                id_stitch_line_distance_px=float(self.config.get("id_stitch_line_distance_px", 160)),
                id_stitch_direction_tolerance_px=float(self.config.get("id_stitch_direction_tolerance_px", 12)),
                fast_crossing_enabled=bool(self.config.get("fast_crossing_enabled", True)),
                fast_crossing_min_normal_speed_px=float(
                    self.config.get("fast_crossing_min_normal_speed_px", 15)
                ),
                fast_crossing_stable_frames=int(self.config.get("fast_crossing_stable_frames", 2)),
            )
            video_counts = {slot: {"유입": 0, "유출": 0} for slot in range(96)}
            video_events: list[CountEvent] = []
            start_seconds = video_start_seconds(video.name)
            cap = cv2.VideoCapture(str(video))
            frame_width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            frame_height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            if roi_crop_enabled:
                crop_left, crop_top, crop_right, crop_bottom = roi_crop_bounds(
                    frame_width,
                    frame_height,
                    self.config["roi_polygon"],
                    roi_crop_padding,
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
                f"(원본 {frame_width}x{frame_height})"
            )
            writer = None
            review_size: tuple[int, int] | None = None
            review_stride = max(1, int(self.config.get("review_stride", 3)))
            if self.config.get("save_review_video", False):
                review_width = min(frame_width, int(self.config.get("review_width", 640)))
                review_size = (review_width, round(frame_height * review_width / frame_width))
                review_path = output_dir / f"검수_{video.stem}.mp4"
                writer = cv2.VideoWriter(str(review_path), cv2.VideoWriter_fourcc(*"mp4v"), fps / review_stride, review_size)

            frame_index = 0
            prefetcher = FramePrefetcher(cap, expected_frames, frame_prefetch_buffer, self.stop_event)
            for frame in prefetcher:
                if self.stop_event.is_set():
                    break
                cropped = frame[crop_top:crop_bottom, crop_left:crop_right]
                cv2.bitwise_and(cropped, roi_mask_bgr, dst=masked_buffer)
                result = model.track(masked_buffer, **track_options)[0]

                annotated = frame.copy() if writer is not None else frame
                boxes = result.boxes
                if boxes is not None and boxes.id is not None:
                    box_rows = torch.cat(
                        (boxes.xyxy, boxes.id[:, None], boxes.cls[:, None], boxes.conf[:, None]),
                        dim=1,
                    ).cpu().numpy()
                    track_details: list[tuple[int, tuple[float, float], int, float, tuple[float, float, float, float]]] = []
                    for x1, y1, x2, y2, track_id_value, class_id_value, confidence_value in box_rows:
                        x1, y1, x2, y2 = map(float, (x1, y1, x2, y2))
                        track_id = int(track_id_value)
                        class_id = int(class_id_value)
                        confidence = float(confidence_value)
                        if not box_meets_minimum_size(
                            (x1, y1, x2, y2),
                            min_box_width,
                            min_box_height,
                            min_box_area,
                        ):
                            continue
                        x1 += crop_left
                        x2 += crop_left
                        y1 += crop_top
                        y2 += crop_top
                        point = ((x1 + x2) / 2, y2)
                        if not point_in_polygon(point, self.config["roi_polygon"]):
                            continue
                        track_details.append((track_id, point, class_id, float(confidence), (x1, y1, x2, y2)))

                    # 한 프레임의 모든 ID를 함께 전달해야 동시 가림을 일대일로 연결할 수 있다.
                    directions = direction_counter.update_frame(
                        [(track_id, point, class_id) for track_id, point, class_id, _, _ in track_details],
                        frame_index,
                    )
                    for new_id, old_id in direction_counter.last_recoveries.items():
                        self.log(f"  가림 ID 연결: {old_id} → {new_id}")

                    for track_id, point, class_id, confidence, (x1, y1, x2, y2) in track_details:
                        direction = directions.get(track_id)
                        color = (255, 160, 40) if direction == "유입" else (40, 130, 255)
                        if writer is not None:
                            cv2.rectangle(annotated, (int(x1), int(y1)), (int(x2), int(y2)), color, 2)
                            cv2.putText(annotated, f"ID {track_id}", (int(x1), max(25, int(y1) - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
                        if direction is None:
                            continue
                        video_seconds = frame_index / fps
                        real_seconds = (start_seconds + video_seconds * speed_factor) % 86400
                        slot = int(real_seconds // 900)
                        counts[slot][direction] += 1
                        video_counts[slot][direction] += 1
                        event = CountEvent(
                            video=video.name,
                            frame=frame_index,
                            video_seconds=video_seconds,
                            real_seconds=real_seconds,
                            slot=slot,
                            direction=direction,
                            track_id=track_id,
                            class_id=class_id,
                            class_name=CLASS_LABELS.get(class_id, "차량"),
                            confidence=confidence,
                            point_x=point[0],
                            point_y=point[1],
                        )
                        if save_detailed_records:
                            events.append(event)
                            video_events.append(event)
                        self.log(f"  {event.real_time} {direction} {event.class_name} (ID {track_id})")
                        if self.config.get("save_event_images", True):
                            snapshot = frame.copy()
                            cv2.polylines(snapshot, [polygon], True, (0, 220, 255), 2)
                            cv2.line(snapshot, tuple(self.config["counting_line"][0]), tuple(self.config["counting_line"][1]), (255, 0, 255), 3)
                            cv2.rectangle(snapshot, (int(x1), int(y1)), (int(x2), int(y2)), color, 3)
                            image_name = f"{event.real_time.replace(':', '')}_{direction}_ID{track_id}_{video.stem}.jpg"
                            cv2.imwrite(str(event_image_dir / image_name), snapshot)

                if writer is not None and frame_index % review_stride == 0 and review_size is not None:
                    cv2.polylines(annotated, [polygon], True, (0, 220, 255), 2)
                    cv2.line(annotated, tuple(self.config["counting_line"][0]), tuple(self.config["counting_line"][1]), (255, 0, 255), 3)
                    cv2.putText(annotated, f"IN {sum(item['유입'] for item in counts.values())} / OUT {sum(item['유출'] for item in counts.values())}", (24, 42), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (30, 255, 30), 3)
                    writer.write(cv2.resize(annotated, review_size, interpolation=cv2.INTER_AREA))

                frame_index += 1
                processed_frames += 1
                if frame_index % 120 == 0:
                    self.progress(processed_frames / max(total_frames, 1))

            prefetcher.close()
            if writer is not None:
                writer.release()
            video_summary_path, video_events_path = write_video_results(
                output_dir,
                video,
                video_counts,
                video_events,
                save_detailed_records=save_detailed_records,
            )
            # Refresh the compatible all-video outputs after every completed video.
            write_summary(summary_path, counts)
            if events_path is not None:
                write_events(events_path, events)
                self.log(
                    f"  영상별 CSV 저장: {video_summary_path.name}, {video_events_path.name}"
                )
            else:
                self.log(f"  영상별 집계 CSV 저장: {video_summary_path.name}")
            self.log(f"[{video_index}/{len(videos)}] 완료: {frame_index:,} 프레임")

        write_summary(summary_path, counts)
        if events_path is not None:
            write_events(events_path, events)
        self.progress(1.0 if not self.stop_event.is_set() else processed_frames / max(total_frames, 1))
        self.log(f"완료: 유입 {sum(item['유입'] for item in counts.values()):,}대 / 유출 {sum(item['유출'] for item in counts.values()):,}대")
        return summary_path, events_path
