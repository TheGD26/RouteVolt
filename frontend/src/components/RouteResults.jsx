import RouteMap from "./RouteMap";
import RouteSummaryBar from "./RouteSummaryBar";

export default function RouteResults({ result, selectedRouteIdx, onSelectRoute }) {
  const route = result.routes[selectedRouteIdx];

  return (
    <div className="relative h-full w-full">
      <RouteMap route={route} />

      {result.routes.length > 1 && (
        <div className="absolute left-3 top-3 flex gap-1.5 rounded-full border border-cream-300 bg-white/95 p-1 shadow-md backdrop-blur-sm sm:left-4 sm:top-4">
          {result.routes.map((r, idx) => (
            <button
              key={r.label}
              type="button"
              onClick={() => onSelectRoute(idx)}
              className={`rounded-full px-3 py-1.5 text-[12px] font-medium transition-colors ${
                idx === selectedRouteIdx
                  ? "bg-forest-700 text-white shadow-sm"
                  : "text-ink-700 hover:bg-cream-200"
              }`}
            >
              {r.label}
            </button>
          ))}
        </div>
      )}

      <RouteSummaryBar route={route} />
    </div>
  );
}
