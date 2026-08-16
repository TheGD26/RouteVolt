import StationCard from "./StationCard";

function stopEta(route, legIndex) {
  const legs = route.legs.slice(0, legIndex + 1);
  const seconds = legs.reduce((sum, leg) => sum + (leg.duration_s || 0), 0);
  const trafficAware = route.legs[legIndex]?.traffic_source === "live";
  return { etaMinutes: Math.round(seconds / 60), trafficAware };
}

function buildStationView(alt, meta, extras) {
  const stationId = alt.station_id;
  return {
    stationId,
    stationName: alt.station_name,
    address: meta[stationId]?.address,
    expectedWaitMinutes: alt.expected_wait_minutes,
    queueLength: alt.queue_length,
    confidence: extras[stationId]?.confidence,
  };
}

export default function RouteResults({
  result,
  selectedRouteIdx,
  onSelectRoute,
  stationMeta,
  stationExtras,
  onReport,
  reportStates,
}) {
  const route = result.routes[selectedRouteIdx];

  return (
    <section className="route-results">
      <div className="route-summary-top">
        <span>Estimated time: {result.estimated_time}</span>
        <span>{result.traffic_aware ? "Live traffic" : "Traffic estimate unavailable"}</span>
      </div>

      {result.routes.length > 1 && (
        <div className="route-tabs">
          {result.routes.map((r, idx) => (
            <button
              key={r.label}
              type="button"
              className={idx === selectedRouteIdx ? "active" : ""}
              onClick={() => onSelectRoute(idx)}
            >
              {r.label}
            </button>
          ))}
        </div>
      )}

      <div className="route-summary">
        <span>{route.distance_km} km</span>
        <span>{route.duration_min} min drive</span>
        <span>{route.predicted_energy_kwh} kWh predicted</span>
        <span>Arrives at {route.final_battery_pct}% battery</span>
        {route.status === "unreachable_gap" && <span className="warning">Route has an unreachable gap</span>}
      </div>

      {route.gap && (
        <div className="route-gap">
          No reachable station near leg {route.gap.leg_index} ({route.gap.reason}) -- battery would drop
          to {route.gap.battery_pct_at_gap}%.
        </div>
      )}

      {route.charging_plan.length === 0 && route.status === "ok" && (
        <p>No charging stops needed -- battery lasts the whole trip.</p>
      )}

      {route.charging_plan.map((stop, i) => {
        const { etaMinutes, trafficAware } = stopEta(route, stop.leg_index);
        const ranked = stop.alternatives.length > 0 ? stop.alternatives : [stop];

        return (
          <div key={i} className="charging-stop">
            <h3>
              Stop {i + 1} -- charge to {stop.charge_to_percent}%
              {stop.exceeded_ceiling && " (above normal ceiling)"}
            </h3>
            <p className="stop-meta">
              Arriving at {stop.battery_pct_on_arrival}% battery, ~{stop.estimated_charge_duration_min} min
              charge time
            </p>

            <div className="station-list">
              {ranked.map((alt, idx) => {
                const view = buildStationView(alt, stationMeta, stationExtras);
                return (
                  <StationCard
                    key={`${view.stationId}-${idx}`}
                    rank={idx + 1}
                    station={{ ...view, etaMinutes, trafficAware }}
                    onReport={onReport}
                    reportState={reportStates[view.stationId]}
                  />
                );
              })}
            </div>
          </div>
        );
      })}
    </section>
  );
}
