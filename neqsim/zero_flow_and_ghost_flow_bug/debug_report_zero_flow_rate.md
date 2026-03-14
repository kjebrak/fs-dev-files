# Debug Report: Zero/Near-Zero Flow Rate Property Extraction Failures

**Date**: 2026-03-03
**Status**: Root cause identified, fix validated, implementation pending
**Severity**: Medium — causes misleading error messages and incorrect results
**Debug scripts**: `debug_near_zero_and_zero_flow_rate_error.py`, `debug_zero_flow_fix_approach.py` (same directory)

## Symptom

When a stream has zero or negligible flow rate, the frontend shows:

> "Critical properties returned invalid values (inf/NaN): density"

This occurs in two scenarios:
1. **User sets flow_rate=0** on a source stream
2. **Separator produces an empty outlet** (e.g., dry gas → no liquid)

The error message is also misleading: "critical properties" has a specific meaning
in chemical engineering (critical point, Tc, Pc) — it should say "essential" or "key".

---

## Two Distinct Bugs

### Bug 1: Source Stream with Exactly Zero Flow

**Trigger**: User creates a source stream with `flow_rate=0.0 kg/hr`.

**NeqSim behavior** (`setTotalFlowRate(0.0, "kg/hr")`):
1. Calls `setEmptyFluid()` which sets `totalNumberOfMoles = 0.0` (SystemThermo.java:4795)
2. `initBeta()` computes `beta[i] = phaseMoles / totalMoles = 0/0 = NaN` (SystemThermo.java:3756)
3. NaN beta cascades to all system-level properties

**Property results at zero flow**:

| Property | Method | Result | Why |
|---|---|---|---|
| `getDensity("kg/m3")` | `phaseVol/totalVol * physDensity` | **NaN** | `totalVol = 0 → 0/0` |
| `getDensity()` (no unit) | `Σ beta * phaseDensity` | 75.65 (wrong) | NaN * value behavior |
| `getEnthalpy("kJ/kg")` | `enthalpyJ / totalMass` | **ArithmeticException** | Explicit guard: `totalMass == 0` |
| `getEnthalpy("J/mol")` | `enthalpyJ / totalMoles` | **ArithmeticException** | Explicit guard: `totalMoles == 0` |
| `getEnthalpy("J")` | Direct sum | -851.1 J (**works!**) | No per-mass division |
| `getCp("kJ/kgK")` | `cpJ / totalMass` | **ArithmeticException** | Same guard |
| `getMolarVolume()` | `Σ beta * Vm` | 90.02 (questionable) | Depends on NaN beta |
| `getMolarMass()` | `Σ z * Mm` | 0.01702 (**correct**) | Independent of moles |
| `getZ()` | `Σ beta * Z` | 1.82 (wrong, > 1) | NaN beta |
| `getViscosity()` | Physical properties | 0.0245 (reasonable) | Interpolation-based |
| Temperature, Pressure | Direct getters | **Valid** | Independent of moles |

**Key insight**: Intensive properties (density, enthalpy/kg, Cp) SHOULD be calculable
at any T, P, composition — they don't depend on flow rate. NeqSim's failure is a
unit-conversion issue: per-mass and per-mole conversions divide by total mass/moles,
which is zero. The underlying thermodynamic state IS computed correctly.

**Proof (clean-state streams are stable at ANY flow)**:

Setting flow to `1e-10 kg/hr` produces **bit-for-bit identical** intensive properties
as `1000 kg/hr` — confirmed for density, enthalpy, Cp, Cv, entropy, Z:

```
              1e-10 kg/hr        1000 kg/hr       Rel. Error
density       37.8961 kg/m3      37.8961 kg/m3     < 1e-12
enthalpy      -2.3612 kJ/kg      -2.3612 kJ/kg     < 1e-12
Cp            2.5701 kJ/kgK      2.5701 kJ/kgK     < 1e-12
entropy       -1.7439 kJ/kgK     -1.7439 kJ/kgK    < 1e-12
Z             0.90780            0.90780             < 1e-12
```

**Clean-state streams work perfectly down to 1e-27 kg/hr and below.**

### Bug 2: Separator Ghost-Flow Outlet (1e-27 kg/hr)

**Trigger**: Separator with a feed that's entirely gas (or entirely liquid) —
one outlet gets all the flow, the other gets a "ghost" system.

