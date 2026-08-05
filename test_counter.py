import csv
import tempfile
import unittest
from pathlib import Path

from two_way_counter import CountEvent, DirectionCounter, FramePrefetcher, box_meets_minimum_size, point_in_polygon, reset_model_tracking_state, roi_crop_bounds, slot_label, video_start_seconds, write_events, write_summary, write_video_results


class DirectionCounterTests(unittest.TestCase):
    def test_fast_crossing_uses_two_post_line_frames(self):
        counter = DirectionCounter(
            [[50, 0], [50, 100]],
            deadband_px=1,
            min_stable_frames=3,
            min_horizontal_motion_px=5,
            fast_crossing_min_normal_speed_px=10,
            fast_crossing_stable_frames=2,
        )
        for frame, x in enumerate((30, 32, 34)):
            self.assertIsNone(counter.update(1, (x, 50), frame_index=frame))
        self.assertIsNone(counter.update(1, (60, 50), frame_index=3))
        self.assertEqual(counter.update(1, (75, 50), frame_index=4), "유출")

    def test_slow_crossing_still_requires_three_post_line_frames(self):
        counter = DirectionCounter(
            [[50, 0], [50, 100]],
            deadband_px=1,
            min_stable_frames=3,
            min_horizontal_motion_px=5,
            fast_crossing_min_normal_speed_px=10,
            fast_crossing_stable_frames=2,
        )
        for frame, x in enumerate((40, 42, 44)):
            self.assertIsNone(counter.update(2, (x, 50), frame_index=frame))
        self.assertIsNone(counter.update(2, (52, 50), frame_index=3))
        self.assertIsNone(counter.update(2, (55, 50), frame_index=4))
        self.assertEqual(counter.update(2, (58, 50), frame_index=5), "유출")

    def test_counts_left_to_right_as_outgoing(self):
        counter = DirectionCounter([[50, 0], [50, 100]], deadband_px=1, min_stable_frames=2, min_horizontal_motion_px=5)
        points = [(30, 50), (32, 50), (48, 50), (55, 50), (65, 50)]
        events = [counter.update(1, point) for point in points]
        self.assertEqual(events[-1], "유출")
        self.assertIsNone(counter.update(1, (80, 50)))

    def test_counts_right_to_left_as_incoming(self):
        counter = DirectionCounter([[50, 0], [50, 100]], deadband_px=1, min_stable_frames=2, min_horizontal_motion_px=5)
        points = [(70, 50), (68, 50), (52, 50), (45, 50), (35, 50)]
        events = [counter.update(2, point) for point in points]
        self.assertEqual(events[-1], "유입")

    def test_ignores_crossing_outside_finite_line(self):
        counter = DirectionCounter([[50, 20], [50, 80]], deadband_px=1, min_stable_frames=1, min_horizontal_motion_px=2, line_margin_px=0)
        self.assertIsNone(counter.update(3, (40, 150)))
        self.assertIsNone(counter.update(3, (60, 150)))

    def test_recovers_id_switch_across_counting_line(self):
        counter = DirectionCounter(
            [[50, 0], [50, 100]],
            deadband_px=1,
            min_stable_frames=2,
            min_horizontal_motion_px=5,
            id_stitch_max_gap=10,
            id_stitch_max_distance_px=50,
            id_stitch_line_distance_px=80,
        )
        counter.update_frame([(1, (30, 50), 2)], 0)
        counter.update_frame([(1, (36, 50), 2)], 1)
        counter.update_frame([(1, (43, 50), 2)], 2)

        first_new_frame = counter.update_frame([(9, (55, 50), 2)], 5)
        self.assertEqual(counter.last_recoveries, {9: 1})
        self.assertEqual(first_new_frame, {})
        self.assertEqual(counter.update_frame([(9, (63, 50), 2)], 6), {9: "유출"})

    def test_recovers_id_switch_before_pre_line_side_is_stable(self):
        counter = DirectionCounter(
            [[50, 0], [50, 100]],
            deadband_px=1,
            min_stable_frames=3,
            min_horizontal_motion_px=5,
            id_stitch_max_gap=10,
            id_stitch_max_distance_px=50,
            id_stitch_line_distance_px=80,
        )
        counter.update_frame([(1, (42, 50), 2)], 0)
        self.assertEqual(counter.update_frame([(9, (55, 50), 2)], 2), {})
        self.assertEqual(counter.last_recoveries, {9: 1})
        self.assertEqual(counter.update_frame([(9, (61, 50), 2)], 3), {})
        self.assertEqual(counter.update_frame([(9, (67, 50), 2)], 4), {9: "유출"})

    def test_accumulates_post_line_confirmation_across_repeated_id_switches(self):
        counter = DirectionCounter(
            [[50, 0], [50, 100]],
            deadband_px=1,
            min_stable_frames=3,
            min_horizontal_motion_px=5,
            id_stitch_max_gap=10,
            id_stitch_max_distance_px=50,
            id_stitch_line_distance_px=80,
        )
        counter.update_frame([(1, (42, 50), 2)], 0)
        self.assertEqual(counter.update_frame([(9, (55, 50), 2)], 2), {})
        self.assertEqual(counter.update_frame([(10, (61, 50), 2)], 3), {})
        self.assertEqual(counter.update_frame([(11, (67, 50), 2)], 4), {11: "유출"})
        self.assertEqual(counter.last_recoveries, {11: 10})

    def test_does_not_stitch_vehicle_moving_in_opposite_direction(self):
        counter = DirectionCounter(
            [[50, 0], [50, 100]],
            deadband_px=1,
            min_stable_frames=2,
            min_horizontal_motion_px=5,
            id_stitch_max_gap=10,
            id_stitch_max_distance_px=80,
            id_stitch_line_distance_px=100,
        )
        counter.update_frame([(1, (30, 50), 2)], 0)
        counter.update_frame([(1, (38, 50), 2)], 1)
        counter.update_frame([(9, (20, 50), 2)], 3)
        self.assertEqual(counter.last_recoveries, {})
        self.assertIn(1, counter.states)
        self.assertIn(9, counter.states)

    def test_matches_simultaneous_new_ids_one_to_one_by_position(self):
        counter = DirectionCounter(
            [[50, 0], [50, 100]],
            deadband_px=1,
            min_stable_frames=1,
            min_horizontal_motion_px=5,
            id_stitch_max_gap=10,
            id_stitch_max_distance_px=80,
            id_stitch_line_distance_px=100,
        )
        counter.update_frame([(1, (30, 25), 2), (2, (30, 75), 2)], 0)
        counter.update_frame([(1, (38, 25), 2), (2, (38, 75), 2)], 1)
        counter.update_frame([(10, (55, 73), 2), (11, (55, 27), 2)], 3)
        self.assertEqual(counter.last_recoveries, {10: 2, 11: 1})
        self.assertEqual(set(counter.states), {10, 11})

    def test_stitches_across_any_vehicle_class_change(self):
        counter = DirectionCounter(
            [[50, 0], [50, 100]],
            min_stable_frames=1,
            id_stitch_max_gap=10,
            id_stitch_max_distance_px=80,
            id_stitch_line_distance_px=100,
        )
        counter.update_frame([(1, (40, 50), 2)], 0)
        counter.update_frame([(9, (55, 50), 3)], 2)
        self.assertEqual(counter.last_recoveries, {9: 1})

    def test_does_not_attach_a_new_vehicle_to_an_already_counted_track(self):
        counter = DirectionCounter(
            [[50, 0], [50, 100]],
            deadband_px=1,
            min_stable_frames=1,
            min_horizontal_motion_px=5,
            id_stitch_max_gap=10,
            id_stitch_max_distance_px=80,
            id_stitch_line_distance_px=100,
        )
        counter.update_frame([(1, (35, 50), 2)], 0)
        self.assertEqual(counter.update_frame([(1, (60, 50), 2)], 1), {1: "유출"})
        counter.update_frame([(9, (58, 50), 2)], 2)
        self.assertEqual(counter.last_recoveries, {})

    def test_prunes_tracks_older_than_stitch_window(self):
        counter = DirectionCounter(
            [[50, 0], [50, 100]],
            min_stable_frames=1,
            id_stitch_max_gap=2,
        )
        counter.update_frame([(1, (40, 50), 2)], 0)
        counter.update_frame([], 3)
        self.assertNotIn(1, counter.states)
        self.assertIn(1, counter.retired_ids)
        counter.update_frame([], 6)
        self.assertNotIn(1, counter.retired_ids)


