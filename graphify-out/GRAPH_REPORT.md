# Graph Report - Routevolt  (2026-10-24)

## Corpus Check
- 33 files · ~919,227 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1014 nodes · 2365 edges · 51 communities (44 shown, 7 thin omitted)
- Extraction: 96% EXTRACTED · 4% INFERRED · 0% AMBIGUOUS · INFERRED: 86 edges (avg confidence: 0.86)
- Token cost: 269,696 input · 0 output

## Community Hubs (Navigation)
- Routing Service (OSRM/Ola/Google)
- Route API, Traffic & Schemas
- Station Graph Planner
- Battery Sim & Route Optimizer
- Margin & Health-Check Reports
- Frontend Dependencies
- Diagnostic Script Imports
- Edge Fan-out & Range Limits
- UI Icon Set
- Fan-out Reconstruction Diagnostics
- SFIC Concept Note
- Occupancy & Wait Estimation
- Database & Station Seeding
- Model Training Pipeline
- ML Pipeline Notes
- Frontend API & Queue Demo
- Synthetic Trip Generation
- Stations API Endpoints
- Feature Map & Leakage
- Route Map UI
- Config & Dataset Split
- Leg Traffic & Elevation
- Energy Prediction
- Audit Fixes 1-5
- Station Data Sources & Deps
- App Shell & Battery Chart
- Data Validation Checks
- Master Map & Big Picture
- Feature Relationships & Targets
- Battery Simulator Tests
- Route Form Controls
- Charging Stations Summary UI
- EV Physics Formula
- Journey Simulation & Elevation
- Trip Dataset Schema
- Stop Rerouting
- Model Bake-off
- README Feature Overview
- Demo Fixture Capture
- File Inventory & Layers
- Reservation Queue Check
- Live Inputs (Elevation/Traffic)
- Connector Filter & Dijkstra
- Vite Template Icons
- FastAPI App Entry
- Synthetic Capacity Generator
- Lint Config
- Occupancy Blend Check
- Noise & Formula Leakage
- Favicon
- Python Project Metadata

## God Nodes (most connected - your core abstractions)
1. `plan_journey_via_graph()` - 30 edges
2. `RouteVolt_ML_Pipeline` - 30 edges
3. `Icon()` - 26 edges
4. `estimate_total_mass_kg()` - 23 edges
5. `estimate_leg_energy_kwh_batch()` - 22 edges
6. `build_station_graph()` - 21 edges
7. `RouteVolt README` - 20 edges
8. `get_route()` - 19 edges
9. `haversine_km()` - 18 edges
10. `calculate_best_route()` - 18 edges

## Surprising Connections (you probably didn't know these)
- `compute_reachable_to_destination backward-DP check (ruled out)` --references--> `compute_reachable_to_destination()`  [EXTRACTED]
  tests/stop21_structural_diagnostic_2026-08-24.md → backend/app/services/graph_planner.py
- `Minimum feasible starting battery 8.0% (Chennai-Nagercoil mid_ev)` --references--> `find_minimum_starting_battery()`  [EXTRACTED]
  tests/baseline_rerun_and_health_check_2026-08-24.md → backend/app/services/graph_planner.py
- `find_minimum_starting_battery monotonicity guard holds:false anomaly` --references--> `find_minimum_starting_battery()`  [EXTRACTED]
  tests/baseline_rerun_and_health_check_2026-08-24.md → backend/app/services/graph_planner.py
- `Elevation methodology difference (coarse 2-point vs per-leg sampled geometry)` --references--> `_leg_elevation()`  [EXTRACTED]
  tests/energy_prediction_consistency_2026-08-24.md → backend/app/services/battery_simulator.py
- `BackupStation / ChargingStop.backup_stations API field` --references--> `BackupStation`  [EXTRACTED]
  tests/redundancy_charging_implementation_2026-08-24.md → backend/app/models/schemas.py

## Import Cycles
- None detected.