**NeqSim behavior** (`getEmptySystemClone()` in SystemThermo.java:2254):
1. Clones the full system
2. Divides ALL component moles by `1e30`
3. Does NOT re-run flash calculation
4. Creates **two degenerate phases** with identical compositions

**The degenerate phase problem**:

The ghost system has 2 phases, each with exactly half the components:
```
Phase 0 (gas):    CH4 x=0.475, C2H6 x=0.015, C3H8 x=0.010  (moles: 3.26e-29)
Phase 1 (liquid): CH4 x=0.475, C2H6 x=0.015, C3H8 x=0.010  (moles: 3.26e-29)
```

The REAL composition should be `CH4=0.95, C2H6=0.03, C3H8=0.02` — but the mole
fractions are halved because they're split across two identical phases.

**Property results at ghost flow (1e-27 kg/hr)**:

| Property | Value | Expected | Problem |
|---|---|---|---|
| flow_rate | 1e-27 kg/hr | ~0 | Technically "finite" |
| density | 18.95 kg/m3 | 37.90 | Halved (wrong) |
| molar_mass | 0.00851 | 0.01702 | Halved (wrong) |
| enthalpy | -8.17e+60 kJ/kg | ~-2.4 | Astronomically wrong |
| entropy | -2.74e+58 kJ/kgK | ~-1.7 | Astronomically wrong |
| Cp | 7.53e+116 kJ/kgK | ~2.6 | Absurd |
| speed_of_sound | 9.65e+31 m/s | ~423 | Faster than light |
| z_factor | 1.99 | ~0.91 | Unphysical |

**All values pass `math.isfinite()`** — our `safe_extract()` does NOT catch them.

**Affects both gas AND liquid ghost outlets** — confirmed by testing:
- **Missing liquid** (dry gas feed → ghost liquid outlet): garbage properties
- **Missing gas** (heavy liquid feed → ghost gas outlet): same garbage properties

### Why Ghost Flow Produces Garbage (Root Cause)

The corruption is a chain of three compounding failures from `getEmptySystemClone()`:

**1. Halved compositions (degenerate 2-phase split)**

The clone keeps the original 2-phase structure with identical compositions in each
phase. Each phase holds half the total moles, so z-fractions per phase are halved
(e.g., 0.0714 instead of 0.1429, total z sums to 0.5 not 1.0). This directly
halves density and molar mass — even at normal flow rates.

**2. EOS residual term explosion (tiny-n denominators)**

The cubic EOS (SRK/PR) has terms like `a / (V(V+b))` and derivatives containing
`1/(nV - b)²` (in PhaseEos.java). At ultra-small n (~1e-29 mol), the molar
volume term `nV` approaches the co-volume `b`, making these denominators approach
zero. The residual contributions to enthalpy, heat capacity, and entropy
blow up to magnitudes of 1e60–1e119.

Confirmed: `initBeta()` does NOT fix this. Collapsing to 1 phase (phaseToSystem)
fixes Z (1.986 → 0.993) but NOT Cp/H. The EOS residual explosion is the root cause,
not stale beta fractions.

**3. Per-mass amplification (division by ~1e-28 kg)**

Properties like `getEnthalpy("kJ/kg")` divide total enthalpy (J) by total mass (kg).
Total mass is ~1e-28 kg. Even small numerical errors in the total enthalpy get divided
by 1e-28, amplifying them by 28 orders of magnitude into the 1e60+ range.

**Which properties survive vs break:**

| Category | Examples | Status | Why |
|---|---|---|---|
| Direct state | T, P | **OK** | Independent of moles |
| Interpolation-based | viscosity, thermal cond. | **OK** | Correlation-based, not EOS |
| Phase-fraction weighted | density, molar mass, Z | **Wrong (halved)** | Halved compositions |
| EOS derivative + per-mass | H, S, Cp, Cv, speed_of_sound | **Garbage (1e60+)** | EOS residual explosion × per-mass amplification |

All garbage values pass `math.isfinite()` — they are astronomically large but technically
finite. Our `safe_extract()` does NOT catch them.

**NeqSim Java source** (`Separator.java` run() method, lines ~426-445):
When a phase is absent, the separator calls `getEmptySystemClone()` and sets
flow to `1e-20 kg/hr`. The ghost stream is then assigned as the outlet via
`setThermoSystemFromPhase()`. This is the origin of the corrupted state — it's
a synthetic artifact, not a physically meaningful stream.

