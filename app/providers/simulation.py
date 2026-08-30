"""Deterministic synthetic traffic provider for development and verification."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from ..config import CorridorDefinition
from ..models import CorridorTrafficData, TrafficSample


SCENARIOS = ("morning_rush", "evening_rush", "accident", "free_flow", "gridlock")


class SimulationTrafficProvider:
    """Generate repeatable corridor observations without external network calls."""

    name = "simulation"

    def __init__(self, scenario: str = "free_flow") -> None:
        self.scenario = scenario if scenario in SCENARIOS else "free_flow"
        self.tick = 0

    def set_scenario(self, scenario: str) -> None:
        if scenario not in SCENARIOS:
            raise ValueError(f"scenario must be one of: {', '.join(SCENARIOS)}")
        self.scenario = scenario
        self.tick = 0

    async def get_corridor_data(self, corridor: CorridorDefinition) -> CorridorTrafficData:
        """Create a three-point corridor snapshot with evolving congestion."""

        await asyncio.sleep(0)
        self.tick += 1
        samples: list[TrafficSample] = []
        now = datetime.now(timezone.utc)
        for index, (latitude, longitude) in enumerate(corridor.monitor_points):
            position = index / max(1, len(corridor.monitor_points) - 1)
            congestion = self._congestion(position)
            free_flow_speed = 60.0
            samples.append(
                TrafficSample(
                    latitude=latitude,
                    longitude=longitude,
                    current_speed=free_flow_speed * (1.0 - congestion),
                    free_flow_speed=free_flow_speed,
                    observed_at=now,
                    source_metadata={
                        "provider": self.name,
                        "scenario": self.scenario,
                        "simulation_tick": self.tick,
                        "monitor_point_index": index,
                    },
                )
            )
        return CorridorTrafficData(
            corridor_id=corridor.corridor_id,
            samples=tuple(samples),
            fetched_at=now,
        )

    def _congestion(self, position: float) -> float:
        phase = min(1.0, self.tick / 4.0)
        if self.scenario == "free_flow":
            return 0.05 + (0.01 * position)
        if self.scenario == "gridlock":
            return 0.88 + (0.02 * position)
        if self.scenario == "morning_rush":
            return 0.22 + (0.38 * phase * position)
        if self.scenario == "evening_rush":
            return 0.22 + (0.38 * phase * (1.0 - position))
        # An accident creates a localized, opposing disturbance rather than
        # pretending that the entire corridor has a single flow direction.
        distance_from_center = abs(position - 0.5)
        return 0.25 + (0.55 * max(0.0, 1.0 - (distance_from_center * 3.0))) + (0.04 * phase)