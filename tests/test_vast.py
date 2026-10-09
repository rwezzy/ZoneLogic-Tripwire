"""Contract tests against the documented VSS schemas; no live credentials needed."""
import asyncio
import copy
import json
import unittest

import httpx

from zonelogic.vast import VastClient, VastError, normalize_import, normalize_sidecar, validate_source


SOURCE = "s3://team-1-vss-chunks-segments/clip_002.mp4"
META = {"source": SOURCE, "filename": "clip_002.mp4", "duration": 2.0,
        "segment_start_sec": 12.0, "segment_end_sec": 14.0,
        "original_video": "s3://team-1-vss-chunks/clip.mp4", "reasoning_content": "A person walks.",
        "camera_id": "warehouse-camera", "location": "warehouse"}
SIDECAR = {"source": "yolo11_coco", "segment_source": SOURCE,
           "video_shape": [480, 640], "fps": 2.0, "frame_count": 4,
           "frames": [
               {"frame_index": 0, "time_sec": 0.0, "shape": [480, 640],
                "detections": [{"label": "person", "confidence": 0.9, "bbox": [64, 48, 320, 480]}]},
               {"frame_index": 1, "time_sec": 0.5, "shape": [480, 640], "detections": []},
               {"frame_index": 2, "time_sec": 1.0, "shape": [480, 640], "detections": []},
               {"frame_index": 3, "time_sec": 1.5, "shape": [480, 640], "detections": []},
           ]}


class SidecarTests(unittest.TestCase):
    def test_normalizes_pixels_and_keeps_segment_clock(self):
        output = normalize_sidecar(SIDECAR, META, SOURCE)
        self.assertEqual((output["width"], output["height"], output["duration"]), (640, 480, 2))
        self.assertEqual(output["frames"][0]["timestamp"], 0)
        self.assertEqual(output["frames"][0]["detections"][0]["bbox"], [0.1, 0.1, 0.5, 1])
        self.assertNotIn("track_id", output["frames"][0]["detections"][0])

    def test_fps_and_index_are_explicit_clock_fallback(self):
        payload = copy.deepcopy(SIDECAR)
        for frame in payload["frames"]:
            del frame["time_sec"]
        output = normalize_sidecar(payload, META, SOURCE)
        self.assertEqual(output["frames"][-1]["timestamp"], 1.5)

    def test_missing_duration_requires_fps(self):
        self.assertEqual(normalize_sidecar(SIDECAR, {}, SOURCE)["duration"], 2)
        payload = copy.deepcopy(SIDECAR)
        del payload["fps"]
        with self.assertRaisesRegex(VastError, "duration"):
            normalize_sidecar(payload, {}, SOURCE)

    def test_mismatched_segment_is_rejected(self):
        payload = copy.deepcopy(SIDECAR)
        payload["segment_source"] = "s3://other/video.mp4"
        with self.assertRaisesRegex(VastError, "different video"):
            normalize_sidecar(payload, META, SOURCE)

    def test_missing_frames_are_not_claimed_as_clear_scene(self):
        with self.assertRaisesRegex(VastError, "no per-frame"):
            normalize_sidecar({"object_counts": {"person": 3}}, META, SOURCE)

    def test_rejects_parent_clock_and_nonmonotonic_frames(self):
        for timestamp in [12, 0]:
            with self.subTest(timestamp=timestamp):
                payload = copy.deepcopy(SIDECAR)
                payload["frames"][1]["time_sec"] = timestamp
                with self.assertRaises(VastError):
                    normalize_sidecar(payload, META, SOURCE)

    def test_rejects_missing_dimensions_and_invalid_boxes(self):
        payload = copy.deepcopy(SIDECAR)
        del payload["video_shape"]
        for frame in payload["frames"]:
            del frame["shape"]
        with self.assertRaisesRegex(VastError, "height, width"):
            normalize_sidecar(payload, META, SOURCE)
        for bbox in [[0, 0, 641, 480], [100, 0, 50, 100], [0, 0, float("nan"), 100], [0, 0, 30]]:
            with self.subTest(bbox=bbox):
                payload = copy.deepcopy(SIDECAR)
                payload["frames"][0]["detections"][0]["bbox"] = bbox
                with self.assertRaises(VastError):
                    normalize_sidecar(payload, META, SOURCE)

    def test_source_is_not_an_arbitrary_url(self):
        for source in ["https://example.test/file.mp4", "file:///secret", "s3://bucket", "s3://bucket/key?token=x", None]:
            with self.subTest(source=source), self.assertRaises(VastError):
                validate_source(source)

    def test_normalized_import_requires_explicit_coordinates(self):
        doc = {"width": 640, "height": 480, "duration": 2, "frames": [
            {"timestamp": 0, "detections": [{"class_name": "Person", "confidence": 0.8,
                                            "bbox": [0.1, 0.2, 0.4, 0.6], "track_id": 9}]}]}
        normalized = normalize_import(doc)
        self.assertEqual(normalized["frames"][0]["detections"][0]["track_id"], "9")
        self.assertEqual(normalized["frames"][0]["detections"][0]["class_name"], "person")
        doc["frames"][0]["detections"][0]["bbox"] = [10, 20, 40, 60]
        with self.assertRaisesRegex(VastError, "normalized"):
            normalize_import(doc)


