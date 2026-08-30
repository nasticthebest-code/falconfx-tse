"""FastAPI entrypoint for FalconFX Traffic Signal Engine v1."""

from __future__ import annotations

import logging
import os
import secrets
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

from . import __version__
from .config import Settings
from .core.engine import SignalEngine
from .models import TrafficSignal
from .providers.simulation import SCENARIOS, SimulationTrafficProvider
from .providers.tomtom import TomTomTrafficProvider

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


class SimulationScenarioRequest(BaseModel):
    scenario: str = Field(description="One of the built-in deterministic traffic scenarios")


def create_provider(settings: Settings):
    if settings.provider_name == "simulation":
        return SimulationTrafficProvider(settings.simulation_scenario)
    return TomTomTrafficProvider(settings)


def create_app(
    settings: Settings | None = None,
    provider: Any | None = None,
) -> FastAPI:
    """Create an app, allowing tests and future integrations to inject providers."""

    runtime_settings = settings or Settings.from_env()
    traffic_provider = provider or create_provider(runtime_settings)
    engine = SignalEngine(traffic_provider, runtime_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await engine.start()
        try:
            yield
        finally:
            await engine.stop()

    app = FastAPI(
        title="FalconFX Traffic Signal Engine",
        version=runtime_settings.version,
        description=(
            "Signal generator for FalconFX Booster. TSE emits deterministic "
            "traffic intelligence and never makes destination decisions."
        ),
        lifespan=lifespan,
    )
    app.state.engine = engine
    app.state.settings = runtime_settings

    @app.get("/health")
    async def health() -> dict[str, Any]:
        snapshot = await engine.cache.snapshot()
        return {
            "status": "ok",
            "version": runtime_settings.version,
            "cached_corridors": sorted(snapshot),
            "last_refresh": engine.cache.last_refresh,
            "refresh_in_progress": engine.cache.refresh_in_progress,
            "provider": traffic_provider.name,
        }

    @app.get("/api/v1/signals")
    async def all_corridor_signals() -> dict[str, Any]:
        return {
            "signals": [asdict(signal) for signal in await engine.get_all_signals()],
            "generated_at": datetime.now(timezone.utc),
            "provider": traffic_provider.name,
        }

    @app.get("/api/v1/signals/{corridor_id}")
    async def corridor_signal(corridor_id: str) -> dict[str, Any]:
        try:
            signal = await engine.get_signal(corridor_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return asdict(signal)

    @app.get("/internal/v1/debug/signals/{corridor_id}")
    async def corridor_debug_signal(
        corridor_id: str,
        x_tse_internal_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Internal calculation trace; intentionally outside the public API."""

        if not runtime_settings.debug_endpoint_enabled:
            raise HTTPException(status_code=404, detail="not found")
        if runtime_settings.internal_debug_token and not secrets.compare_digest(
            x_tse_internal_token or "",
            runtime_settings.internal_debug_token,
        ):
            raise HTTPException(status_code=403, detail="internal token required")
        try:
            debug_data = await engine.get_debug_data(corridor_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        return asdict(debug_data)

    @app.get("/api/v1/simulation/scenarios")
    async def simulation_scenarios() -> dict[str, Any]:
        return {"scenarios": list(SCENARIOS)}

    @app.post("/api/v1/simulation/scenario")
    async def set_simulation_scenario(payload: SimulationScenarioRequest) -> dict[str, str]:
        if not isinstance(traffic_provider, SimulationTrafficProvider):
            raise HTTPException(status_code=409, detail="service is not using simulation provider")
        try:
            traffic_provider.set_scenario(payload.scenario)
            await engine.cache.refresh_now()
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {"status": "updated", "scenario": payload.scenario}

    return app


app = create_app()