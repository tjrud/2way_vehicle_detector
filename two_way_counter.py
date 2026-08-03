from __future__ import annotations

import csv
import json
import math
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable


VEHICLE_CLASSES = [2, 3, 5, 7]  # COCO: car, motorcycle, bus, truck
CLASS_LABELS = {1: "자전거", 2: "승용차", 3: "오토바이", 5: "버스", 7: "트럭"}
VIDEO_PATTERN = re.compile(r"_(\d{2})(\d{2})-(\d{2})(\d{2})_")


@dataclass
class TrackState:
    stable_side: int = 0
    candidate_side: int = 0
    candidate_frames: int = 0
    anchor_point: tuple[float, float] | None = None
    last_point: tuple[float, float] | None = None
    counted: bool = False


@dataclass
class CountEvent:
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
        self.states: dict[int, TrackState] = {}

    def signed_distance(self, point: tuple[float, float]) -> float:
        ax, ay = self.a
        bx, by = self.b
        px, py = point
        return ((bx - ax) * (py - ay) - (by - ay) * (px - ax)) / self.length

    def side(self, point: tuple[float, float]) -> int:
        distance = self.signed_distance(point)
        if abs(distance) <= self.deadband_px:
            return 0
        return 1 if distance > 0 else -1

    def projection_ratio(self, point: tuple[float, float]) -> float:
        ax, ay = self.a
        bx, by = self.b
        px, py = point
        dx, dy = bx - ax, by - ay
        return ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)

    def update(self, track_id: int, point: tuple[float, float]) -> str | None:
        state = self.states.setdefault(track_id, TrackState())
        state.last_point = point
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
            return None

        if current_side == state.stable_side:
            state.anchor_point = point
            state.candidate_side = 0
            state.candidate_frames = 0
            return None

        if current_side == state.candidate_side:
            state.candidate_frames += 1
        else:
            state.candidate_side = current_side
            state.candidate_frames = 1

        if state.candidate_frames < self.min_stable_frames or state.anchor_point is None:
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
    return {
        "source_dir": source_dir,
        "output_dir": "results",
        "model": "yolo26s.pt",
        "tracker": "bytetrack.yaml",
        "device": "0",
        "confidence": 0.22,
        "iou": 0.65,
        "image_size": 960,
        "speed_factor": 3.0,
        "vehicle_classes": VEHICLE_CLASSES,
        "roi_polygon": [[410, 470], [1260, 430], [1270, 900], [650, 870], [400, 670]],
        "counting_line": [[790, 790], [1035, 545]],
        "increasing_x_direction": "유출",
        "deadband_px": 8,
        "min_stable_frames": 3,
        "min_horizontal_motion_px": 12,
        "line_margin_px": 40,
        "save_event_images": True,
        "save_review_video": False,
        "review_width": 640,
        "review_stride": 3,
    }


