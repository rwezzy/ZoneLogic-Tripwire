"""Integration checks for the optional examples against the unchanged engine."""
from copy import deepcopy
import unittest

from zonelogic.engine import RuleEngine
from zonelogic.models import Configuration, DetectionDocument
from zonelogic_scenarios.scenarios import configuration, rule_options, sources


class ScenarioDataTests(unittest.TestCase):
    def setUp(self):
        self.sources = {source["id"]: source for source in sources()}

    def events(self, source_id, config=None):
        config = Configuration.model_validate(config or configuration(source_id)).model_dump()
        engine = RuleEngine(config["zones"], config["rules"])
        events = []
        for frame in self.sources[source_id]["frames"]:
            events.extend(engine.process(frame))
            self.assertEqual([], engine.process(frame), "Repeated frame must not create a repeated event")
        return events

    def test_sources_validate_with_core_contracts(self):
        self.assertEqual({"scene-sorting", "scene-wheelchair", "scene-crosswalk", "scene-cane"}, set(self.sources))
        for source_id, source in self.sources.items():
            with self.subTest(source=source_id):
                DetectionDocument.model_validate({key: source[key] for key in
                                                  ("width", "height", "duration", "provenance", "frames")})
                config = Configuration.model_validate(configuration(source_id))
                self.assertEqual("simulation", source["kind"])
                self.assertIsNone(source["video_url"])
                self.assertTrue(source["extra"]["scenario_notes"])
                self.assertTrue(all(rule.anchor == "center" for rule in config.rules))
                self.assertEqual({r.id for r in config.rules}, set(rule_options(source_id)))
                self.assertTrue(all(not option["vibrate"] for option in rule_options(source_id).values()))

    def test_sorting_logs_both_destinations_and_flags_wrong_bin(self):
        events = self.events("scene-sorting")
        self.assertEqual([
            ("sort-recycle-log", "bottle-1", "info", 3.4),
            ("sort-trash-review", "bottle-2", "warning", 9.4),
            ("sort-trash-log", "banana-1", "info", 15.4),
        ], [(e["rule_id"], e["track_id"], e["severity"], e["timestamp"]) for e in events])

    def test_wheelchair_has_one_continuous_occupancy_alert(self):
        events = self.events("scene-wheelchair")
        self.assertEqual(1, len(events))
        self.assertEqual("wheelchair-obstruction", events[0]["rule_id"])
        self.assertEqual("suitcase", events[0]["class_name"])
        self.assertAlmostEqual(4.8, events[0]["timestamp"])
        self.assertTrue(all(d["class_name"] != "wheelchair" for frame in self.sources["scene-wheelchair"]["frames"]
                            for d in frame["detections"]))

    def test_crosswalk_has_one_observed_vehicle_entry(self):
        events = self.events("scene-crosswalk")
        self.assertEqual(1, len(events))
        self.assertEqual(("car", "enter", 3.4),
                         (events[0]["class_name"], events[0]["condition"], events[0]["timestamp"]))

    def test_cane_uses_short_continuous_dwell(self):
        events = self.events("scene-cane")
        self.assertEqual(1, len(events))
        self.assertEqual(("car", "dwell", 5.2),
                         (events[0]["class_name"], events[0]["condition"], events[0]["timestamp"]))
        self.assertEqual(["log", "sound"], events[0]["actions"])

    def test_removed_zones_and_rules_produce_no_events(self):
        for source_id in self.sources:
            self.assertEqual([], self.events(source_id, {"source_id": source_id, "zones": [], "rules": []}))
        self.assertEqual([], configuration("uploaded-clip")["rules"])
        self.assertEqual({}, rule_options("uploaded-clip"))

    def test_disabled_rules_and_no_detections_do_not_alert(self):
        for source_id, source in self.sources.items():
            config = configuration(source_id)
            for rule in config["rules"]:
                rule["enabled"] = False
            self.assertEqual([], self.events(source_id, config))
            active = configuration(source_id)
            engine = RuleEngine(active["zones"], active["rules"])
            self.assertEqual([], [event for frame in source["frames"]
                                 for event in engine.process({"timestamp": frame["timestamp"], "detections": []})])

    def test_dwell_requires_uninterrupted_observations(self):
        source = deepcopy(self.sources["scene-wheelchair"])
        config = configuration(source["id"])
        engine = RuleEngine(config["zones"], config["rules"])
        # Drop the last observation before the original 4.8s trigger. The new
        # observed dwell starts at 4.8s, so its first alert must move to 6.8s.
        events = []
        for frame in source["frames"]:
            if frame["timestamp"] == 4.6:
                frame["detections"] = []
            events.extend(engine.process(frame))
        self.assertEqual([6.8], [event["timestamp"] for event in events])

    def test_configuration_and_metadata_are_fresh(self):
        source = sources()[0]
        source["extra"]["signage"][0]["label"] = "changed"
        self.assertEqual("RECYCLING", sources()[0]["extra"]["signage"][0]["label"])
        config = configuration("scene-sorting")
        config["zones"][0]["points"][0][0] = .99
        self.assertEqual(.10, configuration("scene-sorting")["zones"][0]["points"][0][0])
        options = rule_options("scene-cane")
        options["cane-vehicle"]["vibrate"] = True
        self.assertFalse(rule_options("scene-cane")["cane-vehicle"]["vibrate"])


if __name__ == "__main__":
    unittest.main()
