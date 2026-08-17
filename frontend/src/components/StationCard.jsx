const CONFIDENCE_LABEL = {
  heuristic: "Heuristic",
  blended: "Blended",
  crowdsourced: "Crowdsourced",
};

export default function StationCard({ station, rank, onReport, reportState }) {
  const {
    stationId,
    stationName,
    address,
    expectedWaitMinutes,
    queueLength,
    confidence,
    etaMinutes,
    trafficAware,
  } = station;

  const busy = queueLength > 0;

  return (
    <div className="rounded-lg border border-cream-300 bg-cream-50 p-3">
      <div className="flex items-center gap-2">
        {rank != null && (
          <span className="rounded-full bg-forest-100 px-2 py-0.5 text-[10px] font-bold text-forest-700">
            #{rank}
          </span>
        )}
        <h4 className="truncate text-[13px] font-semibold text-ink-900">
          {stationName || `Station ${stationId}`}
        </h4>
        <span
          className={`ml-auto shrink-0 rounded-full px-2 py-0.5 text-[10px] font-medium ${
            busy ? "bg-clay-100 text-clay-600" : "bg-forest-50 text-forest-700"
          }`}
        >
          {busy ? "Busy" : "Available"}
        </span>
      </div>

      {address && <p className="mt-1 text-[11px] text-ink-500">{address}</p>}

      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-ink-700">
        <span>Wait: {expectedWaitMinutes ?? 0} min</span>
        {queueLength > 0 && <span>Queue: {queueLength}</span>}
        {etaMinutes != null && (
          <span>
            ETA: {etaMinutes} min {trafficAware ? "(live traffic)" : "(estimated)"}
          </span>
        )}
        {confidence && (
          <span className="rounded-full bg-slate-100 px-1.5 py-0.5 text-slate-600">
            {CONFIDENCE_LABEL[confidence] || confidence}
          </span>
        )}
      </div>

      {stationId != null && (
        <div className="mt-2.5 flex items-center gap-2">
          <button
            type="button"
            onClick={() => onReport(stationId, "busy")}
            className="rounded-md border border-cream-300 bg-white px-2.5 py-1 text-[11px] font-medium text-ink-700 transition-colors hover:border-clay-600 hover:text-clay-600"
          >
            Report Busy
          </button>
          <button
            type="button"
            onClick={() => onReport(stationId, "free")}
            className="rounded-md border border-cream-300 bg-white px-2.5 py-1 text-[11px] font-medium text-ink-700 transition-colors hover:border-forest-600 hover:text-forest-700"
          >
            Report Free
          </button>
          {reportState && <span className="text-[11px] text-forest-700">{reportState}</span>}
        </div>
      )}
    </div>
  );
}
