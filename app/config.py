"""Configuration and static geographic definitions for TSE v1.

Tuning values live here so signal behavior can be adjusted without changing
the algorithms. Corridor coordinates are intentionally configuration, not
logic: a future provider or a learned geometry source can replace them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True, slots=True)
class HubDefinition:
    name: str
    latitude: float
    longitude: float
    monitoring_radius_km: float
    classification: str = "ordinary"


@dataclass(frozen=True, slots=True)
class CorridorDefinition:
    corridor_id: str
    name: str
    coordinates: tuple[tuple[float, float], ...]
    direction_vector: tuple[float, float]
    transport_hub_references: tuple[str, ...]
    monitoring_radius_km: float

    @property
    def monitor_points(self) -> tuple[tuple[float, float], ...]:
        """Return the fixed points used by providers for sequential sampling."""

        return self.coordinates


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime-adjustable TSE tuning and service settings."""

    version: str = "1.0.0"
    cache_ttl_seconds: int = 120
    refresh_interval_seconds: int = 120
    request_timeout_seconds: float = 5.0
    direction_change_threshold: float = 0.05
    hub_factor_ordinary: float = 1.00
    hub_factor_near: float = 1.15
    hub_factor_major_terminal: float = 1.30
    time_factor_normal: float = 1.00
    time_factor_shoulder_peak: float = 1.15
    time_factor_rush_hour: float = 1.25
    debug_endpoint_enabled: bool = True
    internal_debug_token: str | None = None
    monitored_corridors: tuple[str, ...] = (
        "N1",
        "Spintex",
        "Legon",
        "Madina",
        "Airport",
        "Circle",
        "Lapaz",
        "Achimota",
    )
    provider_name: str = "tomtom"
    simulation_scenario: str = "free_flow"

    @classmethod
    def from_env(cls) -> Settings:
        """Load safe runtime overrides from environment variables."""
        defaults = cls()

        def as_int(name: str, default: int) -> int:
            try:
                return int(os.getenv(name, default))
            except (TypeError, ValueError):
                return default

        def as_float(name: str, default: float) -> float:
            try:
                return float(os.getenv(name, default))
            except (TypeError, ValueError):
                return default

        def as_bool(name: str, default: bool) -> bool:
            value = os.getenv(name)
            if value is None:
                return default
            return value.lower() in {"1", "true", "yes", "on"}

        configured_corridors = os.getenv("TSE_MONITORED_CORRIDORS")
        corridors = (
            tuple(item.strip() for item in configured_corridors.split(",") if item.strip())
            if configured_corridors
            else defaults.monitored_corridors
        )
        return cls(
            cache_ttl_seconds=max(1, as_int("TSE_CACHE_TTL_SECONDS", defaults.cache_ttl_seconds)),
            refresh_interval_seconds=max(
                1, as_int("TSE_REFRESH_INTERVAL_SECONDS", defaults.refresh_interval_seconds)
            ),
            request_timeout_seconds=max(
                0.5, as_float("TSE_REQUEST_TIMEOUT_SECONDS", defaults.request_timeout_seconds)
            ),
            direction_change_threshold=max(
                0.0,
                as_float(
                    "TSE_DIRECTION_CHANGE_THRESHOLD",
                    defaults.direction_change_threshold,
                ),
            ),
            hub_factor_ordinary=as_float("TSE_HUB_FACTOR_ORDINARY", defaults.hub_factor_ordinary),
            hub_factor_near=as_float("TSE_HUB_FACTOR_NEAR", defaults.hub_factor_near),
            hub_factor_major_terminal=as_float(
                "TSE_HUB_FACTOR_MAJOR_TERMINAL",
                defaults.hub_factor_major_terminal,
            ),
            time_factor_normal=as_float("TSE_TIME_FACTOR_NORMAL", defaults.time_factor_normal),
            time_factor_shoulder_peak=as_float(
                "TSE_TIME_FACTOR_SHOULDER_PEAK",
                defaults.time_factor_shoulder_peak,
            ),
            time_factor_rush_hour=as_float(
                "TSE_TIME_FACTOR_RUSH_HOUR",
                defaults.time_factor_rush_hour,
            ),
            debug_endpoint_enabled=as_bool(
                "TSE_DEBUG_ENDPOINT_ENABLED",
                defaults.debug_endpoint_enabled,
            ),
            internal_debug_token=os.getenv("TSE_INTERNAL_DEBUG_TOKEN"),
            monitored_corridors=corridors,
            provider_name=os.getenv("TSE_PROVIDER", defaults.provider_name).lower(),
            simulation_scenario=os.getenv(
                "TSE_SIMULATION_SCENARIO",
                defaults.simulation_scenario,
            ).lower(),
        )


