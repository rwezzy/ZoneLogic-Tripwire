"""VAST VSS integration using the public Builders Challenge / Blueprint contracts.

The application owns its HTTP endpoints; clients provide S3 segment identifiers,
never proxy URLs. Credentials and VSS JWTs stay in this server-side adapter.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import math
import os
from pathlib import PurePosixPath
from typing import Any, Mapping
from urllib.parse import urlsplit

import httpx


class VastError(ValueError):
    """An actionable, safe-to-display integration error (no upstream body/URL)."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def _number(value: Any, field: str, *, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise VastError(f"{field} must be a finite number.", 422)
    value = float(value)
    if not math.isfinite(value) or value < minimum:
        raise VastError(f"{field} must be finite and at least {minimum:g}.", 422)
    return value


def _shape(value: Any, field: str) -> tuple[int, int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise VastError(f"{field} must contain [height, width].", 422)
    h, w = (_number(v, field, minimum=1) for v in value)
    if not h.is_integer() or not w.is_integer():
        raise VastError(f"{field} dimensions must be whole pixels.", 422)
    return int(h), int(w)


def validate_source(source: str) -> str:
    """Accept only a plain S3 object identifier for the configured VSS service."""
    if not isinstance(source, str) or len(source) > 4096:
        raise VastError("Choose a valid S3 segment from the VAST archive.", 422)
    try:
        parsed = urlsplit(source)
    except ValueError as exc:
        raise VastError("Choose a valid S3 segment from the VAST archive.", 422) from exc
    if (parsed.scheme != "s3" or not parsed.netloc or not parsed.path.strip("/")
            or parsed.query or parsed.fragment or parsed.username or parsed.password
            or any(ord(c) < 32 for c in source)):
        raise VastError("Choose an s3://bucket/segment source from the VAST archive.", 422)
    return source


def _duration(metadata: Mapping[str, Any]) -> float | None:
    duration = metadata.get("duration")
    if duration is not None:
        return _number(duration, "Segment duration", minimum=0.000001)
    start, end = metadata.get("segment_start_sec"), metadata.get("segment_end_sec")
    if start is not None and end is not None:
        duration = _number(end, "Segment end") - _number(start, "Segment start")
        if duration > 0:
            return duration
    return None


def normalize_sidecar(
    payload: Mapping[str, Any],
    metadata: Mapping[str, Any] | None = None,
    source: str | None = None,
) -> dict[str, Any]:
    """Validate VSS sidecars and map pixel xyxy boxes to normalized xyxy boxes.

    VSS ``time_sec`` is relative to the segment MP4, while metadata start/end are
    relative to the parent. We retain that distinction in provenance. No inferred
    dimensions, clock offsets, made-up identities, or guessed bbox conventions.
    """
    if not isinstance(payload, Mapping):
        raise VastError("Detection sidecar must be a JSON object.", 422)
    metadata = metadata or {}
    expected_source = source or metadata.get("source") or payload.get("segment_source")
    if expected_source:
        validate_source(expected_source)
    if payload.get("segment_source") and payload["segment_source"] != expected_source:
        raise VastError("Detection sidecar belongs to a different video segment.", 422)
    if metadata.get("source") and expected_source != metadata["source"]:
        raise VastError("Metadata belongs to a different video segment.", 422)
    raw_frames = payload.get("frames")
    if not isinstance(raw_frames, list) or not raw_frames:
        raise VastError("This segment has no per-frame detections. Enable stored detection frames or run YOLO on the segment.", 422)
    if len(raw_frames) > 100_000:
        raise VastError("Detection sidecar exceeds the 100,000-frame import limit. Choose a shorter segment.", 413)
    global_shape = payload.get("video_shape")
    if not global_shape:
        global_shape = raw_frames[0].get("shape") if isinstance(raw_frames[0], dict) else None
    height, width = _shape(global_shape, "Detection video_shape")
    fps = payload.get("fps")
    fps = _number(fps, "Detection fps", minimum=0.000001) if fps is not None else None
    duration = _duration(metadata)
    frames = []
    previous = -1.0
    for i, raw in enumerate(raw_frames):
        if not isinstance(raw, Mapping):
            raise VastError(f"Detection frame {i} must be an object.", 422)
        if raw.get("shape") and _shape(raw["shape"], f"Frame {i} shape") != (height, width):
            raise VastError("Detection frame dimensions change inside the segment; use a fixed camera clip.", 422)
        if raw.get("time_sec") is not None:
            timestamp = _number(raw["time_sec"], f"Frame {i} time_sec")
        elif fps is not None and raw.get("frame_index") is not None:
            index = _number(raw["frame_index"], f"Frame {i} frame_index")
            if not index.is_integer():
                raise VastError(f"Frame {i} frame_index must be an integer.", 422)
            timestamp = index / fps
        else:
            raise VastError("Detection frames need segment-local time_sec, or frame_index with fps. Raw YOLO output needs the clip duration first.", 422)
        if timestamp <= previous:
            raise VastError("Detection frame timestamps must increase strictly in segment-local seconds.", 422)
        if duration is not None and timestamp > duration:
            raise VastError("Detection timestamps exceed this segment's duration. Use segment-local seconds, not parent-video timestamps.", 422)
        previous = timestamp
        raw_detections = raw.get("detections")
        if not isinstance(raw_detections, list):
            raise VastError(f"Frame {i} detections must be a list (empty is allowed).", 422)
        detections = []
        for j, detection in enumerate(raw_detections):
            if not isinstance(detection, Mapping):
                raise VastError(f"Frame {i} detection {j} must be an object.", 422)
            label = detection.get("label")
            if not isinstance(label, str) or not label.strip():
                raise VastError(f"Frame {i} detection {j} needs a label.", 422)
            confidence = _number(detection.get("confidence"), "Detection confidence")
            if confidence > 1:
                raise VastError("Detection confidence must be between 0 and 1.", 422)
            bbox = detection.get("bbox")
            if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                raise VastError("VSS bbox must be [x1, y1, x2, y2] in pixels.", 422)
            x1, y1, x2, y2 = [_number(n, "Bounding-box coordinate") for n in bbox]
            if not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height):
                raise VastError("VSS bbox must have positive area and fit inside video_shape in pixel coordinates.", 422)
            item = {"class_name": label.strip().lower(), "confidence": confidence,
                    "bbox": [x1 / width, y1 / height, x2 / width, y2 / height]}
            if detection.get("track_id") is not None:
                identity = detection["track_id"]
                if isinstance(identity, bool) or not isinstance(identity, (str, int)):
                    raise VastError("Detection track_id must be a string or integer.", 422)
                item["track_id"] = str(identity)
            detections.append(item)
        frames.append({"timestamp": timestamp, "detections": detections})
    if duration is None:
        if fps is None:
            raise VastError("Provide segment duration in metadata or fps in the sidecar; duration cannot be guessed.", 422)
        duration = frames[-1]["timestamp"] + 1 / fps
    return {"width": width, "height": height, "duration": duration, "frames": frames}