class UtilityTests(unittest.TestCase):
    def test_resets_trackers_without_replacing_predictor(self):
        class FakeTracker:
            def __init__(self):
                self.reset_count = 0

            def reset(self):
                self.reset_count += 1

        class FakePredictor:
            def __init__(self):
                self.trackers = [FakeTracker(), FakeTracker()]
                self.vid_path = ["old-a", "old-b"]
                self._feats = object()

        class FakeModel:
            def __init__(self):
                self.predictor = FakePredictor()

        model = FakeModel()
        original_predictor = model.predictor
        self.assertTrue(reset_model_tracking_state(model))
        self.assertIs(model.predictor, original_predictor)
        self.assertEqual([tracker.reset_count for tracker in model.predictor.trackers], [1, 1])
        self.assertEqual(model.predictor.vid_path, [None, None])
        self.assertIsNone(model.predictor._feats)

    def test_tracker_reset_is_noop_before_first_prediction(self):
        class EmptyModel:
            predictor = None

        self.assertFalse(reset_model_tracking_state(EmptyModel()))

    def test_frame_prefetcher_preserves_order_and_releases_capture(self):
        class FakeCapture:
            def __init__(self):
                self.items = iter(["a", "b", "c"])
                self.released = False

            def read(self):
                try:
                    return True, next(self.items)
                except StopIteration:
                    return False, None

            def release(self):
                self.released = True

        capture = FakeCapture()
        self.assertEqual(list(FramePrefetcher(capture, 10, buffer_size=2)), ["a", "b", "c"])
        self.assertTrue(capture.released)

    def test_roi_crop_bounds_adds_padding_and_clamps_to_frame(self):
        polygon = [[10, 20], [90, 20], [90, 80], [10, 80]]
        self.assertEqual(roi_crop_bounds(100, 100, polygon, 15), (0, 5, 100, 96))
        self.assertEqual(roi_crop_bounds(100, 100, polygon, 0), (10, 20, 91, 81))

    def test_minimum_box_size_rejects_tiny_detections(self):
        self.assertFalse(box_meets_minimum_size((0, 0, 34, 100), 35, 35, 2000))
        self.assertFalse(box_meets_minimum_size((0, 0, 100, 34), 35, 35, 2000))
        self.assertFalse(box_meets_minimum_size((0, 0, 40, 40), 35, 35, 2000))
        self.assertTrue(box_meets_minimum_size((0, 0, 40, 55), 35, 35, 2000))

    def test_video_start_and_slot_labels(self):
        self.assertEqual(video_start_seconds("03_0600-0900_3배속.mp4"), 6 * 3600)
        self.assertEqual(slot_label(0), "00:00~00:15")
        self.assertEqual(slot_label(95), "23:45~00:00")

    def test_polygon(self):
        polygon = [[0, 0], [100, 0], [100, 100], [0, 100]]
        self.assertTrue(point_in_polygon((50, 50), polygon))
        self.assertFalse(point_in_polygon((150, 50), polygon))

    def test_csv_outputs(self):
        counts = {slot: {"유입": 0, "유출": 0} for slot in range(96)}
        counts[0] = {"유입": 2, "유출": 1}
        event = CountEvent("01_0000-0300_3배속.mp4", 30, 1.0, 3.0, 0, "유입", 4, 2, "승용차", 0.9, 10, 20)
        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / "summary.csv"
            events = Path(directory) / "events.csv"
            write_summary(summary, counts)
            write_events(events, [event])
            with summary.open(encoding="utf-8-sig") as file:
                rows = list(csv.reader(file))
            self.assertEqual(rows[1], ["00:00~00:15", "2", "1", "3"])
            self.assertIn("00:00:03", events.read_text(encoding="utf-8-sig"))

    def test_writes_separate_csv_pair_for_each_video(self):
        counts = {slot: {"유입": 0, "유출": 0} for slot in range(96)}
        counts[0]["유입"] = 1
        event = CountEvent("01_0000-0300_3배속.mp4", 30, 1.0, 3.0, 0, "유입", 4, 2, "차량", 0.9, 10, 20)
        with tempfile.TemporaryDirectory() as directory:
            summary, events = write_video_results(
                Path(directory),
                Path(event.video),
                counts,
                [event],
            )
            self.assertEqual(summary.name, "01_0000-0300_3배속_15분_유입유출_집계.csv")
            self.assertEqual(events.name, "01_0000-0300_3배속_차량별_통과기록.csv")
            self.assertTrue(summary.exists())
            self.assertIn("00:00:03", events.read_text(encoding="utf-8-sig"))

    def test_can_skip_detailed_csv_for_each_video(self):
        counts = {slot: {"유입": 0, "유출": 0} for slot in range(96)}
        with tempfile.TemporaryDirectory() as directory:
            summary, events = write_video_results(
                Path(directory),
                Path("01_0000-0300_3배속.mp4"),
                counts,
                [],
                save_detailed_records=False,
            )
            self.assertTrue(summary.exists())
            self.assertIsNone(events)
            self.assertFalse(
                (Path(directory) / "01_0000-0300_3배속_차량별_통과기록.csv").exists()
            )


if __name__ == "__main__":
    unittest.main()
