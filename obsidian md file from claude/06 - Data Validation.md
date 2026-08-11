#RouteVolt #data-validation

Back to [[00 - RouteVolt Master Map]] · Previous: [[05 - Synthetic Dataset]]

> [!info] This note is process guidance
> Applies once you've actually generated the dataset described in [[05 - Synthetic Dataset]]. None of these checks have been run yet — there's no data to check.

## Checklist of validations to run

### 1. Feature distributions
- Plot a histogram for every numeric column (`distance_km`, `elevation_gain_m/loss_m`, `avg_speed_kmh`, `traffic_congestion_level`, `ambient_temperature_c`).
- **Expect:** roughly the shape you intended when choosing sampling distributions (e.g., if you sampled distance from a log-normal, the histogram should look log-normal, not uniform).
- **Watch for:** hard cutoffs/spikes at bounds (a sign of clipping bugs), or unintentionally bimodal shapes.

### 2. Outliers
- Check for physically impossible rows: elevation gain exceeding what distance could plausibly allow, `avg_speed_kmh` above realistic road limits, negative values anywhere they shouldn't exist.
- **Expect:** zero impossible rows if generation logic is correct.

### 3. Correlations (bivariate)
- Compute a correlation matrix (Pearson for roughly linear relationships; Spearman for monotonic-but-nonlinear ones like the speed² effect).
- **Specific pairs to check, with expected direction:**
  - `avg_speed_kmh` vs `road_type` — expect ordered means: city < arterial < highway
  - `avg_speed_kmh` vs `traffic_congestion_level` — expect negative correlation
  - `elevation_gain_m` vs `distance_km` — expect weak-to-moderate positive correlation if you bounded elevation by distance, not strong (they shouldn't be near-deterministic)
  - `ambient_temperature_c` vs efficiency (`wh_per_km`) — expect a **U-shape**, not a straight line; a plain Pearson correlation coefficient will likely look weak/near-zero even though a real relationship exists, because Pearson only detects linear trends. Use a scatter plot, not just the coefficient.
  - `distance_km` vs `energy_consumed_kwh` — expect strong positive correlation (this is the dominant, near-linear driver)

### 4. Multivariate relationships
- Group by `road_type` and `vehicle_profile`, then look at the distribution of `wh_per_km` within each group — should shift sensibly (e.g., highway + large_ev should generally show higher average `wh_per_km` than city + small_ev at moderate speeds, given the drag term, but note the U-shape means very slow city crawling might *also* show elevated Wh/km — don't be surprised if the relationship isn't monotonic).

### 5. Distance vs energy, and efficiency vs distance
- Plot `distance_km` vs `energy_consumed_kwh` — expect an almost straight line with a positive intercept-ish spread from elevation/adjustment noise, **not** a perfectly straight line through the origin (if it's perfectly linear with zero scatter, your noise term isn't working — see [[08 - Leakage]]).
- Plot `distance_km` vs `wh_per_km` — this should show **much less** relationship than distance vs total energy, since `wh_per_km` factors distance out. If `wh_per_km` still correlates strongly with `distance_km`, something in your formula is unintentionally distance-dependent beyond the intended linear term.

### 6. U-shaped relationships specifically
- `ambient_temperature_c` vs `wh_per_km`: plot a scatter with a LOWESS/rolling-mean smoother, not just a correlation number, to visually confirm the U-shape exists and sits near your intended reference temperature.
- `avg_speed_kmh` vs `wh_per_km` (if you added a low-speed penalty per the limitation noted in [[03 - EV Physics]] §4): same treatment.

### 7. Is the synthetic relationship too strong?
- Check R² of a *simple* linear regression using only `distance_km` as a predictor of `energy_consumed_kwh`. If this alone explains >90-95% of variance, the other features (speed, temperature, traffic, elevation) may be contributing too little relative noise/signal for a model to learn anything about *them* specifically — the dataset would mostly just be testing "can you multiply distance by a near-constant."
- Conversely, if variance is *so* high that no combination of features explains much of anything, the formula (or its noise term) may be miscalibrated.

### 8. Is the target trivially recoverable?
- Check whether any single feature (or simple combination) can predict `energy_consumed_kwh` to near-perfect accuracy. If a linear model on `distance_km` alone achieves R² > 0.98, that's a strong signal you're looking at (or close to) direct **formula leakage** — see [[08 - Leakage]] for the full treatment.
- A rough guide: a *useful* synthetic dataset for this project should require the model to combine distance with at least 2-3 other features to reach its best achievable score, with the noise term capping the achievable R² below ~1.0 (e.g., in the 0.85-0.97 range, depending on how much noise you chose).

## What "good" looks like, summarized

| Check | Green flag | Red flag |
|---|---|---|
| Distributions | Match intended sampling shape | Hard spikes, unintended bimodality |
| Outliers | None physically impossible | Negative/implausible values present |
| distance vs energy | Strong but *not perfect* linear relationship | R² ≈ 1.0 (no noise) |
| temperature vs wh/km | Visible U-shape via smoother | Flat / no visible pattern (formula bug) |
| Simple-feature R² | Meaningfully below 1.0 | ≥ 0.98 from distance alone |

Next: [[07 - ML Pipeline]]
