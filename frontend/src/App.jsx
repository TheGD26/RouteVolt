import { useEffect, useState } from "react";
import BatteryChart from "./components/BatteryChart";
import ChargingStationsSummary from "./components/ChargingStationsSummary";
import { IconRoute } from "./components/icons";
import RouteForm from "./components/RouteForm";
import RouteResults from "./components/RouteResults";
import Sidebar from "./components/Sidebar";
import Topbar from "./components/Topbar";
import { API_BASE_URL, getStationWaitEstimate, optimizeRoute, reportStationStatus } from "./api";

const INITIAL_FORM = {
  current_location: "",
  destination: "",
  battery_percentage: 80,
  vehicle_profile: "mid_ev",
  load_state: "half_load",
};

function uniqueStationIds(route) {
  const ids = new Set();
  for (const stop of route.charging_plan) {
    const alts = stop.alternatives.length > 0 ? stop.alternatives : [stop];
    for (const alt of alts) {
      if (alt.station_id != null) ids.add(alt.station_id);
    }
  }
  return [...ids];
}

// Only "Fastest" reflects real backend behavior (see PreferenceToggle) --
// applying it means auto-selecting whichever of the returned OSRM
// alternatives actually has the lowest duration_min, using real response
// data rather than a fabricated ranking.
function fastestRouteIndex(routes) {
  let bestIdx = 0;
  for (let i = 1; i < routes.length; i++) {
    if (routes[i].duration_min < routes[bestIdx].duration_min) bestIdx = i;
  }
  return bestIdx;
}

export default function App() {
  const [form, setForm] = useState(INITIAL_FORM);
  const [preference, setPreference] = useState("fastest");
  const [result, setResult] = useState(null);
  const [selectedRouteIdx, setSelectedRouteIdx] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const [stationMeta, setStationMeta] = useState({});
  const [stationExtras, setStationExtras] = useState({});
  const [reportStates, setReportStates] = useState({});

  useEffect(() => {
    fetch(`${API_BASE_URL}/stations/`)
      .then((res) => res.json())
      .then((stations) => {
        const byId = {};
        for (const s of stations) byId[s.id] = s;
        setStationMeta(byId);
      })
      .catch(() => {
        // Address/connector lookups are a nice-to-have; route planning still works without them.
      });
  }, []);

  useEffect(() => {
    if (!result) return;
    const route = result.routes[selectedRouteIdx];
    const ids = uniqueStationIds(route);
    const missing = ids.filter((id) => !(id in stationExtras));
    if (missing.length === 0) return;

    Promise.all(
      missing.map((id) =>
        getStationWaitEstimate(id)
          .then((data) => [id, data])
          .catch(() => [id, null])
      )
    ).then((pairs) => {
      setStationExtras((prev) => {
        const next = { ...prev };
        for (const [id, data] of pairs) {
          if (data) next[id] = data;
        }
        return next;
      });
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [result, selectedRouteIdx]);

  async function handleSubmit(e) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const data = await optimizeRoute({
        ...form,
        battery_percentage: Number(form.battery_percentage),
        // Required by RouteRequest but not consumed by the mass-aware route
        // planner (vehicle_profile/load_state drive it instead) -- send
        // placeholders rather than surfacing dead fields in the form.
        vehicle_range: 300,
        preferred_charging_speed: "fast",
      });
      setResult(data);
      setSelectedRouteIdx(preference === "fastest" ? fastestRouteIndex(data.routes) : 0);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  async function handleReport(stationId, status) {
    setReportStates((prev) => ({ ...prev, [stationId]: "Reporting..." }));
    try {
      await reportStationStatus(stationId, status);
      setReportStates((prev) => ({ ...prev, [stationId]: `Reported ${status} ✓` }));
    } catch (err) {
      setReportStates((prev) => ({ ...prev, [stationId]: `Failed: ${err.message}` }));
    }
  }

  const route = result?.routes[selectedRouteIdx];

  return (
    <div className="flex h-screen bg-cream-100 text-ink-900">
      <Sidebar />

      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar />

        <main className="flex min-h-0 flex-1 flex-col lg:flex-row">
          <section className="thin-scroll flex w-full shrink-0 flex-col gap-5 overflow-y-auto border-cream-300 px-6 py-6 lg:w-[380px] lg:border-r xl:w-[420px]">
            <RouteForm
              form={form}
              onChange={setForm}
              preference={preference}
              onPreferenceChange={setPreference}
              onSubmit={handleSubmit}
              loading={loading}
            />

            {error && (
              <div className="rounded-lg border border-clay-600/30 bg-clay-100 px-3.5 py-2.5 text-[13px] text-clay-600">
                {error}
              </div>
            )}

            {route?.status === "unreachable_gap" && route.gap && (
              <div className="rounded-lg border border-amber-600/30 bg-amber-100 px-3.5 py-2.5 text-[12px] text-amber-600">
                No reachable station near leg {route.gap.leg_index} ({route.gap.reason}) -- battery would
                drop to {route.gap.battery_pct_at_gap}%.
              </div>
            )}

            {route && (
              <ChargingStationsSummary
                route={route}
                stationMeta={stationMeta}
                stationExtras={stationExtras}
                onReport={handleReport}
                reportStates={reportStates}
              />
            )}

            {route && route.charging_plan.length > 0 && (
              <div>
                <span className="mb-2 block text-[11px] font-medium uppercase tracking-wider text-ink-500">
                  Battery Profile
                </span>
                <BatteryChart route={route} />
              </div>
            )}

            {route && route.charging_plan.length === 0 && route.status === "ok" && (
              <p className="text-[12px] text-ink-500">No charging stops needed -- battery lasts the whole trip.</p>
            )}
          </section>

          <section className="relative min-h-[320px] flex-1">
            {result ? (
              <RouteResults result={result} selectedRouteIdx={selectedRouteIdx} onSelectRoute={setSelectedRouteIdx} />
            ) : (
              <div className="flex h-full w-full flex-col items-center justify-center gap-3 bg-cream-200 text-center">
                <span className="flex h-12 w-12 items-center justify-center rounded-2xl bg-white text-forest-700 shadow-sm">
                  <IconRoute className="h-6 w-6" />
                </span>
                <p className="text-sm font-medium text-ink-700">
                  {loading ? "Calculating your dynamic route..." : "Enter an origin and destination to see your route"}
                </p>
                <p className="max-w-xs text-[12px] text-ink-300">
                  The map, charging stops, and battery profile will appear here once a route is calculated.
                </p>
              </div>
            )}
          </section>
        </main>
      </div>
    </div>
  );
}
