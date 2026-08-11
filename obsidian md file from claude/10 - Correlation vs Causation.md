#RouteVolt #correlation-vs-causation

Back to [[00 - RouteVolt Master Map]] · Previous: [[09 - Physics to Energy Flow]]

For each relationship, the same six questions from your request are answered.

---

### Speed → Energy
- **Physical relationship?** Yes.
- **Expected shape?** Roughly quadratic-in-energy-per-distance (from v² drag term, see [[03 - EV Physics]] §4); possibly U-shaped overall if a low-speed penalty is added.
- **Why?** Aerodynamic drag force grows with v², and energy-per-distance inherits that scaling (the v³ power term divided by v speed-of-travel).
- **Synthetic relationship?** Yes, via the `speed_factor` multiplicative term in the proposed formula.
- **Possible confounders?** `road_type` and `traffic_congestion_level` both influence sampled speed, so a naive speed-energy correlation partly reflects road type and traffic riding along with it.
- **What would correlation analysis show?** A positive, likely convex (accelerating) relationship between speed and Wh/km once above the reference speed.
- **What would NOT prove causation?** Observing the correlation in the generated CSV doesn't independently confirm the v² *physics* — that's true because you coded it that way, not because the correlation "discovered" physics. In real-world observational data (not this synthetic set), you'd additionally need to rule out confounds like driver aggressiveness correlating with both higher speed and other efficiency-reducing habits.

---

### Traffic congestion → Energy
- **Physical relationship?** Indirect — real (stop-start driving is less efficient), but bundled into the model two ways: via lowering sampled speed, and via a direct `traffic_factor`.
- **Expected shape?** Roughly linear-to-mild in the direct factor; nonlinear if you also flow it through the speed→energy quadratic term.
- **Why?** Congestion changes driving *pattern* (accel/brake cycles), not just average speed — this is the physical justification for giving it a separate term.
- **Synthetic relationship?** Yes, and deliberately applied twice through two channels (see [[02 - Feature Relationships]]) — a design choice that needs careful calibration to avoid over-penalizing congestion.
- **Possible confounders?** `road_type` (city roads tend to have both lower speeds *and* higher typical congestion by design).
- **What would correlation analysis show?** Positive correlation between congestion and Wh/km.
- **What would NOT prove causation?** The magnitude of the correlation in your data reflects your chosen `m` constant (see [[03 - EV Physics]] §6), not a measured real-world elasticity — don't read a specific slope as "how much Chennai traffic really costs an EV" without calibrating against real telemetry.

---

### Road type → Energy
- **Physical relationship?** No *direct* physical relationship in the proposed design — road type is a **proxy/label**, not a physical force.
- **Expected shape?** Ordered group means (city < arterial < highway, roughly, once the U-shape and drag effects combine) if it flows entirely through speed/traffic.
- **Why?** Road type stands in for unmodeled factors (traffic lights, speed limits, pedestrian interaction).
- **Synthetic relationship?** Yes, entirely through influencing the *sampling* of speed and traffic (see [[05 - Synthetic Dataset]] and [[09 - Physics to Energy Flow]]).
- **Possible confounders?** None additional — road type *is* the confounder for the speed-energy relationship, in the technical sense that it affects both speed and (through speed) energy.
- **What would correlation analysis show?** A meaningful marginal correlation with energy/Wh-per-km, purely mediated through speed.
- **What would NOT prove causation?** Finding "road_type predicts energy well" doesn't mean road surface/type itself has a physical effect on an EV's motor — it means it's a good stand-in for speed and traffic patterns in this dataset. A model relying heavily on `road_type` is really relying on unobserved speed/traffic information road_type happens to encode.

---

### Elevation gain → Energy
- **Physical relationship?** Yes, direct — `mgh`, textbook physics.
- **Expected shape?** Linear in elevation gain (for fixed mass).
- **Why?** Gravitational potential energy must come from the battery.
- **Synthetic relationship?** Directly coded as an additive term — no ambiguity here, this one is as close to "ground truth physics" as anything in this dataset.
- **Possible confounders?** `distance_km`, if the generator bounds elevation gain proportionally to distance (longer trips can physically contain more total climbing) — don't mistake this generator-imposed bound for a physical law; a short, very steep trip could have high elevation gain with low distance in reality.
- **What would correlation analysis show?** Strong positive linear correlation with energy.
- **What would NOT prove causation?** Nothing suspicious here — this is one of the few relationships in the dataset that's genuinely physically causal by direct calculation, not inferred.

