"""Behavioral tests for spatial geometry, tracking, and temporal rules."""

import math
import unittest

from zonelogic.engine import RuleEngine, box_overlap_ratio, point_in_polygon, validate_polygon


ZONE = {"id": "zone", "name": "Protected passage", "source_id": "demo",
        "points": [[.4, .2], [.8, .2], [.8, .8], [.4, .8]]}
INSIDE = [.45, .3, .55, .5]
OUTSIDE = [.25, .3, .35, .5]


def detector(box=INSIDE, *, class_name="person", confidence=.95, track_id="p1"):
    return {"class_name": class_name, "confidence": confidence, "bbox": box,
            "track_id": track_id}


def frame(timestamp, *detections):
    return {"timestamp": timestamp, "detections": list(detections)}


def engine(condition="enter", **changes):
    return RuleEngine([ZONE], [{"id": "r1", "zone_id": "zone", "name": "Keep passage clear",
                               "condition": condition, "classes": ["person"],
                               "dwell_seconds": 2, "cooldown_seconds": 0, **changes}])


class GeometryTests(unittest.TestCase):
    def test_boundary_and_winding(self):
        polygon = validate_polygon(ZONE["points"])
        for point in ((.4, .4), (.8, .8), (.5, .5)):
            self.assertTrue(point_in_polygon(point, polygon))
            self.assertTrue(point_in_polygon(point, polygon[::-1]))
        self.assertFalse(point_in_polygon((.2, .5), polygon))

    def test_overlap_uses_actual_polygon_not_bounding_rectangle(self):
        triangle = validate_polygon([[0, 0], [1, 0], [0, 1]])
        self.assertAlmostEqual(box_overlap_ratio([0, 0, 1, 1], triangle), .5)
        self.assertEqual(box_overlap_ratio([.8, .8, 1, 1], triangle), 0)
        self.assertAlmostEqual(box_overlap_ratio([0, 0, .2, .2], triangle), 1)

    def test_concave_polygon_and_disconnected_clipping(self):
        # A U shape; the top slice intersects two disconnected legs.
        polygon = validate_polygon([[0, 0], [.2, 0], [.2, .8], [.8, .8],
                                    [.8, 0], [1, 0], [1, 1], [0, 1]])
        self.assertAlmostEqual(box_overlap_ratio([0, 0, 1, .5], polygon), .4)
        self.assertAlmostEqual(box_overlap_ratio([0, 0, 1, .5], polygon[::-1]), .4)
        self.assertFalse(point_in_polygon((.5, .5), polygon))

    def test_invalid_polygons(self):
        for points in ([[0, 0], [1, 1]], [[0, 0], [.5, .5], [1, 1]],
                       [[0, 0], [1, 1], [0, 1], [1, 0]],
                       [[0, 0], [1, 0], [.5, 0], [1, 1]],
                       [[0, 0], [math.nan, 0], [1, 1]],
                       [[0, 0], [1.1, 0], [1, 1]]):
            with self.subTest(points=points), self.assertRaises(ValueError):
                validate_polygon(points)
        self.assertEqual(len(validate_polygon([[0, 0], [1, 0], [1, 1], [0, 0]])), 3)


