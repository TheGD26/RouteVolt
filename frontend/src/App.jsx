import { useEffect, useState } from "react";
import RouteForm from "./components/RouteForm";
import RouteResults from "./components/RouteResults";
import { API_BASE_URL, getStationWaitEstimate, optimizeRoute, reportStationStatus } from "./api";
import "./App.css";

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

export default function App() {
  const [form, setForm] = useState(INITIAL_FORM);
  const [result, setResult] = useState(null);
  const [selectedRouteIdx, setSelectedRouteIdx] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const [stationMeta, setStationMeta] = useState({});
  const [stationExtras, setStationExtras] = useState({});
  const [reportStates, setReportStates] = useState({});

  // Station catalogue (for address lookups) -- fetched once.
  useEffect(() => {
    fetch(`${API_BASE_URL}/stations/`)
      .then((res) => res.json())
      .then((stations) => {
        const byId = {};
        for (const s of stations) byId[s.id] = s;
        setStationMeta(byId);
      })
      .catch(() => {
        // Address lookups are a nice-to-have; route planning still works without them.
      });
  }, []);

  // Confidence level per station shown for the selected route.
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
    setSelectedRouteIdx(0);
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

  return (
    <div className="app">
      <header>
        <h1>RouteVolt</h1>
        <p>Plan an EV route with real charging-stop guidance.</p>
      </header>

      <RouteForm form={form} onChange={setForm} onSubmit={handleSubmit} loading={loading} />

      {error && <p className="error">{error}</p>}
      {loading && <p className="loading">Loading...</p>}

      {result && (
        <RouteResults
          result={result}
          selectedRouteIdx={selectedRouteIdx}
          onSelectRoute={setSelectedRouteIdx}
          stationMeta={stationMeta}
          stationExtras={stationExtras}
          onReport={handleReport}
          reportStates={reportStates}
        />
      )}
    </div>
  );
}