---

### Elevation loss → Energy (regen)
- **Physical relationship?** Yes, direct, but attenuated by regen efficiency < 100%.
- **Expected shape?** Linear negative contribution, shallower slope than elevation gain's positive slope (because of the η_regen factor).
- **Why?** Motor-as-generator recovery, lossy conversion.
- **Synthetic relationship?** Directly coded, with a fixed efficiency constant that is a **simplified modelling assumption**, not a precise physical law (real regen efficiency varies with descent speed/steepness).
- **Possible confounders?** Same distance-bounding issue as elevation gain.
- **What would correlation analysis show?** Negative correlation with energy, weaker in magnitude than elevation gain's positive correlation (asymmetry is expected and correct).
- **What would NOT prove causation?** If the negative slope for loss and positive slope for gain came out numerically identical in the data, that would actually be a red flag that η_regen wasn't applied — the asymmetry itself is evidence the formula is working as intended, not a coincidence to explain away.

---

### Temperature → Energy
- **Physical relationship?** Yes — battery chemistry + HVAC load, both real mechanisms.
- **Expected shape?** U-shaped (per [[03 - EV Physics]] §5), not linear.
- **Why?** Two combined effects with opposite-temperature-direction causes converge on the same U shape.
- **Synthetic relationship?** Coded as a quadratic-in-deviation-from-reference multiplicative factor.
- **Possible confounders?** `weather_condition`, if it's correlated with temperature in the sampler (e.g., heavy_rain more common in cooler/monsoon conditions) — could create an apparent temperature-energy relationship that's partly weather riding along.
- **What would correlation analysis show?** A **weak or near-zero linear (Pearson) correlation**, because U-shapes largely cancel out in a linear correlation coefficient — this is the single best example in this whole project of why you must plot the data, not just compute a correlation number. See [[06 - Data Validation]] check 6.
- **What would NOT prove causation?** A low Pearson correlation would NOT prove temperature doesn't matter — it would only prove the relationship isn't linear, which you already knew by design.

---

### Weather → Energy
- **Physical relationship?** Yes, but explicitly "minor" per the schema.
- **Expected shape?** Small step-like increase from clear → rain → heavy_rain.
- **Why?** Slight rolling-resistance increase on wet roads; minor speed reduction (if also flowed through the sampler).
- **Synthetic relationship?** Coded as a small multiplicative bump.
- **Possible confounders?** `avg_speed_kmh`, if weather also lowers sampled speed — double-channel effect similar to traffic.
- **What would correlation analysis show?** A small but detectable positive correlation with energy, likely the weakest of all the condition variables.
- **What would NOT prove causation?** A near-invisible effect size doesn't mean weather "doesn't matter" in reality — it means this particular model deliberately made it minor; don't over-interpret a small synthetic effect as evidence about real-world magnitude without external calibration.

---

### Distance → Energy
- **Physical relationship?** Yes — the dominant one.
- **Expected shape?** Approximately linear, with scatter from all the multiplicative/additive adjustments and noise.
- **Why?** Energy accumulates roughly proportionally to how far you drive at a given efficiency.
- **Synthetic relationship?** The literal multiplier in the driving-energy formula.
- **Possible confounders?** None needed — this relationship doesn't require another variable to explain it, which is exactly why it's the biggest leakage risk (see [[08 - Leakage]]) — it's *too good* an explainer on its own.
- **What would correlation analysis show?** Very strong positive correlation, likely the strongest single-feature correlation in the dataset.
- **What would NOT prove causation?** In this case correlation and causation genuinely align — the caution here isn't about *whether* it's causal (it obviously is), it's about **not letting this one legitimately strong relationship crowd out evaluation of everything else** — see [[07 - ML Pipeline]]'s discussion of the `wh_per_km` evaluation trick.

Next: [[11 - RouteVolt Learning Checklist]]