## Hyperedges (group relationships)
- **RouteVolt Five-stage Planning Pipeline** — routevolt_sfic_concept_note_road_network_routing, routevolt_sfic_concept_note_energy_prediction, routevolt_sfic_concept_note_battery_simulation, routevolt_sfic_concept_note_charging_station_selection, routevolt_sfic_concept_note_capacity_aware_allocation [EXTRACTED 1.00]
- **Routing Data Sources with Fallback** — routevolt_sfic_concept_note_ola_maps, routevolt_sfic_concept_note_osrm, routevolt_sfic_concept_note_google_maps_fallback [EXTRACTED 1.00]
- **Route Optimize Request Pipeline** — readme_ola_maps_geocoding, readme_osrm, docs_project_context_graph_dijkstra_planner, readme_multi_stop_planner, readme_wait_time_estimate, readme_route_optimize_endpoint [EXTRACTED 1.00]
- **Energy Model Live Inputs (elevation, traffic, mass)** — readme_energy_model, readme_elevation_integration, readme_traffic_congestion, dataset_2_dataset_schema_total_mass, readme_per_leg_fallback [INFERRED 0.85]
- **Station Availability Intelligence** — readme_occupancy_blending, readme_crowdsourced_reports, readme_wait_time_estimate, readme_confidence_levels, readme_station_score [EXTRACTED 1.00]
- **RouteVolt Leakage Taxonomy** — obsidian_md_file_from_claude_08___leakage_formula_leakage, obsidian_md_file_from_claude_08___leakage_target_leakage, obsidian_md_file_from_claude_08___leakage_feature_leakage, obsidian_md_file_from_claude_08___leakage_snapshot_leakage, obsidian_md_file_from_claude_08___leakage_generator_leakage [EXTRACTED 1.00]
- **Physics Energy Formula Terms** — obsidian_md_file_from_claude_03___ev_physics_baseline_wh_per_km, obsidian_md_file_from_claude_03___ev_physics_speed_factor, obsidian_md_file_from_claude_03___ev_physics_temp_factor, obsidian_md_file_from_claude_03___ev_physics_traffic_factor, obsidian_md_file_from_claude_03___ev_physics_weather_factor, obsidian_md_file_from_claude_03___ev_physics_elevation_gain_energy, obsidian_md_file_from_claude_03___ev_physics_elevation_loss_regen, obsidian_md_file_from_claude_03___ev_physics_apply_noise [EXTRACTED 1.00]
- **train_model.py Steps 2-10 Evaluation Flow** — obsidian_md_file_from_claude_routevolt_ml_pipeline_run_baselines, obsidian_md_file_from_claude_routevolt_ml_pipeline_run_ablation, obsidian_md_file_from_claude_routevolt_ml_pipeline_run_tree_comparison, obsidian_md_file_from_claude_routevolt_ml_pipeline_run_log_target_wrapper_comparison, obsidian_md_file_from_claude_routevolt_ml_pipeline_run_rf_tuning, obsidian_md_file_from_claude_routevolt_ml_pipeline_run_hgb_tuning, obsidian_md_file_from_claude_routevolt_ml_pipeline_run_hgb_ablation, obsidian_md_file_from_claude_routevolt_ml_pipeline_run_final_test_evaluation, obsidian_md_file_from_claude_routevolt_ml_pipeline_run_permutation_importance [EXTRACTED 1.00]
- **Simulation margin recalibration (Option C) flow** — tests_energy_prediction_consistency_2026_08_24_margin_mechanism_gap_finding, tests_margin_recalibration_options_2026_08_24_option_a_match_build_rate, tests_margin_recalibration_options_2026_08_24_option_b_statistical_cushion, tests_margin_recalibration_options_2026_08_24_option_c_floor_hybrid, tests_margin_recalibration_implementation_2026_08_24_simulation_margin_rate_floor [INFERRED 0.85]
- **Redundancy charging rule evolution top-8 to top-3** — tests_redundancy_charging_impact_diagnostic_2026_08_24_worst_case_top8_redundancy_rule, tests_redundancy_charging_impact_diagnostic_2026_08_24_softening_options, tests_redundancy_cutoff_selection_2026_08_24_top3_cutoff, tests_redundancy_cutoff_selection_2026_08_24_top5_cutoff, tests_redundancy_cutoff_selection_2026_08_24_percentile75_cutoff, tests_redundancy_charging_implementation_2026_08_24_redundancy_neighbor_count [INFERRED 0.85]
- **Station-graph edge pre-filters before Dijkstra** — tests_graph_dijkstra_ground_truth_2026_08_24_filter_candidate_stations_detour_filter, tests_graph_dijkstra_ground_truth_2026_08_24_dag_order_gap_break, tests_graph_dijkstra_ground_truth_2026_08_24_max_edges_per_node_fanout_cap, tests_coarse_admission_diagnostic_summary_2026_08_24_coarse_admission_check [EXTRACTED 1.00]

