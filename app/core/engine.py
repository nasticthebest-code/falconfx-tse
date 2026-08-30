"""Signal generation orchestration and provider/cache boundary."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import datetime, timezone
from time import perf_counter

from ..cache import SignalCache
from ..config import CORRIDORS, HUBS, CorridorDefinition, Settings, monitored_corridor_definitions
from ..models import CorridorDebugData, CorridorTrafficData, TrafficSignal
from ..providers.base import TrafficProvider
from .calculations import (
    FlowDirectionEngine,
    calculate_congestion,
    calculate_queue_pressure,
    calculate_speed_ratio,
    time_window_factor,
    transport_hub_factor,
)

logger = logging.getLogger("falconfx.tse.engine")


class SignalEngine:
    """Generate signals without making any Booster decisions."""

    def __init__(self, provider: TrafficProvider, settings: Settings) -> None:
        self.provider = provider
        self.settings = settings
        self.corridors = monitored_corridor_definitions(settings)
        self.direction_engine = FlowDirectionEngine(settings.direction_change_threshold)
        self.cache = SignalCache(
            settings.cache_ttl_seconds,
            settings.refresh_interval_seconds,
            self.refresh_all,
        )
        self._previous_data: dict[str, CorridorTrafficData] = {}
        self._last_data: dict[str, CorridorTrafficData] = {}

    async def start(self) -> None:
        await self.cache.start()

    async def stop(self) -> None:
        await self.cache.stop()

    async def get_signal(self, corridor_id: str) -> TrafficSignal:
        corridor = self._find_corridor(corridor_id)
        await self.cache.ensure_available()
        entry = await self.cache.get(corridor.corridor_id)
        if entry is None:
            await self.cache.refresh_now()
            entry = await self.cache.get(corridor.corridor_id)
        if entry is None:
            raise RuntimeError(f"no signal available for corridor {corridor_id}")
        if not entry.is_fresh(self.settings.cache_ttl_seconds):
            await self.cache.trigger_background_refresh()
        return replace(
            entry.signal,
            cache_age_seconds=entry.age_seconds(),
            stale=entry.stale,
        )

    async def get_all_signals(self) -> list[TrafficSignal]:
        await self.cache.ensure_available()
        entries = await self.cache.snapshot()
        missing = [corridor.corridor_id for corridor in self.corridors if corridor.corridor_id not in entries]
        if missing:
            await self.cache.refresh_now()
            entries = await self.cache.snapshot()
        signals: list[TrafficSignal] = []
        stale_found = False
        for corridor in self.corridors:
            entry = entries.get(corridor.corridor_id)
            if entry is None:
                continue
            stale_found = stale_found or not entry.is_fresh(self.settings.cache_ttl_seconds)
            signals.append(
                replace(
                    entry.signal,
                    cache_age_seconds=entry.age_seconds(),
                    stale=entry.stale,
                )
            )
        if stale_found:
            await self.cache.trigger_background_refresh()
        return signals

    async def get_debug_data(self, corridor_id: str) -> CorridorDebugData:
        signal = await self.get_signal(corridor_id)
        corridor = self._find_corridor(corridor_id)
        data = self._last_data.get(corridor_id)
        return CorridorDebugData(
            signal=signal,
            sample_count=len(data.samples) if data else 0,
            monitor_points=corridor.monitor_points,
            direction_vector=corridor.direction_vector,
            hub_references=corridor.transport_hub_references,
            cache_age_seconds=signal.cache_age_seconds,
            last_refresh=self.cache.last_refresh,
        )

    async def refresh_all(self) -> None:
        """Fetch every corridor concurrently and publish only completed signals."""

        started = perf_counter()
        results = await asyncio.gather(
            *(self._refresh_corridor(corridor) for corridor in self.corridors),
            return_exceptions=True,
        )
        failures = [result for result in results if isinstance(result, Exception)]
        if failures and not self._last_data:
            raise RuntimeError(f"initial traffic refresh failed: {failures[0]}")
        logger.info(
            "signal refresh duration_ms=%.2f corridors=%d failures=%d",
            (perf_counter() - started) * 1000,
            len(self.corridors),
            len(failures),
        )

    async def _refresh_corridor(self, corridor: CorridorDefinition) -> None:
        started = perf_counter()
        try:
            traffic_data = await self.provider.get_corridor_data(corridor)
            signal = self._build_signal(corridor, traffic_data)
            self._previous_data[corridor.corridor_id] = self._last_data.get(
                corridor.corridor_id,
                traffic_data,
            )
            self._last_data[corridor.corridor_id] = traffic_data
            await self.cache.put(corridor.corridor_id, signal)
            logger.info(
                "corridor processed corridor=%s duration_ms=%.2f",
                corridor.corridor_id,
                (perf_counter() - started) * 1000,
            )
        except Exception as error:
            await self.cache.mark_stale(corridor.corridor_id, str(error))
            logger.exception("corridor refresh failed corridor=%s", corridor.corridor_id)
            raise

    def _build_signal(
        self,
        corridor: CorridorDefinition,
        traffic_data: CorridorTrafficData,
    ) -> TrafficSignal:
        """Apply the approved formulas to the representative observation."""

        current_congestion_values = tuple(
            calculate_congestion(
                calculate_speed_ratio(sample.current_speed, sample.free_flow_speed)
            )
            for sample in traffic_data.samples
        )
        previous_data = self._last_data.get(corridor.corridor_id)
        previous_congestion_values = (
            tuple(
                calculate_congestion(
                    calculate_speed_ratio(sample.current_speed, sample.free_flow_speed)
                )
                for sample in previous_data.samples
            )
            if previous_data and len(previous_data.samples) == len(traffic_data.samples)
            else None
        )
        representative = traffic_data.representative_sample
        speed_ratio = calculate_speed_ratio(
            representative.current_speed,
            representative.free_flow_speed,
        )
        congestion = calculate_congestion(speed_ratio)
        previous_congestion = (
            previous_congestion_values[len(previous_congestion_values) // 2]
            if previous_congestion_values
            else congestion
        )
        queue_pressure, congestion_delta = calculate_queue_pressure(
            congestion,
            previous_congestion,
            speed_ratio,
        )
        flow_direction = self.direction_engine.infer(
            current_congestion_values,
            previous_congestion_values,
        )
        spillover = min(
            1.0,
            queue_pressure
            * transport_hub_factor(corridor, HUBS, self.settings)
            * time_window_factor(representative.observed_at, self.settings),
        )
        return TrafficSignal(
            corridor_id=corridor.corridor_id,
            corridor_name=corridor.name,
            generated_at=datetime.now(timezone.utc),
            current_speed=representative.current_speed,
            free_flow_speed=representative.free_flow_speed,
            speed_ratio=speed_ratio,
            congestion=congestion,
            previous_congestion=previous_congestion,
            congestion_delta=congestion_delta,
            queue_pressure=queue_pressure,
            flow_direction=flow_direction,
            spillover_probability=spillover,
            transport_hub_factor=transport_hub_factor(corridor, HUBS, self.settings),
            time_window_factor=time_window_factor(representative.observed_at, self.settings),
            provider=self.provider.name,
            source_metadata={
                "sample_count": len(traffic_data.samples),
                "corridor_direction_vector": corridor.direction_vector,
            },
        )

    def _find_corridor(self, corridor_id: str) -> CorridorDefinition:
        try:
            return next(corridor for corridor in self.corridors if corridor.corridor_id == corridor_id)
        except StopIteration as error:
            raise KeyError(f"unknown corridor: {corridor_id}") from error