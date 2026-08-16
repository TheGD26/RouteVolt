const VEHICLE_PROFILES = [
  { value: "small_ev", label: "Small EV" },
  { value: "mid_ev", label: "Mid EV" },
  { value: "large_ev", label: "Large EV" },
];

const LOAD_STATES = [
  { value: "unladen", label: "Unladen" },
  { value: "half_load", label: "Half load" },
  { value: "full_load", label: "Full load" },
];

export default function RouteForm({ form, onChange, onSubmit, loading }) {
  function handleChange(field) {
    return (e) => onChange({ ...form, [field]: e.target.value });
  }

  return (
    <form className="route-form" onSubmit={onSubmit}>
      <div className="field">
        <label htmlFor="current_location">Current location</label>
        <input
          id="current_location"
          type="text"
          value={form.current_location}
          onChange={handleChange("current_location")}
          placeholder="e.g. Chennai"
          required
        />
      </div>

      <div className="field">
        <label htmlFor="destination">Destination</label>
        <input
          id="destination"
          type="text"
          value={form.destination}
          onChange={handleChange("destination")}
          placeholder="e.g. Coimbatore"
          required
        />
      </div>

      <div className="field">
        <label htmlFor="battery_percentage">Battery %</label>
        <input
          id="battery_percentage"
          type="number"
          min="0"
          max="100"
          value={form.battery_percentage}
          onChange={handleChange("battery_percentage")}
          required
        />
      </div>

      <div className="field">
        <label htmlFor="vehicle_profile">Vehicle profile</label>
        <select
          id="vehicle_profile"
          value={form.vehicle_profile}
          onChange={handleChange("vehicle_profile")}
        >
          {VEHICLE_PROFILES.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      </div>

      <div className="field">
        <label htmlFor="load_state">Load state</label>
        <select id="load_state" value={form.load_state} onChange={handleChange("load_state")}>
          {LOAD_STATES.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      </div>

      <button type="submit" disabled={loading}>
        {loading ? "Planning route..." : "Plan route"}
      </button>
    </form>
  );
}