def normalize_import(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the app's explicit normalized JSON interchange format.

    Required: width, height, duration, frames[{timestamp,detections[{class_name,
    confidence,bbox:[x1,y1,x2,y2] normalized,track_id?}]}]. Media is attached by
    the local upload handler, never taken from a URL in this document.
    """
    if not isinstance(payload, Mapping):
        raise VastError("Import JSON must be an object.", 422)
    height, width = _shape([payload.get("height"), payload.get("width")], "Import dimensions")
    duration = _number(payload.get("duration"), "Import duration", minimum=0.000001)
    raw_frames = payload.get("frames")
    if not isinstance(raw_frames, list):
        raise VastError("Import requires a frames list with normalized bounding boxes.", 422)
    pixel_frames = []
    for frame in raw_frames:
        if not isinstance(frame, Mapping) or not isinstance(frame.get("detections"), list):
            raise VastError("Each import frame needs timestamp and a detections list.", 422)
        detections = []
        for detection in frame["detections"]:
            if not isinstance(detection, Mapping):
                raise VastError("Each import detection must be an object.", 422)
            bbox = detection.get("bbox")
            if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
                raise VastError("Import bbox must contain four normalized xyxy coordinates.", 422)
            normalized = [_number(v, "Normalized bbox") for v in bbox]
            if any(v > 1 for v in normalized):
                raise VastError("Import bbox coordinates must be normalized between 0 and 1.", 422)
            item = {"label": detection.get("class_name"), "confidence": detection.get("confidence"),
                    "bbox": [normalized[0] * width, normalized[1] * height,
                             normalized[2] * width, normalized[3] * height]}
            if detection.get("track_id") is not None:
                item["track_id"] = detection["track_id"]
            detections.append(item)
        pixel_frames.append({"time_sec": frame.get("timestamp"), "detections": detections})
    normalized = normalize_sidecar({"video_shape": [height, width], "frames": pixel_frames}, {"duration": duration})
    return {**normalized, "name": str(payload.get("name") or "Imported detections")[:200],
            "kind": "imported", "description": str(payload.get("description") or "User supplied per-frame detections.")[:4000],
            "video_url": None, "provenance": "User-supplied detections; model provenance not verified. Segment-local seconds and normalized xyxy coordinates."}


class VastClient:
    """Async session client with one token refresh and bounded segment downloads."""

    def __init__(self, base_url: str | None = None, username: str | None = None,
                 password: str | None = None, *, yolo_url: str | None = None,
                 gpu_bearer_token: str | None = None, transport: httpx.AsyncBaseTransport | None = None,
                 max_media_bytes: int = 100 * 1024 * 1024):
        self.base_url = (base_url or os.getenv("VSS_URL") or os.getenv("INGRESS_URL") or "").rstrip("/")
        # USERNAME alone is usually the Windows login. Only use workshop aliases
        # when PASSWORD is also set; VSS_* is the deployment Secret contract.
        self.username = username or os.getenv("VSS_USERNAME") or (os.getenv("USERNAME") if os.getenv("PASSWORD") else "") or ""
        self.password = password or os.getenv("VSS_PASSWORD") or os.getenv("PASSWORD") or ""
        self.yolo_url = (yolo_url or os.getenv("YOLO_URL") or "").rstrip("/")
        self.gpu_bearer_token = gpu_bearer_token or os.getenv("GPU_BEARER_TOKEN") or ""
        self.max_media_bytes = max_media_bytes
        self._token: str | None = None
        self._login_lock = asyncio.Lock()
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(90, connect=10), follow_redirects=False, transport=transport)
        for value in (self.base_url, self.yolo_url):
            if value:
                parsed = urlsplit(value)
                if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
                    raise VastError("VSS_URL and YOLO_URL must be plain HTTP(S) service base URLs.", 503)

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.username and self.password)

    async def close(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> VastClient:
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    def _require_config(self) -> None:
        if not self.configured:
            raise VastError("VAST is not connected. Set VSS_URL (or INGRESS_URL), VSS_USERNAME and VSS_PASSWORD on the server using your team's VM configuration.", 503)

    @staticmethod
    def _check_response(response: httpx.Response, *, route: str = "request") -> None:
        status = response.status_code
        if status < 300:
            return
        if status in (401, 403):
            raise VastError("VAST authentication failed. Check this team's server-side VSS credentials and access.", 502)
        if status == 404:
            detail = "No detection sidecar is stored for this segment. Choose another segment or run YOLO on it." if route.endswith("detections") else "The segment was not found or is not accessible to this team."
            raise VastError(detail, 404)
        if status == 429:
            raise VastError("The VAST service is busy. Retry in a moment.", 503)
        raise VastError(f"The VAST service returned HTTP {status}. Check the configured service and retry.", 502)

    @staticmethod
    def _json(response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except (ValueError, UnicodeDecodeError) as exc:
            raise VastError("VAST returned invalid JSON. Verify the server-side INGRESS_URL points to the VSS backend.") from exc
        if not isinstance(body, dict):
            raise VastError("VAST returned an unexpected response schema. Check the deployed VSS version.")
        return body

    async def _login(self, expired_token: str | None = None) -> None:
        self._require_config()
        async with self._login_lock:
            if self._token and (expired_token is None or self._token != expired_token):
                return
            try:
                response = await self._http.post(self.base_url + "/api/v1/auth/login",
                    json={"username": self.username, "password": self.password})
            except httpx.HTTPError as exc:
                raise VastError("Cannot reach the VAST authentication service. Check the server's network and INGRESS_URL.", 503) from exc
            self._check_response(response, route="login")
            token = self._json(response).get("access_token")
            if not isinstance(token, str) or not token:
                raise VastError("VAST login did not return an access token. Check the configured VSS backend.")
            self._token = token

    async def _request(self, route: str, *, params: dict | None = None) -> dict[str, Any]:
        await self._login()
        for attempt in range(2):
            token = self._token
            try:
                response = await self._http.get(self.base_url + "/api/v1/" + route,
                    params=params, headers={"Authorization": f"Bearer {token}"})
            except httpx.HTTPError as exc:
                raise VastError("Cannot reach the VAST retrieval service. Check network access from the server.", 503) from exc
            if response.status_code == 401 and attempt == 0:
                await self._login(expired_token=token)
                continue
            self._check_response(response, route=route)
            return self._json(response)
        raise VastError("VAST authentication failed after refreshing the session.")

    async def list_videos(self, location: str | None = None, limit: int = 30) -> list[dict[str, Any]]:
        """Return real segment choices; parent timelines aren't imported as clips."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise VastError("Video limit must be between 1 and 100.", 422)
        params: dict[str, Any] = {"scope": "all", "limit": limit, "offset": 0}
        if location:
            params["location"] = location
        payload = await self._request("videos/explore", params=params)
        chunks = payload.get("chunks")
        if not isinstance(chunks, list):
            raise VastError("VAST explore did not return chunks. Check the deployed API schema.")
        result, seen = [], set()
        for chunk in chunks:
            if not isinstance(chunk, dict):
                raise VastError("VAST explore returned an invalid chunk.")
            timeline = chunk.get("timeline")
            if not isinstance(timeline, list):
                raise VastError("VAST chunks need a segment timeline; cannot safely import parent-video detections.")
            for segment in timeline:
                if not isinstance(segment, dict):
                    raise VastError("VAST returned an invalid segment timeline.")
                source = validate_source(segment.get("source"))
                if source in seen:
                    continue
                seen.add(source)
                result.append({"source": source, "name": f"{chunk.get('filename') or 'VAST video'} · segment {segment.get('segment_number', len(result) + 1)}",
                    "duration": _duration(segment), "description": segment.get("reasoning_content") or chunk.get("reasoning_content") or "",
                    "camera_id": chunk.get("camera_id"), "location": chunk.get("location"),
                    "original_video": chunk.get("original_video"), "object_classes": segment.get("object_classes"),
                    "parent_start_sec": segment.get("segment_start_sec")})
                if len(result) >= limit:
                    return result
        return result

    async def import_clip(self, source: str) -> dict[str, Any]:
        source = validate_source(source)
        metadata = await self._request("videos/metadata", params={"source": source})
        sidecar = await self._request("videos/detections", params={"source": source})
        normalized = normalize_sidecar(sidecar, metadata, source)
        detector = sidecar.get("source") or "YOLO"
        return {"id": "vast-" + hashlib.sha256(source.encode()).hexdigest()[:20],
                "name": metadata.get("filename") or PurePosixPath(urlsplit(source).path).name,
                "kind": "vast", **normalized, "description": metadata.get("reasoning_content") or "",
                "video_url": None,
                "provenance": f"VAST VSS stored {detector} sidecar. Segment-local seconds; pixel xyxy boxes normalized using the stored frame dimensions. Object identities are assigned locally when no tracking IDs are provided.",
                "vast_metadata": {"provider": "VAST VSS", "source": source,
                    "original_video": metadata.get("original_video"), "camera_id": metadata.get("camera_id"),
                    "location": metadata.get("location"), "parent_start_sec": metadata.get("segment_start_sec"),
                    "time_basis": "segment_local_seconds", "bbox_format": "normalized_xyxy",
                    "detector": sidecar.get("source") or "YOLO", "detection_source": "VSS stored sidecar"}}

    async def fetch_clip(self, source: str) -> bytes:
        source = validate_source(source)
        await self._login()
        for attempt in range(2):
            token = self._token
            try:
                async with self._http.stream("GET", self.base_url + "/api/v1/videos/stream",
                        params={"source": source, "token": token}) as response:
                    if response.status_code == 401 and attempt == 0:
                        await response.aread()
                    else:
                        self._check_response(response, route="videos/stream")
                        size = 0
                        chunks = []
                        async for chunk in response.aiter_bytes():
                            size += len(chunk)
                            if size > self.max_media_bytes:
                                raise VastError("VAST segment exceeds the media size limit. Select a shorter indexed segment.", 413)
                            chunks.append(chunk)
                        if size == 0:
                            raise VastError("VAST returned an empty video segment.")
                        return b"".join(chunks)
            except httpx.HTTPError as exc:
                raise VastError("Cannot download the VAST segment. Check the server's network connection.", 503) from exc
            await self._login(expired_token=token)
        raise VastError("VAST authentication failed after refreshing the stream session.")

    async def infer_clip(self, video: bytes, *, duration: float, filename: str = "segment.mp4") -> dict[str, Any]:
        """Explicit optional inference for a video, not an undocumented image API."""
        if not self.yolo_url:
            raise VastError("Set YOLO_URL on the server to enable direct video detection.", 503)
        duration = _number(duration, "Clip duration", minimum=0.000001)
        if not video or len(video) > self.max_media_bytes:
            raise VastError("YOLO input must be a nonempty video within the media size limit.", 413)
        headers = {"Authorization": f"Bearer {self.gpu_bearer_token}"} if self.gpu_bearer_token else {}
        try:
            response = await self._http.post(self.yolo_url + "/v1/infer", headers=headers,
                json={"video_base64": base64.b64encode(video).decode("ascii"), "filename": filename,
                      "include_frames": True}, timeout=180)
        except httpx.HTTPError as exc:
            raise VastError("Cannot reach the YOLO inference service. Check YOLO_URL and the GPU service.", 503) from exc
        if response.status_code in (401, 403):
            raise VastError("YOLO authentication failed. Configure your team's GPU_BEARER_TOKEN on the server.", 502)
        self._check_response(response, route="YOLO inference")
        payload = self._json(response)
        perception = payload.get("perception_json") or {}
        if not isinstance(perception, Mapping):
            raise VastError("YOLO returned an unknown perception_json schema.")
        frames = payload.get("frames") if isinstance(payload.get("frames"), list) else perception.get("frames")
        if not isinstance(frames, list) or not frames:
            raise VastError("YOLO did not return video frames. Verify the segment is a readable video and include_frames is supported.")
        # The official service predicts every decoded frame and returns sequential
        # frame_index without timestamps. Match its own detector-sidecar clock.
        for index, frame in enumerate(frames):
            if not isinstance(frame, Mapping) or frame.get("frame_index") != index:
                raise VastError("YOLO frame indices are not sequential. Explicit frame timestamps are required.")
        return normalize_sidecar({"frames": frames, "fps": len(frames) / duration}, {"duration": duration})
