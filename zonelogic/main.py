"""FastAPI application and playback-driven rule evaluation."""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import os
import time
import uuid
from bisect import bisect_right
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
from pydantic import ValidationError

from . import __version__, demo
from .engine import RuleEngine
from .models import Configuration, DetectionDocument, EvaluateRequest, EvidenceRequest, IncidentUpdate, SessionRequest, VastImportRequest
from .store import Store


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _public_source(source):
    return {k: v for k, v in source.items() if k not in {"frames", "media_path", "upstream_source"}}


@dataclass
class Session:
    source_id: str
    engine: RuleEngine
    config_hash: str
    config: dict
    frame_times: list[float]
    index: int = -1
    requested_time: float | None = None
    touched: float = field(default_factory=time.monotonic)


def create_app(data_dir: str | Path | None = None, vast_client=None):
    directory = Path(data_dir or os.getenv("ZONELOGIC_DATA_DIR", "data")).resolve()
    store = Store(directory)
    source_map = {s["id"]: s for s in store.sources()}
    for source in demo.sources():
        source_map[source["id"]] = source
        if not store.has_configuration(source["id"]):
            store.save_configuration(Configuration.model_validate(demo.configuration(source["id"])).model_dump(mode="json"))
    sessions: dict[str, Session] = {}

    @asynccontextmanager
    async def lifespan(app):
        yield
        client = app.state.vast
        if client is not None and hasattr(client, "close"):
            await client.close()

    app = FastAPI(title="ZoneLogic", version=__version__, lifespan=lifespan)
    app.state.store = store
    app.state.sources = source_map
    app.state.sessions = sessions
    app.state.vast = vast_client
    app.state.data_dir = directory

    @app.middleware("http")
    async def same_origin_writes(request: Request, call_next):
        # Prevent another website from posting into a localhost monitoring server.
        origin = request.headers.get("origin")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin:
            from urllib.parse import urlsplit
            parsed = urlsplit(origin)
            if parsed.netloc != request.headers.get("host"):
                return JSONResponse({"detail": "Cross-origin writes are not allowed"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Do not echo uploaded base64 or sensitive request inputs in error responses.
        details = [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
        return JSONResponse({"detail": "; ".join(details)}, status_code=422)

    def get_source(source_id):
        source = source_map.get(source_id)
        if source is None:
            raise HTTPException(404, "Video source not found")
        return source

    def get_vast():
        if app.state.vast is None:
            from .vast import VastClient, VastError
            try:
                app.state.vast = VastClient()
            except (VastError, ValueError) as exc:
                raise HTTPException(503, str(exc)) from exc
        return app.state.vast

    def get_session(session_id):
        session = sessions.get(session_id)
        if session is None:
            raise HTTPException(404, "Monitoring session expired. Reload the source to start a new session.")
        current = store.configuration(session.source_id)
        if _digest(current) != session.config_hash:
            raise HTTPException(409, "Zone rules changed. Start a new monitoring session.")
        session.touched = time.monotonic()
        return session

    @app.get("/api/status")
    def status():
        configured = bool((os.getenv("VSS_URL") or os.getenv("INGRESS_URL")) and
                          (os.getenv("VSS_USERNAME") or (os.getenv("USERNAME") if os.getenv("PASSWORD") else None)) and
                          (os.getenv("VSS_PASSWORD") or os.getenv("PASSWORD")))
        return {"version": __version__, "mode": "workshop" if configured else "local",
                "vast_configured": configured or vast_client is not None, "yolo_configured": bool(os.getenv("YOLO_URL")),
                "storage": "sqlite", "live_webcam": False,
                "notice": "Candidate incidents for review. ZoneLogic does not measure physical clearance or determine recycling materials."}

    @app.get("/health", include_in_schema=False)
    def health():
        return {"status": "ok", "application": "ZoneLogic", "version": __version__}

    @app.get("/api/sources")
    def list_sources():
        return {"sources": [_public_source(s) for s in source_map.values()]}

    @app.get("/api/sources/{source_id}/detections")
    def detections(source_id: str):
        source = get_source(source_id)
        return {k: source[k] for k in ("width", "height", "duration", "frames", "provenance")}

    @app.get("/api/sources/{source_id}/video")
    def video(source_id: str):
        source = get_source(source_id)
        path = source.get("media_path")
        if not path or not Path(path).is_file():
            raise HTTPException(404, "Video file is unavailable")
        return FileResponse(path, media_type="video/webm" if str(path).endswith(".webm") else "video/mp4")

    @app.get("/api/config")
    def read_config(source_id: str):
        get_source(source_id)
        return store.configuration(source_id)

    @app.put("/api/config")
    async def save_config(config: Configuration):
        get_source(config.source_id)
        payload = config.model_dump(mode="json")
        # Engine construction catches unsupported rules before persistent mutation.
        try:
            RuleEngine(payload["zones"], payload["rules"])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        store.save_configuration(payload)
        return payload

    @app.post("/api/sessions", status_code=201)
    async def new_session(request: SessionRequest):
        source = get_source(request.source_id)
        stale = [key for key, value in sessions.items() if time.monotonic() - value.touched > 3600]
        for key in stale:
            del sessions[key]
        if len(sessions) >= 100:
            # Oldest state can be recreated; all incidents remain durable.
            del sessions[min(sessions, key=lambda key: sessions[key].touched)]
        config = store.configuration(request.source_id)
        session_id = uuid.uuid4().hex
        sessions[session_id] = Session(request.source_id, RuleEngine(config["zones"], config["rules"]),
                                       _digest(config), config, [f["timestamp"] for f in source["frames"]])
        return {"id": session_id, "source_id": request.source_id}

    @app.post("/api/sessions/{session_id}/reset")
    async def reset_session(session_id: str):
        session = get_session(session_id)
        session.engine.reset()
        session.index = -1
        session.requested_time = None
        return {"ok": True}

    @app.post("/api/sessions/{session_id}/evaluate")
    async def evaluate(session_id: str, request: EvaluateRequest):
        session = get_session(session_id)
        source = get_source(session.source_id)
        if request.timestamp > source["duration"] + .1:
            raise HTTPException(422, "Playback timestamp exceeds the clip duration")
        target = bisect_right(session.frame_times, request.timestamp) - 1
        rewind = session.requested_time is not None and request.timestamp < session.requested_time
        if request.seek or rewind or target - session.index > 2000:
            session.engine.reset()
            session.index = target - 1
        elif session.index == -1:
            # Loading or starting halfway through a clip is not an observed entry.
            session.index = target - 1
        events = []
        if target >= 0:
            for index in range(max(0, session.index + 1), target + 1):
                frame = source["frames"][index]
                for event in session.engine.process(frame):
                    identifier = _digest([source["id"], session.config_hash, event])[:32]
                    zone = next((z for z in session.config["zones"] if z["id"] == event["zone_id"]), None)
                    incident = {**event, "id": identifier, "source_id": source["id"], "source_name": source["name"],
                                "source_kind": source["kind"], "provenance": source["provenance"], "zone": zone,
                                "created_at": datetime.now(timezone.utc).isoformat(), "status": "open",
                                "evidence_url": None, "configuration_hash": session.config_hash}
                    if store.add_incident(incident):
                        events.append(incident)
            session.index = target
        session.requested_time = request.timestamp
        frame = source["frames"][target] if target >= 0 else {"timestamp": 0, "detections": []}
        # Old boxes should disappear when detections no longer cover the playback time.
        visible = frame["detections"] if request.timestamp - frame["timestamp"] <= 2 else []
        return {"events": events, "detections": visible, "timestamp": frame["timestamp"], "source_id": source["id"]}

    @app.get("/api/incidents/export")
    def export_incidents(source_id: str | None = None):
        payload = {"exported_at": datetime.now(timezone.utc).isoformat(), "application": "ZoneLogic",
                   "incidents": store.incidents(source_id, limit=100000)}
        return JSONResponse(payload, headers={"Content-Disposition": 'attachment; filename="zonelogic-incidents.json"'})

    @app.get("/api/incidents")
    def incidents(source_id: str | None = None, limit: int = Query(200, ge=1, le=1000)):
        return {"incidents": store.incidents(source_id, limit)}

    @app.patch("/api/incidents/{incident_id}")
    def update_incident(incident_id: str, update: IncidentUpdate):
        if not store.set_status(incident_id, update.status):
            raise HTTPException(404, "Incident not found")
        return store.incident(incident_id)

    @app.post("/api/incidents/{incident_id}/evidence")
    def save_evidence(incident_id: str, request: EvidenceRequest):
        incident = store.incident(incident_id)
        if incident is None:
            raise HTTPException(404, "Incident not found")
        if abs(incident["timestamp"] - request.timestamp) > .75:
            raise HTTPException(422, "Evidence must be captured within 0.75 seconds of the event")
        prefix, sep, encoded = request.data_url.partition(",")
        if not sep or prefix not in {"data:image/jpeg;base64", "data:image/png;base64"}:
            raise HTTPException(422, "Evidence must be a JPEG or PNG data URL")
        try:
            data = base64.b64decode(encoded, validate=True)
            with Image.open(io.BytesIO(data)) as im:
                if im.format not in {"JPEG", "PNG"} or im.width * im.height > 8_500_000:
                    raise ValueError("Invalid image")
                actual_type = "image/jpeg" if im.format == "JPEG" else "image/png"
                im.verify()
        except (ValueError, binascii.Error, UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
            raise HTTPException(422, "Evidence image could not be verified") from exc
        store.save_evidence(incident_id, data, actual_type, request.timestamp)
        return store.incident(incident_id)

    @app.get("/api/incidents/{incident_id}/evidence")
    def evidence(incident_id: str):
        saved = store.evidence(incident_id)
        if saved is None:
            raise HTTPException(404, "No screenshot saved; detection geometry is available in the incident")
        return Response(saved[0], media_type=saved[1], headers={"Cache-Control": "private, max-age=3600"})

    async def read_bounded(upload: UploadFile, maximum: int):
        result = bytearray()
        while chunk := await upload.read(1024 * 1024):
            result.extend(chunk)
            if len(result) > maximum:
                raise HTTPException(413, f"{upload.filename or 'File'} exceeds the upload limit")
        return bytes(result)

    @app.post("/api/sources/upload", status_code=201)
    async def upload_source(video: UploadFile = File(...), detections: UploadFile = File(...), name: str = Form("Uploaded clip")):
        extension = Path(video.filename or "video.mp4").suffix.lower()
        if extension not in {".mp4", ".webm", ".m4v"}:
            raise HTTPException(422, "Upload an MP4 or WebM video")
        raw = await read_bounded(detections, 40 * 1024 * 1024)
        try:
            payload = json.loads(raw)
            if isinstance(payload, dict) and "video_shape" in payload:
                from .vast import normalize_sidecar
                payload = normalize_sidecar(payload)
            document = DetectionDocument.model_validate(payload).model_dump(mode="json")
        except (ValueError, ValidationError) as exc:
            raise HTTPException(422, "Invalid detections JSON. Use the documented normalized schema or a VAST bbox sidecar with timing and dimensions.") from exc
        content = await read_bounded(video, 200 * 1024 * 1024)
        if not content:
            raise HTTPException(422, "Video is empty")
        source_id = "upload-" + hashlib.sha256(content + raw).hexdigest()[:16]
        path = directory / "media" / f"{source_id}{extension}"
        path.write_bytes(content)
        source = {**document, "id": source_id, "name": name.strip()[:160] or "Uploaded clip", "kind": "uploaded",
                  "description": "Uploaded video paired with supplied detections.", "video_url": f"api/sources/{source_id}/video", "media_path": str(path)}
        store.save_source(source)
        source_map[source_id] = source
        return _public_source(source)

    @app.get("/api/vast/videos")
    async def vast_videos(location: str | None = None, limit: int = Query(30, ge=1, le=100)):
        from .vast import VastError
        try:
            videos = await get_vast().list_videos(location=location, limit=limit)
            return {"videos": videos}
        except VastError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc

    @app.post("/api/vast/import", status_code=201)
    async def vast_import(request: VastImportRequest):
        from .vast import VastError
        client = get_vast()
        try:
            source = await client.import_clip(request.source)
            source_id = source["id"]
            if source_id in source_map and Path(source_map[source_id].get("media_path", "")).is_file():
                return _public_source(source_map[source_id])
            content = await client.fetch_clip(request.source)
            document = DetectionDocument.model_validate({k: source[k] for k in ("width", "height", "duration", "frames", "provenance")})
        except VastError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc
        except ValidationError as exc:
            raise HTTPException(502, "VAST detections did not pass normalized-coordinate and timestamp validation") from exc
        extension = ".webm" if request.source.lower().endswith(".webm") else ".mp4"
        path = directory / "media" / f"{source_id}{extension}"
        path.write_bytes(content)
        source.update(document.model_dump(mode="json"))
        source.update({"media_path": str(path), "video_url": f"api/sources/{source_id}/video"})
        store.save_source(source)
        source_map[source_id] = source
        return _public_source(source)

    static = Path(__file__).parent / "static"
    static.mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(static / "index.html")

    return app


# Support both a stripped /app proxy and a proxy that forwards the prefix intact.
inner_app = create_app()


@asynccontextmanager
async def mounted_lifespan(_):
    # Starlette does not automatically run mounted applications' lifespans.
    async with inner_app.router.lifespan_context(inner_app):
        yield


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=mounted_lifespan)


@app.get("/app", include_in_schema=False)
def app_redirect():
    return RedirectResponse("/app/", status_code=307)


app.mount("/app", inner_app)
app.mount("/", inner_app)
