"""여러 교차로 형태(2방향·T자·십자·회전교차로)에 대한 통과 판정과 결과 저장 테스트."""

from __future__ import annotations

import csv
import math
import tempfile
import unittest
from pathlib import Path

from intersection_counter import (
    INBOUND,
    OUTBOUND,
    TRIP_COMPLETE,
    TRIP_NO_EXIT,
    DirectionCounter,
    Gate,
    IntersectionCounter,
    MovementClassifier,
    build_gates,
    gates_center,
    screen_direction_text,
)
from two_way_counter import (
    build_counter,
    default_config,
    empty_counts,
    resolve_pixel_scale,
    write_gate_summary,
    write_movement_summary,
    write_od_matrix,
)

ROI = [[150, 150], [850, 150], [850, 850], [150, 850]]
FOUR_WAY = [
    {"name": "북", "line": [[420, 300], [580, 300]]},
    {"name": "동", "line": [[700, 420], [700, 580]]},
    {"name": "남", "line": [[420, 700], [580, 700]]},
    {"name": "서", "line": [[300, 420], [300, 580]]},
]
T_JUNCTION = [
    {"name": "서", "line": [[300, 420], [300, 580]]},
    {"name": "동", "line": [[700, 420], [700, 580]]},
    {"name": "남", "line": [[420, 700], [580, 700]]},
]