def load_config(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_config(path: Path, config: dict) -> None:
    with path.open("w", encoding="utf-8") as file:
        json.dump(config, file, ensure_ascii=False, indent=2)


def video_start_seconds(filename: str) -> int:
    match = VIDEO_PATTERN.search(filename)
    if not match:
        raise ValueError(f"파일명에서 시작 시간을 찾을 수 없습니다: {filename}")
    start_hour, start_minute = int(match.group(1)), int(match.group(2))
    return start_hour * 3600 + start_minute * 60


def slot_label(slot: int) -> str:
    start = slot * 15
    end = (start + 15) % (24 * 60)
    return f"{start // 60:02d}:{start % 60:02d}~{end // 60:02d}:{end % 60:02d}"


def point_in_polygon(point: tuple[float, float], polygon: list[list[int]]) -> bool:
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


def write_summary(path: Path, counts: dict[int, dict[str, int]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.writer(file)
        writer.writerow(["시간", "유입", "유출", "합계"])
        for slot in range(96):
            incoming = counts[slot]["유입"]
            outgoing = counts[slot]["유출"]
            writer.writerow([slot_label(slot), incoming, outgoing, incoming + outgoing])


def write_events(path: Path, events: Iterable[CountEvent]) -> None:
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


class BatchAnalyzer:
    def __init__(
        self,
        config: dict,
        config_dir: Path,
        log: Callable[[str], None] | None = None,
        progress: Callable[[float], None] | None = None,
        stop_event: threading.Event | None = None,
    ) -> None:
        self.config = config
        self.config_dir = config_dir
        self.log = log or print
        self.progress = progress or (lambda value: None)
        self.stop_event = stop_event or threading.Event()

    def run(self, limit_seconds: float | None = None, video_paths: list[Path] | None = None) -> tuple[Path, Path]:
        import cv2
        import numpy as np
        import torch
        from ultralytics import YOLO

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
        counts = {slot: {"유입": 0, "유출": 0} for slot in range(96)}
        events: list[CountEvent] = []
        total_frames = 0
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

        for video_index, (video, expected_frames, fps) in enumerate(metadata, start=1):
            if self.stop_event.is_set():
                break
            self.log(f"[{video_index}/{len(videos)}] {video.name} 분석 시작")
            if getattr(model, "predictor", None) is not None:
                model.predictor = None
            direction_counter = DirectionCounter(
                self.config["counting_line"],
                deadband_px=float(self.config.get("deadband_px", 8)),
                min_stable_frames=int(self.config.get("min_stable_frames", 3)),
                min_horizontal_motion_px=float(self.config.get("min_horizontal_motion_px", 12)),
                line_margin_px=float(self.config.get("line_margin_px", 40)),
                increasing_x_direction=self.config.get("increasing_x_direction", "유출"),
            )
            start_seconds = video_start_seconds(video.name)
            cap = cv2.VideoCapture(str(video))
            writer = None
            review_size: tuple[int, int] | None = None
            review_stride = max(1, int(self.config.get("review_stride", 3)))
            if self.config.get("save_review_video", False):
                width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                review_width = min(width, int(self.config.get("review_width", 640)))
                review_size = (review_width, round(height * review_width / width))
                review_path = output_dir / f"검수_{video.stem}.mp4"
                writer = cv2.VideoWriter(str(review_path), cv2.VideoWriter_fourcc(*"mp4v"), fps / review_stride, review_size)

            frame_index = 0
            while frame_index < expected_frames and not self.stop_event.is_set():
                ok, frame = cap.read()
                if not ok:
                    break
                mask = np.zeros(frame.shape[:2], dtype=np.uint8)
                cv2.fillPoly(mask, [polygon], 255)
                masked = cv2.bitwise_and(frame, frame, mask=mask)
                result = model.track(
                    masked,
                    persist=True,
                    tracker=self.config.get("tracker", "bytetrack.yaml"),
                    conf=float(self.config.get("confidence", 0.22)),
                    iou=float(self.config.get("iou", 0.65)),
                    imgsz=int(self.config.get("image_size", 960)),
                    classes=classes,
                    device=device,
                    verbose=False,
                )[0]

                annotated = frame.copy() if writer is not None else frame
                boxes = result.boxes
                if boxes is not None and boxes.id is not None:
                    coordinates = boxes.xyxy.cpu().numpy()
                    track_ids = boxes.id.int().cpu().tolist()
                    class_ids = boxes.cls.int().cpu().tolist()
                    confidences = boxes.conf.cpu().tolist()
                    for xyxy, track_id, class_id, confidence in zip(coordinates, track_ids, class_ids, confidences):
                        x1, y1, x2, y2 = map(float, xyxy)
                        point = ((x1 + x2) / 2, y2)
                        if not point_in_polygon(point, self.config["roi_polygon"]):
                            continue
                        direction = direction_counter.update(track_id, point)
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
                        event = CountEvent(
                            video=video.name,
                            frame=frame_index,
                            video_seconds=video_seconds,
                            real_seconds=real_seconds,
                            slot=slot,
                            direction=direction,
                            track_id=track_id,
                            class_id=class_id,
                            class_name=CLASS_LABELS.get(class_id, str(class_id)),
                            confidence=float(confidence),
                            point_x=point[0],
                            point_y=point[1],
                        )
                        events.append(event)
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

            cap.release()
            if writer is not None:
                writer.release()
            self.log(f"[{video_index}/{len(videos)}] 완료: {frame_index:,} 프레임")

        summary_path = output_dir / "15분_유입유출_집계.csv"
        events_path = output_dir / "차량별_통과기록.csv"
        write_summary(summary_path, counts)
        write_events(events_path, events)
        self.progress(1.0 if not self.stop_event.is_set() else processed_frames / max(total_frames, 1))
        self.log(f"완료: 유입 {sum(item['유입'] for item in counts.values()):,}대 / 유출 {sum(item['유출'] for item in counts.values()):,}대")
        return summary_path, events_path
