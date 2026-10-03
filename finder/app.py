"""FastAPI app: JSON API + the static web UI.  Run:  python -m finder"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles

from .config import ROOT, load_settings
from .services import BY_KEY, SERVICES

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def create_app(settings=None) -> FastAPI:
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.demo:
            from .demo import DemoEngine

            app.state.engine = DemoEngine(settings)
        else:
            from .engine import Engine

            app.state.engine = Engine(settings)
        yield
        await app.state.engine.close()

    app = FastAPI(title="Health Provider Discovery", lifespan=lifespan)

    @app.get("/api/config")
    async def config():
        return {
            "home": settings.home,
            "radius": settings.radius_miles,
            "demo": settings.demo,
            "network": settings.network.type,
            "services": [
                {"key": s.key, "label": s.label, "group": s.group, "kind": s.kind, "tip": s.tip}
                for s in SERVICES
            ],
        }

    @app.get("/api/search")
    async def search(service: str, where: str | None = None, radius: float | None = None):
        if service not in BY_KEY:
            raise HTTPException(404, f"Unknown service {service}")
        if radius is not None and not 0 < radius <= 60:
            raise HTTPException(400, "radius must be 1-60 miles")
        try:
            return await app.state.engine.search(service, where, radius)
        except ValueError as e:
            raise HTTPException(400, str(e))

    @app.post("/api/network/{pid}")
    async def check_network(pid: str):
        try:
            return await app.state.engine.check_network(pid)
        except KeyError:
            raise HTTPException(404, "Run a search that includes this provider first")

    @app.get("/api/networks")
    async def networks(q: str):
        try:
            return await app.state.engine.find_networks(q)
        except ValueError as e:
            raise HTTPException(400, str(e))

    app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")
    return app
