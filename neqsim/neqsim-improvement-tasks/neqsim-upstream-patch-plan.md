# NeqSim Upstream Patch Plan

Concrete patch proposals for a NeqSim fork, organized by priority and risk level.
Based on systematic investigation of C3-C7 alkane system across multiple T,P conditions.

**Strategy**: Fork NeqSim, apply patches incrementally, use forked JAR in `libs/jneqsim/`.
Each PR below is independently valuable and reviewable.

---

## PR A: Fix Corrupted Ghost Outlets in Separator (HIGH priority, LOW risk)

### Problem

When a separator receives single-phase feed, the absent-phase outlet is built from
`getEmptySystemClone()` which produces a system with placeholder composition (feed_z/2
per cloned phase), near-zero flow (~7.7e-31 mol), and garbage intensive properties
(Cp, enthalpy, density all meaningless).

### Affected Files

- `neqsim/src/main/java/neqsim/processSimulation/processEquipment/separator/Separator.java`
  - Missing-phase branch in `run()` method — creates gas/liquid outlet via `getEmptySystemClone()`
- `neqsim/src/main/java/neqsim/processSimulation/processEquipment/separator/ThreePhaseSeparator.java`
  - Same pattern for three-phase case

### Proposed Fix

Replace `getEmptySystemClone()` usage in separator missing-phase branches with a new
helper that builds a proper trace-flow system for the absent outlet:

1. **New helper method** (on `SystemThermo` or as a separator utility):
   ```
   buildIncipientPhaseSystem(feedSystem, absentPhaseType)
   ```
   - Compute incipient composition from K-values:
     - Absent liquid: `x_i = z_i / K_i`, normalized
     - Absent gas: `y_i = z_i * K_i`, normalized
   - Clamp K-values to reasonable range (e.g., 1e-10 to 1e10) to avoid division by zero
   - Fallback to normalized feed z if K-values are invalid (all zero, NaN, etc.)
   - Force the system to single phase of the requested type
   - Set a stable trace flow (e.g., 1e-10 mol/s — not the current 1e-20/1e-30 range)
   - Run TP flash at outlet T,P to get consistent intensive properties

2. **In `Separator.run()`**: Replace the `getEmptySystemClone()` call in the single-phase
   branch with `buildIncipientPhaseSystem(thermoSystem, "gas"/"liquid")`.

### What This Fixes

- Eliminates garbage Cp/enthalpy/density on ghost outlets
- Provides thermodynamically meaningful incipient composition
- Removes our Python-side workaround in `separator.py` (`_replace_ghost_outlets_with_incipient_phase`)

### Risk Assessment

LOW — only changes the absent-phase (zero-flow) outlet path. Two-phase operation is
completely untouched. The ghost outlet already has no effect on mass/energy balances.

### Test Cases

- Two-phase feed (e.g., C3-C7 at 30 bar, 180C): verify gas/liquid outlets unchanged
- Single-phase liquid feed (C3-C7 at 100 bar, 10C): verify gas outlet has incipient
  vapor composition (y_i = z_i * K_i, normalized), zero flow, valid intensive properties
- Single-phase gas feed: verify liquid outlet has incipient liquid composition
- Verify outlet Cp, enthalpy, density are physically reasonable (no NaN, no 1e+30 artifacts)

---

## PR B: Make dewPointTemperatureFlash() Fail Explicitly (HIGH priority, TINY change)

### Problem

`dewPointTemperatureFlash()` catches its own convergence exception and silently returns
the starting temperature. Callers have no way to know the result is garbage.

### Affected Files

- `neqsim/src/main/java/neqsim/thermodynamicoperations/ThermodynamicOperations.java`
  - Line ~1852: exception is caught and commented out / suppressed
  - The `dewPointTemperatureFlash()` method

### Proposed Fix

Re-enable the exception (or return a sentinel/status flag):

**Option 1 (minimal):** Re-throw the convergence exception:
```java
// Current: silently returns starting temperature
catch (Exception e) {
    // logger.error("...", e);  // commented out
}

// Fix: propagate failure
catch (Exception e) {
    throw new neqsim.util.exception.IsNaNException(
        this, "dewPointTemperatureFlash", "Dew point calculation did not converge");
}
```

**Option 2 (API-friendly):** Return a result object with convergence status:
```java
public FlashResult dewPointTemperatureFlash() {
    // ... existing logic ...
    return new FlashResult(temperature, converged, iterations);
}
```

Option 1 is the safest minimal change. Option 2 is better API design but larger scope.

### What This Fixes

- Prevents silent garbage results when dew point calculation fails to converge
- Callers can handle failure explicitly (try alternative approach, report to user)
- `bubblePointTemperatureFlash()` already works correctly — this brings dew point to parity

### Risk Assessment

TINY — one-line change. Callers that don't catch the exception will get a clear error
instead of a silently wrong temperature. This is strictly better behavior.

### Note

In our investigation, dew point flash failed at some conditions (e.g., high-pressure
supercritical region) but worked at others (P=20 bar). The issue is specifically that
failure is indistinguishable from success.

---

