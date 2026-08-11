#RouteVolt #physics-flow

Back to [[00 - RouteVolt Master Map]] · Previous: [[08 - Leakage]]

> [!warning] Status
> Reflects the [PROPOSED] formula from [[03 - EV Physics]] and [[04 - Target Generation]] — not existing code.

```mermaid
flowchart TD
    V[Vehicle Profile] --> BASE[Baseline Efficiency<br/>Wh/km]
    DIST[Distance km] --> DE[Driving Energy]
    BASE --> DE

    SPD[Speed] -.->|multiplicative factor| ADJ[Efficiency Adjustments]
    TEMP[Temperature] -.->|multiplicative factor| ADJ
    TRAF[Traffic] -.->|multiplicative factor| ADJ
    WX[Weather] -.->|multiplicative factor| ADJ
    RT[Road Type] -.->|influences Speed & Traffic<br/>sampling, not a direct factor| SPD

    ADJ --> DE

    EG[Elevation Gain] --> PE[Potential Energy<br/>additive term]
    EL[Elevation Loss] --> REGEN[Regenerative Recovery<br/>additive credit, less than 100%]

    DE --> TOTAL[Total Energy Consumption]
    PE --> TOTAL
    REGEN --> TOTAL
```

## Reading this diagram

- **Solid arrows** = terms that are literally summed or multiplied into the energy formula in [[04 - Target Generation]].
- **Dashed arrows into "Efficiency Adjustments"** = the four condition-based multiplicative factors from [[03 - EV Physics]] (§4, §5, §6, §10), which combine into one effective Wh/km before being multiplied by distance.
- **Road Type's dashed arrow** deliberately does *not* point into the formula directly — per [[03 - EV Physics]] §7, road type is a **sampling-time proxy** for speed/traffic, not its own term in the energy equation. If a future implementation *also* gives road_type a direct multiplicative factor, that would double-count an effect already captured through speed — flag it as a design bug if you see it in real code.
- **Elevation Gain/Loss** feed into `Total Energy Consumption` as separate additive terms, never through the multiplicative "Efficiency Adjustments" box — this is the crux of the multiplicative-vs-additive discussion in [[03 - EV Physics]].

Next: [[10 - Correlation vs Causation]]
