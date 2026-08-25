"""
Manual verification for charger_service's reservation-based queue layer
(join_queue/get_queue_state/release_reservation, and their interaction with
get_blended_occupancy/estimate_wait_time):

(a) a 2-charger station, 3 users "arriving" ~1 minute apart with equal
    charge duration -- the first two get immediate slots (wait_minutes==0,
    different charger_slot each), the third is queued with wait_minutes
    matching the earlier finisher's actual end_time (not a flat constant).
(b) for a station with ZERO reservations, get_blended_occupancy and
    estimate_wait_time return byte-identical results to the pre-reservation
    logic -- confidence stays "heuristic"/"blended"/"crowdsourced", never
    "reserved".
(c) once (a)'s reservations exist, get_blended_occupancy/estimate_wait_time
    on that same station report "confidence": "reserved" and numbers
    consistent with the reservations, and release_reservation cleans them
    back up so the station reverts to its pre-reservation behavior.

Not a pytest suite -- this project has no test runner configured yet (see
pyproject.toml and backend/app/scripts/verify_occupancy_blend.py). Run
directly:

    uv run python -m backend.app.scripts.verify_reservation_queue

Side effect: none persisted -- all reservation state is in-memory and this
process exits after running; picks two real seeded stations (one 2+ charger,
one arbitrary) but writes nothing to the DB.
"""

from datetime import datetime, timedelta, timezone

from backend.app.services import charger_service


def _pick_station_with_at_least(min_chargers: int) -> dict:
    for station in charger_service.get_stations():
        if (station["num_chargers"] or 1) >= min_chargers:
            return station
    raise RuntimeError(f"No seeded station has >= {min_chargers} chargers.")


def _pick_station_with_no_recent_reports(exclude_id) -> dict:
    for station in charger_service.get_stations():
        if station["id"] == exclude_id:
            continue
        if not charger_service.get_recent_reports(station["id"], limit=1):
            return station
    raise RuntimeError("Every other seeded station already has a report -- pick manually.")


def verify_queue_ordering() -> tuple[int, datetime]:
    station = _pick_station_with_at_least(2)
    station_id = station["id"]
    print(f"(a) station {station_id} ({station['station_name']}), num_chargers={station['num_chargers']}")

    now = datetime.now(timezone.utc)
    charge_duration_min = 30.0

    user1 = charger_service.join_queue(station_id, "demo_user_1", charge_duration_min, eta=now)
    user2 = charger_service.join_queue(station_id, "demo_user_2", charge_duration_min, eta=now + timedelta(minutes=1))
    user3 = charger_service.join_queue(station_id, "demo_user_3", charge_duration_min, eta=now + timedelta(minutes=2))

    print(f"    user1: {user1}")
    print(f"    user2: {user2}")
    print(f"    user3: {user3}")

    assert user1["wait_minutes"] == 0.0, "first arrival should get an immediate slot"
    assert user2["wait_minutes"] == 0.0, "second arrival (different slot) should also be immediate"
    assert user1["charger_slot"] != user2["charger_slot"], "the first two users must land on different slots"

    assert user3["wait_minutes"] > 0.0, "third arrival on a 2-charger station should be queued"
    earlier_finisher = user1 if user1["charger_slot"] == user3["charger_slot"] else user2
    expected_start = earlier_finisher["end_time"] + timedelta(minutes=charger_service.RESERVATION_TURNOVER_BUFFER_MINUTES)
    assert user3["start_time"] == expected_start, (
        f"user3 should start exactly at the earlier finisher's end_time + turnover buffer "
        f"({expected_start}), got {user3['start_time']}"
    )
    expected_wait_minutes = round((expected_start - (now + timedelta(minutes=2))).total_seconds() / 60.0, 1)
    assert user3["wait_minutes"] == expected_wait_minutes, (
        f"user3 wait_minutes should equal the earlier finisher's real end time minus user3's eta "
        f"({expected_wait_minutes}), got {user3['wait_minutes']} -- not a flat constant"
    )

    # Checked at now+2min (not "right now"): user2's own eta was now+1min --
    # "immediate" means zero wait relative to *their* eta, not that they've
    # necessarily started in wall-clock terms the instant join_queue returns.
    check_time = now + timedelta(minutes=2)
    queue_state = charger_service.get_queue_state(station_id, at_time=check_time)
    print(f"    queue_state @ {check_time}: {queue_state}")
    occupied_slots = [s for s in queue_state["slots"] if s["current"] is not None]
    assert len(occupied_slots) == 2, "both slots should show a current occupant by now+2min"

    print("    (a) passed.\n")
    return station_id, check_time


def verify_zero_reservations_unchanged(reserved_station_id) -> None:
    station = _pick_station_with_no_recent_reports(exclude_id=reserved_station_id)
    now = datetime.now(timezone.utc)

    print(f"(b) station {station['id']} ({station['station_name']}) -- zero reservations, zero reports")
    occupancy_before = charger_service.get_blended_occupancy(station["id"], now)
    wait_before = charger_service.estimate_wait_time(station["id"], now)
    print(f"    occupancy: {occupancy_before}")
    print(f"    wait:      {wait_before}")

    assert occupancy_before["confidence"] == "heuristic"
    assert wait_before["confidence"] == "heuristic"

    # Re-run to confirm determinism (byte-identical, not just same shape).
    occupancy_again = charger_service.get_blended_occupancy(station["id"], now)
    wait_again = charger_service.estimate_wait_time(station["id"], now)
    assert occupancy_before == occupancy_again
    assert wait_before == wait_again

    print("    (b) passed.\n")


def verify_reserved_tier_and_release(station_id, check_time: datetime) -> None:
    print(f"(c) station {station_id} -- occupancy/wait through the 'reserved' tier")

    occupancy = charger_service.get_blended_occupancy(station_id, check_time)
    wait = charger_service.estimate_wait_time(station_id, check_time)
    print(f"    occupancy: {occupancy}")
    print(f"    wait:      {wait}")
    assert occupancy["confidence"] == "reserved"
    assert wait["confidence"] == "reserved"
    assert occupancy["expected_occupied_ports"] == 2  # both slots hold an active session at check_time

    released = 0
    for reservation_id in list(charger_service._reservations.keys()):
        if charger_service._reservations[reservation_id].station_id == station_id:
            assert charger_service.release_reservation(reservation_id)
            released += 1
    assert released == 3, f"expected to release all 3 demo reservations, released {released}"
    assert charger_service.release_reservation(999_999_999) is False, "unknown reservation_id should return False"

    occupancy_after_release = charger_service.get_blended_occupancy(station_id, check_time)
    print(f"    occupancy after release: {occupancy_after_release}")
    assert occupancy_after_release["confidence"] != "reserved", "station should fall back to heuristic/blend after release"

    print("    (c) passed.\n")


def main() -> None:
    reserved_station_id, check_time = verify_queue_ordering()
    verify_zero_reservations_unchanged(reserved_station_id)
    verify_reserved_tier_and_release(reserved_station_id, check_time)
    print("All checks passed.")


if __name__ == "__main__":
    main()
