from __future__ import annotations

from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.core.calculations import (
    FlowDirectionEngine,
    calculate_congestion,
    calculate_queue_pressure,
    calculate_speed_ratio,
)
from app.main import create_app
from app.providers.simulation import SimulationTrafficProvider


def test_congestion_formula_retains_speed_ratio() -> None:
    speed_ratio = calculate_speed_ratio(42.0, 60.0)
    assert speed_ratio == pytest.approx(0.7)
    assert calculate_congestion(speed_ratio) == pytest.approx(0.3)


def test_queue_pressure_includes_positive_momentum_and_slow_movement() -> None:
    pressure, delta = calculate_queue_pressure(
        congestion=0.7,
        previous_congestion=0.3,
        speed_ratio=0.3,
    )
    expected = (0.60 * 0.7) + (0.25 * 0.4) + (0.15 * ((0.5 - 0.3) * 2))
    assert delta == pytest.approx(0.4)
    assert pressure == pytest.approx(expected)


def test_negative_congestion_delta_does_not_reduce_queue_pressure() -> None:
    pressure, delta = calculate_queue_pressure(
        congestion=0.4,
        previous_congestion=0.8,
        speed_ratio=0.6,
    )
    assert delta == 0.0
    assert pressure == pytest.approx(0.60 * 0.4)


def test_flow_direction_uses_sequential_samples() -> None:
    engine = FlowDirectionEngine(change_threshold=0.05)
    assert engine.infer((0.2, 0.4, 0.8), (0.2, 0.3, 0.5)) == "inbound"
    assert engine.infer((0.8, 0.4, 0.2), (0.5, 0.3, 0.2)) == "outbound"
    assert engine.infer((0.4, 0.4, 0.4), (0.4, 0.4, 0.4)) == "stable"
    assert engine.infer((0.8, 0.4, 0.2), (0.2, 0.4, 0.8)) == "mixed"


@pytest.mark.asyncio
async def test_simulation_api_generates_cached_signals() -> None:
    settings = Settings(
        provider_name="simulation",
        cache_ttl_seconds=120,
        refresh_interval_seconds=120,
        debug_endpoint_enabled=True,
    )
    app = create_app(settings=settings, provider=SimulationTrafficProvider("free_flow"))
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            first = await client.get("/api/v1/signals")
            second = await client.get("/api/v1/signals")
            assert first.status_code == 200
            assert second.status_code == 200
            assert len(first.json()["signals"]) == 8
            assert second.json()["signals"][0]["provider"] == "simulation"


@pytest.mark.asyncio
async def test_spillover_is_bounded_and_debug_trace_is_available() -> None:
    settings = Settings(
        provider_name="simulation",
        simulation_scenario="morning_rush",
        debug_endpoint_enabled=True,
    )
    app = create_app(settings=settings, provider=SimulationTrafficProvider("morning_rush"))
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/api/v1/signals/N1")
            debug = await client.get("/internal/v1/debug/signals/N1")
            assert response.status_code == 200
            assert debug.status_code == 200
            signal = response.json()
            assert 0.0 <= signal["spillover_probability"] <= 1.0
            for field in (
                "current_speed",
                "free_flow_speed",
                "speed_ratio",
                "congestion",
                "previous_congestion",
                "congestion_delta",
                "queue_pressure",
                "spillover_probability",
                "cache_age_seconds",
            ):
                assert field in debug.json()["signal"]