HUBS: dict[str, HubDefinition] = {
    "Lapaz": HubDefinition("Lapaz", 5.6230, -0.2490, 2.0, "near"),
    "Circle": HubDefinition("Circle", 5.5600, -0.2050, 2.5, "major_terminal"),
    "Madina": HubDefinition("Madina", 5.6830, -0.1660, 2.0, "near"),
    "Kaneshie": HubDefinition("Kaneshie", 5.5680, -0.2350, 2.5, "major_terminal"),
    "Achimota": HubDefinition("Achimota", 5.6410, -0.2340, 2.0, "near"),
    "Legon": HubDefinition("Legon", 5.6500, -0.1870, 1.8, "near"),
    "Spintex": HubDefinition("Spintex", 5.5950, -0.1440, 1.8, "near"),
    "Teshie": HubDefinition("Teshie", 5.5740, -0.1550, 2.0, "near"),
    "Kasoa": HubDefinition("Kasoa", 5.5330, -0.4160, 3.0, "major_terminal"),
    "Ashaiman": HubDefinition("Ashaiman", 5.6970, -0.0300, 2.5, "major_terminal"),
}


CORRIDORS: dict[str, CorridorDefinition] = {
    "N1": CorridorDefinition(
        "N1",
        "N1",
        ((5.5950, -0.2350), (5.6130, -0.2280), (5.6250, -0.2100)),
        (0.87, 0.49),
        ("Kasoa", "Kaneshie", "Circle"),
        3.0,
    ),
    "Spintex": CorridorDefinition(
        "Spintex",
        "Spintex",
        ((5.6020, -0.1500), (5.6150, -0.1250), (5.6230, -0.1050)),
        (-0.68, 0.73),
        ("Spintex", "Teshie"),
        2.5,
    ),
    "Legon": CorridorDefinition(
        "Legon",
        "Legon",
        ((5.6320, -0.1780), (5.6500, -0.1750), (5.6700, -0.1700)),
        (0.93, 0.06),
        ("Legon", "Achimota"),
        2.5,
    ),
    "Madina": CorridorDefinition(
        "Madina",
        "Madina",
        ((5.6650, -0.1680), (5.6800, -0.1650), (5.6950, -0.1600)),
        (0.52, 0.85),
        ("Madina", "Legon"),
        2.5,
    ),
    "Airport": CorridorDefinition(
        "Airport",
        "Airport",
        ((5.5800, -0.1750), (5.5900, -0.1800), (5.5700, -0.1700)),
        (-0.43, -0.90),
        ("Legon", "Circle"),
        2.5,
    ),
    "Circle": CorridorDefinition(
        "Circle",
        "Circle",
        ((5.5580, -0.2150), (5.5520, -0.2200), (5.5650, -0.2100)),
        (0.91, 0.41),
        ("Circle", "Kaneshie"),
        2.5,
    ),
    "Lapaz": CorridorDefinition(
        "Lapaz",
        "Lapaz",
        ((5.5920, -0.2400), (5.5980, -0.2480), (5.5880, -0.2300)),
        (0.83, 0.55),
        ("Lapaz", "Achimota"),
        2.5,
    ),
    "Achimota": CorridorDefinition(
        "Achimota",
        "Achimota",
        ((5.6050, -0.2200), (5.6127, -0.2343), (5.6280, -0.2450)),
        (0.94, 0.34),
        ("Achimota", "Legon"),
        2.5,
    ),
}


def monitored_corridor_definitions(settings: Settings) -> list[CorridorDefinition]:
    """Return configured corridors while preserving the requested order."""

    return [CORRIDORS[corridor_id] for corridor_id in settings.monitored_corridors if corridor_id in CORRIDORS]


def names(values: Iterable[HubDefinition]) -> tuple[str, ...]:
    """Small helper used by callers that need stable hub names."""

    return tuple(value.name for value in values)