## Communities (51 total, 7 thin omitted)

### Community 0 - "Routing Service (OSRM/Ola/Google)"
Cohesion: 0.06
Nodes (53): _cumulative_km(), _decode_polyline(), _fallback_single_leg(), _format_latlng(), get_distance_matrix(), _get_distance_matrix_chunked(), _fetch_block_pair(), _get_distance_matrix_google() (+45 more)

### Community 1 - "Route API, Traffic & Schemas"
Cohesion: 0.05
Nodes (46): _demo_fixture(), _demo_manifest(), _demo_manifest_entry(), optimize_route(), reroute_stop(), BackupStation, ChargingStop, LatLng (+38 more)

### Community 2 - "Station Graph Planner"
Cohesion: 0.06
Nodes (29): get_stations(), build_station_graph(), compute_onward_degree(), compute_reachable_to_destination(), filter_candidate_stations(), find_minimum_starting_battery(), check(), find_shortest_path() (+21 more)

### Community 3 - "Battery Sim & Route Optimizer"
Cohesion: 0.08
Nodes (18): _build_stop_entry(), _estimate_charge_duration_min(), _leg_elevation(), _leg_energy_kwh(), plan_charge_to_percent(), _rank_reachable_candidates(), compute_point_to_point_elevation(), _merge_adjacent_stops() (+10 more)

### Community 4 - "Margin & Health-Check Reports"
Cohesion: 0.06
Nodes (41): get_top_n_neighbors(), RouteVolt report: baseline rerun and health check (2026-08-24), exceeded_ceiling fires on 12 of 31 stops, Minimum feasible starting battery 8.0% (Chennai-Nagercoil mid_ev), find_minimum_starting_battery monotonicity guard holds:false anomaly, RouteVolt report: coarse admission diagnostic summary (2026-08-24), EXTENDED_CHARGE_PENALTY_KWH (8.0), Finding: split A->B->C present in graph; Dijkstra preferred direct A->C on coarse cost (+33 more)

### Community 5 - "Frontend Dependencies"
Cohesion: 0.08
Nodes (34): dependencies, chart.js@^4.5.1, olamaps-web-sdk@^1.4.0, react@^19.2.8, react-chartjs-2@^5.3.1, react-dom@^19.2.8, devDependencies, oxlint@^1.75.0 (+26 more)

### Community 6 - "Diagnostic Script Imports"
Cohesion: 0.16
Nodes (9): main(), main(), main(), main(), main(), main(), main(), main() (+1 more)

### Community 7 - "Edge Fan-out & Range Limits"
Cohesion: 0.11
Nodes (15): _fetch_edge_sub_legs(), _vehicle_range_km(), estimate_leg_energy_kwh_batch(), estimate_total_mass_kg(), build_nodes(), gap_prune_and_cap(), main(), main() (+7 more)

### Community 8 - "UI Icon Set"
Cohesion: 0.16
Nodes (25): base, Icon(), IconAnalytics(), IconBell(), IconBolt(), IconBookmark(), IconClock(), IconCompass() (+17 more)

### Community 9 - "Fan-out Reconstruction Diagnostics"
Cohesion: 0.15
Nodes (13): haversine_km(), build_node_list(), candidate_window(), find_node_index(), main(), vehicle_range_km(), candidate_window_indices(), osrm_one_to_many() (+5 more)

### Community 10 - "SFIC Concept Note"
Cohesion: 0.10
Nodes (21): ABRP (A Better Routeplanner), Apple CarPlay / Android Auto Delivery, RouteVolt SFIC Concept Note, Expansion Across India (corridor by corridor), Expected Impact, Google Maps Fallback, Gradient Boosting Energy Model, Implementation Plan (+13 more)

### Community 11 - "Occupancy & Wait Estimation"
Cohesion: 0.14
Nodes (13): OccupancyStatus, _blend_with_recent_reports(), estimate_wait_time(), get_blended_occupancy(), get_queue_state(), _heuristic_occupancy(), _is_highway_corridor(), _live_reservations() (+5 more)

