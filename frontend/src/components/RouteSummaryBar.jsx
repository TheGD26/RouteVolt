import { IconClock, IconGauge, IconNavigation, IconRoute } from "./icons";

function formatDuration(minutes) {
  const h = Math.floor(minutes / 60);
  const m = Math.round(minutes % 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

function formatEta(minutes) {
  const arrival = new Date(Date.now() + minutes * 60000);
  return arrival.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

export default function RouteSummaryBar({ route }) {
  return (
    <div className="absolute inset-x-3 bottom-3 flex flex-wrap items-center gap-x-6 gap-y-2 rounded-xl border border-cream-300 bg-white/95 px-4 py-3 shadow-lg shadow-ink-900/10 backdrop-blur-sm sm:inset-x-4 sm:bottom-4">
      <Stat icon={IconRoute} label="Distance" value={`${route.distance_km} km`} />
      <Stat
        icon={IconClock}
        label="Est. ETA"
        value={`${formatEta(route.duration_min)} (${formatDuration(route.duration_min)})`}
      />
      <Stat icon={IconGauge} label="Energy Used" value={`${route.predicted_energy_kwh} kWh`} />

      <button
        type="button"
        className="ml-auto flex items-center gap-2 rounded-lg bg-forest-700 px-4 py-2.5 text-[13px] font-semibold text-white shadow-sm transition-colors hover:bg-forest-800"
      >
        <IconNavigation className="h-4 w-4" />
        Start Navigation
      </button>
    </div>
  );
}

function Stat({ icon: StatIcon, label, value }) {
  return (
    <div className="flex items-center gap-2">
      <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-forest-50 text-forest-700">
        <StatIcon className="h-4 w-4" />
      </span>
      <div className="leading-tight">
        <p className="text-[10px] font-medium uppercase tracking-wide text-ink-300">{label}</p>
        <p className="text-[13px] font-semibold text-ink-900">{value}</p>
      </div>
    </div>
  );
}
