"""Domain models shared by providers, signal algorithms, and the API."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .config import CorridorDefinition


@dataclass(frozen=True, slots=True)
class TrafficSample:
    """One provider observation at a fixed position along a corridor."""

    latitude: float
    longitude: float
    current_speed: float
    free_flow_speed: float
    observed_at: datetime
    source_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CorridorTrafficData:
    """Sequential observations for one corridor, ordered by corridor geometry."""

    corridor_id: str
    samples: tuple[TrafficSample, ...]
    fetched_at: datetime

    @property
    def representative_sample(self) -> TrafficSample:
        """Use the middle point as the corridor-level signal representative."""

        return self.samples[len(self.samples) // 2]


@dataclass(frozen=True, slots=True)
class TrafficSignal:
    """The stable signal contract consumed by FalconFX Booster.

    Values explicitly described as signals are normalized to [0, 1].
    ``speed_ratio`` is retained separately because Booster may use it in a
    later mobility-chain calculation.
    """

    corridor_id: str
    corridor_name: str
    generated_at: datetime
    current_speed: float
    free_flow_speed: float
    speed_ratio: float
    congestion: float
    previous_congestion: float
    congestion_delta: float
    queue_pressure: float
    flow_direction: str
    spillover_probability: float
    transport_hub_factor: float
    time_window_factor: float
    cache_age_seconds: float = 0.0
    stale: bool = False
    provider: str = "unknown"
    source_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CorridorDebugData:
    """Expanded calculation trace for the internal diagnostic endpoint."""

    signal: TrafficSignal
    sample_count: int
    monitor_points: tuple[tuple[float, float], ...]
    direction_vector: tuple[float, float]
    hub_references: tuple[str, ...]
    cache_age_seconds: float
    last_refresh: datetime | None


def corridor_to_dict(corridor: CorridorDefinition) -> dict[str, Any]:
    """Serialize static corridor configuration without leaking secrets."""

    return {
        "corridor_id": corridor.corridor_id,
        "name": corridor.name,
        "coordinates": corridor.coordinates,
        "direction_vector": corridor.direction_vector,
        "transport_hub_references": corridor.transport_hub_references,
        "monitoring_radius_km": corridor.monitoring_radius_km,
    }