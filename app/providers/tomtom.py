"""TomTom Traffic Flow Provider Adapter using httpx for async concurrency."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
import os
from typing import Any
import httpx

from app.config import Settings
from app.models import CorridorTrafficData, TrafficSample
from app.providers.base import TrafficProvider

logger = logging.getLogger("falconfx.tse.tomtom")


class TomTomProviderError(Exception):
    """Custom exception for TomTom API errors."""
    pass


class TomTomTrafficProvider(TrafficProvider):
    """Fetches real-time flow data from TomTom Traffic Flow API asynchronously."""

    name = "tomtom"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.api_key = os.getenv("TOMTOM_API_KEY", "").strip()
        
        # Fail-fast/diagnostic check to see if secret loads in FastAPI Cloud logs
        logger.info(f"TomTom API Key loaded: present={bool(self.api_key)}, length={len(self.api_key)}")
        if not self.api_key:
            logger.error("CRITICAL: TOMTOM_API_KEY environment variable is missing or empty!")

        self.endpoint = (
            "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json"
        )

    async def get_corridor_data(self, corridor: Any) -> CorridorTrafficData:
        """Query all static monitor points concurrently using httpx AsyncClient."""
        points = getattr(corridor, "monitor_points", [])
        corridor_id = getattr(corridor, "corridor_id", "unknown")

        timeout = getattr(self.settings, "request_timeout_seconds", 5.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            tasks = [
                self._fetch_point_async(client, point, index)
                for index, point in enumerate(points)
            ]
            samples = await asyncio.gather(*tasks)

        return CorridorTrafficData(
            corridor_id=corridor_id,
            samples=tuple(samples),
            fetched_at=datetime.now(timezone.utc),
        )

    async def _fetch_point_async(
        self, client: httpx.AsyncClient, point: tuple[float, float], index: int
    ) -> TrafficSample:
        """Fetch one flow segment asynchronously and capture TomTom's rejection reason."""
        p0 = float(point[0])
        p1 = float(point[1])

        if p0 < 0 < p1:
            lat, lng = p1, p0
        else:
            lat, lng = p0, p1

        point_str = f"{lat:.6f},{lng:.6f}"
        now = datetime.now(timezone.utc)

        params = {
            "point": point_str,
            "unit": "KMPH",
            "key": self.api_key,
        }

        try:
            response = await client.get(self.endpoint, params=params)
            
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
                # Log full response text so TomTom explains why 401 happened
                logger.warning(
                    f"TomTom HTTP {response.status_code} for point {index} ({point_str}): {response.text}"
                )
        except Exception as exc:
            logger.warning(f"TomTom network error for point {index}: {exc}")

        return TrafficSample(
            latitude=lat,
            longitude=lng,
            current_speed=35.0,
            free_flow_speed=50.0,
            observed_at=now,
        )
