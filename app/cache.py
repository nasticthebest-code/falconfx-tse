"""Thread-safe TTL cache with background refresh and stale protection."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Awaitable, Callable

from .models import TrafficSignal

logger = logging.getLogger("falconfx.tse.cache")


@dataclass(slots=True)
class CacheEntry:
    signal: TrafficSignal
    cached_at: datetime
    stale: bool = False
    error: str | None = None

    def age_seconds(self) -> float:
        return max(0.0, (datetime.now(timezone.utc) - self.cached_at).total_seconds())

    def is_fresh(self, ttl_seconds: int) -> bool:
        return not self.stale and self.age_seconds() < ttl_seconds


class SignalCache:
    """Async service cache whose lock keeps reads and writes consistent."""

    def __init__(
        self,
        ttl_seconds: int,
        refresh_interval_seconds: int,
        refresh_callback: Callable[[], Awaitable[None]],
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.refresh_interval_seconds = refresh_interval_seconds
        self.refresh_callback = refresh_callback
        self._entries: dict[str, CacheEntry] = {}
        self._lock = asyncio.Lock()
        self._refresh_lock = asyncio.Lock()
        self._refresh_task: asyncio.Task[None] | None = None
        self._refresh_in_progress = False
        self.last_refresh: datetime | None = None
        self.last_refresh_duration_ms: float | None = None

    async def start(self) -> None:
        self._refresh_task = asyncio.create_task(self._refresh_loop())

    async def stop(self) -> None:
        if self._refresh_task is None:
            return
        self._refresh_task.cancel()
        try:
            await self._refresh_task
        except asyncio.CancelledError:
            pass
        self._refresh_task = None

    async def get(self, corridor_id: str) -> CacheEntry | None:
        async with self._lock:
            return self._entries.get(corridor_id)

    async def snapshot(self) -> dict[str, CacheEntry]:
        async with self._lock:
            return dict(self._entries)

    async def put(self, corridor_id: str, signal: TrafficSignal) -> None:
        async with self._lock:
            self._entries[corridor_id] = CacheEntry(
                signal=replace(signal, stale=False, cache_age_seconds=0.0),
                cached_at=datetime.now(timezone.utc),
            )

    async def mark_stale(self, corridor_id: str, error: str) -> None:
        async with self._lock:
            entry = self._entries.get(corridor_id)
            if entry is not None:
                entry.stale = True
                entry.error = error
                entry.signal = replace(
                    entry.signal,
                    stale=True,
                    cache_age_seconds=entry.age_seconds(),
                )

    async def ensure_available(self) -> None:
        snapshot = await self.snapshot()
        if snapshot:
            return
        async with self._refresh_lock:
            # Re-check after acquiring the lock. Another request may have
            # populated the cache while this request was waiting.
            if await self.snapshot():
                return
            await self._run_refresh()

    async def trigger_background_refresh(self) -> None:
        if self._refresh_in_progress:
            return
        asyncio.create_task(self.refresh_now(wait=False))

    async def refresh_now(self, wait: bool = True) -> None:
        if self._refresh_lock.locked() and not wait:
            return
        async with self._refresh_lock:
            await self._run_refresh()

    async def _run_refresh(self) -> None:
        self._refresh_in_progress = True
        started = datetime.now(timezone.utc)
        try:
            await self.refresh_callback()
        finally:
            self._refresh_in_progress = False
            self.last_refresh = datetime.now(timezone.utc)
            self.last_refresh_duration_ms = (
                self.last_refresh - started
            ).total_seconds() * 1000
            logger.info(
                "cache refresh completed at=%s duration_ms=%.2f",
                self.last_refresh.isoformat(),
                self.last_refresh_duration_ms,
            )

    async def _refresh_loop(self) -> None:
        while True:
            await asyncio.sleep(self.refresh_interval_seconds)
            try:
                await self.refresh_now()
            except Exception:
                logger.exception("automatic cache refresh failed")

    @property
    def refresh_in_progress(self) -> bool:
        return self._refresh_in_progress