### Community 12 - "Database & Station Seeding"
Cohesion: 0.14
Nodes (8): Base, get_db(), OccupancyReport, Station, _match_key(), reseed_capacity(), _clean(), seed_stations()

### Community 13 - "Model Training Pipeline"
Cohesion: 0.14
Nodes (17): build_linear_preprocessor(), build_preprocessors(), build_tree_preprocessor(), evaluate_model(), evaluate_model_log_target(), evaluate_model_log_target_with_preds(), load_test_data(), run_ablation() (+9 more)

### Community 14 - "ML Pipeline Notes"
Cohesion: 0.15
Nodes (19): build_preprocessors() (linear + tree ColumnTransformers), build_tree_preprocessor(), RouteVolt_ML_Pipeline, download_chargers.py (OpenChargeMap, Chennai), evaluate_model() (5-fold KFold CV), evaluate_model_log_target_with_preds(), Final Model: Tuned HistGradientBoostingRegressor, load_data() (+11 more)

### Community 15 - "Frontend API & Queue Demo"
Cohesion: 0.18
Nodes (18): API_BASE_URL, getStationQueue(), getStationWaitEstimate(), joinStationQueue(), leaveStationQueue(), optimizeRoute(), request(), IconCar() (+10 more)

### Community 16 - "Synthetic Trip Generation"
Cohesion: 0.22
Nodes (13): build_stratified_index(), city_weight(), generate_dataset(), sample_distance(), sample_elevation(), sample_payload(), sample_speed(), sample_temperature() (+5 more)

### Community 17 - "Stations API Endpoints"
Cohesion: 0.20
Nodes (12): get_station_occupancy(), get_station_queue(), get_station_recent_reports(), get_station_wait_estimate(), join_station_queue(), leave_station_queue(), list_stations(), report_station_occupancy() (+4 more)

### Community 18 - "Feature Map & Leakage"
Cohesion: 0.16
Nodes (13): avg_speed_kmh feature, 01 - Feature Map, Station Dataset Features (ports, queue_time_minutes, cost_per_kwh, traffic_index), traffic_congestion_level feature, Vehicle Profile Lookup Table (small/mid/large EV: 24/40/75 kWh, 140/160/190 Wh/km), wh_per_km (secondary/derived target), 08 - Leakage, Feature Leakage (avg_speed timing) (+5 more)

### Community 19 - "Route Map UI"
Cohesion: 0.24
Nodes (13): IconNavigation(), destinationElementHtml(), findStopAlternative(), markerElement(), originElementHtml(), RouteMap(), stopElementHtml(), toLngLat() (+5 more)

### Community 20 - "Config & Dataset Split"
Cohesion: 0.17
Nodes (3): split_dataset(), load_data(), clean_stations()

### Community 21 - "Leg Traffic & Elevation"
Cohesion: 0.17
Nodes (7): _leg_traffic(), compute_leg_elevation(), _downsample_points(), _sub_leg_feature_kwargs(), classify(), main(), real_edge_energy_kwh()

### Community 22 - "Energy Prediction"
Cohesion: 0.24
Nodes (8): _build_trip_features(), predict_energy_kwh(), _capture_warnings(), _check(), _release_warnings(), test_degenerate_leg_prediction_is_never_negative_batch(), test_degenerate_leg_prediction_is_never_negative_single(), test_normal_leg_prediction_is_unaffected_by_the_floor()

### Community 23 - "Audit Fixes 1-5"
Cohesion: 0.24
Nodes (13): Audit Tasks 1-5, Demo Response Marking (precomputed fields), Geocoding Provider Failures -> HTTP 503, GeocodingNotFoundError / GeocodingProviderError, Remove Stale Dindigul/Kumbakonam Fixtures, Audit Offline Verification (test_audit_fixes_1_to_5.py), RouteVolt Project Context, Main Route Flow (geocode -> OSRM -> graph Dijkstra -> battery sim -> wait estimates) (+5 more)

### Community 24 - "Station Data Sources & Deps"
Cohesion: 0.13
Nodes (15): Station Data Sources, Station Data Flow (CSV -> seed -> SQLite -> charger_service), OpenChargeMap, Python Requirements, faker, fastapi, geopy, joblib (+7 more)

