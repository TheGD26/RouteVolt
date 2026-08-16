export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

async function request(path, options) {
  const res = await fetch(`${API_BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed: ${res.status}`);
  }
  return res.json();
}

export function optimizeRoute(payload) {
  return request("/route/optimize", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function reportStationStatus(stationId, reportedStatus) {
  return request(`/stations/${stationId}/report`, {
    method: "POST",
    body: JSON.stringify({ station_id: String(stationId), reported_status: reportedStatus }),
  });
}

export function getStationWaitEstimate(stationId) {
  return request(`/stations/${stationId}/wait-estimate`);
}
