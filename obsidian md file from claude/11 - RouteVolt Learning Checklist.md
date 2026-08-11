#RouteVolt #checklist

Back to [[00 - RouteVolt Master Map]] · Previous: [[10 - Correlation vs Causation]]

Questions you should be able to answer unaided once you've worked through this vault. Check them off as you can explain each one out loud without re-reading the note.

## Fundamentals
- [x] What is Wh/km, and how is it different from kWh?
- [x] What is kWh, physically (energy, not power)?
- [x] Why does distance increase energy consumption roughly linearly?
- [x] Why does aerodynamic drag increase roughly with speed², and why does that become a v² (not v³) term in an energy-per-distance model?
- [x] Why might speed have a *nonlinear* — possibly U-shaped — relationship with Wh/km rather than simply "faster is always worse"?

## Elevation and regen
- [x] Why does elevation gain require additional energy? (Name the formula: mgh)
- [x] Why can downhill travel recover energy?
- [x] Why is regenerative braking never 100% efficient — name at least two loss mechanisms.
- [x] Why is elevation represented as an **additive** term instead of a multiplicative factor?

## Multiplicative vs additive
- [x] Why are speed/temperature/traffic/weather represented as *multiplicative* adjustment factors instead of additive ones?
- [x] What would go wrong if elevation were made multiplicative instead of additive? (Think: same hill, very different trip lengths.)
- [x] What would go wrong if all the efficiency factors were made additive instead of multiplicative? (Think: does a `small_ev` and `large_ev` degrade by the same flat amount, or proportionally?)

## Correlation vs causation
- [x] What is the difference between correlation and causation, in your own words?
- [x] Which correlations in RouteVolt's design are physically real (e.g., elevation → energy)?
- [x] Which correlations were intentionally introduced by the generator's *sampling* logic rather than the energy *formula* (e.g., road_type → speed)?
- [x] Why can a low Pearson correlation coefficient still hide a real relationship? (Hint: temperature vs Wh/km.)

## Leakage
- [x] What is formula leakage, and why is RouteVolt specifically vulnerable to it?
- [x] What is target leakage, and what's the concrete RouteVolt example (`wh_per_km` vs `energy_consumed_kwh`)?
- [x] What is feature leakage, and why does `avg_speed_kmh`'s timing (planned vs. observed) matter?
- [x] What is snapshot/static-value leakage, and how is it different from feature leakage?
- [x] What is generator leakage, and how would you detect it in the data (hint: conditional variance)?
- [x] Why can distance make a model appear better than it actually is?
- [ ] How can you test whether a model learned real efficiency relationships rather than just distance? (Name at least two techniques from [[07 - ML Pipeline]].)

## Design and assumptions
- [x] What assumptions does the synthetic dataset design make that could fail in the real world? (List at least 3 — e.g., fixed regen efficiency, symmetric temperature U-shape, no intra-trip road-type mixing.)
- [x] Where does `mass_kg` come from for the elevation terms, and why is it currently a gap in `dataset_schema.md`?
- [x] Why does the schema explicitly exclude station-level features (`available_ports`, `queue_time_minutes`, `cost_per_kwh`) from the trip/energy dataset?
- [x] What's the difference between the trip/energy subsystem and the station-recommendation subsystem in RouteVolt, and why shouldn't they be merged into one model?

## Validation
- [x] What plot would you make to check whether the temperature-energy relationship looks the way you intended?
- [x] What R² threshold on a distance-only baseline would make you suspicious of formula leakage?
- [x] Why should `energy_consumed_kwh` vs `distance_km` show scatter, not a perfectly straight line?

Next steps once this checklist is mostly checked off: revisit [[00 - RouteVolt Master Map]]'s status callout — the schema needs a `mass_kg` column added, and the actual generator/formula/training code in this vault (marked [PROPOSED] throughout) needs to be written and then re-validated against [[06 - Data Validation]] before trusting any of it.
