const LOAD_STATES = [
  { value: "unladen", label: "Unladen" },
  { value: "half_load", label: "Half load" },
  { value: "full_load", label: "Full load" },
];

export default function LoadStateToggle({ value, onChange }) {
  return (
    <div>
      <span className="mb-1.5 block text-[11px] font-medium uppercase tracking-wider text-ink-500">
        Vehicle Load
      </span>
      <div className="flex rounded-lg border border-cream-300 bg-cream-100 p-0.5">
        {LOAD_STATES.map((opt) => {
          const selected = opt.value === value;
          return (
            <button
              key={opt.value}
              type="button"
              onClick={() => onChange(opt.value)}
              aria-pressed={selected}
              className={`flex-1 rounded-md px-2 py-1.5 text-[12px] font-medium transition-colors ${
                selected ? "bg-white text-forest-800 shadow-sm" : "text-ink-500 hover:text-ink-900"
              }`}
            >
              {opt.label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