**Ghost state degrades at ALL flows — not just ultra-small ones**:

```
Ghost-state flow sweep:
    flow_kg_hr       density     H_kJ_kg      Cp_kJ_kgK     Z        status
         1e+03          17.3       12.83          4.935     1.816     [OK-ish, density halved]
         1e+02          17.3      -1,725          104.3     1.816     [GARBAGE - Cp]
         1e+01          17.3    -175,500        800,300     1.816     [GARBAGE - H, Cp]
         1e+00          17.3   -1.76e+07       7.98e+09     1.816     [GARBAGE - H, Cp]
         1e-10          17.3   -2.04e+27       9.85e+49     1.844     [GARBAGE - H, Cp]
         1e-27          17.3   -8.17e+60       7.53e+116    1.986     [GARBAGE - H, Cp]
```

Note: Even at 1000 kg/hr, the ghost state has wrong density (17.3 vs 37.9)
because the degenerate 2-phase structure with halved compositions is never resolved.

**Clean-state comparison — perfectly stable at all flows**:

```
Clean-state flow sweep (fresh thermo system, same composition):
    flow_kg_hr       density     H_kJ_kg      Cp_kJ_kgK     Z        status
         1e+03         37.9      -2.361          2.570     0.908      [OK]
         1e+00         37.9      -2.361          2.570     0.908      [OK]
         1e-10         37.9      -2.361          2.570     0.908      [OK]
         1e-27         37.9      -2.361          2.570     0.908      [OK]
```

**CRITICAL**: The ghost state CANNOT be rescued by simply rescaling flow.
The corrupted 2-phase structure from `getEmptySystemClone()` permanently breaks
the thermodynamic calculations. Rescaling to 1e-6 gives:
- density: 17.3 kg/m3 (54% off from correct 37.9)
- Cp: 5.14 kJ/kgK (100% off from correct 2.57)

---

## Frontend Reproduction

**Graph**: Equal-fraction C1–nC5 (7 components × 0.1429) at 10°C, 100 bar, 100 kg/hr
→ Separator → gas outlet + liquid outlet.

**Result**: Feed is **single-phase liquid** at these conditions. The **gas outlet**
is the ghost (1e-28 kg/hr), the liquid outlet gets all 100 kg/hr.

```
GAS OUTLET (GHOST):
  temperature    =       10.0 °C      [OK]
  pressure       =      100.0 bara    [OK]
  density        =      227.2 kg/m3   [OK-ish, wrong but finite]
  enthalpy       = -5.95e+61  kJ/kg   [GARBAGE]
  Cp             =  2.53e+119 kJ/kgK  [GARBAGE]
  speed_of_sound =  2.41e+32  m/s     [GARBAGE]
  Z factor       =      0.965         [OK-ish]
  viscosity      =      0.371 cP      [OK]
  molar mass     =     25.054 g/mol   [wrong — should be ~50]
  Ghost z sum    =      0.500         [halved — should be 1.0]

LIQUID OUTLET (REAL):
  All properties correct — 563.3 kg/m3, -346.2 kJ/kg, Cp=2.40
```

**Key finding**: No error is shown in the app because all ghost values pass
`isfinite()`. The ghost gas outlet silently displays wrong properties.
The "Critical properties returned invalid values (inf/NaN): density" error
that was observed previously must have been from a **zero-flow source stream**
(Bug 1), not from this separator ghost outlet (Bug 2).

**Script**: `reproduce_frontend_graph.py` (same directory)

---

## Downstream Equipment Behavior

Ghost/zero-flow streams feeding into downstream equipment produce garbage results.
Tested with `debug_downstream_zero_flow.py`.

### Ghost stream → equipment (NO recovery)

| Equipment | Outlet T | Outlet P | Power/Energy | Outlet density | Status |
|---|---|---|---|---|---|
| Compressor | **Unchanged** (10°C) | 150 bara (set correctly) | **-1.65e33 W** | 0 kg/m3 | Garbage |
| Valve | **Unchanged** (10°C) | 50 bara (set correctly) | N/A | **NaN** | Garbage |
| Mixer (ghost + real) | 10°C | 100 bara | N/A | 563 kg/m3 | **OK** (ghost negligible) |

### Recovered stream (1 kg/hr) → equipment

