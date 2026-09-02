"""Mapbox Traffic Provider Adapter — replaces TomTomTrafficProvider behind the
same TrafficProvider interface. Nothing else in TSE (calculations.py, engine.py,
cache.py, main.py) or in Booster's traffic_engine.py client needs to change —
they only know about TrafficSample(current_speed, free_flow_speed), not which
vendor produced it.

WHY A ROUTE INSTEAD OF POINT-SAMPLING:
TomTom's flowSegmentData endpoint snaps a single lat/lng to the "nearest
existing segment" and fails hard if nothing is close enough — this is what
caused the persistent "Point too far from nearest existing segment" errors.
Mapbox's Directions API instead computes a real route THROUGH the corridor's
existing monitor_points (used as waypoints, not float-parsed to a single
best-guess segment), and returns live congestion/speed for every segment
along that actual road path. This also directly improves spatial coverage —
a real problem raised earlier: 3 isolated points understate a corridor that
can run 10km+; a routed path samples the whole stretch between them.

FREE-FLOW SPEED, A REAL DESIGN CHOICE (documented, not hidden):
Mapbox's traffic-aware route gives real current speed per segment, but has
no dedicated "free flow" field the way TomTom's freeFlowSpeed does. This
provider uses each segment's `maxspeed` annotation (the posted/legal speed
limit) as the free-flow reference — a standard, defensible proxy (you'd
expect to travel close to the speed limit with zero congestion), but worth
knowing it's an interpretive choice, not a measured "free flow" value.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config import Settings
from app.models import CorridorTrafficData, TrafficSample
from app.providers.base import TrafficProvider

logger = logging.getLogger("falconfx.tse.mapbox")

# Fallback free-flow speed (km/h) when a segment has no maxspeed annotation
# (common on smaller/untagged roads). Matches TomTom provider's old fallback
# convention of defaulting rather than failing the whole corridor.
_DEFAULT_FREE_FLOW_KMPH = 40.0
_MPS_TO_KMPH = 3.6


class MapboxProviderError(Exception):
    """Custom exception for Mapbox API errors."""
    pass


class MapboxTrafficProvider(TrafficProvider):
    """Fetches real-time traffic-aware routing data from Mapbox Directions API."""

    name = "mapbox"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.api_key = os.getenv("MAPBOX_API_KEY", "").strip()

        logger.info(f"Mapbox API Key loaded: present={bool(self.api_key)}, length={len(self.api_key)}")
        if not self.api_key:
            logger.error("CRITICAL: MAPBOX_API_KEY environment variable is missing or empty!")

        self.endpoint_base = "https://api.mapbox.com/directions/v5/mapbox/driving-traffic"

    async def get_corridor_data(self, corridor: Any) -> CorridorTrafficData:
        """Request one traffic-aware route through the corridor's existing
        monitor_points (used as waypoints), and turn each route leg's
        annotated segments into TrafficSamples — same output shape as the
        old point-sampling TomTom provider, so nothing downstream changes."""
        points = getattr(corridor, "monitor_points", [])
        corridor_id = getattr(corridor, "corridor_id", "unknown")

        if len(points) < 2:
            logger.warning(f"Corridor {corridor_id} has fewer than 2 points — cannot route.")
            return CorridorTrafficData(
                corridor_id=corridor_id, samples=(), fetched_at=datetime.now(timezone.utc)
            )

        # Mapbox coordinate order is lng,lat (opposite of how corridors are
        # defined in config.py as lat,lng) — swap here, once, at the boundary.
        coord_str = ";".join(f"{lng:.6f},{lat:.6f}" for lat, lng in points)
        url = f"{self.endpoint_base}/{coord_str}"

        params = {
            "access_token": self.api_key,
            "annotations": "congestion,speed,maxspeed,duration,distance",
            "overview": "full",
            "geometries": "geojson",
        }

        timeout = getattr(self.settings, "request_timeout_seconds", 5.0)
        now = datetime.now(timezone.utc)

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.get(url, params=params)

            if response.status_code != 200:
                logger.warning(
                    f"Mapbox HTTP {response.status_code} for corridor {corridor_id}: {response.text}"
                )
                return CorridorTrafficData(
                    corridor_id=corridor_id, samples=(), fetched_at=now
                )

            data = response.json()
            routes = data.get("routes", [])
            if not routes:
                logger.warning(f"Mapbox returned no route for corridor {corridor_id}: {data}")
                return CorridorTrafficData(
                    corridor_id=corridor_id, samples=(), fetched_at=now
                )

            samples = self._route_to_samples(routes[0], points, now)
            return CorridorTrafficData(
                corridor_id=corridor_id, samples=tuple(samples), fetched_at=now
            )

        except Exception as exc:
            logger.warning(f"Mapbox network error for corridor {corridor_id}: {exc}")
            return CorridorTrafficData(
                corridor_id=corridor_id, samples=(), fetched_at=now
            )

    def _route_to_samples(
        self, route: dict, waypoints: tuple, now: datetime
    ) -> list[TrafficSample]:
        """Turn a route's leg annotations into TrafficSamples. Falls back to
        one sample per original waypoint (using route-level aggregate speed)
        if per-segment annotations are missing, so a corridor never silently
        produces zero samples just because annotation data was sparse."""
        samples: list[TrafficSample] = []

        legs = route.get("legs", [])
        geometry_coords = route.get("geometry", {}).get("coordinates", [])

        for leg in legs:
            annotation = leg.get("annotation", {})
            speeds = annotation.get("speed", [])         # m/s, current
            maxspeeds = annotation.get("maxspeed", [])    # dict per segment
            distances = annotation.get("distance", [])

            if not speeds or not geometry_coords:
                continue

            for i, speed_mps in enumerate(speeds):
                if speed_mps is None:
                    continue
                current_kmph = float(speed_mps) * _MPS_TO_KMPH

                free_flow_kmph = _DEFAULT_FREE_FLOW_KMPH
                if i < len(maxspeeds) and isinstance(maxspeeds[i], dict):
                    ms = maxspeeds[i].get("speed")
                    if ms:
                        free_flow_kmph = float(ms)
                        # Mapbox returns maxspeed in mph for some regions —
                        # unit field disambiguates; convert if needed.
                        if maxspeeds[i].get("unit") == "mph":
                            free_flow_kmph *= 1.60934

                # Never let free-flow read lower than current — that would
                # produce a nonsensical >1.0 speed_ratio downstream.
                free_flow_kmph = max(free_flow_kmph, current_kmph)

                coord_idx = min(i, len(geometry_coords) - 1)
                lng, lat = geometry_coords[coord_idx][0], geometry_coords[coord_idx][1]

                samples.append(TrafficSample(
                    latitude=lat,
                    longitude=lng,
                    current_speed=round(current_kmph, 2),
                    free_flow_speed=round(free_flow_kmph, 2),
                    observed_at=now,
                    source_metadata={"segment_distance_m": distances[i] if i < len(distances) else None},
                ))

        if not samples:
            # Annotation data came back empty/malformed — fall back to one
            # sample per original waypoint using the route's overall pace,
            # rather than returning zero samples for the whole corridor.
            total_distance_m = route.get("distance", 0.0)
            total_duration_s = route.get("duration", 1.0)
            avg_kmph = (total_distance_m / max(total_duration_s, 1.0)) * _MPS_TO_KMPH
            for lat, lng in waypoints:
                samples.append(TrafficSample(
                    latitude=lat,
                    longitude=lng,
                    current_speed=round(avg_kmph, 2),
                    free_flow_speed=round(max(avg_kmph, _DEFAULT_FREE_FLOW_KMPH), 2),
                    observed_at=now,
                    source_metadata={"fallback": "route_average_no_annotations"},
                ))

        return samples
