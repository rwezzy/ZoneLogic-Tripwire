"""End-to-end checks for the optional playground's isolation and safe defaults."""
import gc
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

_import_directory = tempfile.TemporaryDirectory(prefix="zonelogic-scenario-import-")
with patch.dict(os.environ, {"ZONELOGIC_DATA_DIR": _import_directory.name}):
    from zonelogic.main import create_app

from zonelogic_scenarios.app import create_optional_app
from zonelogic_scenarios.policies import annotation


def tearDownModule():
    gc.collect()
    _import_directory.cleanup()


class ScenarioApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="zonelogic-scenario-api-")
        self.app = create_optional_app(self.directory.name)
        self.assertTrue(self.app.state.scenarios_enabled, "Optional assets must be available for integration tests")
        self.core = self.app.state.core
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        gc.collect()
        self.directory.cleanup()

    def options(self, source_id="scene-cane", client=None):
        response = (client or self.client).get("/api/scenarios/options", params={"source_id": source_id})
        self.assertEqual(200, response.status_code, response.text)
        return response.json()

    def save_options(self, rules, source_id="scene-cane"):
        response = self.client.put("/api/scenarios/options", json={"source_id": source_id, "rules": rules})
        self.assertEqual(200, response.status_code, response.text)
        return response.json()

    def run_scene(self, source_id="scene-cane", client=None, duration=18):
        client = client or self.client
        response = client.post("/api/sessions", json={"source_id": source_id})
        self.assertEqual(201, response.status_code, response.text)
        session_id = response.json()["id"]
        endpoint = f"/api/sessions/{session_id}/evaluate"
        first = client.post(endpoint, json={"timestamp": 0})
        self.assertEqual(200, first.status_code, first.text)
        self.assertEqual([], first.json()["events"])
        final = client.post(endpoint, json={"timestamp": duration})
        self.assertEqual(200, final.status_code, final.text)
        return final.json()["events"]

    def incidents(self, source_id="scene-cane", client=None):
        response = (client or self.client).get("/api/incidents", params={"source_id": source_id})
        self.assertEqual(200, response.status_code, response.text)
        return response.json()["incidents"]

    def test_four_optional_scenarios_and_original_sources_are_available(self):
        info = self.client.get("/api/scenarios")
        self.assertEqual(200, info.status_code)
        self.assertTrue(info.json()["enabled"])
        expected = {"scene-sorting", "scene-wheelchair", "scene-crosswalk", "scene-cane"}
        self.assertEqual(expected, {scene["id"] for scene in info.json()["scenarios"]})
        listed = self.client.get("/api/sources").json()["sources"]
        self.assertEqual(expected | {"demo-access", "demo-waste"}, {source["id"] for source in listed})
        for source_id in expected:
            source = next(source for source in listed if source["id"] == source_id)
            self.assertEqual("simulation", source["kind"])
            self.assertNotIn("frames", source)
            self.assertTrue(source["extra"]["scenario_notes"])
            detections = self.client.get(f"/api/sources/{source_id}/detections")
            self.assertEqual(200, detections.status_code)
            self.assertEqual(91, len(detections.json()["frames"]))
            self.assertTrue(all(not option["vibrate"] for option in self.options(source_id)["rules"].values()))

    def test_sorting_logs_correct_placements_and_flags_wrong_bin_once(self):
        events = self.run_scene("scene-sorting")
        self.assertEqual(["info", "warning", "info"], [event["severity"] for event in events])
        self.assertEqual([3.4, 9.4, 15.4], [event["timestamp"] for event in events])
        self.assertEqual(["recycling", "trash", "trash"], [event["scenario"]["meaning"] for event in events])
        self.assertNotIn("review placement", events[0]["scenario"]["headline"])
        self.assertIn("review placement", events[1]["scenario"]["headline"])
        self.assertNotIn("review placement", events[2]["scenario"]["headline"])
        self.assertNotIn("recycling candidate", events[2]["scenario"]["notice"].lower())
        self.assertTrue(all(not event["scenario"]["vibration_requested"] for event in events))
        self.assertEqual([], self.run_scene("scene-sorting"))
        self.assertEqual(3, len(self.incidents("scene-sorting")))

    def test_missing_optional_settings_preserve_core_incident_without_device_request(self):
        self.save_options({})
        self.assertEqual({}, self.options()["rules"])
        events = self.run_scene()
        self.assertEqual(1, len(events))
        self.assertNotIn("scenario", events[0])
        self.assertEqual("cane-vehicle", events[0]["rule_id"])
        self.assertEqual(["log", "sound"], events[0]["actions"])
        self.assertNotIn("scenario", self.incidents()[0])

    def test_empty_core_configuration_is_a_valid_noop(self):
        response = self.client.put("/api/config", json={"source_id": "scene-cane", "zones": [], "rules": []})
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual({}, self.options()["rules"])
        self.assertEqual([], self.run_scene())
        self.assertEqual([], self.incidents())

    def test_vibration_options_persist_and_incident_keeps_original_snapshot(self):
        selected = {"cane-vehicle": {"meaning": "cane", "vibrate": True, "pattern": [100, 50, 100]}}
        self.save_options(selected)
        self.assertEqual(selected, self.options()["rules"])
        incident = self.run_scene()[0]
        details = incident["scenario"]
        self.assertTrue(details["vibration_requested"])
        self.assertEqual([100, 50, 100], details["pattern"])
        self.assertFalse(details["physical_delivery_confirmed"])
        replacement = {"cane-vehicle": {"meaning": "path", "vibrate": False, "pattern": [80]}}
        self.save_options(replacement)
        self.assertEqual(details, self.incidents()[0]["scenario"])
        response = self.client.patch(f"/api/incidents/{incident['id']}", json={"status": "resolved"})
        self.assertEqual(200, response.status_code)
        with TestClient(create_optional_app(self.directory.name)) as restarted:
            self.assertEqual(replacement, self.options(client=restarted)["rules"])
            saved = self.incidents(client=restarted)[0]
            self.assertEqual(details, saved["scenario"])
            self.assertEqual("resolved", saved["status"])
            self.assertEqual([], self.run_scene(client=restarted))
            exported = restarted.get("/api/incidents/export", params={"source_id": "scene-cane"})
            self.assertEqual(200, exported.status_code)
            self.assertIn("attachment", exported.headers["content-disposition"])
            self.assertEqual(details, exported.json()["incidents"][0]["scenario"])

    def test_optional_validation_is_bounded_strict_and_atomic(self):
        baseline = self.options()
        invalid_options = [
            {"meaning": "cane", "vibrate": True, "pattern": []},
            {"meaning": "cane", "vibrate": True, "pattern": [501]},
            {"meaning": "cane", "vibrate": True, "pattern": [-1, 10, 20]},
            {"meaning": "cane", "vibrate": True, "pattern": [500] * 5},
            {"meaning": "cane", "vibrate": True, "pattern": [1] * 10},
            {"meaning": "cane", "vibrate": True, "pattern": [0, 100, 0]},
            {"meaning": "cane", "vibrate": True, "pattern": [True]},
            {"meaning": "cane", "vibrate": True, "pattern": [1.5]},
            {"meaning": "cane", "vibrate": "yes", "pattern": [100]},
            {"meaning": "unknown", "vibrate": False, "pattern": [100]},
            {"meaning": "cane", "vibrate": True, "pattern": [100], "device_token": "not-supported"},
        ]
        payloads = [{"source_id": "scene-cane", "rules": {"cane-vehicle": options}} for options in invalid_options]
        payloads.append({"source_id": "scene-cane", "rules": {
            "cane-vehicle": {"meaning": "cane", "vibrate": True, "pattern": [100]},
            "not-a-saved-rule": {"meaning": "path"},
        }})
        for payload in payloads:
            with self.subTest(payload=payload):
                response = self.client.put("/api/scenarios/options", json=payload)
                self.assertEqual(422, response.status_code, response.text)
                self.assertEqual(baseline, self.options(), "Rejected input must not partially replace valid settings")

    def test_none_meaning_disables_vibration(self):
        result = self.save_options({"cane-vehicle": {"meaning": "none", "vibrate": True, "pattern": [100]}})
        self.assertFalse(result["rules"]["cane-vehicle"]["vibrate"])
        self.assertNotIn("scenario", self.run_scene()[0])

    def test_core_rule_change_invalidates_previously_saved_optional_actions(self):
        self.save_options({"cane-vehicle": {"meaning": "cane", "vibrate": True, "pattern": [100]}})
        config = self.client.get("/api/config", params={"source_id": "scene-cane"}).json()
        config["rules"][0]["dwell_seconds"] = 1
        response = self.client.put("/api/config", json=config)
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual({}, self.options()["rules"])
        events = self.run_scene()
        self.assertEqual(1, len(events))
        self.assertNotIn("scenario", events[0])
        self.assertAlmostEqual(5.6, events[0]["timestamp"])

    def test_referenced_zone_edits_invalidate_optional_actions(self):
        for field in ("points", "name"):
            with self.subTest(field=field):
                self.save_options({"cane-vehicle": {"meaning": "cane", "vibrate": True, "pattern": [100]}})
                config = self.client.get("/api/config", params={"source_id": "scene-cane"}).json()
                if field == "points":
                    config["zones"][0]["points"][0][0] = .35
                else:
                    config["zones"][0]["name"] = "Different forward region"
                response = self.client.put("/api/config", json=config)
                self.assertEqual(200, response.status_code, response.text)
                self.assertEqual({}, self.options()["rules"])

    def test_unrelated_zone_addition_keeps_existing_rule_options(self):
        selected = {"cane-vehicle": {"meaning": "cane", "vibrate": True, "pattern": [100]}}
        self.save_options(selected)
        config = self.client.get("/api/config", params={"source_id": "scene-cane"}).json()
        config["zones"].append({"id": "unrelated-zone", "name": "Unused corner",
                                "points": [[.01, .01], [.10, .01], [.10, .10], [.01, .10]]})
        response = self.client.put("/api/config", json=config)
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(selected, self.options()["rules"])

    def test_options_read_failure_preserves_evaluation_and_core_storage(self):
        with patch.object(self.core.state.scenario_options, "read", side_effect=OSError("optional settings unavailable")):
            self.assertEqual({}, self.options()["rules"])
            events = self.run_scene()
        self.assertEqual(1, len(events))
        self.assertNotIn("scenario", events[0])
        self.assertEqual(events[0]["id"], self.incidents()[0]["id"])
        self.assertEqual([], self.run_scene())

    def test_annotation_save_failure_preserves_evaluation_and_core_storage(self):
        self.save_options({"cane-vehicle": {"meaning": "cane", "vibrate": True, "pattern": [100]}})
        with patch.object(self.core.state.scenario_options, "save_annotation", side_effect=OSError("optional metadata unavailable")):
            events = self.run_scene()
        self.assertEqual(1, len(events))
        self.assertNotIn("scenario", events[0], "A failed optional save must not return a device request")
        saved = self.incidents()[0]
        self.assertEqual(events[0]["id"], saved["id"])
        self.assertNotIn("scenario", saved)

    def test_annotation_read_failure_does_not_hide_saved_incidents(self):
        event = self.run_scene()[0]
        with patch.object(self.core.state.scenario_options, "annotate_existing", side_effect=OSError("optional metadata unavailable")):
            saved = self.incidents()[0]
            self.assertEqual(event["id"], saved["id"])
            self.assertNotIn("scenario", saved)
            exported = self.client.get("/api/incidents/export", params={"source_id": "scene-cane"})
            self.assertEqual(200, exported.status_code)
            self.assertEqual(event["id"], exported.json()["incidents"][0]["id"])

    def test_unknown_sources_and_cross_origin_optional_writes_are_rejected(self):
        self.assertEqual(404, self.client.get("/api/scenarios/options", params={"source_id": "missing"}).status_code)
        self.assertEqual(404, self.client.put("/api/scenarios/options", json={"source_id": "missing", "rules": {}}).status_code)
        baseline = self.options()
        response = self.client.put("/api/scenarios/options", json={"source_id": "scene-cane", "rules": {}},
                                   headers={"Origin": "https://unrelated.example"})
        self.assertEqual(403, response.status_code)
        self.assertEqual(baseline, self.options())

    def test_root_and_app_mount_serve_original_and_optional_assets(self):
        redirect = self.client.get("/app", follow_redirects=False)
        self.assertEqual(307, redirect.status_code)
        self.assertEqual("/app/", redirect.headers["location"])
        for prefix in ("", "/app"):
            with self.subTest(prefix=prefix):
                page = self.client.get(prefix + "/")
                self.assertEqual(200, page.status_code)
                self.assertIn("scenarios-static/addons.css", page.text)
                self.assertIn("scenarios-static/addons.js", page.text)
                self.assertEqual(200, self.client.get(prefix + "/api/status").status_code)
                self.assertTrue(self.client.get(prefix + "/api/scenarios").json()["enabled"])
                for asset in ("addons.css", "addons.js"):
                    response = self.client.get(prefix + "/scenarios-static/" + asset)
                    self.assertEqual(200, response.status_code, asset)
                    self.assertTrue(response.content)

    def test_base_app_uses_its_own_data_and_keeps_its_original_routes(self):
        base_path = Path(self.directory.name) / "separate-working-app"
        base = create_app(base_path)
        self.assertNotEqual(base.state.data_dir, self.core.state.data_dir)
        self.assertFalse(hasattr(base.state, "scenario_options"))
        self.run_scene()
        with TestClient(base) as client:
            self.assertEqual({"demo-access", "demo-waste"}, {s["id"] for s in client.get("/api/sources").json()["sources"]})
            self.assertEqual(404, client.get("/api/scenarios").status_code)
            self.assertEqual([], client.get("/api/incidents").json()["incidents"])
            self.assertNotIn("scenarios-static/addons.js", client.get("/").text)
            self.assertEqual(1, len(self.run_scene("demo-waste", client, 20)))
        self.assertEqual(1, len(self.incidents()))
        self.assertEqual([], self.incidents("demo-waste"))

    def test_disabling_extension_serves_original_workspace_with_separate_store(self):
        disabled = create_optional_app(Path(self.directory.name) / "disabled", enabled=False)
        self.assertFalse(disabled.state.scenarios_enabled)
        with TestClient(disabled) as client:
            info = client.get("/api/scenarios").json()
            self.assertFalse(info["enabled"])
            self.assertEqual([], info["scenarios"])
            self.assertEqual(2, len(client.get("/api/sources").json()["sources"]))
            self.assertNotIn("scenarios-static/addons.js", client.get("/").text)
            self.assertEqual(200, client.get("/app/api/status").status_code)
            self.assertEqual(1, len(self.run_scene("demo-waste", client, 20)))

    def test_partial_extension_install_failure_falls_back_to_fresh_core(self):
        def broken_install(core):
            core.state.sources["partial-scenario"] = {"id": "partial-scenario"}
            raise RuntimeError("optional component unavailable")

        with patch("zonelogic_scenarios.extension.install", side_effect=broken_install):
            fallback = create_optional_app(Path(self.directory.name) / "fallback")
        self.assertFalse(fallback.state.scenarios_enabled)
        with TestClient(fallback) as client:
            self.assertFalse(client.get("/api/scenarios").json()["enabled"])
            self.assertEqual({"demo-access", "demo-waste"}, {s["id"] for s in client.get("/api/sources").json()["sources"]})
            self.assertEqual(1, len(self.run_scene("demo-access", client, 20)))

    def test_optional_data_environment_does_not_change_working_app_setting(self):
        destination = str(Path(self.directory.name) / "from-optional-environment")
        original = str(Path(self.directory.name) / "working-data")
        with patch.dict(os.environ, {"ZONELOGIC_DATA_DIR": original, "ZONELOGIC_SCENARIO_DATA_DIR": destination}):
            app = create_optional_app(enabled=False)
            self.assertEqual(Path(destination).resolve(), app.state.core.state.data_dir)
            self.assertEqual(original, os.environ["ZONELOGIC_DATA_DIR"])

    def test_factory_rejects_original_data_directory_before_opening_database(self):
        original = Path(self.directory.name) / "protected-working-data"
        create_app(original)
        before = {path.relative_to(original): path.read_bytes() for path in original.rglob("*") if path.is_file()}
        self.assertTrue(before, "The guard must be tested against an existing app database")
        with patch.dict(os.environ, {"ZONELOGIC_DATA_DIR": str(original)}):
            with patch("zonelogic.main.create_app") as create_core:
                for destination in (original, original / ".", original / "unused" / ".."):
                    with self.subTest(destination=destination):
                        with self.assertRaisesRegex(ValueError, "separate data directory"):
                            create_optional_app(destination)
                create_core.assert_not_called()
        after = {path.relative_to(original): path.read_bytes() for path in original.rglob("*") if path.is_file()}
        self.assertEqual(before, after)

    def test_absence_meaning_does_not_claim_an_object_was_detected(self):
        for meaning in ("path", "crosswalk", "cane"):
            details = annotation({"class_name": "car", "condition": "absent", "severity": "warning"}, {"meaning": meaning})
            self.assertIn("absent", details["headline"])
            self.assertNotIn("detected", details["headline"])


if __name__ == "__main__":
    unittest.main()
