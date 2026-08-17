const MIN = 5;
const MAX = 100;

export default function BatterySlider({ value, onChange }) {
  const pct = ((value - MIN) / (MAX - MIN)) * 100;

  return (
    <div>
      <div className="mb-2 flex items-center justify-between">
        <span className="text-[11px] font-medium uppercase tracking-wider text-ink-500">
          Current Battery
        </span>
        <span className="text-sm font-semibold text-forest-700">{value}%</span>
      </div>

      <input
        type="range"
        className="battery-range"
        min={MIN}
        max={MAX}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        style={{ "--range-fill": `${pct}%` }}
      />

      <div className="mt-1.5 flex justify-between text-[11px] text-ink-300">
        <span>{MIN}%</span>
        <span>{MAX}%</span>
      </div>
    </div>
  );
}
