"""Additive routes and branding; use the existing playback/storage/VAST pipeline."""
import json
import os
from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from zonelogic.models import SessionRequest, EvaluateRequest
from .compound import CompoundConfiguration, CompoundEngine, CombinedEngine, digest


def create_app(data_dir=None, vast_client=None):
    directory = Path(data_dir or os.getenv("OCCLUSIVE_DATA_DIR", "data/occlusive")).resolve()
    if directory == Path(os.getenv("ZONELOGIC_DATA_DIR", "data")).resolve():
        raise ValueError("Use a separate directory to protect the working application")
    previous = os.environ.get("ZONELOGIC_DATA_DIR")
    os.environ["ZONELOGIC_DATA_DIR"] = str(directory)
    try:
        from zonelogic.main import create_app as create_original
    finally:
        if previous is None:
            os.environ.pop("ZONELOGIC_DATA_DIR", None)
        else:
            os.environ["ZONELOGIC_DATA_DIR"] = previous
    core = create_original(directory, vast_client=vast_client)
    core.title = "Occlusive Logic"
    store = core.state.store
    with store.connect() as db:
        db.execute("CREATE TABLE IF NOT EXISTS compound_configurations (source_id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
    original = {getattr(route, "path", ""): getattr(route, "endpoint", None) for route in core.routes}
    router = APIRouter()

    def configuration(source_id):
        if source_id not in core.state.sources:
            raise HTTPException(404, "Source not found")
        with store.connect() as db:
            row = db.execute("SELECT payload FROM compound_configurations WHERE source_id=?", (source_id,)).fetchone()
        return json.loads(row[0]) if row else {"source_id": source_id, "rules": []}

    @router.get("/api/compound/config")
    def read_config(source_id: str):
        return configuration(source_id)

    @router.put("/api/compound/config")
    async def save_config(request: CompoundConfiguration):
        configuration(request.source_id)
        zones = {zone["id"] for zone in store.configuration(request.source_id)["zones"]}
        labels = {item["class_name"] for frame in core.state.sources[request.source_id]["frames"] for item in frame["detections"]}
        for rule in request.rules:
            for sensor in (rule.a, rule.b):
                if sensor.zone_id not in zones:
                    raise HTTPException(422, "Each sensor must reference a saved zone in this source")
                if sensor.class_name not in labels:
                    raise HTTPException(422, "Select a class actually present in this source's detections")
        payload = request.model_dump(mode="json")
        with store.connect() as db:
            db.execute("INSERT OR REPLACE INTO compound_configurations VALUES (?,?)", (request.source_id, json.dumps(payload)))
        return payload

    @router.post("/api/sessions", status_code=201)
    async def session(request: SessionRequest):
        result = await original["/api/sessions"](request)
        state = core.state.sessions[result["id"]]
        config = configuration(request.source_id)
        state.engine = CombinedEngine(state.engine, CompoundEngine(state.config["zones"], config["rules"]))
        state.compound_hash = digest(config)
        return result

    @router.post("/api/sessions/{session_id}/evaluate")
    async def evaluate(session_id: str, request: EvaluateRequest):
        state = core.state.sessions.get(session_id)
        if state is not None and state.compound_hash != digest(configuration(state.source_id)):
            raise HTTPException(409, "Compound rules changed. Reload the source to start a new session.")
        result = await original["/api/sessions/{session_id}/evaluate"](session_id, request)
        engine = state.engine.compound
        fresh = engine.last_timestamp is not None and 0 <= request.timestamp - engine.last_timestamp <= 2
        result["compound_states"] = engine.values if fresh else [
            {**value, "a": None, "b": None, "result": None, "raw_a": None, "raw_b": None} for value in engine.values]
        result["compound_fresh"] = fresh
        return result

    page = (Path(__file__).parents[1] / "zonelogic/static/index.html").read_text(encoding="utf-8")
    page = page.replace("ZoneLogic", "Occlusive Logic").replace("ZONELOGIC", "OCCLUSIVE LOGIC").replace('>Z<span>·</span>', '>O<span>·</span>')
    page = page.replace("</head>", '<link rel="stylesheet" href="compound-static/compound.css"><script src="compound-static/compound.js" defer></script></head>')

    @router.get("/", include_in_schema=False)
    def index():
        return HTMLResponse(page)

    @router.get("/health", include_in_schema=False)
    def health():
        return {"status": "ok", "application": "Occlusive Logic", "compound_rules": True}

    @router.get("/api/status")
    def status():
        result = original["/api/status"]()
        result["notice"] = result["notice"].replace("ZoneLogic", "Occlusive Logic")
        result["compound_rules"] = True
        return result

    @router.get("/api/incidents/export")
    def export(source_id: str | None = None):
        result = json.loads(original["/api/incidents/export"](source_id).body)
        result["application"] = "Occlusive Logic"
        return JSONResponse(result, headers={"Content-Disposition": 'attachment; filename="occlusive-logic-incidents.json"'})

    count = len(core.router.routes)
    core.include_router(router)
    core.mount("/compound-static", StaticFiles(directory=Path(__file__).parent / "static"), name="compound-static")
    core.router.routes[:] = core.router.routes[count:] + core.router.routes[:count]

    @asynccontextmanager
    async def lifespan(_):
        async with core.router.lifespan_context(core):
            yield
    wrapper = FastAPI(lifespan=lifespan)
    wrapper.state.core = core
    @wrapper.get("/app", include_in_schema=False)
    def redirect():
        return RedirectResponse("/app/")
    wrapper.mount("/app", core)
    wrapper.mount("/", core)
    return wrapper
