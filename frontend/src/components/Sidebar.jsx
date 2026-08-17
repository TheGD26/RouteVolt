import {
  IconAnalytics,
  IconBolt,
  IconBookmark,
  IconDashboard,
  IconRoute,
  IconSettings,
  IconShield,
  IconStation,
  IconUser,
} from "./icons";

const NAV_ITEMS = [
  { icon: IconDashboard, label: "Dashboard" },
  { icon: IconRoute, label: "Route Planner", active: true },
  { icon: IconStation, label: "Stations" },
  { icon: IconAnalytics, label: "Analytics" },
  { icon: IconBookmark, label: "Saved Trips" },
  { icon: IconUser, label: "Profile" },
  { icon: IconSettings, label: "Settings" },
];

export default function Sidebar() {
  return (
    <aside className="hidden lg:flex w-64 shrink-0 flex-col bg-forest-900 text-forest-50">
      <div className="flex items-center gap-2.5 px-6 py-6">
        <span className="flex h-9 w-9 items-center justify-center rounded-xl bg-forest-50/10 text-forest-100">
          <IconBolt className="w-5 h-5" />
        </span>
        <div className="leading-tight">
          <p className="text-[15px] font-semibold tracking-tight text-white">RouteVolt</p>
          <p className="text-[11px] uppercase tracking-wide text-forest-200/70">AI-Powered EV</p>
        </div>
      </div>

      <nav className="mt-2 flex-1 px-3">
        <p className="px-3 pb-2 text-[11px] font-medium uppercase tracking-wider text-forest-200/50">
          Navigation
        </p>
        <ul className="flex flex-col gap-0.5">
          {NAV_ITEMS.map(({ icon: ItemIcon, label, active }) => (
            <li key={label}>
              <button
                type="button"
                className={`flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-colors ${
                  active
                    ? "bg-forest-50 text-forest-900 font-medium shadow-sm"
                    : "text-forest-100/80 hover:bg-white/5 hover:text-white"
                }`}
              >
                <ItemIcon className="w-[18px] h-[18px]" />
                {label}
              </button>
            </li>
          ))}
        </ul>

        <p className="mt-6 px-3 pb-2 text-[11px] font-medium uppercase tracking-wider text-forest-200/50">
          Admin
        </p>
        <ul className="flex flex-col gap-0.5">
          <li>
            <button
              type="button"
              className="flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm text-forest-100/80 transition-colors hover:bg-white/5 hover:text-white"
            >
              <IconShield className="w-[18px] h-[18px]" />
              Admin Panel
            </button>
          </li>
        </ul>
      </nav>
    </aside>
  );
}
