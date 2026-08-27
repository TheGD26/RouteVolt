"""
Captures a new pre-cached demo-mode route: POSTs a real /route/optimize
request to a running local server, saves the raw response as a fixture
under backend/app/fixtures/, and registers it in
demo_routes_manifest.json so routes.py's demo-mode lookup can serve it
instantly. See README's "Demo mode" section and
backend/app/api/routes.py's "Demo mode" block for how the manifest is
consumed.

Safe to run with DEMO_MODE=true on the target server -- the manifest only
intercepts exact origin/destination/vehicle_profile/load_state matches,
and a route being captured here isn't in the manifest yet (this script
writes it only after a successful response), so the request always hits
the live pipeline.

Usage:
    uv run python -m backend.app.scripts.capture_demo_fixture \\
        --origin Chennai --destination Madurai
    uv run python -m backend.app.scripts.capture_demo_fixture \\
        --origin Chennai --destination Madurai \\
        --vehicle-profile large_ev --load-state full_load \\
        --base-url http://127.0.0.1:8000
"""

import argparse
import json
import re
import time
from pathlib import Path

import requests

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures"
MANIFEST_PATH = FIXTURES_DIR / "demo_routes_manifest.json"

# Mirrors the frontend's default form values (frontend/src/App.jsx) -- these
# fields are required by RouteRequest but ignored by demo-mode's match logic,
# so any reasonable feasible-route values work here.
DEFAULT_BATTERY_PERCENTAGE = 80.0
DEFAULT_VEHICLE_RANGE = 300.0
DEFAULT_PREFERRED_CHARGING_SPEED = "fast"


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    if not slug:
        raise ValueError(f"Cannot slugify empty/invalid value: {value!r}")
    return slug


def _load_manifest() -> list[dict]:
    if not MANIFEST_PATH.exists():
        return []
    return json.loads(MANIFEST_PATH.read_text())


def _save_manifest(manifest: list[dict]) -> None:
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n")


def capture(origin: str, destination: str, vehicle_profile: str, load_state: str, base_url: str) -> Path:
    origin_key = origin.strip().lower()
    destination_key = destination.strip().lower()
    fixture_name = f"demo_route_{_slugify(origin)}_{_slugify(destination)}.json"
    fixture_path = FIXTURES_DIR / fixture_name

    payload = {
        "current_location": origin,
        "destination": destination,
        "battery_percentage": DEFAULT_BATTERY_PERCENTAGE,
        "vehicle_range": DEFAULT_VEHICLE_RANGE,
        "preferred_charging_speed": DEFAULT_PREFERRED_CHARGING_SPEED,
        "vehicle_profile": vehicle_profile,
        "load_state": load_state,
    }

    start = time.monotonic()
    response = requests.post(f"{base_url.rstrip('/')}/route/optimize", json=payload, timeout=600)
    elapsed = time.monotonic() - start
    response.raise_for_status()
    data = response.json()

    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    fixture_path.write_text(json.dumps(data, indent=2) + "\n")

    manifest = _load_manifest()
    entry = {
        "origin": origin_key,
        "destination": destination_key,
        "vehicle_profile": vehicle_profile,
        "load_state": load_state,
        "fixture": fixture_name,
    }
    for existing in manifest:
        if (
            existing["origin"] == origin_key
            and existing["destination"] == destination_key
            and existing["vehicle_profile"] == vehicle_profile
            and existing["load_state"] == load_state
        ):
            existing["fixture"] = fixture_name
            break
    else:
        manifest.append(entry)
    _save_manifest(manifest)

    print(f"Captured demo fixture in {elapsed:.1f}s -> {fixture_path}")
    print(f"Registered in {MANIFEST_PATH} ({origin_key} -> {destination_key}, {vehicle_profile}/{load_state})")
    return fixture_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--origin", required=True, help="current_location value to send, e.g. 'Chennai'.")
    parser.add_argument("--destination", required=True, help="destination value to send, e.g. 'Madurai'.")
    parser.add_argument("--vehicle-profile", default="mid_ev", help="Default: mid_ev.")
    parser.add_argument("--load-state", default="half_load", help="Default: half_load.")
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="Base URL of the running local server. Default: http://127.0.0.1:8000.",
    )
    args = parser.parse_args()

    capture(
        origin=args.origin,
        destination=args.destination,
        vehicle_profile=args.vehicle_profile,
        load_state=args.load_state,
        base_url=args.base_url,
    )


if __name__ == "__main__":
    main()
