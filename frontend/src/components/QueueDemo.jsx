import { useEffect, useMemo, useState } from "react";
import { getStationQueue, joinStationQueue, leaveStationQueue } from "../api";
import { IconBolt, IconStation } from "./icons";
import VehicleSelector from "./VehicleSelector";

const POLL_INTERVAL_MS = 3000;
const STATION_LIST_LIMIT = 30;

// sessionStorage (not localStorage) is what makes "open a new tab, get a
// new simulated user" work for this demo -- each browser tab has its own
// sessionStorage, so this id is stable across re-renders/reloads within one
// tab but unique per tab, with no login system needed.
function getSessionUserId() {
  let id = sessionStorage.getItem("routevolt_queue_user_id");
  if (!id) {
    id = `user_${Math.random().toString(36).slice(2, 8)}`;
    sessionStorage.setItem("routevolt_queue_user_id", id);
  }
  return id;
}

function getSessionUserName(userId) {
  let name = sessionStorage.getItem("routevolt_queue_user_name");
  if (!name) {
    name = `Driver ${userId.slice(-4).toUpperCase()}`;
    sessionStorage.setItem("routevolt_queue_user_name", name);
  }
  return name;
}

function formatClock(iso) {
  if (!iso) return "--";
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export default function QueueDemo({ stationMeta }) {
  const userId = useMemo(() => getSessionUserId(), []);
  const userName = useMemo(() => getSessionUserName(userId), [userId]);

  const stations = useMemo(
    () => Object.values(stationMeta).sort((a, b) => a.station_name.localeCompare(b.station_name)),
    [stationMeta]
  );

  const [search, setSearch] = useState("");
  const [selectedStationId, setSelectedStationId] = useState(null);
  const [currentBatteryPct, setCurrentBatteryPct] = useState(30);
  const [targetBatteryPct, setTargetBatteryPct] = useState(80);
  const [vehicleProfile, setVehicleProfile] = useState("mid_ev");
  const [etaMinutes, setEtaMinutes] = useState(0);

  const [myReservation, setMyReservation] = useState(null);
  const [queueState, setQueueState] = useState(null);
  const [joining, setJoining] = useState(false);
  const [error, setError] = useState(null);

  const selectedStation = selectedStationId != null ? stationMeta[selectedStationId] : null;

  const filteredStations = useMemo(() => {
    const q = search.trim().toLowerCase();
    const matches = q
      ? stations.filter(
          (s) => s.station_name.toLowerCase().includes(q) || (s.city || "").toLowerCase().includes(q)
        )
      : stations;
    return matches.slice(0, STATION_LIST_LIMIT);
  }, [stations, search]);

  // Poll the selected station's live queue every ~3s -- what makes two tabs
  // that joined the same station agree on who has which slot without a
  // websocket.
  useEffect(() => {
    if (selectedStationId == null) {
      setQueueState(null);
      return;
    }
    let cancelled = false;
    async function poll() {
      try {
        const data = await getStationQueue(selectedStationId);
        if (!cancelled) setQueueState(data);
      } catch {
        // Transient network hiccup -- keep the last known state, retry next tick.
      }
    }
    poll();
    const interval = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [selectedStationId]);

  function selectStation(id) {
    setSelectedStationId(id);
    setMyReservation(null);
    setError(null);
  }

  async function handleJoin() {
    setJoining(true);
    setError(null);
    try {
      const assignment = await joinStationQueue(selectedStationId, {
        user_id: userId,
        user_name: userName,
        current_battery_pct: Number(currentBatteryPct),
        target_battery_pct: Number(targetBatteryPct),
        vehicle_profile: vehicleProfile,
        eta_minutes_from_now: Number(etaMinutes),
      });
      setMyReservation(assignment);
      setQueueState(await getStationQueue(selectedStationId));
    } catch (err) {
      setError(err.message);
    } finally {
      setJoining(false);
    }
  }

  async function handleLeave() {
    if (!myReservation) return;
    try {
      await leaveStationQueue(myReservation.reservation_id);
    } catch {
      // Already released/expired -- clear local state either way.
    }
    setMyReservation(null);
    setQueueState(await getStationQueue(selectedStationId));
  }

  function isMine(reservation) {
    return myReservation != null && reservation?.reservation_id === myReservation.reservation_id;
  }

  return (
    <div className="thin-scroll flex h-full flex-col gap-5 overflow-y-auto bg-cream-200 p-6">
      <div className="flex items-center gap-2.5">
        <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-forest-100 text-forest-700">
          <IconStation className="h-[18px] w-[18px]" />
        </span>
        <div>
          <h1 className="text-[17px] font-semibold tracking-tight text-ink-900">Charger Queue Demo</h1>
          <p className="text-[12px] text-ink-500">
            Open this page in another tab and join the same station -- each tab is its own simulated driver.
          </p>
        </div>
      </div>

      <div className="grid gap-5 lg:grid-cols-[320px_1fr]">
        <div className="flex flex-col gap-4">
          <div className="rounded-xl border border-cream-300 bg-white p-4 shadow-sm">
            <span className="mb-2 block text-[11px] font-medium uppercase tracking-wider text-ink-500">
              Pick a station
            </span>
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search by name or city..."
              className="mb-2 w-full rounded-lg border border-cream-300 bg-cream-100 px-3 py-2 text-sm text-ink-900 placeholder:text-ink-300 outline-none transition-colors focus:border-forest-500 focus:bg-white focus:ring-2 focus:ring-forest-100"
            />
            <div className="thin-scroll flex max-h-56 flex-col gap-1 overflow-y-auto">
              {filteredStations.map((s) => (
                <button
                  key={s.id}
                  type="button"
                  onClick={() => selectStation(s.id)}
                  className={`flex items-center justify-between rounded-lg px-2.5 py-2 text-left text-[12px] transition-colors ${
                    s.id === selectedStationId
                      ? "bg-forest-100 font-medium text-forest-800"
                      : "text-ink-700 hover:bg-cream-100"
                  }`}
                >
                  <span className="truncate">{s.station_name}</span>
                  <span className="ml-2 shrink-0 text-[10px] text-ink-300">
                    {s.num_chargers} slot{s.num_chargers === 1 ? "" : "s"}
                  </span>
                </button>
              ))}
              {filteredStations.length === 0 && (
                <p className="px-2.5 py-2 text-[12px] text-ink-300">No stations match.</p>
              )}
            </div>
          </div>

          {selectedStation && (
            <div className="rounded-xl border border-cream-300 bg-white p-4 shadow-sm">
              <p className="mb-3 truncate text-[13px] font-semibold text-ink-900">{selectedStation.station_name}</p>

              <div className="mb-3">
                <span className="mb-1.5 block text-[11px] font-medium uppercase tracking-wider text-ink-500">
                  Vehicle
                </span>
                <VehicleSelector value={vehicleProfile} onChange={setVehicleProfile} />
              </div>

              <div className="mb-3 grid grid-cols-2 gap-2.5">
                <label className="flex flex-col gap-1 text-[11px] font-medium uppercase tracking-wider text-ink-500">
                  Current %
                  <input
                    type="number"
                    min={0}
                    max={100}
                    value={currentBatteryPct}
                    onChange={(e) => setCurrentBatteryPct(e.target.value)}
                    className="rounded-lg border border-cream-300 bg-cream-100 px-2.5 py-1.5 text-sm normal-case tracking-normal text-ink-900 outline-none focus:border-forest-500 focus:bg-white"
                  />
                </label>
                <label className="flex flex-col gap-1 text-[11px] font-medium uppercase tracking-wider text-ink-500">
                  Target %
                  <input
                    type="number"
                    min={0}
                    max={100}
                    value={targetBatteryPct}
                    onChange={(e) => setTargetBatteryPct(e.target.value)}
                    className="rounded-lg border border-cream-300 bg-cream-100 px-2.5 py-1.5 text-sm normal-case tracking-normal text-ink-900 outline-none focus:border-forest-500 focus:bg-white"
                  />
                </label>
              </div>

              <label className="mb-3 flex flex-col gap-1 text-[11px] font-medium uppercase tracking-wider text-ink-500">
                Arriving in (minutes)
                <input
                  type="number"
                  min={0}
                  value={etaMinutes}
                  onChange={(e) => setEtaMinutes(e.target.value)}
                  className="rounded-lg border border-cream-300 bg-cream-100 px-2.5 py-1.5 text-sm normal-case tracking-normal text-ink-900 outline-none focus:border-forest-500 focus:bg-white"
                />
              </label>

              {!myReservation ? (
                <button
                  type="button"
                  onClick={handleJoin}
                  disabled={joining}
                  className="flex w-full items-center justify-center gap-2 rounded-lg bg-forest-700 px-4 py-2.5 text-sm font-semibold text-white shadow-sm shadow-forest-900/20 transition-colors hover:bg-forest-800 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {joining ? "Joining..." : "Travel to this station"}
                </button>
              ) : (
                <button
                  type="button"
                  onClick={handleLeave}
                  className="flex w-full items-center justify-center gap-2 rounded-lg border border-clay-600/30 bg-clay-100 px-4 py-2.5 text-sm font-semibold text-clay-600 transition-colors hover:border-clay-600"
                >
                  Leave queue
                </button>
              )}

              {myReservation && (
                <p className="mt-2 text-[11px] text-ink-500">
                  Slot #{myReservation.charger_slot} &middot; wait {myReservation.wait_minutes} min &middot; starts{" "}
                  {formatClock(myReservation.start_time)}
                </p>
              )}

              {error && <p className="mt-2 text-[11px] text-clay-600">{error}</p>}
            </div>
          )}
        </div>

        <div className="rounded-xl border border-cream-300 bg-white p-4 shadow-sm">
          {!selectedStation ? (
            <div className="flex h-full flex-col items-center justify-center gap-2 py-16 text-center">
              <IconBolt className="h-6 w-6 text-ink-300" />
              <p className="text-[12px] text-ink-300">Pick a station to see its live charger slots.</p>
            </div>
          ) : (
            <>
              <div className="mb-3 flex items-center justify-between">
                <p className="text-[13px] font-semibold text-ink-900">Live charger slots</p>
                <span className="text-[10px] uppercase tracking-wide text-ink-300">Updates every 3s</span>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                {(queueState?.slots || []).map((slot) => {
                  const mine =
                    isMine(slot.current) || slot.upcoming.some((r) => isMine(r));
                  const occupied = slot.current != null;
                  return (
                    <div
                      key={slot.charger_slot}
                      className={`rounded-lg border p-3 ${
                        mine
                          ? "border-forest-600 bg-forest-50"
                          : occupied
                            ? "border-clay-600/30 bg-clay-100"
                            : "border-cream-300 bg-cream-50"
                      }`}
                    >
                      <div className="flex items-center justify-between">
                        <p className="text-[12px] font-semibold text-ink-900">Slot {slot.charger_slot}</p>
                        <span
                          className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${
                            occupied ? "bg-white text-clay-600" : "bg-forest-100 text-forest-700"
                          }`}
                        >
                          {occupied ? "Occupied" : "Free"}
                        </span>
                      </div>

                      {slot.current ? (
                        <p className="mt-1.5 text-[11px] text-ink-700">
                          {isMine(slot.current) ? "You" : slot.current.user_name || slot.current.user_id} &middot;
                          until {formatClock(slot.current.end_time)}
                        </p>
                      ) : (
                        <p className="mt-1.5 text-[11px] text-ink-300">No one charging</p>
                      )}

                      {slot.upcoming.length > 0 && (
                        <div className="mt-2 border-t border-cream-200 pt-1.5">
                          <p className="text-[10px] uppercase tracking-wide text-ink-300">Queued</p>
                          {slot.upcoming.map((r) => (
                            <p key={r.reservation_id} className="text-[11px] text-ink-500">
                              {isMine(r) ? "You" : r.user_name || r.user_id} &middot; from {formatClock(r.start_time)}
                            </p>
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
