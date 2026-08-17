import { IconClose } from "./icons";

export default function LocationField({ icon: FieldIcon, iconClassName, placeholder, value, onChange, required }) {
  return (
    <div className="relative">
      <span className={`pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 ${iconClassName}`}>
        <FieldIcon className="h-4 w-4" />
      </span>
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        required={required}
        className="w-full rounded-lg border border-cream-300 bg-white py-2.5 pl-9 pr-9 text-sm text-ink-900 placeholder:text-ink-300 outline-none transition-colors focus:border-forest-500 focus:ring-2 focus:ring-forest-100"
      />
      {value && (
        <button
          type="button"
          aria-label="Clear"
          onClick={() => onChange("")}
          className="absolute right-2.5 top-1/2 flex h-5 w-5 -translate-y-1/2 items-center justify-center rounded-full text-ink-300 transition-colors hover:bg-cream-200 hover:text-ink-700"
        >
          <IconClose className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  );
}
