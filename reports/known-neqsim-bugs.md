# Known NeqSim Bugs & Caveats for Flowsheet Studio

Known bugs and pitfalls in NeqSim (jneqsim 3.4.0) that affect Flowsheet Studio's simulation results and wrapper code. These must be accounted for in the NeqSim wrapper layer, result extraction, and integration tests.

Last updated: 2026-03-12

---

## C1: System-level mixture density is wrong for two-phase fluids (BUG)

**Severity**: HIGH — silently produces wrong density values for two-phase streams

**Affected code**: `stream.py:351` calls `thermo.getDensity("kg/m3")` for bulk/mix density extraction.

Both `getDensity()` variants on `SystemThermo` (the fluid/thermo system level) are incorrect for two-phase mixtures. Phase-level `getPhase(i).getDensity()` is correct.

`SystemThermo.getDensity()` (no-argument version) computes mixture density as molar-fraction-weighted average of phase densities:

```java
// SystemThermo.java line 2172 — WRONG for 2+ phases
for (int i = 0; i < numberOfPhases; i++) {
    density += beta[phaseIndex[i]] * getPhase(i).getDensity();
}
```

This is physically incorrect. Density is an intensive property that must be volume-weighted:
`rho_mix = m_total / V_total = sum(phi_V_i * rho_i)` where `phi_V_i` is the volume fraction.

The string-argument version `getDensity("kg/m3")` uses volume-fraction weighting, which looks correct at first glance:

```java
// SystemThermo.java line 2185 — ALSO WRONG (convention crossover)
density += getPhase(i).getVolume() / getVolume() * getPhase(i).getPhysicalProperties().getDensity();
```

However, this is **also wrong** for Peneloux-corrected systems: `getVolume()` returns the **non-shifted** EoS volume (from the cubic root), while `getPhysicalProperties().getDensity()` returns the **Peneloux-shifted** density. Mixing non-shifted volume fractions with shifted densities is a convention crossover that produces errors up to ~20% in some cases.

**Phase-level density is correct**: `getPhase(i).getDensity()` returns `1/V_m * MW * 1e5` directly from the EoS molar volume (consistent convention, shifted or not). This is already used in `stream.py:501` for per-phase density extraction.

**In summary**: Both `getDensity()` (molar-weighted) and `getDensity("kg/m3")` (non-shifted volume fractions × shifted densities) are wrong for two-phase systems. The latter can produce errors up to ~20% when Peneloux volume correction is active.

**Impact on Flowsheet Studio**: The bulk/mix density reported in `StreamResult.physical_properties.density` may be incorrect for two-phase streams. Per-phase densities in `PhaseProperties.density` are correct.

**Workaround**: Compute mixture density from per-phase data instead of calling system-level `getDensity()`:

```python
# Correct mixture density from per-phase data
rho_phase = [phase.getDensity() for phase in phases]  # kg/m3, correct per-phase
V_phase = [phase.getMolarVolume() for phase in phases]  # cm3/mol
beta_phase = [system.getBeta(i) for i in range(n_phases)]
# Volume-weighted mixture density:
V_total = sum(beta_i * V_i for beta_i, V_i in zip(beta_phase, V_phase))
rho_mix = sum(beta_i * V_i / V_total * rho_i
              for beta_i, V_i, rho_i in zip(beta_phase, V_phase, rho_phase))
```

**Status**: Bug exists in NeqSim main. Not yet fixed. Should be addressed in the wrapper's `_extract_mix_properties()` method.

---

## C2: Peneloux volume correction silently enabled by default for PR EoS

**Severity**: HIGH — affects all density values when using Peng-Robinson

NeqSim enables Peneloux volume correction by default for PR EoS (`SystemPrEos`). This means all PR density values include a volume shift that may not be expected or documented.

To disable:
```java
system.useVolumeCorrection(false);  // for non-Peneloux behavior
```

**Impact on Flowsheet Studio**: Currently `useVolumeCorrection()` is **not called anywhere** in the codebase. This means all PR simulations silently include Peneloux volume correction. This is not necessarily wrong — Peneloux generally improves liquid density predictions — but it should be explicitly documented and controlled.

If Flowsheet Studio intends to offer Peneloux as a user-configurable option in the future, the wrapper must explicitly manage this setting. For now, the implicit default should be documented so that density comparisons against other simulators account for the correction.

**Recommendation**: Add explicit `useVolumeCorrection(True)` call in `create_thermo_system()` to make the behavior self-documenting, even though it matches the current default.

---

## C3: `setMultiPhaseCheck(true)` needed for correct 2-phase detection near critical

**Severity**: MEDIUM — affects near-critical region within phase envelope

NeqSim's default TP flash uses a basic stability analysis that can miss phase splits near the mixture critical point, incorrectly reporting single-phase when the system is actually two-phase.

Setting `system.setMultiPhaseCheck(true)` activates `TPmultiflash` which uses a more rigorous multi-trial stability analysis:
- Tests each pure component as a trial phase composition
- With `setEnhancedMultiPhaseCheck(true)`, also tests Wilson K-value-based vapor-like, liquid-like, and LLE-specific trial compositions