def read_rows(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as file:
        return list(csv.reader(file))


def read_dicts(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def polyline(points, step=10.0):
    """꺾은선을 일정 간격의 프레임별 하단 중앙점으로 바꾼다."""
    result = [points[0]]
    for start, end in zip(points, points[1:]):
        length = math.dist(start, end)
        count = max(1, int(length // step))
        for index in range(1, count + 1):
            t = index / count
            result.append((start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t))
    return result


def drive(counter, track_id, points, start_frame, class_id=2, confidence=0.9):
    """한 차량을 프레임 순서대로 판정기에 넣고 통과 목록과 다음 프레임을 반환한다."""
    crossings = []
    frame = start_frame
    for point in points:
        crossings += counter.process_frame([(track_id, point, class_id, confidence)], frame)
        frame += 1
    return crossings, frame


def all_trips(counter):
    return counter.drain_trips() + counter.finish()


class GateGeometryTests(unittest.TestCase):
    def test_default_inside_points_to_intersection_center(self):
        gates = build_gates({"gates": FOUR_WAY, "roi_polygon": ROI})
        center = gates_center(gates)
        self.assertEqual(center, (500.0, 500.0))
        for gate in gates:
            nx, ny = gate.inward_normal()
            to_center = (center[0] - gate.midpoint[0], center[1] - gate.midpoint[1])
            self.assertGreater(nx * to_center[0] + ny * to_center[1], 0, gate.name)

    def test_legacy_config_keeps_left_to_right_as_outgoing(self):
        config = {"counting_line": [[790, 790], [1035, 545]], "increasing_x_direction": "유출"}
        gates = build_gates(config)
        self.assertEqual(len(gates), 1)
        self.assertEqual(screen_direction_text(gates[0]), "화면 오른쪽→왼쪽")
        counter = build_counter(config, gates)
        self.assertEqual(counter.count_mode, "once")
        crossings, _ = drive(counter, 1, polyline([(850, 667), (980, 667)]), 0)
        self.assertEqual([c.direction for c in crossings], [OUTBOUND])
        crossings, _ = drive(counter, 2, polyline([(980, 667), (850, 667)]), 100)
        self.assertEqual([c.direction for c in crossings], [INBOUND])

    def test_swapped_legacy_direction(self):
        gates = build_gates({"counting_line": [[50, 0], [50, 100]], "increasing_x_direction": "유입"})
        self.assertEqual(screen_direction_text(gates[0]), "화면 왼쪽→오른쪽")

    def test_duplicate_names_and_explicit_sides(self):
        gates = build_gates({
            "gates": [
                {"name": "A", "line": [[0, 0], [0, 100]], "inside_side": -1},
                {"name": "A", "line": [[200, 0], [200, 100]]},
            ]
        })
        self.assertEqual([gate.name for gate in gates], ["A", "A(B)"])
        self.assertEqual(gates[0].inside_side, -1)

    def test_rejects_too_short_gate(self):
        with self.assertRaises(ValueError):
            build_gates({"gates": [{"line": [[0, 0], [3, 3]]}]})

    def test_pixel_scale(self):
        self.assertEqual(resolve_pixel_scale({"pixel_scale": "auto"}, 1080), 1.0)
        self.assertAlmostEqual(resolve_pixel_scale({}, 720), 720 / 1080)
        self.assertEqual(resolve_pixel_scale({"pixel_scale": 1.5}, 720), 1.5)


class CrossingRuleTests(unittest.TestCase):
    def test_horizontal_line_counts_vertical_motion(self):
        # 기존 판정은 가로 이동량만 보아 세로로 움직이는 차량을 놓쳤다.
        counter = DirectionCounter([[0, 50], [100, 50]], deadband_px=1, min_stable_frames=2, min_horizontal_motion_px=5)
        events = [counter.update(1, (50, y)) for y in range(20, 85, 5)]
        self.assertEqual([e for e in events if e], [OUTBOUND])
        events = [counter.update(2, (50, y)) for y in range(80, 15, -5)]
        self.assertEqual([e for e in events if e], [INBOUND])

    def test_crossing_line_extension_never_counts_later(self):
        gate = Gate("A", "A", (300, 400), (300, 500), inside_side=1)
        counter = IntersectionCounter([gate], deadband_px=2, min_stable_frames=2, line_margin_px=0)
        path = polyline([(200, 600), (400, 600), (400, 450)])
        crossings, _ = drive(counter, 1, path, 0)
        self.assertEqual(crossings, [])

    def test_majority_class_vote(self):
        gate = Gate("A", "A", (50, 0), (50, 100), inside_side=1)
        counter = IntersectionCounter([gate], deadband_px=1, min_stable_frames=2)
        frames = [((20, 50), 7, 0.3), ((24, 50), 7, 0.3), ((30, 50), 2, 0.9), ((36, 50), 2, 0.9),
                  ((42, 50), 2, 0.9), ((60, 50), 2, 0.9), ((70, 50), 7, 0.4)]
        crossings = []
        for frame, (point, class_id, conf) in enumerate(frames):
            crossings += counter.process_frame([(1, point, class_id, conf)], frame)
        self.assertEqual(len(crossings), 1)
        self.assertEqual(crossings[0].class_id, 2)


class FourWayTests(unittest.TestCase):
    def setUp(self):
        self.gates = build_gates({"gates": FOUR_WAY, "roi_polygon": ROI})
        self.classifier = MovementClassifier(self.gates)
        self.counter = IntersectionCounter(self.gates)

    def names(self, trip):
        entry = self.gates[trip.entry_gate].name if trip.entry_gate is not None else None
        exit_ = self.gates[trip.exit_gate].name if trip.exit_gate is not None else None
        return entry, exit_

    def test_every_movement_from_south(self):
        paths = {
            "직진": [(520, 820), (520, 180)],
            "우회전": [(540, 820), (540, 540), (820, 540)],
            "좌회전": [(520, 820), (520, 460), (180, 460)],
            "유턴": [(540, 820), (540, 560), (460, 560), (460, 820)],
        }
        frame = 0
        crossings = []
        for track_id, points in enumerate(paths.values(), start=1):
            found, frame = drive(self.counter, track_id, polyline(points), frame)
            crossings += found
            frame += 200
        trips = all_trips(self.counter)
        self.assertEqual(len(trips), 4)
        self.assertTrue(all(trip.status == TRIP_COMPLETE for trip in trips))
        labels = {self.classifier.label(t.entry_gate, t.exit_gate): self.names(t) for t in trips}
        self.assertEqual(labels, {
            "직진": ("남", "북"), "우회전": ("남", "동"), "좌회전": ("남", "서"), "유턴": ("남", "남"),
        })
        self.assertEqual(sum(c.direction == INBOUND for c in crossings), 4)
        self.assertEqual(sum(c.direction == OUTBOUND for c in crossings), 4)

    def test_rank_classification_on_rotated_skewed_view(self):
        # 통과선을 비스듬히 그려도 4갈래는 중심 기준 순서로 판정한다.
        gates = build_gates({"gates": [
            {"name": "위", "line": [[430, 180], [600, 230]]},
            {"name": "오른쪽", "line": [[780, 420], [830, 600]]},
            {"name": "아래", "line": [[380, 820], [600, 760]]},
            {"name": "왼쪽", "line": [[180, 380], [230, 560]]},
        ]})
        classifier = MovementClassifier(gates)
        self.assertEqual(classifier.label(2, 0), "직진")
        self.assertEqual(classifier.label(2, 1), "우회전")
        self.assertEqual(classifier.label(2, 3), "좌회전")
        self.assertEqual(classifier.label(3, 2), "우회전")

    def test_movement_label_override(self):
        classifier = MovementClassifier(self.gates, overrides={"남>북": "직진(버스전용)", "A→C": "특수"})
        self.assertEqual(classifier.label(2, 0), "직진(버스전용)")
        self.assertEqual(classifier.label(0, 2), "특수")

    def test_id_switch_inside_intersection_keeps_the_trip(self):
        first, frame = drive(self.counter, 1, polyline([(520, 820), (520, 520)]), 0)
        second, _ = drive(self.counter, 7, polyline([(520, 460), (520, 180)]), frame + 5)
        self.assertEqual(self.counter.recovery_count, 1)
        trips = all_trips(self.counter)
        self.assertEqual(len(trips), 1)
        self.assertEqual(self.names(trips[0]), ("남", "북"))
        self.assertEqual((trips[0].track_id, trips[0].last_track_id), (1, 7))
        self.assertEqual([c.direction for c in first + second], [INBOUND, OUTBOUND])

    def test_lost_vehicle_becomes_unknown_destination(self):
        _, frame = drive(self.counter, 3, polyline([(520, 820), (520, 520)]), 0)
        trips = []
        for empty_frame in range(frame, frame + 95):
            self.counter.process_frame([], empty_frame)
            trips += self.counter.drain_trips()
        self.assertEqual(len(trips), 1)
        self.assertEqual(trips[0].status, TRIP_NO_EXIT)
        self.assertEqual(self.names(trips[0]), ("남", None))

    def test_video_end_closes_vehicle_in_transit(self):
        drive(self.counter, 4, polyline([(520, 820), (520, 520)]), 0)
        trips = self.counter.finish()
        self.assertEqual([trip.status for trip in trips], [TRIP_NO_EXIT])

    def test_stop_line_jitter_is_not_a_u_turn(self):
        path = polyline([(520, 820), (520, 670), (520, 730)])
        crossings, _ = drive(self.counter, 5, path, 0)
        self.assertEqual([c.direction for c in crossings], [INBOUND])
        self.assertEqual(self.counter.ignored_crossings, 1)
        self.assertEqual([t.status for t in all_trips(self.counter)], [TRIP_NO_EXIT])

    def test_new_vehicle_is_not_attached_to_finished_trip(self):
        _, frame = drive(self.counter, 1, polyline([(520, 820), (520, 180)]), 0)
        self.counter.process_frame([(9, (520, 170), 2, 0.9)], frame + 1)
        self.assertEqual(self.counter.last_recoveries, {})


class TJunctionTests(unittest.TestCase):
    def test_t_junction_turns_use_gate_directions(self):
        gates = build_gates({"gates": T_JUNCTION})
        classifier = MovementClassifier(gates)
        counter = IntersectionCounter(gates)
        paths = [
            [(180, 520), (820, 520)],
            [(180, 520), (460, 520), (460, 820)],
            [(540, 820), (540, 460), (180, 460)],
            [(820, 460), (520, 460), (520, 820)],
        ]
        frame = 0
        for track_id, points in enumerate(paths, start=1):
            _, frame = drive(counter, track_id, polyline(points), frame)
            frame += 200
        trips = sorted(all_trips(counter), key=lambda trip: trip.track_id)
        result = [(gates[t.entry_gate].name, gates[t.exit_gate].name, classifier.label(t.entry_gate, t.exit_gate)) for t in trips]
        self.assertEqual(result, [
            ("서", "동", "직진"), ("서", "남", "우회전"), ("남", "서", "좌회전"), ("동", "남", "좌회전"),
        ])


class OutputTests(unittest.TestCase):
    def test_gate_movement_and_od_files(self):
        gates = build_gates({"gates": T_JUNCTION})
        classifier = MovementClassifier(gates)
        gate_counts = {slot: [{INBOUND: 0, OUTBOUND: 0} for _ in gates] for slot in range(96)}
        gate_counts[4][0][INBOUND] = 2
        gate_counts[4][1][OUTBOUND] = 1
        movements = {slot: {} for slot in range(96)}
        movements[4][(0, 1)] = 1
        movements[4][(0, None)] = 1
        totals = {(0, 1): 1, (0, None): 1}
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            write_gate_summary(base / "g.csv", gate_counts, gates)
            write_movement_summary(base / "m.csv", movements, gates, classifier)
            write_od_matrix(base / "od.csv", totals, gates)
            gate_rows = read_rows(base / "g.csv")
            movement_rows = read_rows(base / "m.csv")
            od_rows = read_rows(base / "od.csv")
        self.assertEqual(gate_rows[0][:3], ["시간", "서 유입", "서 유출"])
        self.assertEqual(gate_rows[5][0], "01:00~01:15")
        self.assertEqual(gate_rows[-1][-2:], ["2", "1"])
        self.assertIn("서→동(직진)", movement_rows[0])
        self.assertEqual(movement_rows[-1][-2:], ["1", "2"])
        self.assertEqual(od_rows[1], ["서", "0", "1", "0", "1", "2"])


class EndToEndTests(unittest.TestCase):
    """가짜 YOLO 모델로 영상 읽기부터 CSV·요약 저장까지 전체 경로를 검증한다."""

    @classmethod
    def setUpClass(cls):
        try:
            import cv2  # noqa: F401
            import numpy  # noqa: F401
        except ImportError as error:  # pragma: no cover
            raise unittest.SkipTest(f"OpenCV/numpy 없음: {error}")

    def make_video(self, path, frames=72):
        import cv2
        import numpy as np

        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 30.0, (640, 480))
        for index in range(frames):
            image = np.full((480, 640, 3), 40 + index % 20, dtype=np.uint8)
            writer.write(image)
        writer.release()
        capture = cv2.VideoCapture(str(path))
        count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        capture.release()
        if count <= 0:
            self.skipTest("이 OpenCV에서 mp4v 쓰기를 지원하지 않습니다.")

    @staticmethod
    def fake_model(script):
        import numpy as np

        class Boxes:
            def __init__(self, rows):
                array = np.array(rows, dtype=float).reshape(-1, 7)
                self.xyxy = array[:, :4]
                self.id = array[:, 4] if len(rows) else None
                self.cls = array[:, 5]
                self.conf = array[:, 6]

        class Result:
            def __init__(self, rows):
                self.boxes = Boxes(rows)

        class Model:
            predictor = None

            def __init__(self):
                self.calls = 0

            def track(self, image, **options):
                rows = script.get(self.calls, [])
                self.calls += 1
                return [Result(rows)]

        return Model()

    @staticmethod
    def add_vehicle(script, track_ids, points, start_frame, gap_after=None, gap=0):
        """하단 중앙점 경로를 박스 행으로 바꿔 프레임별 스크립트에 넣는다."""
        frame = start_frame
        for index, (x, y) in enumerate(points):
            track_id = track_ids[0] if gap_after is None or index < gap_after else track_ids[1]
            if gap_after is not None and index == gap_after:
                frame += gap
            script.setdefault(frame, []).append([x - 20, y - 30, x + 20, y, track_id, 2, 0.9])
            frame += 1

    def test_four_way_pipeline(self):
        from two_way_counter import BatchAnalyzer

        script: dict[int, list] = {}
        self.add_vehicle(script, (1,), polyline([(330, 455), (330, 30)], 12), 0)
        self.add_vehicle(script, (2,), polyline([(30, 280), (300, 280), (300, 455)], 12), 3)
        self.add_vehicle(script, (30, 31), polyline([(610, 200), (30, 200)], 12), 6, gap_after=24, gap=4)
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            video = base / "01_0000-0300_3배속.mp4"
            self.make_video(video)
            config = default_config(str(base))
            config.update({
                "output_dir": str(base / "out"),
                "device": "cpu",
                "roi_crop_enabled": False,
                "intersection_type": "4way",
                "roi_polygon": [[20, 20], [620, 20], [620, 460], [20, 460]],
                "gates": [
                    {"name": "북", "line": [[260, 100], [380, 100]]},
                    {"name": "동", "line": [[540, 180], [540, 300]]},
                    {"name": "남", "line": [[260, 380], [380, 380]]},
                    {"name": "서", "line": [[100, 180], [100, 300]]},
                ],
                "save_review_video": True,
            })
            logs: list[str] = []
            model = self.fake_model(script)
            analyzer = BatchAnalyzer(config, base, log=logs.append, model_loader=lambda path: model)
            summary, events = analyzer.run(video_paths=[video])
            out = base / "out"
            summary_rows = read_rows(summary)
            trips = read_dicts(out / "차량별_이동경로.csv")
            od_rows = read_rows(out / "OD_행렬.csv")
            report = (out / "분석요약.txt").read_text(encoding="utf-8-sig")
            images = list((out / "event_images").glob("*.jpg"))
            review_exists = (out / "검수_01_0000-0300_3배속.mp4").exists()
            event_rows = read_dicts(events)
        self.assertEqual(summary_rows[1], ["00:00~00:15", "3", "3", "6"])
        moves = sorted((row["출발"], row["도착"], row["회전"], row["상태"]) for row in trips)
        self.assertEqual(moves, [
            ("남", "북", "직진", "완료"), ("동", "서", "직진", "완료"), ("서", "남", "우회전", "완료"),
        ])
        self.assertTrue(any("가림 ID 연결: 30 → 31" in line for line in logs))
        self.assertEqual(od_rows[0][:5], ["출발＼도착", "북", "동", "남", "서"])
        self.assertIn("합계: 유입 3 / 유출 3", report)
        self.assertEqual(len(images), 6)
        self.assertTrue(review_exists)
        self.assertEqual({row["통과선"] for row in event_rows}, {"북", "동", "남", "서"})

    def test_legacy_single_line_pipeline(self):
        from two_way_counter import BatchAnalyzer

        script: dict[int, list] = {}
        self.add_vehicle(script, (1,), polyline([(150, 250), (500, 250)], 12), 0)
        self.add_vehicle(script, (2,), polyline([(500, 300), (150, 300)], 12), 2)
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            video = base / "02_0300-0600_3배속.mp4"
            self.make_video(video, frames=40)
            config = default_config(str(base))
            config.update({
                "output_dir": str(base / "out"),
                "device": "cpu",
                "roi_polygon": [[20, 20], [620, 20], [620, 460], [20, 460]],
                "counting_line": [[320, 150], [320, 420]],
                "save_event_images": False,
            })
            model = self.fake_model(script)
            analyzer = BatchAnalyzer(config, base, log=lambda message: None, model_loader=lambda path: model)
            summary, _ = analyzer.run(video_paths=[video])
            rows = read_rows(summary)
            movement_exists = (base / "out" / "15분_방향별_집계.csv").exists()
            report = (base / "out" / "분석요약.txt").read_text(encoding="utf-8-sig")
        self.assertEqual(rows[13], ["03:00~03:15", "1", "1", "2"])
        self.assertFalse(movement_exists)
        self.assertIn("유입 방향: 화면 오른쪽→왼쪽", report)


if __name__ == "__main__":
    unittest.main()


class CalibrationTests(unittest.TestCase):
    """OpenCV 창 함수를 대체해 클릭·키 입력 시나리오로 설정 저장을 검증한다."""

    def test_four_way_calibration_saves_gates_names_and_flip(self):
        try:
            import cv2
            import numpy as np
        except ImportError as error:  # pragma: no cover
            self.skipTest(str(error))
        from unittest import mock

        import calibrate as calibration
        from two_way_counter import load_config, save_config

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            writer = cv2.VideoWriter(str(base / "04_0900-1200_3배속.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), 30.0, (1000, 1000))
            for _ in range(10):
                writer.write(np.zeros((1000, 1000, 3), dtype=np.uint8))
            writer.release()
            config_path = base / "config.json"
            save_config(config_path, {**default_config(str(base)), "intersection_type": "4way"})

            callbacks = {}
            clicks = [
                [(150, 150), (850, 150), (850, 850), (150, 850)],
                [(420, 300), (580, 300), (700, 420), (700, 580), (420, 700), (580, 700), (300, 420), (300, 580)],
                [],
            ]
            keys = iter([13, ord("f"), 13])

            def fake_wait_key(delay):
                del delay
                for x, y in clicks.pop(0) if clicks else []:
                    callbacks["mouse"](cv2.EVENT_LBUTTONDOWN, round(x * 0.82), round(y * 0.82), 0, None)
                return next(keys, 27)

            with mock.patch.object(cv2, "namedWindow"), mock.patch.object(cv2, "imshow"), \
                    mock.patch.object(cv2, "destroyAllWindows"), \
                    mock.patch.object(cv2, "setMouseCallback", side_effect=lambda w, f: callbacks.update(mouse=f)), \
                    mock.patch.object(cv2, "waitKey", side_effect=fake_wait_key):
                preview = calibration.calibrate(config_path, "4way", ["북", "동", "남", "서"])
            saved = load_config(config_path)
            self.assertTrue(preview is not None and preview.exists())
        gates = build_gates(saved)
        self.assertEqual([gate.name for gate in gates], ["북", "동", "남", "서"])
        self.assertEqual(saved["intersection_type"], "4way")
        center = gates_center(gates)
        inward = [gate.inward_normal() for gate in gates]
        toward = [
            n[0] * (center[0] - gate.midpoint[0]) + n[1] * (center[1] - gate.midpoint[1]) > 0
            for n, gate in zip(inward, gates)
        ]
        self.assertEqual(toward, [True, True, True, False])  # 마지막 선만 F로 뒤집었다.
        self.assertEqual(saved["counting_line"], saved["gates"][0]["line"])
