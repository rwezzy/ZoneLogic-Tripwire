"""Compose optional routes around the existing app, leaving its files untouched."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

logger = logging.getLogger(__name__)


def create_optional_app(data_dir: str | Path | None = None, *, enabled=True, vast_client=None):
    directory = Path(data_dir or os.getenv("ZONELOGIC_SCENARIO_DATA_DIR", "data/scenarios")).resolve()
    original_directory = Path(os.getenv("ZONELOGIC_DATA_DIR", "data")).resolve()
    if directory == original_directory:
        raise ValueError("The optional playground requires a separate data directory; the original database is protected.")
    # The original module exports an ASGI app at import time. Point that first
    # initialization at this sandbox too; never open the working app's database.
    previous = os.environ.get("ZONELOGIC_DATA_DIR")
    os.environ["ZONELOGIC_DATA_DIR"] = str(directory)
    try:
        from zonelogic.main import create_app
    finally:
        if previous is None:
            os.environ.pop("ZONELOGIC_DATA_DIR", None)
        else:
            os.environ["ZONELOGIC_DATA_DIR"] = previous
    core = create_app(directory, vast_client=vast_client)
    active = False
    if enabled:
        try:
            from .extension import install
            install(core)
            active = True
        except Exception:
            # Optional imports/assets/configuration must not disable the existing
            # workflow. The user's original process/database is never involved.
            logger.exception("Optional scenarios unavailable; serving the original workspace in separate storage.")
            core = create_app(directory, vast_client=vast_client)
    if not active:
        @core.get("/api/scenarios")
        def disabled():
            return {"enabled": False, "scenarios": [], "notice": "Optional scenarios are off. The original workspace is available with separate storage."}

    @asynccontextmanager
    async def lifespan(_):
        async with core.router.lifespan_context(core):
            yield

    wrapper = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    wrapper.state.core = core
    wrapper.state.scenarios_enabled = active

    @wrapper.get("/app", include_in_schema=False)
    def redirect():
        return RedirectResponse("/app/", status_code=307)

    wrapper.mount("/app", core)
    wrapper.mount("/", core)
    return wrapper
