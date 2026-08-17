import { IconCar } from "./icons";

// Mirrors VEHICLE_PROFILES in backend/app/services/route_optimizer.py --
// display-only figures derived from the same battery_capacity_kwh /
// efficiency_baseline_wh_per_km the backend actually plans against, not
// invented specs. Keep in sync by hand if those change (same pattern as
// BatteryChart.jsx's BATTERY_FLOOR_PCT/CEILING_PCT).
const VEHICLE_OPTIONS = [
  { value: "small_ev", label: "Small EV", batteryCapacityKwh: 24, efficiencyWhPerKm: 140 },
  { value: "mid_ev", label: "Mid EV", batteryCapacityKwh: 40, efficiencyWhPerKm: 160 },
  { value: "large_ev", label: "Large EV", batteryCapacityKwh: 75, efficiencyWhPerKm: 190 },
].map((v) => ({
  ...v,
  rangeKm: Math.round((v.batteryCapacityKwh * 1000) / v.efficiencyWhPerKm),
}));

export default function VehicleSelector({ value, onChange }) {
  return (
    <div className="grid grid-cols-3 gap-2.5">
      {VEHICLE_OPTIONS.map((opt) => {
        const selected = opt.value === value;
        return (
          <button
            key={opt.value}
            type="button"
            onClick={() => onChange(opt.value)}
            aria-pressed={selected}
            className={`flex flex-col items-center gap-1.5 rounded-xl border px-2 py-3 text-center transition-colors ${
              selected
                ? "border-forest-600 bg-forest-50 shadow-sm"
                : "border-cream-300 bg-white hover:border-forest-200 hover:bg-forest-50/40"
            }`}
          >
            <span
              className={`flex h-9 w-9 items-center justify-center rounded-lg ${
                selected ? "bg-forest-600 text-white" : "bg-cream-200 text-ink-500"
              }`}
            >
              <IconCar className="h-[18px] w-[18px]" />
            </span>
            <span className={`text-[13px] font-medium ${selected ? "text-forest-800" : "text-ink-900"}`}>
              {opt.label}
            </span>
            <span className="text-[11px] text-ink-300">{opt.rangeKm} km range</span>
          </button>
        );
      })}
    </div>
  );
}
