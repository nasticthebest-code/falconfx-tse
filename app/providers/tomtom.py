"""TomTom Traffic Flow Provider Adapter."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import requests

from app.config import Settings, CorridorConfig
from app.models import CorridorTrafficData, TrafficSample
from app.providers.base import TrafficProvider, TomTomProviderError

logger = logging.getLogger("falconfx.tse.tomtom")


class TomTomTrafficProvider(TrafficProvider):
    """Fetches real-time flow data from TomTom Traffic Flow API."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.api_key = settings.tomtom_api_key
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
        p0 = float(point[0])
        p1 = float(point[1])

        # Ensure (lat, lng) order for TomTom
        if p0 < 0 < p1:
            lat, lng = p1, p0
        else:
            lat, lng = p0, p1

        point_str = f"{lat:.6f},{lng:.6f}"

        params = {
            "point": point_str,
            "unit": "KMPH",
            "key": self.api_key.strip() if self.api_key else "",
        }

        try:
            response = requests.get(
                self.endpoint,
                params=params,
                timeout=self.settings.request_timeout_seconds,
            )
            
            if response.status_code == 200:
                data = response.json().get("flowSegmentData", {})
                return TrafficSample(
                    point_index=index,
                    current_speed=float(data.get("currentSpeed", 35.0)),
                    free_flow_speed=float(data.get("freeFlowSpeed", 50.0)),
                    confidence=float(data.get("confidence", 0.8)),
                )
            else:
                logger.warning(
                    f"TomTom HTTP {response.status_code} for point {index} ({point_str}): {response.text}"
                )
        except Exception as exc:
            logger.warning(f"TomTom network error for point {index}: {exc}")

        # Non-blocking fallback so startup and background loop never crash
        return TrafficSample(
            point_index=index,
            current_speed=35.0,
            free_flow_speed=50.0,
            confidence=0.5,
        )