class ClientTests(unittest.IsolatedAsyncioTestCase):
    def client(self, handler, **kwargs):
        return VastClient("https://vss.example.test", "team-1", "private-password",
                          transport=httpx.MockTransport(handler), **kwargs)

    async def test_import_authenticates_and_uses_only_configured_backend(self):
        requests = []

        def handler(request):
            requests.append(request)
            self.assertEqual(request.url.host, "vss.example.test")
            if request.url.path.endswith("/auth/login"):
                self.assertEqual(json.loads(request.content), {"username": "team-1", "password": "private-password"})
                return httpx.Response(200, json={"access_token": "private-token"})
            self.assertEqual(request.headers["authorization"], "Bearer private-token")
            self.assertEqual(request.url.params["source"], SOURCE)
            return httpx.Response(200, json=META if request.url.path.endswith("/metadata") else SIDECAR)

        async with self.client(handler) as client:
            clip = await client.import_clip(SOURCE)
        self.assertEqual(clip["kind"], "vast")
        self.assertIsNone(clip["video_url"])
        self.assertEqual(clip["vast_metadata"]["parent_start_sec"], 12)
        self.assertIsInstance(clip["provenance"], str)
        self.assertEqual(len(requests), 3)
        self.assertNotIn("private-token", json.dumps(clip))
        self.assertNotIn("private-password", json.dumps(clip))

    async def test_expired_token_is_refreshed_once(self):
        count = {"login": 0, "explore": 0}

        def handler(request):
            if request.url.path.endswith("/auth/login"):
                count["login"] += 1
                return httpx.Response(200, json={"access_token": f"token-{count['login']}"})
            count["explore"] += 1
            if count["explore"] == 1:
                return httpx.Response(401, json={"detail": "expired"})
            self.assertEqual(request.headers["authorization"], "Bearer token-2")
            return httpx.Response(200, json={"chunks": []})

        async with self.client(handler) as client:
            self.assertEqual(await client.list_videos(), [])
        self.assertEqual(count, {"login": 2, "explore": 2})

    async def test_bad_credentials_and_upstream_body_are_redacted(self):
        async with self.client(lambda request: httpx.Response(401, text="private-password private-token")) as client:
            with self.assertRaises(VastError) as raised:
                await client.list_videos()
            self.assertNotIn("private-", str(raised.exception))

    async def test_list_videos_returns_segments_not_parent_as_segment(self):
        def handler(request):
            if request.url.path.endswith("/auth/login"):
                return httpx.Response(200, json={"access_token": "t"})
            self.assertEqual(request.url.params["location"], "warehouse")
            return httpx.Response(200, json={"chunks": [{"filename": "parent.mp4", "original_video": META["original_video"],
                "camera_id": "cam1", "location": "warehouse", "timeline": [
                    {"source": SOURCE, "segment_number": 2, "segment_start_sec": 12, "segment_end_sec": 14}]}]})

        async with self.client(handler) as client:
            videos = await client.list_videos(location="warehouse")
        self.assertEqual(len(videos), 1)
        self.assertEqual(videos[0]["source"], SOURCE)
        self.assertEqual(videos[0]["duration"], 2)

    async def test_fetch_clip_uses_token_query_and_enforces_size(self):
        def handler(request):
            if request.url.path.endswith("/auth/login"):
                return httpx.Response(200, json={"access_token": "secret-token"})
            self.assertEqual(request.url.path, "/api/v1/videos/stream")
            self.assertEqual(request.url.params["token"], "secret-token")
            self.assertEqual(request.url.params["source"], SOURCE)
            return httpx.Response(200, content=b"0123456789")

        async with self.client(handler, max_media_bytes=8) as client:
            with self.assertRaisesRegex(VastError, "size limit"):
                await client.fetch_clip(SOURCE)
        async with self.client(handler) as client:
            self.assertEqual(await client.fetch_clip(SOURCE), b"0123456789")

    async def test_missing_sidecar_is_actionable(self):
        def handler(request):
            if request.url.path.endswith("/auth/login"):
                return httpx.Response(200, json={"access_token": "t"})
            if request.url.path.endswith("/metadata"):
                return httpx.Response(200, json=META)
            return httpx.Response(404, json={"detail": "upstream private diagnostic"})

        async with self.client(handler) as client:
            with self.assertRaisesRegex(VastError, "No detection sidecar") as raised:
                await client.import_clip(SOURCE)
            self.assertEqual(raised.exception.status_code, 404)

    async def test_no_redirect_to_arbitrary_host(self):
        visited = []

        def handler(request):
            visited.append(request.url.host)
            return httpx.Response(302, headers={"location": "https://different.example.test/login"})

        async with self.client(handler) as client:
            with self.assertRaises(VastError):
                await client.list_videos()
        self.assertEqual(visited, ["vss.example.test"])

    async def test_direct_yolo_is_video_contract_and_normalizes_clock(self):
        def handler(request):
            self.assertEqual(request.url, "https://yolo.example.test/v1/infer")
            self.assertEqual(request.headers["authorization"], "Bearer gpu-secret")
            request_body = json.loads(request.content)
            self.assertEqual(set(request_body), {"video_base64", "filename", "include_frames"})
            frames = copy.deepcopy(SIDECAR["frames"])
            for frame in frames:
                del frame["time_sec"]
            return httpx.Response(200, json={"ok": True, "perception_ok": True, "frames": frames})

        async with self.client(handler, yolo_url="https://yolo.example.test", gpu_bearer_token="gpu-secret") as client:
            result = await client.infer_clip(b"video bytes", duration=2)
        self.assertEqual(result["frames"][-1]["timestamp"], 1.5)
        self.assertEqual(result["frames"][0]["detections"][0]["bbox"], [0.1, 0.1, 0.5, 1])


if __name__ == "__main__":
    unittest.main()
