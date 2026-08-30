"""Deterministic FalconFX TSE v1 signal calculations."""

from __future__ import annotations

from datetime import datetime
from math import asin, cos, radians, sin, sqrt
from zoneinfo import ZoneInfo

from ..config import CorridorDefinition, HubDefinition, Settings


def clamp(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
    """Keep a numeric signal inside its contract range."""

    return max(lower, min(upper, float(value)))


def calculate_speed_ratio(current_speed: float, free_flow_speed: float) -> float:
    """Normalize current movement against free-flow movement."""

    if free_flow_speed <= 0:
        raise ValueError("free_flow_speed must be greater than zero")
    return clamp(current_speed / free_flow_speed)


def calculate_congestion(speed_ratio: float) -> float:
    """Convert speed ratio into congestion intensity."""

    return clamp(1.0 - clamp(speed_ratio))


def calculate_queue_pressure(
    congestion: float,
    previous_congestion: float,
    speed_ratio: float,
) -> tuple[float, float]:
    """Apply FalconFX's momentum-aware queue pressure formula.

    The positive delta captures queue formation. A corridor that remains at a
    constant congestion level therefore behaves differently from one whose
    congestion is increasing quickly.
    """

    current = clamp(congestion)
    previous = clamp(previous_congestion)
    ratio = clamp(speed_ratio)
    congestion_delta = max(0.0, current - previous)
    slow_movement_component = max(0.0, 0.5 - ratio) * 2.0
    pressure = (
        (0.60 * current)
        + (0.25 * congestion_delta)
        + (0.15 * slow_movement_component)
    )
    return clamp(pressure), congestion_delta


def haversine_km(first: tuple[float, float], second: tuple[float, float]) -> float:
    """Calculate geographic distance without a geometry dependency."""

    earth_radius_km = 6371.0088
    lat1, lon1 = map(radians, first)
    lat2, lon2 = map(radians, second)
    delta_lat = lat2 - lat1
    delta_lon = lon2 - lon1
    value = sin(delta_lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(delta_lon / 2) ** 2
    return 2 * earth_radius_km * asin(sqrt(value))


def transport_hub_factor(
    corridor: CorridorDefinition,
    hubs: dict[str, HubDefinition],
    settings: Settings,
) -> float:
    """Select the strongest configured hub context for this corridor."""

    classifications = [
        hubs[hub_name].classification
        for hub_name in corridor.transport_hub_references
        if hub_name in hubs
    ]
    if "major_terminal" in classifications:
        return settings.hub_factor_major_terminal
    if "near" in classifications:
        return settings.hub_factor_near
    return settings.hub_factor_ordinary


def time_window_factor(observed_at: datetime, settings: Settings) -> float:
    """Return commuter timing context in Accra local time.

    Timing affects only spillover probability. It never determines flow
    direction, so the direction engine remains data-driven.
    """

    local_time = observed_at.astimezone(ZoneInfo("Africa/Accra"))
    hour = local_time.hour + (local_time.minute / 60.0)
    if 7.0 <= hour < 10.0 or 16.0 <= hour < 19.0:
        return settings.time_factor_rush_hour
    if 6.0 <= hour < 7.0 or 10.0 <= hour < 11.0 or 15.0 <= hour < 16.0 or 19.0 <= hour < 21.0:
        return settings.time_factor_shoulder_peak
    return settings.time_factor_normal


class FlowDirectionEngine:
    """Infer propagation direction from sequential, ordered corridor samples.

    TomTom provides scalar speed observations at points, not a destination
    recommendation. TSE uses the configured corridor order and compares
    congestion deltas at the two ends:

    - deterioration toward the configured corridor terminus => inbound
    - deterioration toward the configured corridor origin => outbound
    - low or evenly distributed change => stable
    - opposing strong changes => mixed

    This is intentionally a temporal/spatial signal, not a morning/evening
    assumption. The direction vector is configuration metadata that gives the
    ordered points a stable reference frame.

    Origin/terminus windows use ceiling division, so for odd sample counts
    the middle point is included in BOTH windows. A 3-point corridor's
    middle sample genuinely sits between "approaching origin" and
    "approaching terminus" -- excluding it from both (the previous floor-
    division behavior) discarded a real signal for no reason.
    """

    def __init__(self, change_threshold: float = 0.05) -> None:
        self.change_threshold = change_threshold

    def infer(
        self,
        current_congestion: tuple[float, ...],
        previous_congestion: tuple[float, ...] | None,
    ) -> str:
        if not previous_congestion or len(current_congestion) != len(previous_congestion):
            return "stable"

        deltas = tuple(current - previous for current, previous in zip(current_congestion, previous_congestion))
        meaningful = [delta for delta in deltas if abs(delta) >= self.change_threshold]
        if not meaningful:
            return "stable"

        origin_delta = sum(deltas[: max(1, (len(deltas) + 1) // 2)]) / max(1, (len(deltas) + 1) // 2)
        terminus_delta = sum(deltas[-max(1, (len(deltas) + 1) // 2) :]) / max(1, (len(deltas) + 1) // 2)
        origin_rising = origin_delta >= self.change_threshold
        terminus_rising = terminus_delta >= self.change_threshold
        origin_falling = origin_delta <= -self.change_threshold
        terminus_falling = terminus_delta <= -self.change_threshold

        if (origin_rising and terminus_falling) or (origin_falling and terminus_rising):
            return "mixed"
        if terminus_delta - origin_delta >= self.change_threshold:
            return "inbound"
        if origin_delta - terminus_delta >= self.change_threshold:
            return "outbound"
        return "stable"