## PR C: Force Converged K-Values for Single-Phase Results (HIGH priority, FUNDAMENTAL fix)

### Problem

After single-phase TP flash, `component.getK()` and `getKvector()` return Wilson
correlation initialization estimates, not converged fugacity-based K-values. Wilson K
uses only critical properties and acentric factor — it knows nothing about the actual
EoS, BIPs, or mixture non-ideality.

Evidence: K*P is exactly constant across pressures (6.3834 for propane in C3-C7 system
at 10C), confirming `K_i = (Pc_i/P) * exp(5.37*(1+w_i)*(1-Tc_i/T))`.

Additionally, for single-phase liquid results, NeqSim clones the liquid phase into both
phase slots — fugacity coefficients are identical (|phi_0 - phi_1| ~ 1e-15), making
fugacity-ratio K trivially 1.0 (also useless).

### Why This Is the Most Important Fix

Converged K-values for single-phase results answer the question: "if an infinitesimal
amount of the absent phase appeared at this T,P,z, what would its composition be?"
This is exactly the stability test trial composition — thermodynamically rigorous,
EoS-consistent, and what every downstream consumer of K-values actually needs.

This fix:
- **Strengthens PR A** — separator ghost outlets get EoS-consistent incipient compositions
  instead of Wilson approximations
- **Fixes the fugacity cloning problem** — proper incipient-phase fugacity coefficients
  are computed, not cloned from the existing phase
- **Still needs a K provenance flag** — even with a convergence attempt, the iteration
  may fail to converge. Callers need to know whether K is converged, partially updated,
  or still Wilson (see Implementation Details below)

### Affected Files

- `neqsim/src/main/java/neqsim/thermodynamicoperations/flashops/TPflash.java`
  - Single-phase early-return path (both liquid-stable and gas-stable branches)
  - Currently: K-values left at Wilson initialization, phase cloned to both slots
  - After fix: iterative incipient K convergence before returning
- `neqsim/src/main/java/neqsim/thermo/system/SystemThermo.java`
  - Add K-value provenance enum/flag: `CONVERGED`, `PARTIALLY_UPDATED`, `WILSON`
- `neqsim/src/main/java/neqsim/thermo/system/SystemInterface.java`
  - Expose K-value provenance getter

### Implementation Details

**Critical**: The existing phase slots cannot be trusted for fugacity ratios in
single-phase results. NeqSim clones the stable phase into both slots, so reading
phi from "phase 0 vs phase 1" gives trivial K=1.0. The fix must build an explicit
incipient-phase solve, not rely on existing phase slot data.

**Approach: Dedicated incipient K convergence routine**

Add a private method in TPflash (or a utility class), called on the single-phase
return path:

```
convergeIncipientK(system, stablePhaseType) -> KValueStatus
```

Algorithm:
1. Extract feed z and stable-phase fugacity coefficients `phi_stable_i` by
   evaluating the EoS on the stable phase explicitly (do NOT read from mirrored slots)
2. Initialize trial composition from Wilson K: `w_i = z_i * K_i` (vapor) or
   `w_i = z_i / K_i` (liquid), normalized
3. **Iterate** (max ~20 iterations, tolerance ~1e-8 on K change):
   a. Build a single-phase system of the absent type at T,P with composition w
   b. Call `init(3)` to compute fugacity coefficients `phi_trial_i`
   c. Update K: `K_i = phi_stable_i / phi_trial_i` (convention: K = phi_L / phi_V)
   d. Update trial composition: `w_i = z_i * K_i` (or `z_i / K_i`), normalized
   e. Check convergence: `max(|ln(K_new) - ln(K_old)|) < tolerance`
4. Store converged K on components via `component.setK(K_i)`
5. Return status: `CONVERGED` if tolerance met, `PARTIALLY_UPDATED` if max iters
   hit (still better than Wilson), `WILSON` if routine failed entirely (fallback)

**This is essentially a stability test** — the same successive substitution that
Michelsen's stability analysis uses. The difference is we're not asking "is the
system stable?" (we already know it is), we're asking "what would the incipient
phase look like?"

### K-Value Provenance Flag

Even with the convergence routine, iteration may fail in edge cases (near critical
point, degenerate mixtures). Callers need to distinguish:

```java
public enum KValueStatus {
    CONVERGED,           // Two-phase VLE or successful incipient K iteration
    PARTIALLY_UPDATED,   // Incipient K iteration hit max iters (better than Wilson)
    WILSON               // Fallback — only Wilson initialization available
}

// On SystemThermo:
private KValueStatus kValueStatus = KValueStatus.WILSON;
public KValueStatus getKValueStatus() { return kValueStatus; }
public void setKValueStatus(KValueStatus status) { this.kValueStatus = status; }
```

Set `CONVERGED` in the normal two-phase convergence path (existing behavior).
Set based on iteration result in the single-phase return path (new behavior).

### What This Fixes

- K-values reflect the actual EoS, BIPs, and mixture behavior — not just pure-component
  critical properties
