"""End-to-end API checks using isolated durable stores and synthetic fixtures."""

import base64
import copy
import gc
import io
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

# main also exports the ASGI deployment app. Keep import-time initialization out
# of the developer's real database when unittest discovers this module.
_import_directory = tempfile.TemporaryDirectory(prefix="zonelogic-api-import-")
with patch.dict(os.environ, {"ZONELOGIC_DATA_DIR": _import_directory.name}):
    from zonelogic.main import app as deployment_app, create_app


VIDEO_BYTES = b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom"


def tearDownModule():
    gc.collect()
    _import_directory.cleanup()


def detection_document():
    return {"width": 640, "height": 360, "duration": 3,
            "provenance": "Integration-test geometry; no inference.", "frames": [
                {"timestamp": 0, "detections": [{"class_name": "person", "confidence": .9,
                                                 "bbox": [.1, .2, .2, .6], "track_id": "p1"}]},
                {"timestamp": 1, "detections": [{"class_name": "person", "confidence": .9,
                                                 "bbox": [.5, .2, .6, .6], "track_id": "p1"}]},
                {"timestamp": 2, "detections": []}]}


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="zonelogic-api-")
        self.app = create_app(self.directory.name)
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        gc.collect()
        self.directory.cleanup()

    def session(self, source_id="demo-waste", client=None):
        client = client or self.client
        response = client.post("/api/sessions", json={"source_id": source_id})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def evaluate(self, session_id, timestamp, *, seek=False, client=None):
        response = (client or self.client).post(f"/api/sessions/{session_id}/evaluate",
                                               json={"timestamp": timestamp, "seek": seek})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def run_demo(self, source_id="demo-waste", client=None):
        client = client or self.client
        session_id = self.session(source_id, client)
        first = self.evaluate(session_id, 0, client=client)
        self.assertEqual(first["events"], [])
        return self.evaluate(session_id, 20, client=client)["events"]

    def upload(self, document=None, *, raw=None, filename="sample.mp4", video=VIDEO_BYTES):
        if raw is None:
            raw = json.dumps(document if document is not None else detection_document()).encode()
        return self.client.post("/api/sources/upload", data={"name": "Test clip"}, files={
            "video": (filename, video, "video/mp4"),
            "detections": ("detections.json", raw, "application/json")})

    def test_demo_events_are_stable_once_and_persist_after_restart(self):
        for source_id, condition in (("demo-access", "dwell"), ("demo-waste", "enter")):
            with self.subTest(source=source_id):
                first = self.run_demo(source_id)
                self.assertEqual(len(first), 1)
                incident = first[0]
                self.assertEqual(incident["condition"], condition)
                self.assertEqual(incident["source_kind"], "simulation")
                self.assertIn("Synthetic", incident["provenance"])
                self.assertEqual(self.run_demo(source_id), [])
                saved = self.client.get("/api/incidents", params={"source_id": source_id}).json()["incidents"]
                self.assertEqual([item["id"] for item in saved], [incident["id"]])
                with TestClient(create_app(self.directory.name)) as restarted:
                    recovered = restarted.get("/api/incidents", params={"source_id": source_id}).json()["incidents"]
                    self.assertEqual([item["id"] for item in recovered], [incident["id"]])
                    self.assertEqual(self.run_demo(source_id, restarted), [])

    def test_explicit_seek_and_initial_inside_are_not_entries(self):
        session_id = self.session()
        self.assertEqual(self.evaluate(session_id, 8)["events"], [])
        self.assertEqual(self.evaluate(session_id, 9)["events"], [])
        self.assertEqual(self.evaluate(session_id, 1, seek=True)["events"], [])
        # A seek skips the intervening crossing; it must not backfill an event.
        self.assertEqual(self.evaluate(session_id, 8, seek=True)["events"], [])
        self.assertEqual(self.evaluate(session_id, 9)["events"], [])
        self.assertEqual(self.client.get("/api/incidents").json()["incidents"], [])

    def test_seek_cannot_backfill_dwell_time(self):
        session_id = self.session("demo-access")
        self.assertEqual(self.evaluate(session_id, 8, seek=True)["events"], [])
        self.assertEqual(self.evaluate(session_id, 9)["events"], [])
        events = self.evaluate(session_id, 10)["events"]
        self.assertEqual(len(events), 1)
        self.assertAlmostEqual(events[0]["timestamp"], 10)

    def test_evaluation_rejects_invalid_time_without_corrupting_session(self):
        session_id = self.session()
        self.evaluate(session_id, 0)
        url = f"/api/sessions/{session_id}/evaluate"
        for payload in ({"timestamp": -1}, {"timestamp": 21}, {"timestamp": "nan"},
                        {"timestamp": "Infinity"}, {"timestamp": "tomorrow"}):
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post(url, json=payload).status_code, 422)
        self.assertEqual(len(self.evaluate(session_id, 20)["events"]), 1)
        self.assertEqual(self.evaluate(session_id, 20)["events"], [])

    def test_config_validation_is_atomic_and_changes_invalidate_sessions(self):
        initial = self.client.get("/api/config", params={"source_id": "demo-waste"}).json()
        invalid_configs = []
        invalid = copy.deepcopy(initial)
        invalid["zones"][0]["points"] = [[0, 0], [1, 1], [0, 1], [1, 0]]
        invalid_configs.append(invalid)
        invalid = copy.deepcopy(initial)
        invalid["rules"][0]["zone_id"] = "missing"
        invalid_configs.append(invalid)
        invalid = copy.deepcopy(initial)
        invalid["zones"][0]["source_id"] = "demo-access"
        invalid_configs.append(invalid)
        for collection in ("zones", "rules"):
            invalid = copy.deepcopy(initial)
            invalid[collection].append(copy.deepcopy(invalid[collection][0]))
            invalid_configs.append(invalid)
            invalid = copy.deepcopy(initial)
            invalid[collection][0]["name"] = "   "
            invalid_configs.append(invalid)
        for invalid in invalid_configs:
            with self.subTest(config=invalid):
                response = self.client.put("/api/config", json=invalid)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(self.client.get("/api/config", params={"source_id": "demo-waste"}).json(), initial)
        session_id = self.session()
        changed = copy.deepcopy(initial)
        changed["rules"][0]["dwell_seconds"] = 4
        self.assertEqual(self.client.put("/api/config", json=changed).status_code, 200)
        self.assertEqual(self.client.post(f"/api/sessions/{session_id}/evaluate", json={"timestamp": 1}).status_code, 409)
        self.assertEqual(self.client.post(f"/api/sessions/{session_id}/reset").status_code, 409)

    def test_canonical_upload_roundtrip_and_restart(self):
        response = self.upload()
        self.assertEqual(response.status_code, 201, response.text)
        source = response.json()
        source_id = source["id"]
        self.assertEqual(source["kind"], "uploaded")
        for private in ("frames", "media_path", "upstream_source"):
            self.assertNotIn(private, source)
        self.assertEqual(self.client.get(f"/api/sources/{source_id}/detections").json(), detection_document())
        video = self.client.get(f"/api/sources/{source_id}/video")
        self.assertEqual(video.status_code, 200)
        self.assertEqual(video.content, VIDEO_BYTES)
        self.assertEqual(self.client.get("/api/config", params={"source_id": source_id}).json(),
                         {"source_id": source_id, "zones": [], "rules": []})
        with TestClient(create_app(self.directory.name)) as restarted:
            listed = restarted.get("/api/sources").json()["sources"]
            self.assertIn(source_id, [item["id"] for item in listed])
            self.assertEqual(restarted.get(f"/api/sources/{source_id}/video").content, VIDEO_BYTES)

    def test_upload_rejects_bad_coordinates_time_order_and_tracking_ids(self):
        malformed = []
        for times in ((0, 0, 2), (0, 2, 1), (0, 1, 5), (0, 1, "NaN"), (0, 1, "not seconds")):
            document = detection_document()
            for item, timestamp in zip(document["frames"], times):
                item["timestamp"] = timestamp
            malformed.append(document)
        for box in ([0, 0, 640, 360], [0, 0, 0, 1]):
            document = detection_document()
            document["frames"][0]["detections"][0]["bbox"] = box
            malformed.append(document)
        for track_id in ("", "   "):
            document = detection_document()
            document["frames"][0]["detections"][0]["track_id"] = track_id
            malformed.append(document)
        document = detection_document()
        first = document["frames"][0]["detections"][0]
        first["track_id"] = 1
        document["frames"][0]["detections"].append({**first, "track_id": "1"})
        malformed.append(document)
        for document in malformed:
            with self.subTest(document=document):
                response = self.upload(document)
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.upload(raw=b"{invalid json").status_code, 422)
        self.assertEqual(self.upload(filename="script.html").status_code, 422)
        self.assertEqual(self.upload(video=b"").status_code, 422)
        self.assertEqual(len(self.client.get("/api/sources").json()["sources"]), 2)

    def test_playback_before_first_detection_and_stale_boxes(self):
        document = detection_document()
        document["duration"] = 8
        document["frames"] = [document["frames"][0]]
        document["frames"][0]["timestamp"] = 3
        response = self.upload(document)
        self.assertEqual(response.status_code, 201)
        session_id = self.session(response.json()["id"])
        self.assertEqual(self.evaluate(session_id, 0)["detections"], [])
        self.assertEqual(len(self.evaluate(session_id, 3)["detections"]), 1)
        self.assertEqual(self.evaluate(session_id, 6)["detections"], [])
        self.assertEqual(self.evaluate(session_id, 0)["detections"], [])
        self.assertEqual(len(self.evaluate(session_id, 3)["detections"]), 1)

    def test_evidence_validation_resolution_and_export_are_durable(self):
        incident = self.run_demo()[0]
        incident_id = incident["id"]
        evidence_url = f"/api/incidents/{incident_id}/evidence"
        self.assertEqual(self.client.get(evidence_url).status_code, 404)
        image_bytes = io.BytesIO()
        Image.new("RGB", (8, 8), "green").save(image_bytes, format="PNG")
        data = image_bytes.getvalue()
        valid_image = "data:image/png;base64," + base64.b64encode(data).decode()
        response = self.client.post(evidence_url, json={"timestamp": incident["timestamp"] + 1,
                                                       "data_url": valid_image})
        self.assertEqual(response.status_code, 422)
        for invalid in ("data:image/png;base64,NOT-BASE64", "data:image/png;base64,SGVsbG8=",
                        "data:text/html;base64,SGVsbG8="):
            self.assertEqual(self.client.post(evidence_url, json={"timestamp": incident["timestamp"],
                                                                  "data_url": invalid}).status_code, 422)
        saved = self.client.post(evidence_url, json={"timestamp": incident["timestamp"], "data_url": valid_image})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertEqual(saved.json()["evidence_timestamp"], incident["timestamp"])
        self.assertEqual(self.client.get(evidence_url).content, data)
        self.assertIn("image/png", self.client.get(evidence_url).headers["content-type"])
        resolved = self.client.patch(f"/api/incidents/{incident_id}", json={"status": "resolved"})
        self.assertEqual(resolved.status_code, 200)
        self.assertEqual(resolved.json()["status"], "resolved")
        self.assertEqual(self.client.patch(f"/api/incidents/{incident_id}", json={"status": "ignored"}).status_code, 422)
        with TestClient(create_app(self.directory.name)) as restarted:
            response = restarted.get("/api/incidents/export", params={"source_id": "demo-waste"})
            self.assertEqual(response.status_code, 200)
            self.assertIn("attachment", response.headers["content-disposition"])
            self.assertEqual(response.json()["incidents"][0]["status"], "resolved")
            self.assertEqual(response.json()["incidents"][0]["evidence_timestamp"], incident["timestamp"])
            self.assertEqual(restarted.get(evidence_url).content, data)

    def test_unknown_resources_and_cross_origin_writes(self):
        for path in ("/api/sources/missing/detections", "/api/sources/missing/video", "/api/incidents/missing/evidence"):
            self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.post("/api/sessions", json={"source_id": "missing"}).status_code, 404)
        self.assertEqual(self.client.patch("/api/incidents/missing", json={"status": "resolved"}).status_code, 404)
        response = self.client.post("/api/sessions", json={"source_id": "demo-waste"},
                                    headers={"Origin": "https://unrelated.example"})
        self.assertEqual(response.status_code, 403)
        response = self.client.post("/api/sessions", json={"source_id": "demo-waste"},
                                    headers={"Origin": "http://testserver"})
        self.assertEqual(response.status_code, 201)

    def test_deployment_mount_serves_direct_and_prefixed_routes(self):
        with TestClient(deployment_app) as client:
            for prefix in ("", "/app"):
                with self.subTest(prefix=prefix):
                    self.assertEqual(client.get(prefix + "/api/status").status_code, 200)
                    self.assertEqual(client.get(prefix + "/api/sources").status_code, 200)
                    response = client.get(prefix + "/")
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("text/html", response.headers["content-type"])


if __name__ == "__main__":
    unittest.main()
