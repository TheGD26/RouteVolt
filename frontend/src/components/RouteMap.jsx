import { useEffect, useRef } from "react";
import { OlaMaps } from "olamaps-web-sdk";
// No separate maplibre-gl.css import: olamaps-web-sdk isn't built on the
// public maplibre-gl package (it's not even a listed dependency) -- it
// bundles its own copy internally and self-injects the equivalent
// stylesheet (.maplibregl-map/-marker/-popup/-ctrl rules) as a <style> tag
// the moment the module is imported.

// Mirrors the Tailwind theme tokens in index.css (forest-700/forest-900/
// clay-600) -- kept as literal hex here since MapLibre paint properties and
// inline marker HTML can't reference CSS custom properties.
const ROUTE_COLOR = "#235a3a";
const ORIGIN_COLOR = "#163a26";
const DESTINATION_COLOR = "#a8503f";

// Ola Maps' vector style JSON, confirmed against the official
// olamaps-web-sdk source (dist/OlaMaps-*.js) -- the SDK's init() appends
// api_key to every tile/style/glyph request itself via transformRequest,
// so the key only needs to be passed once, to the OlaMaps constructor.
const MAP_STYLE_URL = "https://api.olamaps.io/tiles/vector/v1/styles/default-light-standard/style.json";
const OLA_MAPS_API_KEY = import.meta.env.VITE_OLA_MAPS_API_KEY;

function markerElement(html) {
  const el = document.createElement("div");
  el.innerHTML = html;
  return el.firstElementChild;
}

function originElementHtml() {
  return `<div style="width:14px;height:14px;border-radius:50%;background:${ORIGIN_COLOR};border:2px solid white;box-shadow:0 1px 3px rgba(0,0,0,0.35);"></div>`;
}

function destinationElementHtml() {
  return `<svg width="24" height="32" viewBox="0 0 24 32" xmlns="http://www.w3.org/2000/svg">
      <path d="M12 0C5.4 0 0 5.4 0 12c0 9 12 20 12 20s12-11 12-20c0-6.6-5.4-12-12-12z" fill="${DESTINATION_COLOR}"/>
      <circle cx="12" cy="12" r="4.5" fill="white"/>
    </svg>`;
}

function stopElementHtml() {
  return `<div style="width:22px;height:22px;border-radius:50%;background:${ROUTE_COLOR};display:flex;align-items:center;justify-content:center;border:2px solid white;box-shadow:0 1px 3px rgba(0,0,0,0.35);">
      <svg width="12" height="12" viewBox="0 0 24 24" fill="white"><path d="M13 2 3 14h7l-1 8 10-12h-7l1-8z"/></svg>
    </div>`;
}

function findStopAlternative(stop) {
  return stop.alternatives.find((alt) => alt.station_id === stop.station_id) || stop.alternatives[0];
}

// MapLibre (and therefore Ola Maps' web SDK, which wraps it) takes
// coordinates as [lng, lat] everywhere -- backend legs/points are (lat, lng)
// tuples, so every point crossing that boundary gets flipped here.
function toLngLat([lat, lng]) {
  return [lng, lat];
}

export default function RouteMap({ route }) {
  const containerRef = useRef(null);

  const legs = route.legs || [];
  const stops = route.charging_plan || [];

  useEffect(() => {
    if (!containerRef.current || legs.length === 0) return undefined;
    if (!OLA_MAPS_API_KEY) return undefined;

    let map;
    let cancelled = false;

    const olaMaps = new OlaMaps({ apiKey: OLA_MAPS_API_KEY });

    const origin = legs[0].start;
    const destination = legs[legs.length - 1].end;

    olaMaps
      .init({
        style: MAP_STYLE_URL,
        container: containerRef.current,
        center: toLngLat(origin),
        zoom: 7,
      })
      .then((initializedMap) => {
        if (cancelled) {
          initializedMap.remove();
          return;
        }
        map = initializedMap;
        map.scrollZoom.disable(); // don't trap page scroll inside the card
        map.addControl(olaMaps.addNavigationControls({ visualizePitch: false }), "top-right");

        map.on("load", () => {
          const lineCoordinates = legs.flatMap((leg) =>
            (leg.points && leg.points.length > 0 ? leg.points : [leg.start, leg.end]).map(toLngLat)
          );

          map.addSource("route-line", {
            type: "geojson",
            data: { type: "Feature", geometry: { type: "LineString", coordinates: lineCoordinates }, properties: {} },
          });
          map.addLayer({
            id: "route-line",
            type: "line",
            source: "route-line",
            layout: { "line-join": "round", "line-cap": "round" },
            paint: { "line-color": ROUTE_COLOR, "line-width": 4, "line-opacity": 0.85 },
          });

          olaMaps.addMarker({ element: markerElement(originElementHtml()) }).setLngLat(toLngLat(origin)).addTo(map);
          olaMaps
            .addMarker({ element: markerElement(destinationElementHtml()) })
            .setLngLat(toLngLat(destination))
            .addTo(map);

          stops.forEach((stop) => {
            const alt = findStopAlternative(stop);
            const popup = olaMaps.addPopup({ offset: 18 }).setHTML(
              `<strong>${stop.station_name || "Charging stop"}</strong><br/>
               Detour: ${alt?.detour_km ?? "—"} km<br/>
               Expected wait: ${stop.expected_wait_minutes ?? 0} min`
            );
            olaMaps
              .addMarker({ element: markerElement(stopElementHtml()) })
              .setLngLat([stop.location.lng, stop.location.lat])
              .setPopup(popup)
              .addTo(map);
          });

          // Fit to every leg's start/end point (cheap and enough to frame
          // the whole route without walking the full polyline geometry).
          // Built as a plain [[west,south],[east,north]] box -- fitBounds
          // accepts that directly, no LngLatBounds class needed (the SDK
          // doesn't expose maplibre-gl's classes as a public import).
          const boundaryPoints = legs.flatMap((leg) => [leg.start, leg.end].filter(Boolean).map(toLngLat));
          const lngs = boundaryPoints.map(([lng]) => lng);
          const lats = boundaryPoints.map(([, lat]) => lat);
          if (boundaryPoints.length > 0) {
            map.fitBounds(
              [
                [Math.min(...lngs), Math.min(...lats)],
                [Math.max(...lngs), Math.max(...lats)],
              ],
              { padding: 28 }
            );
          }
        });
      });

    return () => {
      cancelled = true;
      map?.remove();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [route]);

  if (legs.length === 0) return null;

  if (!OLA_MAPS_API_KEY) {
    return (
      <div className="flex h-full w-full items-center justify-center bg-cream-200 text-[13px] text-ink-300">
        Set VITE_OLA_MAPS_API_KEY to render the route map.
      </div>
    );
  }

  return <div ref={containerRef} className="h-full w-full" />;
}
