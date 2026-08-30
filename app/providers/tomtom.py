"""TomTom Traffic Flow provider adapter."""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Any

import requests

from ..config import CorridorDefinition, Settings
from ..models import CorridorTrafficData, TrafficSample


class TomTomProviderError(RuntimeError):
    """Raised when TomTom cannot provide a valid corridor observation."""


class TomTomTrafficProvider:
    """Fetch TomTom flow data without exposing TomTom details to the engine."""

    name = "tomtom"
    endpoint = "https://api.tomtom.com/traffic/services/4/flowSegmentData/absolute/10/json"

    def __init__(self, settings: Settings, api_key: str | None = None) -> None:
        self.settings = settings
        self.api_key = api_key or os.getenv("TOMTOM_API_KEY")
        if not self.api_key:
            raise TomTomProviderError(
                "TOMTOM_API_KEY is not configured; use simulation mode for local development"
            )

    async def get_corridor_data(self, corridor: CorridorDefinition) -> CorridorTrafficData:
        """Query each static monitor point concurrently to keep refreshes fast."""

        started_at = datetime.now(timezone.utc)
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

        response = requests.get(
            self.endpoint,
            params={
                "point": f"{point[0]},{point[1]}",
                "key": self.api_key,
            },
            timeout=self.settings.request_timeout_seconds,
        )
        if response.status_code >= 400:
            raise TomTomProviderError(
                f"TomTom returned HTTP {response.status_code} for monitor point {index}"
            )
        try:
            payload: dict[str, Any] = response.json()
            flow = payload["flowSegmentData"]
            current_speed = float(flow["currentSpeed"])
            free_flow_speed = float(flow["freeFlowSpeed"])
        except (ValueError, KeyError, TypeError) as error:
            raise TomTomProviderError(
                f"TomTom returned an invalid flow response for monitor point {index}"
            ) from error
        if current_speed < 0 or free_flow_speed <= 0:
            raise TomTomProviderError(f"TomTom returned invalid speeds for monitor point {index}")
        return TrafficSample(
            latitude=point[0],
            longitude=point[1],
            current_speed=current_speed,
            free_flow_speed=free_flow_speed,
            observed_at=datetime.now(timezone.utc),
            source_metadata={
                "provider": self.name,
                "monitor_point_index": index,
                "confidence": flow.get("confidence"),
                "road_closure": flow.get("roadClosure"),
            },
        )