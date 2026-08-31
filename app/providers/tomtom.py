"""TomTom Traffic Flow Provider Adapter."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import requests

from app.config import Settings
from app.models import CorridorConfig, CorridorTrafficData, TrafficSample
from app.providers.base import TrafficProvider, TomTomProviderError


class TomTomTrafficProvider(TrafficProvider):
    """Fetches real-time flow data from TomTom Traffic Flow API."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.api_key = settings.tomtom_api_key
        # Ensure full endpoint with relative0 style and zoom level 10
        self.endpoint = (
            "https://api.tomtom.com/traffic/services/4/flowSegmentData/relative0/10/json"
        )

    async def get_corridor_data(self, corridor: CorridorConfig) -> CorridorTrafficData:
        """Query monitor points concurrently."""
        samples = await asyncio.gather(
            *(
                asyncio.to_thread(self._fetch_point, point, index)
                for index, point in enumerate(corridor.monitor_points)
            )
        )
        return CorridorTrafficData(
            corridor_id=corridor.corridor_id,
            samples=tuple(samples),
            fetched_at=datetime.now(timezone.utc),
        )

    def _fetch_point(self, point: tuple[float, float], index: int) -> TrafficSample:
        """Fetch one flow segment and normalize the provider response."""
        # Detect if point is (lat, lng) or (lng, lat):
        # Accra latitude is ~5.5 to 5.7; longitude is negative ~ -0.15 to -0.25
        p0, p1 = point[0], point[1]
        if p0 < 0 and p1 > 0:
            lat, lng = p1, p0  # Swap if stored as (lng, lat)
        else:
            lat, lng = p0, p1

        params = {
            "point": f"{lat:.6f},{lng:.6f}",
            "unit": "KMPH",
            "key": self.api_key,
        }

        try:
            response = requests.get(
                self.endpoint,
                params=params,
                timeout=self.settings.request_timeout_seconds,
            )
        except Exception as exc:
            raise TomTomProviderError(f"Network error on monitor point {index}: {exc}") from exc

        if response.status_code != 200:
            raise TomTomProviderError(
                f"TomTom returned HTTP {response.status_code} for monitor point {index}: {response.text}"
            )

        try:
            flow_data = response.json().get("flowSegmentData", {})
            current_speed = float(flow_data.get("currentSpeed", 30.0))
            free_flow_speed = float(flow_data.get("freeFlowSpeed", 45.0))
            confidence = float(flow_data.get("confidence", 0.8))

            return TrafficSample(
                point_index=index,
                current_speed=current_speed,
                free_flow_speed=free_flow_speed,
                confidence=confidence,
            )
        except Exception as exc:
            raise TomTomProviderError(f"Failed to parse TomTom response: {exc}") from exc