class RuleTests(unittest.TestCase):
    def test_enter_requires_observed_transition(self):
        monitor = engine()
        self.assertEqual(monitor.process(frame(0, detector())), [])
        self.assertEqual(monitor.process(frame(1, detector())), [])
        self.assertEqual(monitor.process(frame(2, detector(OUTSIDE))), [])
        events = monitor.process(frame(3, detector()))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["condition"], "enter")
        self.assertEqual(events[0]["track_id"], "p1")
        self.assertEqual(monitor.process(frame(4, detector())), [])

    def test_dwell_is_continuous_and_once_per_occupancy(self):
        monitor = engine("dwell")
        for t in (0, 1):
            self.assertEqual(monitor.process(frame(t, detector())), [])
        self.assertEqual(len(monitor.process(frame(2, detector()))), 1)
        for t in (3, 4, 5):
            self.assertEqual(monitor.process(frame(t, detector())), [])
        monitor.process(frame(6, detector(OUTSIDE)))
        monitor.process(frame(7, detector()))
        monitor.process(frame(8, detector()))
        self.assertEqual(len(monitor.process(frame(9, detector()))), 1)

    def test_missing_or_low_confidence_breaks_dwell(self):
        for missing in ([], [detector(confidence=.1)]):
            with self.subTest(missing=missing):
                monitor = engine("dwell")
                monitor.process(frame(0, detector()))
                monitor.process(frame(1, *missing))
                monitor.process(frame(2, detector()))
                self.assertEqual(monitor.process(frame(3, detector())), [])
                self.assertEqual(len(monitor.process(frame(4, detector()))), 1)

    def test_missing_detection_does_not_count_as_exit(self):
        monitor = engine()
        monitor.process(frame(0, detector(OUTSIDE)))
        self.assertEqual(len(monitor.process(frame(1, detector()))), 1)
        monitor.process(frame(2))
        self.assertEqual(monitor.process(frame(3, detector())), [])

    def test_cooldown_and_later_dwell_episode(self):
        monitor = engine("dwell", dwell_seconds=1, cooldown_seconds=5)
        monitor.process(frame(0, detector()))
        self.assertEqual(len(monitor.process(frame(1, detector()))), 1)
        monitor.process(frame(2, detector(OUTSIDE)))
        monitor.process(frame(3, detector()))
        self.assertEqual(monitor.process(frame(4, detector())), [])
        self.assertEqual(monitor.process(frame(5, detector())), [])
        self.assertEqual(len(monitor.process(frame(6, detector()))), 1)

    def test_absence_starts_at_first_observation_and_resets_on_presence(self):
        monitor = engine("absent")
        self.assertEqual(monitor.process(frame(100)), [])
        self.assertEqual(monitor.process(frame(101)), [])
        events = monitor.process(frame(102))
        self.assertEqual(len(events), 1)
        self.assertIsNone(events[0]["bbox"])
        self.assertIsNone(events[0]["track_id"])
        self.assertEqual(monitor.process(frame(103)), [])
        monitor.process(frame(104, detector()))
        monitor.process(frame(105))
        monitor.process(frame(106))
        self.assertEqual(len(monitor.process(frame(107))), 1)

    def test_absence_is_per_class_or_any_class(self):
        monitor = engine("absent", classes=["person", "bottle"], dwell_seconds=1)
        monitor.process(frame(0, detector()))
        events = monitor.process(frame(1, detector()))
        self.assertEqual([event["class_name"] for event in events], ["bottle"])
        monitor = engine("absent", classes=[], dwell_seconds=1)
        monitor.process(frame(0, detector(class_name="bottle")))
        self.assertEqual(monitor.process(frame(1, detector(class_name="bottle"))), [])

    def test_discontinuities_cannot_create_false_dwell_or_enter(self):
        for condition in ("enter", "dwell", "absent"):
            with self.subTest(condition=condition):
                monitor = engine(condition)
                monitor.process(frame(0, detector(OUTSIDE if condition == "enter" else INSIDE)))
                self.assertEqual(monitor.process(frame(20, detector())), [])
                self.assertEqual(monitor.process(frame(1, detector())), [])

    def test_duplicate_timestamps_are_idempotent(self):
        monitor = engine("dwell", dwell_seconds=1)
        monitor.process(frame(0, detector()))
        self.assertEqual(len(monitor.process(frame(1, detector()))), 1)
        self.assertEqual(monitor.process(frame(1, detector(OUTSIDE))), [])
        self.assertEqual(monitor.process(frame(2, detector())), [])

    def test_tracking_without_ids_and_expiry(self):
        monitor = engine()
        monitor.process(frame(0, detector([.31, .3, .39, .5], track_id=None)))
        events = monitor.process(frame(1, detector([.38, .3, .46, .5], track_id=None)))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["track_id"], "auto-1")
        monitor = engine()
        monitor.process(frame(0, detector(OUTSIDE)))
        for t in (1, 2, 3):
            monitor.process(frame(t))
        self.assertEqual(monitor.process(frame(4, detector())), [])

    def test_same_class_objects_do_not_share_occupancy(self):
        monitor = engine("dwell", dwell_seconds=1)
        monitor.process(frame(0, detector(track_id="a"), detector(OUTSIDE, track_id="b")))
        events = monitor.process(frame(1, detector(track_id="a"), detector(track_id="b")))
        self.assertEqual([event["track_id"] for event in events], ["a"])
        events = monitor.process(frame(2, detector(track_id="a"), detector(track_id="b")))
        self.assertEqual([event["track_id"] for event in events], ["b"])

    def test_classes_confidence_disabled_and_anchors(self):
        monitor = engine("dwell", dwell_seconds=0)
        self.assertEqual(monitor.process(frame(0, detector(class_name="bottle"))), [])
        self.assertEqual(monitor.process(frame(1, detector(confidence=.1))), [])
        self.assertEqual(engine("dwell", dwell_seconds=0, enabled=False).process(frame(0, detector())), [])
        hanging = [.5, .6, .6, 1]
        self.assertEqual(engine("dwell", dwell_seconds=0).process(frame(0, detector(hanging))), [])
        self.assertEqual(len(engine("dwell", dwell_seconds=0, anchor="center").process(frame(0, detector(hanging)))), 1)
        self.assertEqual(len(engine("dwell", dwell_seconds=0, anchor="overlap", min_overlap=.5)
                             .process(frame(0, detector(hanging)))), 1)

    def test_invalid_input_is_predictable_and_does_not_mutate_history(self):
        monitor = engine()
        monitor.process(frame(0, detector(OUTSIDE)))
        for bad in (frame(math.nan), frame(-1), frame(1, detector([0, 0, 0, 1])),
                    frame(1, detector(confidence=1.1)), frame(1, detector(), detector())):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                monitor.process(bad)
        self.assertEqual(len(monitor.process(frame(1, detector()))), 1)
        for changes in ({"zone_id": "missing"}, {"condition": "unknown"},
                        {"dwell_seconds": -1}, {"classes": "person"}, {"actions": ["email"]},
                        {"actions": [{}]}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                engine(**changes)

    def test_reset_starts_new_observation_session(self):
        monitor = engine()
        monitor.process(frame(0, detector(OUTSIDE)))
        monitor.reset()
        self.assertEqual(monitor.process(frame(1, detector())), [])


if __name__ == "__main__":
    unittest.main()
