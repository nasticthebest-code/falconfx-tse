"""Provider interface used to keep TSE independent from traffic vendors."""

from __future__ import annotations

from typing import Protocol

from ..config import CorridorDefinition
from ..models import CorridorTrafficData


class TrafficProvider(Protocol):
    """Common interface implemented by TomTom and simulation providers."""

    name: str

    async def get_corridor_data(self, corridor: CorridorDefinition) -> CorridorTrafficData:
        """Fetch sequential traffic observations for one configured corridor."""
