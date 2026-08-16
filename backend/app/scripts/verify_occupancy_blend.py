"""
Manual verification for charger_service.get_blended_occupancy (Phase 4):

(a) a station with no recent reports returns the pure heuristic estimate
    unchanged ("confidence": "heuristic").
(b) the same station, after several fresh "busy" reports, returns a
    noticeably higher occupied-ports estimate than the heuristic alone, with
    "confidence" flipping to "blended"/"crowdsourced".

Not a pytest suite -- this project has no test runner configured yet (see
pyproject.toml). Run directly:

    uv run python -m backend.app.scripts.verify_occupancy_blend

Side effect: inserts 5 real "busy" occupancy_reports rows (source
"verify_script") for whichever station it picks, into whatever DB
routevolt.db currently points at -- same as any real crowdsourced report.
"""

from datetime import datetime, timezone

from backend.app.services import charger_service


def _pick_station_with_no_recent_reports() -> dict:
    for station in charger_service.get_stations():
        if not charger_service.get_recent_reports(station["id"], limit=1):
            return station
    raise RuntimeError(
        "Every seeded station already has at least one occupancy report -- "
        "can't demonstrate the zero-reports case. Pick a station manually instead."
    )


def main() -> None:
    now = datetime.now(timezone.utc)

    # (a) no reports -> pure heuristic
    station = _pick_station_with_no_recent_reports()
    baseline = charger_service._heuristic_occupancy(station, now)
    heuristic_only = charger_service.get_blended_occupancy(station["id"], now)

    print(f"(a) station {station['id']} ({station['station_name']}), num_chargers={station['num_chargers']}, no reports:")
    print(f"    {heuristic_only}")
    assert heuristic_only == baseline, "with zero reports, get_blended_occupancy should return the heuristic unchanged"
    assert heuristic_only["confidence"] == "heuristic"

    # (b) several fresh "busy" reports -> noticeably higher occupied estimate
    for _ in range(5):
        charger_service.add_occupancy_report(station["id"], "busy", source="verify_script")

    blended = charger_service.get_blended_occupancy(station["id"], now)
    print("\n(b) same station after 5 fresh 'busy' reports:")
    print(f"    heuristic baseline: {baseline}")
    print(f"    blended:            {blended}")

    assert blended["confidence"] in ("blended", "crowdsourced"), blended
    assert blended["expected_occupied_ports"] >= baseline["expected_occupied_ports"], (
        "5 fresh busy reports should not produce a *lower* occupied estimate than the heuristic alone"
    )
    if baseline["expected_occupied_ports"] < station["num_chargers"]:
        assert blended["expected_occupied_ports"] > baseline["expected_occupied_ports"], (
            "5 fresh busy reports should noticeably raise the occupied estimate above the heuristic "
            "(unless the heuristic was already at full occupancy, in which case there's no room to rise)"
        )

    print("\nAll checks passed.")


if __name__ == "__main__":
    main()
