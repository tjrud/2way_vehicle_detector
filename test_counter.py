import csv
import tempfile
import unittest
from pathlib import Path

from two_way_counter import CountEvent, DirectionCounter, point_in_polygon, slot_label, video_start_seconds, write_events, write_summary


class DirectionCounterTests(unittest.TestCase):
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


class UtilityTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
