import { useMemo } from "react";
import {
  CategoryScale,
  Chart as ChartJS,
  Filler,
  LinearScale,
  LineElement,
  PointElement,
  Tooltip,
} from "chart.js";
import { Line } from "react-chartjs-2";

ChartJS.register(CategoryScale, LinearScale, PointElement, LineElement, Filler, Tooltip);

// Mirrors battery_simulator.py's BATTERY_FLOOR_PCT / BATTERY_CEILING_PCT --
// hardcoded here (not fetched) since the API response carries per-stop
// charge_to_percent but not these band constants themselves. Keep in sync
// by hand if those change on the backend.
const BATTERY_FLOOR_PCT = 20;
const BATTERY_CEILING_PCT = 80;

const LINE_COLOR = "#2952a3"; // existing blue token (see .confidence-badge in App.css)
const FILL_COLOR = "rgba(41, 82, 163, 0.08)";
const STOP_COLOR = "#1a7f37"; // app's existing green accent
const ARRIVAL_COLOR = "#999999";
const REFERENCE_COLOR = "#bbbbbb";

function round1(n) {
  return Math.round(n * 10) / 10;
}

/**
 * Walks legs in order accumulating distance_km, plotting battery_pct_after
 * per leg. Wherever a charging stop falls before leg[stop.leg_index], two
 * points are inserted at the same x (cumulative distance at that point on
 * the route): the arrival battery (dip) and charge_to_percent (jump back
 * up) -- so the line visibly drops then jumps at each stop instead of
 * silently interpolating across it.
 */
function buildSeries(route) {
  const legs = route.legs || [];
  const stopsByLegIndex = new Map((route.charging_plan || []).map((stop) => [stop.leg_index, stop]));

  const points = [];
  const pointRadius = [];
  const pointColors = [];

  let cumulativeKm = 0;
  for (let i = 0; i < legs.length; i++) {
    const stop = stopsByLegIndex.get(i);
    if (stop) {
      const x = round1(cumulativeKm);
      points.push({ x, y: stop.battery_pct_on_arrival });
      pointRadius.push(3);
      pointColors.push(ARRIVAL_COLOR);

      points.push({ x, y: stop.charge_to_percent });
      pointRadius.push(5);
      pointColors.push(STOP_COLOR);
    }

    cumulativeKm += legs[i].distance_km || 0;
    points.push({ x: round1(cumulativeKm), y: legs[i].battery_pct_after });
    pointRadius.push(0);
    pointColors.push(LINE_COLOR);
  }

  return { points, pointRadius, pointColors, totalKm: cumulativeKm };
}

export default function BatteryChart({ route }) {
  const { points, pointRadius, pointColors, totalKm } = useMemo(() => buildSeries(route), [route]);

  if (points.length === 0) return null;

  const data = {
    datasets: [
      {
        label: "Battery",
        data: points,
        borderColor: LINE_COLOR,
        backgroundColor: FILL_COLOR,
        fill: true,
        tension: 0.15,
        pointRadius,
        pointBackgroundColor: pointColors,
        pointBorderColor: "white",
        pointBorderWidth: 1,
        borderWidth: 2,
      },
      {
        label: "Ceiling",
        data: [
          { x: 0, y: BATTERY_CEILING_PCT },
          { x: totalKm, y: BATTERY_CEILING_PCT },
        ],
        borderColor: REFERENCE_COLOR,
        borderDash: [5, 4],
        borderWidth: 1,
        pointRadius: 0,
        fill: false,
      },
      {
        label: "Floor",
        data: [
          { x: 0, y: BATTERY_FLOOR_PCT },
          { x: totalKm, y: BATTERY_FLOOR_PCT },
        ],
        borderColor: REFERENCE_COLOR,
        borderDash: [5, 4],
        borderWidth: 1,
        pointRadius: 0,
        fill: false,
      },
    ],
  };

  const options = {
    responsive: true,
    maintainAspectRatio: false,
    animation: false,
    interaction: { mode: "nearest", intersect: false },
    plugins: {
      legend: { display: false },
      tooltip: {
        filter: (item) => item.datasetIndex === 0,
        callbacks: {
          title: (items) => `${items[0].parsed.x} km`,
          label: (item) => `${item.parsed.y}% battery`,
        },
      },
    },
    scales: {
      x: {
        type: "linear",
        title: { display: true, text: "Distance (km)", color: "#555", font: { size: 11 } },
        grid: { color: "#f0f0f0" },
        ticks: { color: "#555" },
      },
      y: {
        min: 0,
        max: 100,
        title: { display: true, text: "Battery %", color: "#555", font: { size: 11 } },
        grid: { color: "#f0f0f0" },
        ticks: { color: "#555" },
      },
    },
  };

  return (
    <div className="battery-chart-card">
      <Line data={data} options={options} />
    </div>
  );
}
