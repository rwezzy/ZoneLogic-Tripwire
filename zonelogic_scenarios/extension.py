"""Additive endpoints and frontend assets for the explicitly enabled playground."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from zonelogic.models import Configuration, DetectionDocument, EvaluateRequest

from .policies import OptionStore, SourceOptions, annotation
from . import scenarios

logger = logging.getLogger(__name__)


def install(app):
    static = Path(__file__).parent / "static"
    base_index = Path(__file__).parents[1] / "zonelogic" / "static" / "index.html"
    if not all((static / filename).is_file() for filename in ("addons.js", "addons.css")):
        raise FileNotFoundError("Optional frontend assets are missing")
    page = base_index.read_text(encoding="utf-8").replace("</head>",
        '<link rel="stylesheet" href="scenarios-static/addons.css">\n'
        '<script src="scenarios-static/addons.js" defer></script>\n</head>')
    candidates = scenarios.sources()
    prepared = []
    for source in candidates:
        # Validate the entire addition before registering any new source.
        DetectionDocument.model_validate({key: source[key] for key in ("width", "height", "duration", "frames", "provenance")})
        config = Configuration.model_validate(scenarios.configuration(source["id"])).model_dump(mode="json")
        SourceOptions.model_validate({"source_id": source["id"], "rules": scenarios.rule_options(source["id"])})
        prepared.append((source, config))
    store = app.state.store
    options = OptionStore(store)
    app.state.scenario_options = options
    for source, config in prepared:
        app.state.sources[source["id"]] = source
        if not store.has_configuration(source["id"]):
            store.save_configuration(config)
        if not options.has_options(source["id"]):
            current = store.configuration(source["id"])
            defaults = {key: value for key, value in scenarios.rule_options(source["id"]).items()
                        if key in {r["id"] for r in current["rules"]}}
            options.save(source["id"], defaults, current)
    router = APIRouter()
    core_evaluate = next(route.endpoint for route in app.routes if getattr(route, "path", "") == "/api/sessions/{session_id}/evaluate")

    def require_source(source_id):
        if source_id not in app.state.sources:
            raise HTTPException(404, "Source not found")

    def enrich(event):
        try:
            return options.annotate_existing(event)
        except Exception:
            # Metadata is optional; core evidence and incident records stay usable.
            logger.warning("Optional incident metadata could not be read", exc_info=True)
            return event

    @router.get("/", include_in_schema=False)
    def index():
        return HTMLResponse(page)

    @router.get("/api/scenarios")
    def information():
        return {"enabled": True, "scenarios": [{"id": s["id"], "title": s["name"], "description": s["description"]} for s, _ in prepared],
                "notice": "Optional playground with separate data. Simulated detections illustrate configurable rules; device cues require explicit configuration and browser support."}

    @router.get("/api/scenarios/options")
    def get_options(source_id: str):
        require_source(source_id)
        try:
            values = options.read(source_id, store.configuration(source_id))
        except Exception:
            logger.warning("Optional actions unavailable; keeping them off", exc_info=True)
            values = {}
        return {"source_id": source_id, "rules": values}

    @router.put("/api/scenarios/options")
    async def save_options(request: SourceOptions):
        require_source(request.source_id)
        values = {key: value.model_dump() for key, value in request.rules.items()}
        try:
            options.save(request.source_id, values, store.configuration(request.source_id))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return get_options(request.source_id)

    @router.post("/api/sessions/{session_id}/evaluate")
    async def evaluate(session_id: str, request: EvaluateRequest):
        result = await core_evaluate(session_id, request)
        for index, event in enumerate(result["events"]):
            try:
                configured = options.read(event["source_id"], store.configuration(event["source_id"]))
                details = annotation(event, configured.get(event["rule_id"]))
                if details:
                    options.save_annotation(event["id"], details)
                    result["events"][index] = {**event, "scenario": details}
            except Exception:
                # Fail closed for device actions while preserving the core event.
                logger.warning("Optional event action skipped; core incident was saved", exc_info=True)
        return result

    @router.get("/api/incidents")
    def incidents(source_id: str | None = None, limit: int = Query(200, ge=1, le=1000)):
        return {"incidents": [enrich(event) for event in store.incidents(source_id, limit)]}

    @router.get("/api/incidents/export")
    def export(source_id: str | None = None):
        payload = {"exported_at": datetime.now(timezone.utc).isoformat(), "application": "ZoneLogic optional scenarios",
                   "incidents": [enrich(event) for event in store.incidents(source_id, 100000)]}
        return JSONResponse(payload, headers={"Content-Disposition": 'attachment; filename="zonelogic-scenario-events.json"'})

    # New route definitions take precedence only in this newly constructed app.
    # The original route objects, source files, and running process stay untouched.
    previous_count = len(app.router.routes)
    app.include_router(router)
    app.mount("/scenarios-static", StaticFiles(directory=static), name="scenarios-static")
    additions = app.router.routes[previous_count:]
    app.router.routes[:] = additions + app.router.routes[:previous_count]
