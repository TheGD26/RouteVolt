import { IconBell, IconSearch, IconUser } from "./icons";

export default function Topbar() {
  return (
    <header className="flex items-center gap-4 border-b border-cream-300 bg-cream-50 px-6 py-3.5">
      <div className="relative w-full max-w-sm">
        <IconSearch className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-300" />
        <input
          type="text"
          placeholder="Search stations, routes..."
          className="w-full rounded-lg border border-cream-300 bg-cream-100 py-2 pl-9 pr-3 text-sm text-ink-900 placeholder:text-ink-300 outline-none transition-colors focus:border-forest-500 focus:bg-white focus:ring-2 focus:ring-forest-100"
        />
      </div>

      <div className="ml-auto flex items-center gap-3">
        <button
          type="button"
          aria-label="Notifications"
          className="relative flex h-9 w-9 items-center justify-center rounded-lg text-ink-500 transition-colors hover:bg-cream-200 hover:text-ink-900"
        >
          <IconBell className="h-[18px] w-[18px]" />
          <span className="absolute right-2 top-2 h-1.5 w-1.5 rounded-full bg-forest-600" />
        </button>

        <div className="flex items-center gap-2.5 rounded-lg py-1 pl-1 pr-2">
          <span className="flex h-8 w-8 items-center justify-center rounded-full bg-forest-100 text-forest-700">
            <IconUser className="h-4 w-4" />
          </span>
          <div className="hidden leading-tight sm:block">
            <p className="text-sm font-medium text-ink-900">Guest Driver</p>
            <p className="text-[11px] text-ink-300">Free Plan</p>
          </div>
        </div>
      </div>
    </header>
  );
}