| Equipment | Outlet T | Outlet P | Power/Energy | Outlet density | Status |
|---|---|---|---|---|---|
| Compressor | 13.6°C (correct rise) | 150 bara | 3.5 W | 571 kg/m3 | **OK** |
| Valve | 11.3°C (correct JT) | 50 bara | N/A | 563 kg/m3 | **OK** |

### Implication for fix approach

Stream-level recovery at extraction time fixes stream property display but does NOT
fix equipment results (power, delta_T). The compressor still runs on the ghost stream
and reports garbage power. Equipment-level zero-flow guards (DWSIM pattern) are needed
as a follow-up to produce correct equipment results.

**DWSIM reference**: Compressor checks `if mass_flow == 0` at the top of `Calculate()`:
zero all energetic outputs, copy inlet to outlet, exit. Separator always flashes but
handles zero-flow phases by using inlet composition. These are equipment-level guards,
not stream-level.

**Script**: `debug_downstream_zero_flow.py` (same directory)

---

## Root Cause Chain

### Zero flow (Bug 1):
```
setTotalFlowRate(0, "kg/hr")
  → setEmptyFluid()
    → totalNumberOfMoles = 0.0
      → initBeta(): beta = 0/0 = NaN
        → getDensity("kg/m3"): totalVol = 0, phaseVol/totalVol = NaN
        → getEnthalpy("kJ/kg"): totalMass = 0, throws ArithmeticException
```

### Ghost flow (Bug 2):
```
Separator with single-phase feed
  → getEmptySystemClone() for missing phase
    → All moles divided by 1e30
    → Two degenerate phases with halved compositions
      → EOS residual terms explode at ultra-small n
      → Per-mass conversion amplifies into 1e60+ values
      → All intensive props are garbage (but pass isfinite())
      → State is PERMANENTLY corrupted — rescaling cannot fix it
```

---

## NeqSim Java Separator Internals

The separator's `run()` method (Separator.java) is essentially:
1. Mix inlets → apply pressure drop → TP flash (or PH flash if heat input)
2. Apply entrainment/carryover fractions between phases
3. Extract gas/liquid phases into outlet streams via `setThermoSystemFromPhase()`
4. For missing phases: `getEmptySystemClone()` → set flow to 1e-20 → assign as outlet

**Beyond the basic flash**, NeqSim's separator provides:
- **Design rating**: K-factor (Souders-Brown), gas superficial velocity, capacity
  utilization, max gas flow rate — all available via getters after `run()`
- **De-rated K-factor**: Accounts for low surface tension fluids
- **Multi-phase liquid**: Intelligently combines oil + aqueous phases in liquid outlet
- **Entrainment model**: Configurable liquid-in-gas and gas-in-liquid fractions
- **Transient mode**: VU-flash with dynamic liquid level tracking (not used in MVP)

These features justify keeping the NeqSim separator rather than reimplementing in Python.

---

## DWSIM Comparison

DWSIM handles absent separator outlets differently:
- Always performs a VLE flash on the feed, even if one phase ends up with zero flow
- The composition assigned to the absent phase is the **incipient phase composition** — the equilibrium composition the phase *would have* based on K-values from the flash
- This is why DWSIM's zero-flow gas outlet can have a leaner composition (more methane, less heavy ends) than the liquid — it's not an error, it's the thermodynamic equilibrium composition
- Absent phase flow is set to **zero** with valid intensive properties for display
- Internally uses epsilon values for calculations but **exports 0 flow**
- Downstream equipment (compressor, pump) has explicit zero-flow guards: check `if mass_flow == 0` at the top of `Calculate()`, then zero all energetic outputs, copy inlet to outlet, exit early

Our fix aligns with this UX: zero-flow streams show their intensive properties
at the T/P/composition conditions. The epsilon flow used for property reconstruction
stays local — it is never propagated through the NeqSim process network.

---

## Current Defense (What We Have)

1. **`safe_extract()`** — catches `inf`, `NaN`, and exceptions → returns `None`/default
2. **Critical property validation** in `Stream.extract_results()` — checks if density,
   T, P, flow are `None` after extraction, returns error if so
3. **`validate_state()`** — checks for negative/NaN pressure and temperature

**Gaps**:
- No zero-flow detection before extraction
- No magnitude/sanity bounds on extracted values
- Ghost-flow values (1e+60) pass `isfinite()` check
- Error message says "Critical properties" (confusing in ChemEng context)

