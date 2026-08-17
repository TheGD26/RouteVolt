import { useMemo, useState } from "react";
import { IconBolt, IconChevronDown } from "./icons";
import StationCard from "./StationCard";

const DC_BUCKETS = new Set(["CCS2", "CHADEMO", "BHARAT_DC"]);
const AC_BUCKETS = new Set(["AC_TYPE2", "TYPE1_16A"]);

function ranked(stop) {
  return stop.alternatives.length > 0 ? stop.alternatives : [stop];
}

// Aggregates real fields from the /route/optimize response (connector
// buckets already normalized server-side, queue_length from the same
// queueing estimate StationCard shows) -- nothing here is fabricated, it's
// a rollup of what the backend already returned for this route's stops.
function computeStats(route, stationMeta) {
  const seen = new Map(); // station_id -> { queueLength, buckets }

  for (const stop of route.charging_plan) {
    for (const alt of ranked(stop)) {
      if (alt.station_id == null || seen.has(alt.station_id)) continue;
      const meta = stationMeta[alt.station_id];
      seen.set(alt.station_id, {
        queueLength: alt.queue_length ?? 0,
        buckets: meta?.connector_buckets || [],
      });
    }
  }

  let fastDC = 0;
  let slowAC = 0;
  let available = 0;
  let busy = 0;

  for (const { queueLength, buckets } of seen.values()) {
    if (buckets.some((b) => DC_BUCKETS.has(b))) fastDC += 1;
    if (buckets.some((b) => AC_BUCKETS.has(b))) slowAC += 1;
    if (queueLength > 0) busy += 1;
    else available += 1;
  }

  return { totalStations: seen.size, fastDC, slowAC, available, busy, requiredStops: route.charging_plan.length };
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

function stopEta(route, legIndex) {
  const legs = route.legs.slice(0, legIndex + 1);
  const seconds = legs.reduce((sum, leg) => sum + (leg.duration_s || 0), 0);
  const trafficAware = route.legs[legIndex]?.traffic_source === "live";
  return { etaMinutes: Math.round(seconds / 60), trafficAware };
}

const STAT_LABELS = [
  { key: "fastDC", label: "Fast DC" },
  { key: "slowAC", label: "Slow AC" },
  { key: "available", label: "Available" },
  { key: "busy", label: "Busy" },
  { key: "requiredStops", label: "Req. Stops" },
];

export default function ChargingStationsSummary({ route, stationMeta, stationExtras, onReport, reportStates }) {
  const [expanded, setExpanded] = useState(false);
  const stats = useMemo(() => computeStats(route, stationMeta), [route, stationMeta]);

  return (
    <div className="rounded-xl border border-cream-300 bg-white shadow-sm">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        disabled={stats.totalStations === 0}
        className="flex w-full items-start gap-3 px-4 py-3.5 text-left disabled:cursor-default"
      >
        <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-forest-100 text-forest-700">
          <IconBolt className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <p className="text-[13px] font-semibold text-ink-900">
              {stats.totalStations} Charging Station{stats.totalStations === 1 ? "" : "s"} Found Along This Route
            </p>
            <span className="shrink-0 rounded-full bg-forest-50 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-forest-700">
              Live Corridor
            </span>
          </div>
          {stats.totalStations > 0 && (
            <p className="mt-0.5 text-[12px] text-ink-500">
              Click to {expanded ? "hide" : "view"} complete list &amp; navigation options
            </p>
          )}
        </div>
        {stats.totalStations > 0 && (
          <IconChevronDown
            className={`mt-1 h-4 w-4 shrink-0 text-ink-300 transition-transform ${expanded ? "rotate-180" : ""}`}
          />
        )}
      </button>

      {stats.totalStations > 0 && (
        <div className="grid grid-cols-5 gap-1 border-t border-cream-200 px-4 py-3">
          {STAT_LABELS.map(({ key, label }) => (
            <div key={key} className="text-center">
              <p
                className={`text-[15px] font-semibold ${
                  key === "busy" && stats[key] > 0
                    ? "text-clay-600"
                    : key === "available"
                      ? "text-forest-700"
                      : "text-ink-900"
                }`}
              >
                {stats[key]}
              </p>
              <p className="text-[9px] font-medium uppercase tracking-wide text-ink-300">{label}</p>
            </div>
          ))}
        </div>
      )}

      {expanded && (
        <div className="border-t border-cream-200 px-4 py-3">
          <div className="flex flex-col gap-4">
            {route.charging_plan.map((stop, i) => {
              const { etaMinutes, trafficAware } = stopEta(route, stop.leg_index);
              return (
                <div key={i}>
                  <p className="text-[12px] font-semibold text-ink-900">
                    Stop {i + 1} &middot; charge to {stop.charge_to_percent}%
                    {stop.exceeded_ceiling && " (above normal ceiling)"}
                  </p>
                  <p className="mb-2 text-[11px] text-ink-500">
                    Arriving at {stop.battery_pct_on_arrival}% battery &middot; ~
                    {stop.estimated_charge_duration_min} min charge
                  </p>
                  <div className="flex flex-col gap-2">
                    {ranked(stop).map((alt, idx) => (
                      <StationCard
                        key={`${alt.station_id}-${idx}`}
                        rank={idx + 1}
                        station={{
                          ...buildStationView(alt, stationMeta, stationExtras),
                          etaMinutes,
                          trafficAware,
                        }}
                        onReport={onReport}
                        reportState={reportStates[alt.station_id]}
                      />
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
