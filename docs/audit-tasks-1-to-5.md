# Audit Tasks 1-5

This file records the first five requested audit tasks and their implementation
surface.

## 1. Add a TTL to the Traffic Cache

Implemented in `backend/app/services/traffic_service.py`.

- `_leg_traffic_cache` now stores `(stored_at_monotonic, result)` tuples.
- `LEG_TRAFFIC_CACHE_TTL_S` is set to 20 minutes.
- `get_leg_traffic` returns cached entries only while they are fresh; expired
  entries are re-queried and overwritten.

Reason: traffic readings should be reused inside a long optimization request,
but should not remain valid for the full backend process lifetime.

## 2. Mark Demo Responses and Improve Demo Matching

Implemented in:

- `backend/app/api/routes.py`
- `backend/app/models/schemas.py`
- `backend/app/fixtures/demo_routes_manifest.json`
- `frontend/src/App.jsx`
- `backend/app/scripts/capture_demo_fixture.py`

Behavior:

- Demo fixtures are returned with `precomputed: true`.
- Responses include `precomputed_battery_percentage`.
- Demo matching uses origin, destination, `vehicle_profile`, and `load_state`.
- Requests with exact `payload_kg` never match fixtures.
- Battery percentage is intentionally not a match key, because the existing
  fixtures were captured at fixed battery percentages and are now labeled as
  such.
- The frontend displays a saved-plan notice for precomputed responses.

## 3. Remove Stale Dindigul/Kumbakonam Demo Fixtures and Fix README

Implemented in:

- `backend/app/fixtures/demo_routes_manifest.json`
- `backend/app/fixtures/demo_route_chennai_dindigul.json` deleted
- `backend/app/fixtures/demo_route_chennai_kumbakonam.json` deleted
- `README.md`
- `backend/app/scripts/capture_demo_fixture.py`

Behavior:

- Dindigul and Kumbakonam were removed from the manifest because their saved
  fixtures represented stale `unreachable_gap` results.
- The capture script refuses to save infeasible plans as demo fixtures.
- README now describes the current Ola geocoding dependency, demo scope,
  manifest behavior, precomputed fields, and removed stale routes.

## 4. Return 503 for Geocoding Provider Failures

Implemented in:

- `backend/app/services/routing_service.py`
- `backend/app/api/routes.py`

Behavior:

- `GeocodingNotFoundError` means the provider answered but found no match; API
  returns HTTP 422.
- `GeocodingProviderError` means missing key, network failure, invalid JSON,
  unexpected response shape, or provider outage; API returns HTTP 503.
- Both subclasses still inherit from `GeocodingError`.

## 5. Enforce Connector Compatibility in the Graph

Implemented in:

- `backend/app/services/graph_planner.py`
- `backend/app/services/route_optimizer.py`
- `backend/app/utils/connector_normalizer.py`
- `tests/test_graph_planner.py`
- `tests/test_audit_fixes_1_to_5.py`

Behavior:

- `filter_candidate_stations` drops stations with no connector bucket
  compatible with the requested vehicle profile.
- Compatibility is checked before `MAX_CANDIDATE_STATIONS`, so the candidate
  cap is spent only on usable stations.
- Unknown connector types are excluded.

## Verification

Offline verification run:

```bash
uv run python tests/test_audit_fixes_1_to_5.py
```

Result observed: all checks passed.

