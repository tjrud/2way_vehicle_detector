"""여러 형태의 교차로에 쓰는 통과선(게이트) 기반 차량 이동 판정 엔진.

- 통과선을 원하는 개수만큼 둔다. 2방향 도로는 1개, T자·Y자는 3개, 십자는 4개,
  오거리는 5개, 회전교차로는 진출입로 수만큼 둔다.
- 각 통과선에는 '안쪽'(교차로 내부) 방향이 있다. 바깥→안쪽 통과는 유입,
  안쪽→바깥 통과는 유출이다. 방향은 통과선의 법선으로 판정하므로 선이
  가로·세로·대각선 어느 방향이어도 같은 규칙이 적용된다.
- 한 차량(추적 ID)의 유입 통과선과 유출 통과선을 이어 출발→도착(OD)과
  직진·좌회전·우회전·유턴을 만든다.

이 모듈은 영상·AI 라이브러리에 의존하지 않으므로 단위 테스트로 전부 검증할 수 있다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

INBOUND = "유입"
OUTBOUND = "유출"
UNKNOWN = "미확인"

TURN_STRAIGHT = "직진"
TURN_LEFT = "좌회전"
TURN_RIGHT = "우회전"
TURN_UTURN = "유턴"

TRIP_COMPLETE = "완료"
TRIP_NO_EXIT = "도착 미확인"
TRIP_NO_ENTRY = "출발 미확인"

Point = tuple[float, float]


def _to_point(value: Sequence[float]) -> Point:
    """[x, y] 형태의 설정값을 실수 좌표로 바꾼다."""
    return float(value[0]), float(value[1])


def gate_codes(count: int) -> list[str]:
    """이미지에 그릴 수 있는 ASCII 통과선 코드 A, B, C …를 만든다."""
    codes: list[str] = []
    for index in range(count):
        code = ""
        number = index
        while True:
            code = chr(ord("A") + number % 26) + code
            number = number // 26 - 1
            if number < 0:
                break
        codes.append(code)
    return codes


def screen_angle(vector: Point) -> float:
    """화면 좌표(y 아래쪽)의 벡터를 위에서 내려다본 수학 각도(도, 반시계 +)로 바꾼다."""
    return math.degrees(math.atan2(-vector[1], vector[0]))


def normalize_angle(angle: float) -> float:
    """각도를 (-180, 180] 범위로 정규화한다."""
    value = (angle + 180.0) % 360.0 - 180.0
    return 180.0 if value == -180.0 else value


def polygon_centroid(points: Sequence[Sequence[float]]) -> Point | None:
    """다각형 면적 중심을 계산하고, 면적이 0이면 꼭짓점 평균을 쓴다."""
    if not points:
        return None
    area = 0.0
    cx = 0.0
    cy = 0.0
    count = len(points)
    for index in range(count):
        x1, y1 = _to_point(points[index])
        x2, y2 = _to_point(points[(index + 1) % count])
        cross = x1 * y2 - x2 * y1
        area += cross
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    if abs(area) < 1e-9:
        return (
            sum(float(point[0]) for point in points) / count,
            sum(float(point[1]) for point in points) / count,
        )
    area *= 0.5
    return cx / (6.0 * area), cy / (6.0 * area)


@dataclass(frozen=True)
class Gate:
    """교차로 접근로 하나를 가로지르는 유한 통과선."""

    code: str
    name: str
    a: Point
    b: Point
    inside_side: int = 1

    def __post_init__(self) -> None:
        """잘못된 방향 값과 너무 짧은 선을 거부한다."""
        if self.inside_side not in (1, -1):
            raise ValueError(f"통과선 {self.name}의 inside_side는 1 또는 -1이어야 합니다.")
        if math.dist(self.a, self.b) < 10:
            raise ValueError(f"통과선 {self.name}이 너무 짧습니다. 10px 이상으로 그려 주세요.")

    @property
    def length(self) -> float:
        """통과선 길이(px)."""
        return math.dist(self.a, self.b)

    @property
    def midpoint(self) -> Point:
        """통과선 중앙점."""
        return (self.a[0] + self.b[0]) / 2.0, (self.a[1] + self.b[1]) / 2.0

    def signed_distance(self, point: Point) -> float:
        """통과선 한쪽은 양수, 반대쪽은 음수가 되는 수직 거리를 계산한다."""
        ax, ay = self.a
        bx, by = self.b
        px, py = point
        return ((bx - ax) * (py - ay) - (by - ay) * (px - ax)) / self.length

    def projection_ratio(self, point: Point) -> float:
        """점이 통과선의 시작 0과 끝 1 사이 어디에 투영되는지 계산한다."""
        ax, ay = self.a
        bx, by = self.b
        dx, dy = bx - ax, by - ay
        return ((point[0] - ax) * dx + (point[1] - ay) * dy) / (dx * dx + dy * dy)

    def normal_component(self, vector: Point) -> float:
        """벡터의 통과선 수직 성분(signed_distance와 같은 부호 규칙)을 계산한다."""
        dx = self.b[0] - self.a[0]
        dy = self.b[1] - self.a[1]
        return (dx * vector[1] - dy * vector[0]) / self.length

    def inward_normal(self) -> Point:
        """교차로 안쪽을 가리키는 단위 법선."""
        dx = self.b[0] - self.a[0]
        dy = self.b[1] - self.a[1]
        return -dy / self.length * self.inside_side, dx / self.length * self.inside_side

    def crossing_ratio(self, start: Point, end: Point) -> float | None:
        """start→end 이동이 통과선의 무한 연장선을 지나는 지점의 투영 비율."""
        d0 = self.signed_distance(start)
        d1 = self.signed_distance(end)
        if d0 == d1:
            return None
        t = d0 / (d0 - d1)
        crossing = (start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t)
        return self.projection_ratio(crossing)

    def direction_to(self, new_side: int) -> str:
        """새로 도달한 측면이 안쪽이면 유입, 바깥이면 유출."""
        return INBOUND if new_side == self.inside_side else OUTBOUND

    def flipped(self) -> "Gate":
        """안쪽 방향만 반대로 바꾼 통과선."""
        return Gate(self.code, self.name, self.a, self.b, -self.inside_side)

    def to_config(self) -> dict[str, Any]:
        """config.json에 저장할 형태."""
        return {
            "code": self.code,
            "name": self.name,
            "line": [[round(self.a[0]), round(self.a[1])], [round(self.b[0]), round(self.b[1])]],
            "inside_side": self.inside_side,
        }


def side_of(a: Point, b: Point, point: Point) -> int:
    """점이 a→b 선의 어느 쪽에 있는지 1 또는 -1로 반환한다(선 위는 0)."""
    value = (b[0] - a[0]) * (point[1] - a[1]) - (b[1] - a[1]) * (point[0] - a[0])
    if abs(value) < 1e-9:
        return 0
    return 1 if value > 0 else -1


def legacy_inside_side(a: Point, b: Point, increasing_x_direction: str = OUTBOUND) -> int:
    """기존 2방향 설정(화면 왼쪽→오른쪽 = increasing_x_direction)을 안쪽 측면으로 바꾼다."""
    # +x 이동은 signed_distance를 -(by-ay)/L 만큼 바꾼다. 선이 수평이면 +y 이동으로 대신 정한다.
    delta = -(b[1] - a[1])
    if abs(delta) < 1e-9:
        delta = b[0] - a[0]
    reached_side = 1 if delta > 0 else -1
    return reached_side if increasing_x_direction == INBOUND else -reached_side


def screen_direction_text(gate: Gate) -> str:
    """통과선 하나의 유입 방향을 '화면 오른쪽→왼쪽' 같은 문장으로 설명한다."""
    nx, ny = gate.inward_normal()
    if abs(nx) >= abs(ny):
        return "화면 왼쪽→오른쪽" if nx > 0 else "화면 오른쪽→왼쪽"
    return "화면 위→아래" if ny > 0 else "화면 아래→위"


def gates_center(
    gates: Sequence[Gate],
    roi_polygon: Sequence[Sequence[float]] | None = None,
    center_point: Sequence[float] | None = None,
) -> Point | None:
    """교차로 중심: 지정값 → 통과선 중앙점 평균(2개 이상) → ROI 중심 순으로 정한다."""
    if center_point:
        return _to_point(center_point)
    if len(gates) >= 2:
        return (
            sum(gate.midpoint[0] for gate in gates) / len(gates),
            sum(gate.midpoint[1] for gate in gates) / len(gates),
        )
    return polygon_centroid(roi_polygon) if roi_polygon else None


def default_inside_sides(
    lines: Sequence[tuple[Point, Point]],
    roi_polygon: Sequence[Sequence[float]] | None = None,
    increasing_x_direction: str = OUTBOUND,
    center_point: Sequence[float] | None = None,
) -> list[int]:
    """통과선별 기본 안쪽 측면. 2개 이상이면 교차로 중심 쪽, 1개면 기존 2방향 규칙."""
    if len(lines) == 1 and not center_point:
        a, b = lines[0]
        return [legacy_inside_side(a, b, increasing_x_direction)]
    if center_point:
        center: Point | None = _to_point(center_point)
    else:
        center = (
            sum((a[0] + b[0]) / 2 for a, b in lines) / len(lines),
            sum((a[1] + b[1]) / 2 for a, b in lines) / len(lines),
        ) if lines else None
    sides: list[int] = []
    roi_center = polygon_centroid(roi_polygon) if roi_polygon else None
    for a, b in lines:
        side = side_of(a, b, center) if center is not None else 0
        if side == 0 and roi_center is not None:
            side = side_of(a, b, roi_center)
        sides.append(side or 1)
    return sides


def build_gates(config: dict) -> list[Gate]:
    """설정에서 통과선 목록을 만든다. gates가 없으면 기존 counting_line 1개로 만든다."""
    raw_gates = config.get("gates") or []
    roi = config.get("roi_polygon") or None
    increasing = str(config.get("increasing_x_direction", OUTBOUND))
    if raw_gates:
        lines = []
        for index, item in enumerate(raw_gates):
            line = item.get("line") if isinstance(item, dict) else None
            if not line or len(line) != 2:
                raise ValueError(f"{index + 1}번째 통과선 좌표가 올바르지 않습니다.")
            lines.append((_to_point(line[0]), _to_point(line[1])))
        defaults = default_inside_sides(lines, roi, increasing, config.get("center_point"))
        codes = gate_codes(len(raw_gates))
        gates: list[Gate] = []
        used_codes: set[str] = set()
        used_names: set[str] = set()
        for index, item in enumerate(raw_gates):
            code = str(item.get("code") or codes[index]).strip() or codes[index]
            if code in used_codes:
                code = codes[index]
            name = str(item.get("name") or code).strip() or code
            if name in used_names:
                name = f"{name}({code})"
            side = item.get("inside_side")
            side = int(side) if side in (1, -1, "1", "-1") else defaults[index]
            a, b = lines[index]
            gates.append(Gate(code, name, a, b, side))
            used_codes.add(code)
            used_names.add(name)
        return gates
    line = config.get("counting_line")
    if not line or len(line) != 2:
        raise ValueError("통과선이 설정되지 않았습니다. 영역·통과선 설정을 먼저 실행하세요.")
    a, b = _to_point(line[0]), _to_point(line[1])
    return [Gate("A", "통과선", a, b, legacy_inside_side(a, b, increasing))]


def _parse_movement_key(key: str) -> tuple[str, str] | None:
    """'A→C', 'A>C', 'A-C' 형식의 회전 이름 지정 키를 해석한다."""
    for separator in ("→", "->", ">", "-"):
        if separator in key:
            left, right = key.split(separator, 1)
            return left.strip(), right.strip()
    return None


class MovementClassifier:
    """진입·진출 통과선 쌍을 직진·좌회전·우회전·유턴으로 분류한다.

    - 4갈래는 교차로 중심을 기준으로 한 통과선의 순환 순서로 판정한다. 원근 왜곡이
      커도 순서는 보존되므로 비스듬한 카메라에서도 안정적이다.
    - 그 밖의 형태는 진입 방향(안쪽 법선)과 진출 방향(바깥 법선)의 각도 차이로 판정한다.
    - config의 movement_labels로 특정 쌍의 이름을 직접 지정할 수 있다.
    """

    def __init__(
        self,
        gates: Sequence[Gate],
        center: Point | None = None,
        overrides: dict[str, str] | None = None,
        straight_tolerance_deg: float = 45.0,
        uturn_threshold_deg: float = 135.0,
    ) -> None:
        self.gates = list(gates)
        self.center = center or gates_center(self.gates)
        self.straight_tolerance = straight_tolerance_deg
        self.uturn_threshold = uturn_threshold_deg
        self.overrides: dict[tuple[int, int], str] = {}
        lookup: dict[str, int] = {}
        for index, gate in enumerate(self.gates):
            lookup[gate.code] = index
            lookup[gate.name] = index
        for key, label in (overrides or {}).items():
            parsed = _parse_movement_key(str(key))
            if parsed and parsed[0] in lookup and parsed[1] in lookup:
                self.overrides[(lookup[parsed[0]], lookup[parsed[1]])] = str(label)
        self.cyclic_position: dict[int, int] | None = None
        if len(self.gates) == 4 and self.center is not None:
            angles = []
            for index, gate in enumerate(self.gates):
                vector = (gate.midpoint[0] - self.center[0], gate.midpoint[1] - self.center[1])
                angles.append((screen_angle(vector) % 360.0, index))
            ordered = [index for _, index in sorted(angles)]
            self.cyclic_position = {index: position for position, index in enumerate(ordered)}

    def turn_angle(self, entry: int, exit_: int) -> float:
        """진입 방향 대비 진출 방향의 회전각(좌회전 +, 우회전 -)."""
        entry_heading = screen_angle(self.gates[entry].inward_normal())
        nx, ny = self.gates[exit_].inward_normal()
        exit_heading = screen_angle((-nx, -ny))
        return normalize_angle(exit_heading - entry_heading)

    def label(self, entry: int | None, exit_: int | None) -> str:
        """진입·진출 인덱스로 회전 이름을 정한다. 한쪽이라도 모르면 미확인."""
        if entry is None or exit_ is None:
            return UNKNOWN
        if (entry, exit_) in self.overrides:
            return self.overrides[(entry, exit_)]
        if entry == exit_:
            return TURN_UTURN
        if self.cyclic_position is not None:
            step = (self.cyclic_position[exit_] - self.cyclic_position[entry]) % 4
            return {1: TURN_RIGHT, 2: TURN_STRAIGHT, 3: TURN_LEFT}.get(step, TURN_UTURN)
        angle = self.turn_angle(entry, exit_)
        if abs(angle) <= self.straight_tolerance:
            return TURN_STRAIGHT
        if abs(angle) >= self.uturn_threshold:
            return TURN_UTURN
        return TURN_LEFT if angle > 0 else TURN_RIGHT


@dataclass
class GateSideState:
    """한 차량이 통과선 하나에 대해 어느 쪽에 있는지와 통과 후보 상태."""

    stable_side: int = 0
    candidate_side: int = 0
    candidate_frames: int = 0
    candidate_min_normal_speed: float = 0.0
    anchor_point: Point | None = None
    crossing_start: Point | None = None
    counted: bool = False

    def reset_candidate(self) -> None:
        """반대편 후보 누적을 초기화한다."""
        self.candidate_side = 0
        self.candidate_frames = 0
        self.candidate_min_normal_speed = 0.0
        self.crossing_start = None

    def settle(self, side: int, point: Point) -> None:
        """새 측면을 확정하고 기준점을 옮긴다."""
        self.stable_side = side
        self.anchor_point = point
        self.reset_candidate()


@dataclass
class GateCrossing:
    """확정된 통과선 통과 한 건."""

    track_id: int
    gate_index: int
    direction: str
    frame: int
    point: Point
    class_id: int | None
    crossing_ratio: float


@dataclass
class Trip:
    """한 차량의 진입→진출 이동."""

    track_id: int
    last_track_id: int
    class_id: int | None
    entry: GateCrossing | None
    exit: GateCrossing | None

    @property
    def status(self) -> str:
        """완료 / 도착 미확인 / 출발 미확인."""
        if self.entry is not None and self.exit is not None:
            return TRIP_COMPLETE
        return TRIP_NO_EXIT if self.entry is not None else TRIP_NO_ENTRY

    @property
    def entry_gate(self) -> int | None:
        return self.entry.gate_index if self.entry is not None else None

    @property
    def exit_gate(self) -> int | None:
        return self.exit.gate_index if self.exit is not None else None

    @property
    def reference_frame(self) -> int:
        """시간대 집계 기준 프레임: 진입 시각, 없으면 진출 시각."""
        if self.entry is not None:
            return self.entry.frame
        return self.exit.frame if self.exit is not None else -1


@dataclass
class TrackState:
    """추적 ID 하나(재연결된 ID 조각 포함)의 누적 상태."""

    gates: list[GateSideState]
    first_track_id: int
    last_point: Point | None = None
    velocity: Point = (0.0, 0.0)
    last_frame: int = -1
    observations: int = 0
    class_id: int | None = None
    class_votes: dict[int, float] = field(default_factory=dict)
    entry: GateCrossing | None = None
    exit: GateCrossing | None = None
    entry_depth: float = 0.0
    finished: bool = False

    @property
    def counted(self) -> bool:
        """기존 코드 호환: 더 이상 집계할 것이 없는 상태인지."""
        return self.finished

    def voted_class(self) -> int | None:
        """신뢰도 가중 다수결 차종. 프레임마다 흔들리는 차종 판정을 안정화한다."""
        if not self.class_votes:
            return self.class_id
        return max(self.class_votes.items(), key=lambda item: item[1])[0]


Observation = tuple  # (track_id, point, class_id) 또는 (track_id, point, class_id, confidence)


class IntersectionCounter:
    """통과선 N개에 대해 ID별 통과를 확정하고, 가림으로 끊긴 ID 조각을 이어 붙인다.

    count_mode
      - "once": 추적 ID 하나당 한 번만 집계(기존 2방향 동작).
      - "trip": ID 하나당 유입 1회 + 유출 1회. 유입 통과선과 유출 통과선으로 OD를 만든다.
      - "auto": 통과선이 1개면 once, 2개 이상이면 trip.
    """

    def __init__(
        self,
        gates: Sequence[Gate],
        deadband_px: float = 8.0,
        min_stable_frames: int = 3,
        min_crossing_motion_px: float = 12.0,
        line_margin_px: float = 40.0,
        id_stitch_enabled: bool = True,
        id_stitch_max_gap: int = 90,
        id_stitch_max_distance_px: float = 140.0,
        id_stitch_line_distance_px: float = 160.0,
        id_stitch_direction_tolerance_px: float = 12.0,
        fast_crossing_enabled: bool = True,
        fast_crossing_min_normal_speed_px: float = 15.0,
        fast_crossing_stable_frames: int = 2,
        count_mode: str = "auto",
        stitch_in_transit: bool = True,
        uturn_min_depth_px: float = 60.0,
        velocity_smoothing: float = 0.5,
    ) -> None:
        if not gates:
            raise ValueError("통과선이 1개 이상 필요합니다.")
        self.gates = list(gates)
        self.deadband_px = max(0.0, float(deadband_px))
        self.min_stable_frames = max(1, int(min_stable_frames))
        self.min_crossing_motion_px = max(0.0, float(min_crossing_motion_px))
        self.line_margin_px = max(0.0, float(line_margin_px))
        self.id_stitch_enabled = bool(id_stitch_enabled)
        self.id_stitch_max_gap = max(1, int(id_stitch_max_gap))
        self.id_stitch_max_distance_px = max(1.0, float(id_stitch_max_distance_px))
        self.id_stitch_line_distance_px = max(1.0, float(id_stitch_line_distance_px))
        self.id_stitch_direction_tolerance_px = max(0.0, float(id_stitch_direction_tolerance_px))
        self.fast_crossing_enabled = bool(fast_crossing_enabled)
        self.fast_crossing_min_normal_speed_px = max(0.0, float(fast_crossing_min_normal_speed_px))
        self.fast_crossing_stable_frames = min(self.min_stable_frames, max(1, int(fast_crossing_stable_frames)))
        if count_mode not in ("auto", "once", "trip"):
            raise ValueError("count_mode는 auto, once, trip 중 하나여야 합니다.")
        self.count_mode = ("once" if len(self.gates) == 1 else "trip") if count_mode == "auto" else count_mode
        self.stitch_in_transit = bool(stitch_in_transit)
        self.uturn_min_depth_px = max(0.0, float(uturn_min_depth_px))
        # 1.0이면 순간 속도, 작을수록 박스 흔들림에 둔감한 평균 속도를 쓴다.
        self.velocity_smoothing = min(1.0, max(0.05, float(velocity_smoothing)))
        self.states: dict[int, TrackState] = {}
        # 재연결 보호 구간 동안만 버린 ID를 기억한다. 영구 집합은 장시간 분석에서 계속 커진다.
        self.retired_ids: dict[int, int] = {}
        self.last_recoveries: dict[int, int] = {}
        self.completed_trips: list[Trip] = []
        self.recovery_count = 0
        self.ignored_crossings = 0

    # ------------------------------------------------------------------ 상태 관리
    def _new_state(self, track_id: int) -> TrackState:
        return TrackState(gates=[GateSideState() for _ in self.gates], first_track_id=track_id)

    def _close_state(self, track_id: int, state: TrackState) -> None:
        """사라진 차량의 진입만 있는 이동을 '도착 미확인'으로 마감한다."""
        if self.count_mode == "trip" and state.entry is not None and state.exit is None and not state.finished:
            self.completed_trips.append(
                Trip(state.first_track_id, track_id, state.voted_class(), state.entry, None)
            )
        state.finished = True

    def drain_trips(self) -> list[Trip]:
        """마감된 이동을 꺼내고 내부 목록을 비운다."""
        trips = self.completed_trips
        self.completed_trips = []
        return trips

    def finish(self) -> list[Trip]:
        """영상 끝에서 진행 중인 모든 이동을 마감하고 남은 이동을 반환한다."""
        for track_id, state in list(self.states.items()):
            self._close_state(track_id, state)
        self.states.clear()
        return self.drain_trips()

    def _remember_observation(
        self,
        state: TrackState,
        point: Point,
        frame_index: int | None,
        class_id: int | None,
        confidence: float | None,
    ) -> None:
        """새 좌표로 프레임당 속도, 마지막 관측, 차종 투표를 갱신한다."""
        if state.last_point is not None:
            frame_gap = 1
            if frame_index is not None and state.last_frame >= 0:
                frame_gap = max(1, frame_index - state.last_frame)
            instant = (
                (point[0] - state.last_point[0]) / frame_gap,
                (point[1] - state.last_point[1]) / frame_gap,
            )
            if state.observations <= 1:
                state.velocity = instant
            else:
                alpha = self.velocity_smoothing
                state.velocity = (
                    alpha * instant[0] + (1 - alpha) * state.velocity[0],
                    alpha * instant[1] + (1 - alpha) * state.velocity[1],
                )
        state.last_point = point
        state.observations += 1
        if frame_index is not None:
            state.last_frame = frame_index
        if class_id is not None:
            state.class_id = class_id
            weight = float(confidence) if confidence is not None else 1.0
            state.class_votes[class_id] = state.class_votes.get(class_id, 0.0) + weight

    # ------------------------------------------------------------------ ID 재연결
    def _near_any_gate(self, state: TrackState, point: Point) -> bool:
        """이전 마지막 위치와 새 위치가 같은 통과선 근처(유한 구간 포함)인지."""
        assert state.last_point is not None
        for gate, gate_state in zip(self.gates, state.gates):
            # 짧은 조각도 측면만 알면 재연결 대상이다(안정화 전 ID 교체 대응).
            if gate_state.stable_side == 0 and gate_state.candidate_side == 0:
                continue
            if abs(gate.signed_distance(state.last_point)) > self.id_stitch_line_distance_px:
                continue
            if abs(gate.signed_distance(point)) > self.id_stitch_line_distance_px:
                continue
            margin = self.line_margin_px / gate.length * 1.5
            if not -margin <= gate.projection_ratio(state.last_point) <= 1 + margin:
                continue
            if not -margin <= gate.projection_ratio(point) <= 1 + margin:
                continue
            return True
        return False

    def _stitch_score(self, state: TrackState, point: Point, frame_index: int) -> float | None:
        """사라진 ID와 새 ID가 같은 차량일 가능성을 예상 위치 거리 점수로 평가한다."""
        if state.last_point is None or state.last_frame < 0 or state.finished:
            return None
        gap = frame_index - state.last_frame
        if gap <= 0 or gap > self.id_stitch_max_gap:
            return None
        in_transit = self.stitch_in_transit and state.entry is not None and state.exit is None
        if not in_transit and not self._near_any_gate(state, point):
            return None

        vx, vy = state.velocity
        speed = math.hypot(vx, vy)
        if speed > 0.5:
            along = ((point[0] - state.last_point[0]) * vx + (point[1] - state.last_point[1]) * vy) / speed
            if along < -self.id_stitch_direction_tolerance_px:
                return None

        predicted = (state.last_point[0] + vx * gap, state.last_point[1] + vy * gap)
        distance = math.dist(predicted, point)
        allowed_distance = self.id_stitch_max_distance_px + min(gap, 30) * 2.0
        if distance > allowed_distance:
            return None
        unstable_penalty = 12.0 if all(item.stable_side == 0 for item in state.gates) else 0.0
        return distance + gap * 0.35 + unstable_penalty

    @staticmethod
    def _promote_short_fragment(state: TrackState) -> None:
        """안정화 전에 ID가 바뀐 경우 이미 본 측면을 확정해 통과 판정을 이어 간다."""
        if state.last_point is None:
            return
        for gate_state in state.gates:
            if gate_state.stable_side == 0 and gate_state.candidate_side != 0:
                gate_state.settle(gate_state.candidate_side, state.last_point)

    # ------------------------------------------------------------------ 프레임 처리
    def process_frame(self, observations: Iterable[Observation], frame_index: int) -> list[GateCrossing]:
        """한 프레임의 모든 ID를 갱신한다. 동시 가림은 일대일로만 재연결한다."""
        items = [self._normalize_observation(item) for item in observations]
        self.last_recoveries = {}
        for track_id in [tid for tid, frame in self.retired_ids.items() if frame_index - frame > self.id_stitch_max_gap]:
            self.retired_ids.pop(track_id, None)
        stale_ids = [
            track_id
            for track_id, state in self.states.items()
            if state.last_frame >= 0 and frame_index - state.last_frame > self.id_stitch_max_gap
        ]
        for track_id in stale_ids:
            state = self.states.pop(track_id)
            self._close_state(track_id, state)
            self.retired_ids[track_id] = frame_index

        active_ids = {track_id for track_id, _, _, _ in items}
        if self.id_stitch_enabled:
            new_items = [item for item in items if item[0] not in self.states and item[0] not in self.retired_ids]
            candidates: list[tuple[float, int, int]] = []
            for new_id, point, _, _ in new_items:
                for old_id, state in self.states.items():
                    if old_id in active_ids:
                        continue
                    score = self._stitch_score(state, point, frame_index)
                    if score is not None:
                        candidates.append((score, old_id, new_id))
            matched_old: set[int] = set()
            matched_new: set[int] = set()
            for _, old_id, new_id in sorted(candidates):
                if old_id in matched_old or new_id in matched_new or old_id not in self.states:
                    continue
                recovered = self.states.pop(old_id)
                self._promote_short_fragment(recovered)
                self.states[new_id] = recovered
                self.retired_ids[old_id] = frame_index
                self.last_recoveries[new_id] = old_id
                self.recovery_count += 1
                matched_old.add(old_id)
                matched_new.add(new_id)

        crossings: list[GateCrossing] = []
        for track_id, point, class_id, confidence in items:
            crossings.extend(self.process_track(track_id, point, frame_index, class_id, confidence))
        return crossings

    @staticmethod
    def _normalize_observation(item: Observation) -> tuple[int, Point, int | None, float | None]:
        track_id, point, class_id = item[0], item[1], item[2]
        confidence = item[3] if len(item) > 3 else None
        return int(track_id), (float(point[0]), float(point[1])), class_id, confidence

    def process_track(
        self,
        track_id: int,
        point: Point,
        frame_index: int | None = None,
        class_id: int | None = None,
        confidence: float | None = None,
    ) -> list[GateCrossing]:
        """한 ID의 측면 변화를 통과선마다 누적해 확정된 통과만 반환한다."""
        if track_id in self.retired_ids:
            return []
        state = self.states.get(track_id)
        if state is None:
            state = self._new_state(track_id)
            self.states[track_id] = state
        self._remember_observation(state, point, frame_index, class_id, confidence)
        if state.finished:
            return []
        if state.entry is not None:
            entry_gate = self.gates[state.entry.gate_index]
            depth = entry_gate.signed_distance(point) * entry_gate.inside_side
            state.entry_depth = max(state.entry_depth, depth)
        crossings: list[GateCrossing] = []
        frame = frame_index if frame_index is not None else -1
        for gate_index, gate in enumerate(self.gates):
            crossing = self._update_gate(state, track_id, gate_index, gate, point, frame)
            if crossing is not None:
                crossings.append(crossing)
            if state.finished:
                break
        return crossings

    def _update_gate(
        self,
        state: TrackState,
        track_id: int,
        gate_index: int,
        gate: Gate,
        point: Point,
        frame: int,
    ) -> GateCrossing | None:
        """통과선 하나에 대한 측면 안정화, 빠른 통과, 유한 구간 검사를 수행한다."""
        gate_state = state.gates[gate_index]
        distance = gate.signed_distance(point)
        if abs(distance) <= self.deadband_px:
            return None
        current_side = 1 if distance > 0 else -1

        if gate_state.stable_side == 0:
            if current_side == gate_state.candidate_side:
                gate_state.candidate_frames += 1
            else:
                gate_state.candidate_side = current_side
                gate_state.candidate_frames = 1
            if gate_state.candidate_frames >= self.min_stable_frames:
                gate_state.settle(current_side, point)
            return None

        if current_side == gate_state.stable_side:
            gate_state.anchor_point = point
            gate_state.reset_candidate()
            return None

        moving_away_speed = gate.normal_component(state.velocity) * current_side
        if current_side == gate_state.candidate_side:
            gate_state.candidate_frames += 1
            gate_state.candidate_min_normal_speed = min(gate_state.candidate_min_normal_speed, moving_away_speed)
        else:
            gate_state.candidate_side = current_side
            gate_state.candidate_frames = 1
            gate_state.candidate_min_normal_speed = moving_away_speed
            gate_state.crossing_start = point

        required_frames = self.min_stable_frames
        if self.fast_crossing_enabled and gate_state.candidate_min_normal_speed >= self.fast_crossing_min_normal_speed_px:
            required_frames = self.fast_crossing_stable_frames
        if gate_state.candidate_frames < required_frames or gate_state.anchor_point is None:
            return None

        # 실제로 선을 지난 지점(반대편 직전·직후 관측 사이)이 유한 통과선 위여야 한다.
        start = gate_state.crossing_start or point
        ratio = gate.crossing_ratio(gate_state.anchor_point, start)
        margin = self.line_margin_px / gate.length
        if ratio is None or not -margin <= ratio <= 1 + margin:
            # 연장선만 지나간 경우: 집계하지 않고 새 측면을 확정해 나중의 오집계를 막는다.
            gate_state.settle(current_side, point)
            return None

        motion = abs(distance - gate.signed_distance(gate_state.anchor_point))
        if motion < self.min_crossing_motion_px:
            return None

        direction = gate.direction_to(current_side)
        gate_state.settle(current_side, point)
        crossing = GateCrossing(track_id, gate_index, direction, frame, point, state.voted_class(), ratio)
        return self._register(state, track_id, crossing)

    def _register(self, state: TrackState, track_id: int, crossing: GateCrossing) -> GateCrossing | None:
        """집계 방식에 따라 통과를 채택하고 이동(Trip)을 갱신한다."""
        if self.count_mode == "once":
            state.gates[crossing.gate_index].counted = True
            state.finished = True
            if crossing.direction == INBOUND:
                state.entry = crossing
            else:
                state.exit = crossing
            return crossing

        if crossing.direction == INBOUND:
            if state.entry is not None or state.exit is not None:
                self.ignored_crossings += 1
                return None
            state.entry = crossing
            state.entry_depth = 0.0
            return crossing

        if state.exit is not None:
            self.ignored_crossings += 1
            return None
        if (
            state.entry is not None
            and state.entry.gate_index == crossing.gate_index
            and state.entry_depth < self.uturn_min_depth_px
        ):
            # 정지선 근처에서 흔들린 박스를 유턴으로 오인하지 않는다.
            self.ignored_crossings += 1
            return None
        state.exit = crossing
        state.finished = True
        self.completed_trips.append(Trip(state.first_track_id, track_id, state.voted_class(), state.entry, crossing))
        return crossing


class DirectionCounter(IntersectionCounter):
    """기존 2방향 API 호환 래퍼: 통과선 1개, ID당 한 번, 유입/유출 문자열 반환."""

    def __init__(
        self,
        line: Sequence[Sequence[float]],
        deadband_px: float = 8.0,
        min_stable_frames: int = 3,
        min_horizontal_motion_px: float = 12.0,
        line_margin_px: float = 35.0,
        increasing_x_direction: str = OUTBOUND,
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
        a, b = _to_point(line[0]), _to_point(line[1])
        gate = Gate("A", "통과선", a, b, legacy_inside_side(a, b, increasing_x_direction))
        super().__init__(
            [gate],
            deadband_px=deadband_px,
            min_stable_frames=min_stable_frames,
            min_crossing_motion_px=min_horizontal_motion_px,
            line_margin_px=line_margin_px,
            id_stitch_enabled=id_stitch_enabled,
            id_stitch_max_gap=id_stitch_max_gap,
            id_stitch_max_distance_px=id_stitch_max_distance_px,
            id_stitch_line_distance_px=id_stitch_line_distance_px,
            id_stitch_direction_tolerance_px=id_stitch_direction_tolerance_px,
            fast_crossing_enabled=fast_crossing_enabled,
            fast_crossing_min_normal_speed_px=fast_crossing_min_normal_speed_px,
            fast_crossing_stable_frames=fast_crossing_stable_frames,
            count_mode="once",
        )

    def update_frame(self, observations: Iterable[Observation], frame_index: int) -> dict[int, str]:
        """한 프레임을 갱신하고 {추적ID: 유입/유출}을 반환한다."""
        return {crossing.track_id: crossing.direction for crossing in self.process_frame(observations, frame_index)}

    def update(
        self,
        track_id: int,
        point: Point,
        frame_index: int | None = None,
        class_id: int | None = None,
    ) -> str | None:
        """한 ID를 갱신하고 확정된 유입·유출만 한 번 반환한다."""
        crossings = self.process_track(track_id, point, frame_index, class_id)
        return crossings[0].direction if crossings else None