**Impact on Flowsheet Studio**: Already handled correctly. `create_thermo_system()` in `fluid.py:73` calls `system.setMultiPhaseCheck(True)` for all fluid systems. This is the correct approach.

**Note**: This also sets `maxNumberOfPhases = 3` internally, which is fine since Flowsheet Studio supports gas/oil/aqueous phase reporting.

**Source**: `SystemThermo.java:121,5208-5223`, `TPflash.java:379-384`, `TPmultiflash.java:1890-2166`

---

## C4: `calcPTphaseEnvelope()` may produce incomplete envelopes

**Severity**: LOW — relevant only if phase envelope visualization is added

The `calcPTphaseEnvelope()` method uses Newton-Raphson arc-length continuation to trace the saturation boundary. Known issues:

1. **Missing bubble curve**: Only the dew-point curve may be produced. The algorithm starts from the dew side and traces toward the critical point. If the solver crashes before reaching critical, the bubble-side restart can also fail silently.

2. **Silent crash handling**: Newton solver failures are caught silently and the envelope is truncated — no error reported to the caller.

3. **Hard-coded step limits**: `dTmax=10 K`, `dPmax=10 bar` may be too large near the critical point, causing overshoots.

4. **Alternative implementations**: NeqSim has `calcPTphaseEnvelope2()` (newer algorithm) and a grid-based variant which may behave differently.

**Impact on Flowsheet Studio**: Not currently used. If phase envelope features are added in the future, validate completeness of returned envelopes (both bubble and dew branches present, critical point detected).

**Source**: `PTphaseEnvelope.java`, `ThermodynamicOperations.java:1937-2055`

---

## C5: Enthalpy/entropy reference state (T_ref = 273.15 K)

**Severity**: INFO — affects cross-simulator comparisons only

NeqSim uses T_ref = 273.15 K as its thermodynamic reference state. Other simulators (e.g., DWSIM) may use T_ref = 298.15 K. This creates a constant offset in absolute H and S values.

**Impact on Flowsheet Studio**: Since NeqSim is the sole simulation engine, this is internally consistent and does **not** affect simulation correctness. All enthalpy/entropy values within a simulation use the same reference state, so energy balances, duty calculations, and isentropic efficiency computations are correct.

This only matters when comparing Flowsheet Studio results against other simulators or literature values that use a different reference state. For such comparisons, use delta values:
- `ΔH = H(T,P) - H(T_ref, P_ref)` at a common reference condition
- `ΔS = S(T,P) - S(T_ref, P_ref)`

---

## C6: Component data may differ from other simulators

**Severity**: INFO — affects cross-simulator comparisons only

NeqSim has its own component database with literature values for Tc, Pc, ω that may differ slightly from other simulators (e.g., methane Tc: 190.564 vs 190.560 K). While individually small, these propagate through EoS parameters and compound across mixing rules and flash iterations.

**Impact on Flowsheet Studio**: Since NeqSim is the sole engine, this is internally consistent. Component data differences only matter when:
- Comparing results against other simulators (HYSYS, DWSIM, etc.)
- Validating against published literature data that used different property databases
- Users report discrepancies vs. their reference simulator

**Recommendation**: If cross-simulator validation becomes important, document which component property values NeqSim uses and note any significant differences from common databases (DIPPR, NIST).

---

## C7: `initPhysicalProperties()` required before density extraction

**Severity**: HIGH — returns 0 without it

In NeqSim 3.4.0+, `getDensity("kg/m3")` returns 0 unless `initPhysicalProperties()` has been called on the thermo system first. This initializes transport property calculation paths needed by the density method.

**Impact on Flowsheet Studio**: When extracting properties outside the normal `Stream.run()` flow (e.g., in test harnesses or direct thermo system manipulation), `initPhysicalProperties()` must be called after flash calculations and before density extraction.

In production code, `Stream.run()` handles this internally. The risk is in test code and scripts that bypass the stream wrapper — `tests/integration/thermo/conftest.py` already includes this call correctly.

**Workaround**: Always call `system.initPhysicalProperties()` after flash and before property extraction when working with raw thermo systems outside the stream wrapper.

---

## Summary: Wrapper Development Checklist

When working with NeqSim in the Flowsheet Studio wrapper layer:

- [ ] Never use `system.getDensity()` or `system.getDensity("kg/m3")` for two-phase bulk density — compute from per-phase data (C1)
- [ ] Be aware that Peneloux volume correction is silently enabled for PR EoS (C2)
- [ ] Ensure `setMultiPhaseCheck(True)` is called on all thermo systems — already done in `create_thermo_system()` (C3)
- [ ] Call `initPhysicalProperties()` before density extraction when working outside the stream wrapper (C7)
- [ ] For cross-simulator comparisons: use delta H/S values to account for reference state differences (C5)
- [ ] For cross-simulator comparisons: account for component data differences in Tc, Pc, ω (C6)
- [ ] If adding phase envelope features: validate envelope completeness (C4)
