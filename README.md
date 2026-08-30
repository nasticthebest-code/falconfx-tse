# FalconFX Traffic Signal Engine (TSE v1)

FalconFX Traffic Signal Engine is a Python 3.11 FastAPI microservice that
generates deterministic traffic intelligence signals for FalconFX Booster.
It is **not** a navigation app, recommendation engine, or Opportunity Score
engine. The Booster remains the only decision engine.

## What was built

- Provider-neutral `TrafficProvider` interface.
- TomTom Traffic Flow adapter in `app/providers/tomtom.py`.
- Deterministic simulation provider with:
  - `morning_rush`
  - `evening_rush`
  - `accident`
  - `free_flow`
  - `gridlock`
- Configurable Accra corridors:
  `N1`, `Spintex`, `Legon`, `Madina`, `Airport`, `Circle`, `Lapaz`, and
  `Achimota`.
- Starter transport hub layer:
  `Lapaz`, `Circle`, `Madina`, `Kaneshie`, `Achimota`, `Legon`, `Spintex`,
  `Teshie`, `Kasoa`, and `Ashaiman`.
- Thread-safe asynchronous TTL cache with:
  - 120-second default TTL
  - background automatic refresh
  - stale protection
  - last-known-good results during provider failures
- Momentum-aware queue pressure using FalconFX's approved formula.
- Sequential-sample flow direction inference returning only:
  `inbound`, `outbound`, `stable`, or `mixed`.
- Bounded spillover probability using queue pressure, transport hub context,
  and commuter timing.
- Internal diagnostic endpoint exposing the full signal calculation chain.
- Unit and API tests for formulas, direction, caching, simulation, and
  diagnostics.

## Signal contract

Every signal uses the following v1 calculations:

```python
speed_ratio = clamp(current_speed / free_flow_speed, 0.0, 1.0)
congestion = 1.0 - speed_ratio

congestion_delta = max(0.0, current_congestion - previous_congestion)

queue_pressure = clamp(
    (0.60 * congestion)
    + (0.25 * congestion_delta)
    + (0.15 * max(0.0, 0.5 - speed_ratio) * 2),
    0.0,
    1.0,
)

spillover_probability = clamp(
    queue_pressure * transport_hub_factor * time_window_factor,
    0.0,
    1.0,
)
```

The API retains both `speed_ratio` and `congestion`. All normalized signals
are bounded between `0.0` and `1.0`.

## Run locally

The project is written for Python 3.11. From this directory:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

For local development without network calls:

```bash
export TSE_PROVIDER=simulation
export TSE_SIMULATION_SCENARIO=free_flow
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

With TomTom:

```bash
export TSE_PROVIDER=tomtom
export TOMTOM_API_KEY="stored-outside-source-control"
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Do not commit a real TomTom key. In Replit, use the Secret named
`TOMTOM_API_KEY`; the service reads it server-side.

## Replit deployment

1. Upload or copy this directory into a Python 3.11 Replit project.
2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Add `TOMTOM_API_KEY` in Replit Secrets.
4. Start the service:

   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
   ```

5. Set `TSE_PROVIDER=tomtom`.
6. For production diagnostics, set:
   `TSE_DEBUG_ENDPOINT_ENABLED=true` and configure
   `TSE_INTERNAL_DEBUG_TOKEN` as a separate secret. Set the token in the
   `X-TSE-Internal-Token` header when calling the diagnostic route.

The included service is standalone and does not change FalconFX Booster
architecture. Booster calls TSE over HTTP and remains responsible for any
decision, ranking, or recommendation.

## API

### Health

```bash
curl http://localhost:8000/health
```

Response shape:

```json
{
  "status": "ok",
  "version": "1.0.0",
  "cached_corridors": ["N1", "Spintex"],
  "last_refresh": "2026-08-29T12:00:00+00:00",
  "refresh_in_progress": false,
  "provider": "tomtom"
}
```

### All corridor signals

```bash
curl http://localhost:8000/api/v1/signals
```

### One corridor

```bash
curl http://localhost:8000/api/v1/signals/N1
```

Example:

```json
{
  "corridor_id": "N1",
  "corridor_name": "N1",
  "generated_at": "2026-08-29T12:00:00+00:00",
  "current_speed": 42.0,
  "free_flow_speed": 60.0,
  "speed_ratio": 0.7,
  "congestion": 0.3,
  "previous_congestion": 0.2,
  "congestion_delta": 0.1,
  "queue_pressure": 0.285,
  "flow_direction": "inbound",
  "spillover_probability": 0.35625,
  "transport_hub_factor": 1.3,
  "time_window_factor": 1.15,
  "cache_age_seconds": 4.2,
  "stale": false,
  "provider": "tomtom"
}
```

### Internal diagnostic signal

This route is deliberately under `/internal` and is not intended for riders:

```bash
curl \
  -H "X-TSE-Internal-Token: $TSE_INTERNAL_DEBUG_TOKEN" \
  http://localhost:8000/internal/v1/debug/signals/N1
```

It returns the full signal plus sample count, monitor points, direction vector,
hub references, cache age, and last refresh. If no internal token is configured,
the route is available only while `TSE_DEBUG_ENDPOINT_ENABLED=true`; configure
a token in production.

### Simulation controls

```bash
curl http://localhost:8000/api/v1/simulation/scenarios

curl -X POST http://localhost:8000/api/v1/simulation/scenario \
  -H "Content-Type: application/json" \
  -d '{"scenario":"morning_rush"}'
```

Simulation controls return `409` when the service is configured for TomTom.

## Project structure

```text
falconfx-tse/
├── app/
│   ├── cache.py                  # TTL cache and background refresh
│   ├── config.py                 # tuning and Accra corridor/hub definitions
│   ├── main.py                   # FastAPI app and routes
│   ├── models.py                 # TrafficSignal and provider data models
│   ├── core/
│   │   ├── calculations.py       # approved signal formulas
│   │   └── engine.py             # provider/cache/signal orchestration
│   └── providers/
│       ├── base.py               # provider interface
│       ├── simulation.py          # synthetic scenarios
│       └── tomtom.py              # TomTom adapter
├── tests/
├── .env.example
├── pyproject.toml
├── requirements.txt
└── run.py
```

## Verification

Run:

```bash
pytest -q
```

The test suite confirms the approved congestion and queue formulas, positive
momentum behavior, sequential flow direction, bounded spillover probability,
simulation mode, cache reuse, and diagnostic output.

## Explicit non-goals

TSE v1 does not calculate Opportunity Score, recommend destinations, combine
weather, read rider telemetry, or make decisions. Those remain inside FalconFX
Booster. The provider interface and modular engine leave room for future queue
wave detection, event signals, weather inputs, historical learning, telemetry
feedback, and multi-source traffic fusion without changing Booster's decision
architecture.