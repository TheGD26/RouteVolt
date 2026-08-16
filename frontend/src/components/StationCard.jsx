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

  return (
    <div className="station-card">
      <div className="station-card-header">
        {rank != null && <span className="station-rank">#{rank}</span>}
        <h4>{stationName || `Station ${stationId}`}</h4>
      </div>

      {address && <p className="station-address">{address}</p>}

      <div className="station-stats">
        <span>Wait: {expectedWaitMinutes ?? 0} min</span>
        {queueLength > 0 && <span>Queue: {queueLength}</span>}
        {etaMinutes != null && (
          <span>
            ETA: {etaMinutes} min {trafficAware ? "(live traffic)" : "(estimated)"}
          </span>
        )}
        {confidence && (
          <span className="confidence-badge">{CONFIDENCE_LABEL[confidence] || confidence}</span>
        )}
      </div>

      {stationId != null && (
        <div className="station-actions">
          <button type="button" onClick={() => onReport(stationId, "busy")}>
            Report as Busy
          </button>
          <button type="button" onClick={() => onReport(stationId, "free")}>
            Report as Free
          </button>
          {reportState && <span className="report-feedback">{reportState}</span>}
        </div>
      )}
    </div>
  );
}
