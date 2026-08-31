"""TomTom Traffic Flow Provider Adapter."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any
import requests

from app.config import Settings
from app.models import CorridorTrafficData, TrafficSample
from app.providers.base import TrafficProvider

logger = logging.getLogger("falconfx.tse.tomtom")


class TomTomProviderError(Exception):
    """Custom exception for TomTom API errors."""
    pass


class TomTomTrafficProvider(TrafficProvider):
    """Fetches real-time flow data from TomTom Traffic Flow API."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.api_key = getattr(settings, "tomtom_api_key", getattr(settings, "TOMTOM_API_KEY", ""))
        self.endpoint = (
            "https://api.tomtom.com/traffic/services/4/flowSegmentData/relative0/10/json"
        )

    async def get_corridor_data(self, corridor: Any) -> CorridorTrafficData:
        """Query monitor points concurrently."""
        points = getattr(corridor, "monitor_points", [])
        corridor_id = getattr(corridor, "corridor_id", "unknown")

        samples = await asyncio.gather(
            *(
                asyncio.to_thread(self._fetch_point, point, index)
                for index, point in enumerate(points)
            )
        )
        return CorridorTrafficData(
            corridor_id=corridor_id,
            samples=tuple(samples),
            fetched_at=datetime.now(timezone.utc),
        )

    def _fetch_point(self, point: tuple[float, float], index: int) -> TrafficSample:
        """Fetch one flow segment and normalize the provider response."""
        p0 = float(point[0])
        p1 = float(point[1])

        # Accra lat is positive (~5.5 to 5.7), lng is negative (~-0.15 to -0.3)
        if p0 < 0 < p1:
            lat, lng = p1, p0
        else:
            lat, lng = p0, p1

        point_str = f"{lat:.6f},{lng:.6f}"
        now = datetime.now(timezone.utc)

        params = {
            "point": point_str,
            "unit": "KMPH",
            "key": str(self.api_key).strip(),
        }

        try:
            response = requests.get(
                self.endpoint,
                params=params,
                timeout=getattr(self.settings, "request_timeout_seconds", 5.0),
            )
            
            if response.status_code == 200:
                data = response.json().get("flowSegmentData", {})
                return TrafficSample(
                    latitude=lat,
                    longitude=lng,
                    current_speed=float(data.get("currentSpeed", 35.0)),
                    free_flow_speed=float(data.get("freeFlowSpeed", 50.0)),
                    observed_at=now,
                )
            else:
                logger.warning(
                    f"TomTom HTTP {response.status_code} for point {index} ({point_str})"
                )
        except Exception as exc:
            logger.warning(f"TomTom error for point {index}: {exc}")

        # Safe fallback with all required fields
        return TrafficSample(
            latitude=lat,
            longitude=lng,
            current_speed=35.0,
            free_flow_speed=50.0,
            observed_at=now,
        )