- Incipient phase composition becomes pressure-dependent and thermodynamically meaningful
- Explicit incipient-phase solve avoids the mirrored phase slot pitfall
- Provenance flag lets callers make informed decisions when convergence fails
- Eliminates the fugacity cloning problem as a side effect

### Risk Assessment

MEDIUM — modifies the single-phase return path in TPflash. Two-phase results are
completely untouched (K-values already converged via the normal VLE iteration).

Key safety measures:
- Only runs after single-phase stability is confirmed — phase split decision unchanged
- Failure falls back to Wilson K (current behavior) with explicit status flag
- No changes to two-phase code path whatsoever

### Performance

Each iteration requires one EoS evaluation on the trial phase. With ~5-10 iterations
typical for successive substitution, this adds ~5-10 EoS calls per single-phase flash.
Near the critical point convergence may be slower (successive substitution is first-order).
This is acceptable since single-phase flashes are already the "fast" path.

### Test Cases

- **Two-phase flash unchanged**: C3-C7 at 30 bar, 180C — verify K-values, beta, and
  compositions are identical to current behavior
- **Single-phase liquid**: C3-C7 at 100 bar, 10C — verify K-values are NOT Wilson
  (K*P should vary with pressure, unlike current constant 6.3834)
- **Single-phase gas**: verify K-values give meaningful incipient liquid composition
- **Near phase boundary**: verify converged K approaches the two-phase K continuously
  as conditions move toward the phase envelope
- **Cross-validate**: compare incipient compositions with DWSIM dew/bubble point results
- **Convergence status**: verify `getKValueStatus()` returns `CONVERGED` for two-phase
  and for successful incipient K iteration
- **Fallback behavior**: verify that if iteration fails, K reverts to Wilson and status
  is `WILSON` (no crash, no garbage)
- **Performance**: verify no measurable slowdown for typical flowsheet solve

---

## PR D: Mixing Rule Type Getter (LOW priority, convenience)

### Problem

When reconstructing a thermo system (e.g., for incipient phase outlet), we need to know
the mixing rule type number to pass to `setMixingRule()`. Currently available via
`getMixingRule().getValue()` but this is indirect and undocumented.

### Affected Files

- `neqsim/src/main/java/neqsim/thermo/system/SystemInterface.java`
- `neqsim/src/main/java/neqsim/thermo/system/SystemThermo.java`

### Proposed Fix

```java
// In SystemInterface.java:
int getMixingRuleTypeNumber();

// In SystemThermo.java:
public int getMixingRuleTypeNumber() {
    return getMixingRule().getValue();
}
```

### Risk Assessment

NONE — purely additive convenience getter.

---

## Implementation Order

| Order | PR | Priority | Risk | Effort | Dependencies |
|-------|-----|----------|------|--------|--------------|
| 1 | B | HIGH | TINY | ~1 hour | None |
| 2 | C | HIGH | MEDIUM | ~1 day | None |
| 3 | A | HIGH | LOW | ~4 hours | Benefits greatly from C |
| 4 | D | LOW | NONE | ~30 min | None |

**Recommended first patch**: PR B (dew point failure) — smallest change, highest
confidence, immediately useful. Validates the fork/build/deploy workflow with
minimal risk.

**Most fundamental fix**: PR C (converged K-values) — fixes the root cause. Wilson K
is a crude approximation that ignores the EoS entirely. With converged K, the separator
ghost outlet fix (PR A) gets thermodynamically rigorous incipient compositions for free,
and the fugacity cloning problem disappears as a side effect.

**Highest practical impact**: PR A (separator ghost outlets) — eliminates our Python-side
workaround entirely. Even better when combined with PR C (EoS-consistent K instead of
Wilson K for incipient composition).

---

## Fork & Integration Strategy

1. Fork NeqSim repo
2. Apply patches on a branch per PR
3. Build JAR: `mvn package -DskipTests` (or with tests if they pass)
4. Replace `libs/jneqsim/` JAR with patched version
5. Update wrapper code to remove Python-side workarounds as patches land
6. Tag fork versions (e.g., `v3.4.0-fs.1`) for traceability

### Removing Our Workarounds After Patches

After **PR C** (converged K-values) lands:
- Our Wilson K-based incipient composition in `separator.py` automatically improves —
  same code path now uses EoS-consistent K instead of Wilson K
- Can check `getKValueStatus()` to decide whether to trust K-values or fall back
- No code change needed in our wrapper for the improvement, but we could add
  provenance-aware logic (e.g., warn if K is still Wilson after the convergence attempt)

After **PR A** (separator ghost outlets) lands in the fork:
- Remove `_replace_ghost_outlets_with_incipient_phase()` from `separator.py`
- Remove `_compute_incipient_liquid_composition()` and `_compute_incipient_vapor_composition()`
- Remove `_set_incipient_thermo_on_outlet()`
- Simplify `calculate()` method — no post-processing needed
- If PR C is also applied, ghost outlets get fully rigorous incipient compositions

After **PR B** (dew point failure) lands:
- Can properly handle dew point calculation failures in any future dew point flash usage
- Wrap calls in try/catch with meaningful fallback instead of silently using garbage T
