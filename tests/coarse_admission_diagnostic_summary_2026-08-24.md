# RouteVolt — Coarse admission check diagnostic, 12 finding-(2) segments

## Part 1 — admission check logic (`graph_planner.py:561-569`, unchanged since last read)

```python
    edges: dict[int, list[tuple[int, float, float]]] = {}
    cursor = 0
    for (i, j), count in zip(pair_ij, pair_sub_leg_counts):
        energy_kwh = sum(sub_leg_energies_kwh[cursor:cursor + count])
        cursor += count
        if energy_kwh <= usable_kwh_at_ceiling:
            edges.setdefault(i, []).append((j, energy_kwh, energy_kwh))
        elif energy_kwh <= usable_kwh_at_full_charge:
            edges.setdefault(i, []).append((j, energy_kwh + EXTENDED_CHARGE_PENALTY_KWH, energy_kwh))
```

Three outcomes, one comparison, two fixed thresholds (`graph_planner.py:550-553`):

- **`admitted_normal`**: `energy_kwh ≤ usable_kwh_at_ceiling` (mid_ev: 30.40 kWh) → edge added, Dijkstra cost = real kWh, no penalty.
- **`admitted_bridged`**: `usable_kwh_at_ceiling < energy_kwh ≤ usable_kwh_at_full_charge` (38.00 kWh) → still added, Dijkstra cost = real kWh + `EXTENDED_CHARGE_PENALTY_KWH` (8.0). "Bridge" is nothing more than a fixed cost penalty on an otherwise-normal edge — it doesn't alter routing, distance, or real energy.
- **`rejected`**: `energy_kwh > usable_kwh_at_full_charge` → no `else` exists; the pair is silently never added to `edges`, full stop.

## Part 2 — real recomputation for all 24 edges (12 segments × A→B, B→C)

Approved and run: 24 real `get_route` calls + real per-sub-leg traffic (mirroring `build_station_graph`'s Stage 2 exactly, `graph_planner.py:296-335` + `:514-542`). Wall-clock: **51.2s**. mid_ev thresholds: `usable_kwh_at_ceiling=30.40 kWh`, `usable_kwh_at_full_charge=38.00 kWh`.

| Stop | A | B (best candidate) | C | A→B | B→C |
|---|---|---|---|---|---|
| 6 | MM Legacy | Sheraton Grand Chennai Resort | Mamallapuram Radisson Blu | **admitted_normal** (20.25 kWh) | **admitted_normal** (1.05 kWh) |
| 7 | Mamallapuram Radisson Blu | Sheraton Grand Chennai Resort | Silavattam Village | **admitted_normal** (1.37 kWh) | **admitted_normal** (22.52 kWh) |
| 14 | Cuddalore Argunt Aggregates | Virudhachalam Hotel Shree Bhawan | IOCL Raja Traders | **admitted_normal** (12.33 kWh) | **admitted_normal** (5.28 kWh) |
| 15 | IOCL Raja Traders | RAS Residency Auto Nagar | Quality Inn Viha | **admitted_normal** (27.35 kWh) | **admitted_normal** (0.0 kWh) |
| 16 | Quality Inn Viha | RAS Residency Auto Nagar | Kumbakonam Cholaa Dynasty | **admitted_normal** (0.0 kWh) | **admitted_normal** (4.22 kWh) |
| 17 | Kumbakonam Cholaa Dynasty | Jayaraj Karz Vallam Pudur Sethi | Trichy Hotel Balaji Ariyas | **admitted_normal** (19.75 kWh) | **admitted_normal** (6.23 kWh) |
| 20 | Kaveri Garage Piratiyur | Ather-Kaveri Tata (Piratiyur) | IOCL Sugan Tradings | **admitted_normal** (0.51 kWh) | **admitted_normal** (8.00 kWh) |
| 21 | IOCL Sugan Tradings | Hotel Arun Cafe | Melur Zeon X A.N Energy | **admitted_normal** (0.83 kWh) | **admitted_normal** (23.40 kWh) |
| 25 | Ather-TRZ Virudhunagar | IOCL Annai Saraswathi | Ather-TRZ Sivakasi | **admitted_normal** (0.0 kWh) | **admitted_normal** (11.06 kWh) |
| 26 | Ather-TRZ Sivakasi | IOCL Thangamani | IOCL Rajam Agency | **admitted_normal** (0.0 kWh) | **admitted_normal** (21.16 kWh) |
| 27 | IOCL Rajam Agency | HPCL Valliammai | IOCL GSM | **admitted_normal** (0.0 kWh) | **admitted_normal** (17.27 kWh) |
| 28 | IOCL GSM | IOCL St Thomas | Nagercoil Thovalai Carnival City | **admitted_normal** (29.47 kWh) | **admitted_normal** (4.02 kWh) |

**All 24 of 24 edges: `admitted_normal`.** Not one was bridged, not one was rejected. Several A→B/B→C pairs share identical coordinates with an already-used station on the chosen path (e.g. stop 15/16's B = RAS Residency Auto Nagar sits at the exact same coordinates as stop 16's own station Quality Inn Viha, giving a 0.0 km / 0.0 kWh edge) — these are genuinely trivial, essentially-free edges, not near-misses.

## Conclusion

For all 12 finding-(2) segments, the coarse admission check at `graph_planner.py:561-569` is **not** the thing removing the split option — every single A→B and B→C edge that would make up the split comfortably clears the check as a normal, non-penalized edge, well under the 30.40 kWh ceiling budget in every case. Combined with the earlier fan-out cap diagnostic (these same B candidates already confirmed inside the real top-8 by driving distance) and the ground-truth read (Dijkstra runs textbook relaxation, no special-casing), this means: **the split option is fully present and available in the built graph for all 12 segments, and Dijkstra's own coarse-cost minimization chose the direct A→C edge instead** — because the direct edge's coarse estimate (also `admitted_normal` at graph-build time, that's precisely why `exceeded_ceiling` only fires later, in the fine-grained simulation) looked cheaper in total kWh than the two-hop A→B→C alternative. Nothing in the admission check, the fan-out cap, or Dijkstra's algorithm itself blocked the split — the coarse-vs-fine energy model disagreement on the *direct* edge is what left it looking preferable when the graph was built.