### Community 25 - "App Shell & Battery Chart"
Cohesion: 0.24
Nodes (11): reportStationStatus(), App(), handleReport(), handleSubmit(), fastestRouteIndex(), INITIAL_FORM, uniqueStationIds(), BatteryChart() (+3 more)

### Community 26 - "Data Validation Checks"
Cohesion: 0.16
Nodes (13): distance_km feature, Distance-only R^2 Triviality Check (>0.98 = leakage), 06 - Data Validation, U-shaped Relationship Check (LOWESS smoother), Data Validation Checklist (distributions, outliers, correlations, U-shapes), Distance Dominance Problem, 07 - ML Pipeline, Domain-Knowledge Baseline (linear regression on distance + vehicle_profile) (+5 more)

### Community 27 - "Master Map & Big Picture"
Cohesion: 0.21
Nodes (11): RouteVolt Big-Picture Pipeline (features -> physics -> target -> ML -> routing), dataset_schema.md (approved trip dataset design), 00 - RouteVolt Master Map, Route / Charging Decisions (optimize_route stub), ev_station_dataset.csv / charging_stations.csv (real station data), 09 - Physics to Energy Flow, 10 - Correlation vs Causation, Road Type as Confounder for Speed-Energy (+3 more)

### Community 28 - "Feature Relationships & Targets"
Cohesion: 0.18
Nodes (8): 02 - Feature Relationships, Feature Relationship Diagram (A-J arrows), 04 - Target Generation, Target Generation 9-Stage Pipeline, 05 - Synthetic Dataset, Proposed Sampling Dependency Order, Per-Relationship Correlation vs Causation Analysis (6 questions), generate_dataset.py (synthetic target generator)

### Community 29 - "Battery Simulator Tests"
Cohesion: 0.19
Nodes (5): _check(), test_build_stop_entry_charging_required_flag(), test_plan_full_journey_normal_stop_unaffected_by_clamp(), test_plan_full_journey_phantom_stop_battery_clamp(), fake_build_stop_entry()

### Community 30 - "Route Form Controls"
Cohesion: 0.27
Nodes (8): BatterySlider(), IconClose(), IconRoute(), LOAD_STATES, LoadStateToggle(), LocationField(), PreferenceToggle(), RouteForm()

### Community 31 - "Charging Stations Summary UI"
Cohesion: 0.26
Nodes (11): AC_BUCKETS, buildStationView(), ChargingStationsSummary(), computeStats(), DC_BUCKETS, ranked(), STAT_LABELS, stopEta() (+3 more)

### Community 32 - "EV Physics Formula"
Cohesion: 0.33
Nodes (11): BASELINE_WH_PER_KM (baseline vehicle efficiency), 03 - EV Physics, elevation_gain_energy_kwh (mgh potential energy), elevation_loss_regen_kwh (regen credit, eta_regen=0.65), speed_factor (aerodynamic drag v^2 term), temp_factor (U-shaped temperature/HVAC factor), total_energy_kwh (proposed combined formula), traffic_factor (linear congestion penalty) (+3 more)

### Community 33 - "Journey Simulation & Elevation"
Cohesion: 0.20
Nodes (6): plan_full_journey(), _cache_key(), ElevationError, get_elevation_profile(), main(), node_label()

### Community 34 - "Trip Dataset Schema"
Cohesion: 0.26
Nodes (9): Trip Dataset Schema v2, energy_consumed_kwh target, Load State (unladen/half_load/full_load), total_mass_kg (curb weight + payload), Vehicle Profiles (small_ev, mid_ev, large_ev), Energy Dataset and Model Training Pipeline, Mass-aware Energy Model (energy_model.joblib), OSRM Routing (+1 more)

### Community 35 - "Stop Rerouting"
Cohesion: 0.18
Nodes (7): reroute_from_stop(), _station_is_available(), estimate_charge_duration_for_station(), get_station(), join_queue(), _station_power_kw(), _station_to_dict()

### Community 37 - "README Feature Overview"
Cohesion: 0.31
Nodes (8): RouteVolt README, Baseline Rerun and Health Check 2026-08-24, Occupancy Confidence Levels (heuristic/blended/crowdsourced/ml), Crowdsourced Occupancy Reports, Environment Variables (OCM/OSRM/Ola/Google/Elevation/DEMO_MODE), POST /route/reroute-stop, RouteVolt (EV route planner for Tamil Nadu), Stations API (/stations, occupancy, wait-estimate, report)

