import { IconBolt, IconLeaf, IconScale, IconWallet } from "./icons";

// Only "Fastest" reflects real backend behavior today: /route/optimize has
// no cost model or scenic-routing concept, and the OSRM alternatives it
// returns aren't labeled by any preference -- picking "Fastest" just means
// the route with the lowest duration_min is auto-selected client-side (see
// App.jsx). The other three are shown for the target layout but disabled
// rather than faking a preference the backend can't actually honor.
const PREFERENCES = [
  { value: "fastest", label: "Fastest", icon: IconBolt, available: true },
  { value: "cheapest", label: "Cheapest", icon: IconWallet, available: false },
  { value: "balanced", label: "Balanced", icon: IconScale, available: false },
  { value: "scenic", label: "Scenic", icon: IconLeaf, available: false },
];

export default function PreferenceToggle({ value, onChange }) {
  return (
    <div>
      <span className="mb-1.5 block text-[11px] font-medium uppercase tracking-wider text-ink-500">
        Preference
      </span>
      <div className="grid grid-cols-2 gap-2">
        {PREFERENCES.map((opt) => {
          const selected = opt.value === value;
          return (
            <button
              key={opt.value}
              type="button"
              disabled={!opt.available}
              title={opt.available ? undefined : "Coming soon"}
              onClick={() => opt.available && onChange(opt.value)}
              aria-pressed={selected}
              className={`flex items-center justify-center gap-1.5 rounded-lg border px-3 py-2 text-[13px] font-medium transition-colors ${
                selected
                  ? "border-forest-600 bg-forest-600 text-white shadow-sm"
                  : opt.available
                    ? "border-cream-300 bg-white text-ink-700 hover:border-forest-300 hover:bg-forest-50/40"
                    : "cursor-not-allowed border-cream-200 bg-cream-100 text-ink-300"
              }`}
            >
              <opt.icon className="h-4 w-4" />
              {opt.label}
              {!opt.available && (
                <span className="ml-0.5 rounded-full bg-cream-200 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-ink-300">
                  Soon
                </span>
              )}
            </button>
          );
        })}
      </div>
    </div>
  );
}
