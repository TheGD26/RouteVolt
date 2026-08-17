import BatterySlider from "./BatterySlider";
import { IconMapPin, IconNavigation, IconRoute } from "./icons";
import LoadStateToggle from "./LoadStateToggle";
import LocationField from "./LocationField";
import PreferenceToggle from "./PreferenceToggle";
import VehicleSelector from "./VehicleSelector";

export default function RouteForm({ form, onChange, preference, onPreferenceChange, onSubmit, loading }) {
  function set(field) {
    return (val) => onChange({ ...form, [field]: val });
  }

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-5">
      <div className="flex items-center gap-2.5">
        <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-forest-100 text-forest-700">
          <IconRoute className="h-[18px] w-[18px]" />
        </span>
        <div>
          <h1 className="text-[17px] font-semibold tracking-tight text-ink-900">Dynamic EV Route Planner</h1>
          <p className="text-[12px] text-ink-500">Real-time driving routes, geocoding &amp; AI charging optimization</p>
        </div>
      </div>

      <div className="flex flex-col gap-2.5">
        <LocationField
          icon={IconMapPin}
          iconClassName="text-forest-600"
          placeholder="Current location, e.g. Bengaluru, Karnataka"
          value={form.current_location}
          onChange={set("current_location")}
          required
        />
        <LocationField
          icon={IconNavigation}
          iconClassName="text-clay-600"
          placeholder="Destination, e.g. Chennai, Tamil Nadu"
          value={form.destination}
          onChange={set("destination")}
          required
        />
      </div>

      <div>
        <span className="mb-2 block text-[11px] font-medium uppercase tracking-wider text-ink-500">
          EV Model
        </span>
        <VehicleSelector value={form.vehicle_profile} onChange={set("vehicle_profile")} />
      </div>

      <LoadStateToggle value={form.load_state} onChange={set("load_state")} />

      <BatterySlider
        value={form.battery_percentage}
        onChange={(v) => onChange({ ...form, battery_percentage: v })}
      />

      <PreferenceToggle value={preference} onChange={onPreferenceChange} />

      <button
        type="submit"
        disabled={loading}
        className="flex items-center justify-center gap-2 rounded-lg bg-forest-700 px-4 py-3 text-sm font-semibold text-white shadow-sm shadow-forest-900/20 transition-colors hover:bg-forest-800 disabled:cursor-not-allowed disabled:opacity-60"
      >
        {loading ? "Calculating Route..." : "Calculate Dynamic Route"}
      </button>
    </form>
  );
}