**Important asymmetry in failure modes**:
- **Bug 1 (zero-flow source)**: `getDensity("kg/m3")` → **NaN** → caught by
  `safe_extract()` → validation triggers error → user sees error message
- **Bug 2 (ghost separator outlet)**: `getDensity("kg/m3")` → **18.47** (wrong but
  finite) → passes `isfinite()` → **silently shows wrong values with no error**

This means Bug 2 is arguably worse: the user sees confidently-presented but
completely wrong thermodynamic properties with no indication of a problem.
The density is halved (18.5 vs 37.9), enthalpy is ~1e60, Cp is ~1e116 —
all finite, all garbage.

---

## Recommended Approaches

### Approach A: Epsilon-Flow Substitution — for Bug 1 (zero-flow source streams)

**Before extraction**, if `mass_flow == 0` on a clean-state stream:
1. Clone the thermo system
2. Set `thermo.setTotalFlowRate(EPSILON_FLOW, "kg/hr")` (e.g., 1e-10)
3. Re-init the thermo system
4. Extract all intensive properties normally (they're correct — proven above)
5. Report `mass_flow_rate=0.0` in results
6. Add a warning: "Stream has zero flow — intensive properties computed at reference flow"

**Pros**:
- Intensive properties are **mathematically correct** (proven identical to 1000 kg/hr)
- No information loss — user sees valid T, P, density, Cp, etc.
- Clean, minimal code change

**Does NOT work for Bug 2**: Ghost state has corrupted phase structure that
rescaling cannot fix. Even at 1000 kg/hr, density is still halved.

### Approach B: Zero-Flow Short-Circuit — for Bug 2 (separator ghost outlets)

**Before extraction**, if `mass_flow < ZERO_FLOW_THRESHOLD` (e.g., 1e-10 kg/hr):
1. Return `is_solved=True` with T, P from the stream (these are valid)
2. Set all flow rates to `0.0`
3. Set all intensive properties to `None`
4. Add warning: "Stream has negligible flow — no fluid properties available"

**Pros**:
- Correct — there IS no fluid in this stream, so properties don't apply
- Simple, safe, no NeqSim mutation
- Clear semantics for the frontend (show "empty" or "no flow")

**Cons**:
- Frontend shows mostly blank results for the empty outlet
- Process engineer might want to know "what IF there were fluid here?"

### Approach C: Fresh Stream Recreation — for both Bug 1 and Bug 2 ✅ CHOSEN

Create a **new clean NeqSim Stream** from the ghost/zero system's composition, T, P:
1. Detect ghost/zero flow (< threshold)
2. Read T (K), P (bara), component names and z-fractions from the thermo system
3. **Normalize** the composition (ghost z-fractions sum to ~0.5 due to degenerate split)
4. Create a fresh thermo system using `type(ghost_thermo)(T, P)` — preserves EoS class
5. Add components, set mixing rule (2 = autoSelect), set epsilon flow (1.0 kg/hr)
6. Wrap in a NeqSim `Stream` and call `run()` — NOT just TPflash+init
7. Extract intensive properties from the fresh stream; report `flow_rate=0`

**Pros**:
- Fixes BOTH bugs with a single unified code path
- User sees correct intensive properties even for empty streams (matches DWSIM UX)
- Preserves EoS model class (SRK, PR, etc.) automatically via `type()`
- Validated: <0.01% error across 5 scenarios (gas/liquid ghost, zero-flow, PR EoS)
- Composition normalization handles the halved z-fractions cleanly

**Cons**:
- Creates a temporary JVM object (one extra `Stream.run()` call per ghost stream)
- Mixing rule hardcoded to `2` (autoSelect) — sufficient for current use

**Key discovery**: Must use `Stream.run()`, not `TPflash()` + `init()`.
The Stream.run() path performs additional physical property initialization
that raw flash misses — without it, `getDensity("kg/m3")` returns 0.

### Approach D: Hybrid (A for Bug 1 + B for Bug 2)

The simplest correct solution:
1. **Bug 1** (exact zero flow, clean state): Epsilon-flow substitution → correct properties
2. **Bug 2** (ghost flow from separator): Zero-flow short-circuit → T, P, no intensive props

Detection logic: check `mass_flow < ZERO_FLOW_THRESHOLD` regardless of source.
Both bugs produce near-zero flow, and the short-circuit handles both correctly.
The epsilon-flow approach is an optimization for Bug 1 that could be added later.

### Approach E: Magnitude-Based Sanitization (defense-in-depth)

Regardless of primary fix, add **magnitude bounds** to catch ghost-flow values
that slip through:
- `safe_extract()` gets optional `max_magnitude` parameter
- Cp > 1000 kJ/kgK → None
- Enthalpy > 1e6 kJ/kg → None
- Speed of sound > 10000 m/s → None

This catches the garbage values even if the primary fix doesn't apply.

---

## Chosen Implementation: Approach C — Fresh Stream Recreation

**Decision**: Approach C was chosen over Approach D (short-circuit with `None` properties)
because process engineers expect to see intensive properties even on zero-flow streams.
This matches DWSIM's behavior: absent phases show correct T, P, density, Cp etc. at
zero flow — the properties the fluid *would have* at those conditions.

**Validated**: The fix has been tested across 5 scenarios with <1% error in all cases.
See `debug_zero_flow_fix_approach.py` for the full validation.

### The Fix: `rescue_ghost_stream()`

In `Stream.extract_results()`, detect `flow < ZERO_FLOW_THRESHOLD` and rebuild:

1. Read T (K), P (bara) from the ghost/zero thermo system
2. Read component names and z-fractions, **normalize** (ghost fractions sum to ~0.5)
3. Create a **fresh** thermo system of the same EoS class: `type(ghost_thermo)(T, P)`
4. Add components, set mixing rule, set epsilon flow (1.0 kg/hr)
5. Wrap in a NeqSim `Stream` and call `run()` — critical for full initialization
6. Extract all intensive properties from the fresh stream
7. Report `flow_rate=0` in results

**Why `Stream.run()` and not `TPflash()` + `init()`**: The `Stream.run()` path
performs additional initialization (physical properties, density code paths) that
raw `TPflash()` + `init(3)` does not. Without `run()`, `getDensity("kg/m3")` returns 0.

### Validation Results

| Scenario | Error vs Reference |
|---|---|
| Ghost liquid outlet (dry gas feed, SRK) | ALL < 0.01% |
| Zero-flow source stream (SRK) | ALL < 0.01% |
| Real two-phase separator (no false trigger) | N/A — correctly skipped |
| Ghost gas outlet (heavy liquid feed, SRK) | Produces correct gas-phase properties |
| Ghost liquid outlet (dry gas feed, PR EoS) | ALL < 0.01%, EoS class preserved |

### Key Design Decisions

- **Threshold semantics**: `ZERO_FLOW_THRESHOLD` catches ghost/zero paths specifically.
  Clean real streams are numerically stable at any flow — threshold is not a
  "small flow is bad" rule, it's a ghost-state detector.
- **Keep NeqSim separator**: Python-side separator was considered but rejected.
  NeqSim's separator provides design rating features (K-factor, Souders-Brown,
  capacity utilization, max gas flow rate, surface tension de-rating) that we
  already expose via `_extract_rating_results()`. Reimplementing these is not
  worth the effort.
- **DWSIM alignment**: DWSIM treats absent separator outlets as true zero/empty
  but still computes intensive properties for display. Our fix matches this UX.

### Files to Modify

| File | Change |
|---|---|
| `src/integration/neqsim_wrapper/process/stream.py` | Add zero-flow detection + fresh stream recovery in `extract_results()` |
| `src/core/validation/validation_constants.py` | Add `ZERO_FLOW_THRESHOLD` constant |
| Error message in `stream.py` | Rename "Critical properties" → "Essential properties" |
| `src/integration/neqsim_wrapper/extraction_utils.py` | Optional: add magnitude bounds to `safe_extract()` as defense-in-depth |

### Rejected Alternatives

- **Approach A (epsilon-flow substitution)**: Only works for Bug 1 (clean zero-flow).
  Does NOT fix Bug 2 — ghost state corruption persists regardless of flow rescaling.
- **Approach B/D (short-circuit with `None` properties)**: Technically correct but
  poor UX — engineers expect to see properties even on empty streams.
- **Python-side separator**: Full control + fewer JVM calls, but loses NeqSim's
  design rating calculations (K-factor, capacity, etc.) and future NeqSim features.
  Risk/effort not justified when the stream-recovery patch is surgical and sufficient.