### Community 38 - "Demo Fixture Capture"
Cohesion: 0.33
Nodes (5): capture(), _load_manifest(), main(), _save_manifest(), _slugify()

### Community 39 - "File Inventory & Layers"
Cohesion: 0.22
Nodes (10): File Inventory, Backend Layer (FastAPI services), Frontend Layer (React/Vite components), Obsidian ML/Physics Notes, Queue/Reservation Demo, RouteVolt Frontend HTML Entry, #root mount loading /src/main.jsx, React + Vite Template README (+2 more)

### Community 40 - "Reservation Queue Check"
Cohesion: 0.42
Nodes (6): main(), _pick_station_with_at_least(), _pick_station_with_no_recent_reports(), verify_queue_ordering(), verify_reserved_tier_and_release(), verify_zero_reservations_unchanged()

### Community 41 - "Live Inputs (Elevation/Traffic)"
Cohesion: 0.25
Nodes (5): Trip Input Features (distance, road_type, elevation, speed, traffic, temp, weather, vehicle), Google Distance Matrix (fallback traffic), In-memory Provider Caching (elevation/traffic), Ola Maps Distance Matrix (primary traffic), Open-Elevation API

### Community 42 - "Connector Filter & Dijkstra"
Cohesion: 0.32
Nodes (3): Station Graph + Dijkstra Planner (plan_journey_via_graph), Connector Taxonomy (CCS2, AC_TYPE2, CHADEMO, BHARAT_DC, TYPE1_16A, UNKNOWN), VEHICLE_CONNECTOR_COMPATIBILITY

### Community 43 - "Vite Template Icons"
Cohesion: 0.36
Nodes (8): icons.svg Sprite Sheet (Vite template social/doc icons), bluesky-icon (Bluesky butterfly logo), discord-icon (Discord controller logo), documentation-icon (document page with code brackets, purple stroke), github-icon (GitHub Octocat logo), social-icon (person silhouette with star, purple stroke), Vite React starter template boilerplate, x-icon (X / Twitter logo)

### Community 46 - "Lint Config"
Cohesion: 0.33
Nodes (5): plugins, rules, react/only-export-components, react/rules-of-hooks, $schema

## Knowledge Gaps
- **45 isolated node(s):** `routevolt`, `Vite React starter template boilerplate`, `Vite default scaffold branding`, `Favicon (purple lightning bolt logo)`, `Apple CarPlay / Android Auto Delivery` (+40 more)
  These have ≤1 connection - possible missing edges or undocumented components. (Counts symbols only; 288 node(s) total have ≤1 connection when file, concept and rationale nodes are included.)
- **7 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `RouteVolt report: redundancy charging implementation (2026-08-24)` connect `Margin & Health-Check Reports` to `Battery Sim & Route Optimizer`, `Charging Stations Summary UI`?**
  _High betweenness centrality (0.185) - this node is a cross-community bridge._
- **What connects `routevolt`, `Vite React starter template boilerplate`, `Vite default scaffold branding` to the rest of the system?**
  _45 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Routing Service (OSRM/Ola/Google)` be split into smaller, more focused modules?**
  _Cohesion score 0.056910569105691054 - nodes in this community are weakly interconnected._
- **Why does `plan_charge_to_percent()` connect `Battery Sim & Route Optimizer` to `Station Graph Planner`, `Stop Rerouting`, `Margin & Health-Check Reports`, `Edge Fan-out & Range Limits`?**
  _High betweenness centrality (0.122) - this node is a cross-community bridge._
- **Should `Route API, Traffic & Schemas` be split into smaller, more focused modules?**
  _Cohesion score 0.05128205128205128 - nodes in this community are weakly interconnected._
- **Why does `_build_stop_entry()` connect `Battery Sim & Route Optimizer` to `Journey Simulation & Elevation`, `Station Graph Planner`, `Margin & Health-Check Reports`, `Occupancy & Wait Estimation`, `Battery Simulator Tests`?**
  _High betweenness centrality (0.065) - this node is a cross-community bridge._
- **Should `Station Graph Planner` be split into smaller, more focused modules?**
  _Cohesion score 0.05928614640048397 - nodes in this community are weakly interconnected._