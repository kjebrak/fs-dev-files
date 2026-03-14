#!/usr/bin/env python3
"""Debug: Zero and near-zero flow rate property extraction failures.

Investigates two distinct bugs:
  Bug 1: Source stream with flow_rate=0.0 → NaN density, ArithmeticException for kJ/kg
  Bug 2: Separator ghost outlet (~1e-27 kg/hr) → garbage but finite intensive properties

Key finding from NeqSim repo investigation:
  - Bug 1 is a unit-conversion issue: per-mass division by zero total mass
  - Bug 2 is a DIFFERENT, deeper issue: EOS residual terms with tiny-n denominators
    in the ghost state, NOT just stale beta. Confirmed by Java diagnostic test:
      * initBeta() does NOT fix Cp/H (still huge)
      * Collapsing to 1 phase fixes Z (1.986→0.993) but NOT Cp/H
      * Rescaling same one-phase state to 1000 kg/hr DOES fix everything
    → Ghost state has thermodynamic instability at ultra-small n, amplified by
      per-mass conversion. EOS residual/derivative terms (1/(nV-B)^2 etc.) explode.

Run with: uv run python .dev/dev_wdir/debugging/debug_near_zero_and_zero_flow_rate_error.py
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from jneqsim import neqsim  # noqa: E402


# ── Helpers ───────────────────────────────────────────────────────────────


def divider(title: str) -> None:
    print(f"\n{'=' * 72}")
    print(f"  {title}")
    print(f"{'=' * 72}")


def check_value(name: str, getter, width: int = 30) -> tuple[str, float | None, bool]:
    """Extract a value and check if it's finite and reasonable."""
    try:
        v = getter()
        if v is None:
            return (f"  {name:{width}s} = {'None':>25s}  [NONE]", None, False)
        if not math.isfinite(v):
            return (f"  {name:{width}s} = {str(v):>25s}  [INF/NAN]", v, False)
        # Magnitude check: flag absurdly large values (> 1e10 for intensive props)
        if abs(v) > 1e10 and name not in ("numberOfMoles", "flow_rate (kg/hr)"):
            return (
                f"  {name:{width}s} = {v:>25.4e}  [GARBAGE - finite but absurd]",
                v,
                False,
            )
        return (f"  {name:{width}s} = {v:>25.6g}  [OK]", v, True)
    except Exception as e:
        short = str(e).split("\n")[0][:60]
        return (f"  {name:{width}s} = EXCEPTION: {short}", None, False)


def check_stream_properties(
    label: str, stream, thermo, include_transport: bool = True
) -> dict:
    """Check all properties on a stream/thermo system. Returns summary dict."""
    print(f"\n── {label} ──")

    results = {}
    basic_props = {
        "flow_rate (kg/hr)": lambda: stream.getFlowRate("kg/hr"),
        "temperature (C)": lambda: stream.getTemperature("C"),
        "pressure (bara)": lambda: stream.getPressure("bara"),
        "numberOfMoles": lambda: thermo.getNumberOfMoles(),
        "numberOfPhases": lambda: float(thermo.getNumberOfPhases()),
        "density (kg/m3)": lambda: thermo.getDensity("kg/m3"),
        "density (no unit)": lambda: thermo.getDensity(),
        "molar_mass (kg/mol)": lambda: thermo.getMolarMass(),
        "molar_volume": lambda: thermo.getMolarVolume(),
        "z_factor": lambda: thermo.getZ(),
        "enthalpy (kJ/kg)": lambda: thermo.getEnthalpy("kJ/kg"),
        "enthalpy (J)": lambda: thermo.getEnthalpy("J"),
        "entropy (kJ/kgK)": lambda: thermo.getEntropy("kJ/kgK"),
        "cp (kJ/kgK)": lambda: thermo.getCp("kJ/kgK"),
        "cv (kJ/kgK)": lambda: thermo.getCv("kJ/kgK"),
    }

    ok_count = 0
    fail_count = 0
    for name, getter in basic_props.items():
        line, val, is_ok = check_value(name, getter)
        print(line)
        results[name] = {"value": val, "ok": is_ok}
        if is_ok:
            ok_count += 1
        else:
            fail_count += 1

    if include_transport:
        print(f"  {'--- transport ---':30s}")
        try:
            thermo.initPhysicalProperties()
            transport_props = {
                "viscosity (cP)": lambda: thermo.getViscosity("cP"),
                "thermal_cond (W/mK)": lambda: thermo.getThermalConductivity("W/mK"),
                "speed_of_sound": lambda: thermo.getSoundSpeed(),
            }
            for name, getter in transport_props.items():
                line, val, is_ok = check_value(name, getter)
                print(line)
                results[name] = {"value": val, "ok": is_ok}
                if is_ok:
                    ok_count += 1
                else:
                    fail_count += 1
        except Exception as e:
            print(f"  initPhysicalProperties FAILED: {e}")
            fail_count += 1

    print(f"  Summary: {ok_count} OK, {fail_count} failed")
    return results


def make_stream(
    name: str,
    composition: dict[str, float],
    temp_c: float = 25.0,
    pressure_bar: float = 50.0,
    flow_kg_hr: float = 1000.0,
):
    """Create a NeqSim stream (convenience helper)."""
    temp_k = temp_c + 273.15
    thermo = neqsim.thermo.system.SystemSrkEos(temp_k, pressure_bar)
    for comp, frac in composition.items():
        thermo.addComponent(comp, frac)
    thermo.setMixingRule(2)
    thermo.setTotalFlowRate(flow_kg_hr, "kg/hr")
    stream = neqsim.process.equipment.stream.Stream(name, thermo)
    stream.run()
    return stream


COMP_DRY = {"methane": 0.95, "ethane": 0.03, "propane": 0.02}
COMP_RICH = {"methane": 0.70, "ethane": 0.10, "propane": 0.10, "n-butane": 0.10}


# ══════════════════════════════════════════════════════════════════════════
# BUG 1: Source stream with exactly zero flow
# ══════════════════════════════════════════════════════════════════════════
divider("BUG 1: Source stream with flow_rate = 0.0 kg/hr")
print("NeqSim calls setEmptyFluid() → totalNumberOfMoles = 0.0")
print("Per-mass conversions (kJ/kg, kJ/kgK) divide by zero → exception")
print("getDensity('kg/m3') uses phaseVol/totalVol → 0/0 = NaN")

s_zero = make_stream("zero_flow", COMP_DRY, flow_kg_hr=0.0)
r_zero = check_stream_properties(
    "Zero-flow source stream", s_zero, s_zero.getThermoSystem()
)

# ── Show that per-mole conversions ALSO fail ──
print("\n── Additional unit variants ──")
thermo_zero = s_zero.getThermoSystem()
for method, unit in [
    ("getEnthalpy", "J/mol"),
    ("getEnthalpy", "kJ/kmol"),
    ("getCp", "J/molK"),
]:
    try:
        v = getattr(thermo_zero, method)(unit)
        print(f"  {method}('{unit}'): {v}")
    except Exception as e:
        print(f"  {method}('{unit}'): EXCEPTION — {e}")

# ── Show total J enthalpy works (no per-mass division) ──
print(f"\n  getEnthalpy('J') = {thermo_zero.getEnthalpy('J'):.6f}  ← works (no /mass)")


# ══════════════════════════════════════════════════════════════════════════
# BUG 1 FIX VERIFICATION: Tiny flow produces correct intensive properties
# ══════════════════════════════════════════════════════════════════════════
divider("BUG 1 FIX: Verify epsilon-flow substitution")
print("Hypothesis: intensive properties at 1e-10 kg/hr == at 1000 kg/hr")

s_tiny = make_stream("tiny_flow", COMP_DRY, flow_kg_hr=1e-10)
s_ref = make_stream("ref_flow", COMP_DRY, flow_kg_hr=1000.0)

r_tiny = check_stream_properties(
    "Tiny flow (1e-10 kg/hr)", s_tiny, s_tiny.getThermoSystem(), include_transport=False
)
r_ref = check_stream_properties(
    "Reference flow (1000 kg/hr)",
    s_ref,
    s_ref.getThermoSystem(),
    include_transport=False,
)

print("\n── Comparison: tiny vs reference ──")
intensive_keys = [
    "density (kg/m3)",
    "enthalpy (kJ/kg)",
    "entropy (kJ/kgK)",
    "cp (kJ/kgK)",
    "cv (kJ/kgK)",
    "z_factor",
]
for k in intensive_keys:
    v_tiny = r_tiny.get(k, {}).get("value")
    v_ref = r_ref.get(k, {}).get("value")
    if v_tiny is not None and v_ref is not None and v_ref != 0:
        rel_err = abs(v_tiny - v_ref) / abs(v_ref)
        match = "MATCH" if rel_err < 1e-6 else f"DIFFERS by {rel_err:.2e}"
    else:
        match = "N/A"
    print(f"  {k:25s}  tiny={str(v_tiny):>15s}  ref={str(v_ref):>15s}  [{match}]")


# ══════════════════════════════════════════════════════════════════════════
# BUG 2: Separator ghost-flow outlet
# ══════════════════════════════════════════════════════════════════════════
divider("BUG 2: Separator ghost-flow outlet (~1e-27 kg/hr)")
print("NeqSim's getEmptySystemClone() divides all moles by 1e30.")
print("Creates 2 degenerate phases with halved compositions.")
print("EOS residual terms explode at tiny n → garbage intensive properties.")

s_inlet = make_stream("inlet", COMP_DRY)
sep = neqsim.process.equipment.separator.Separator("sep", s_inlet)
sep.run()

gas_out = sep.getGasOutStream()
liq_out = sep.getLiquidOutStream()

gas_flow = gas_out.getFlowRate("kg/hr")
liq_flow = liq_out.getFlowRate("kg/hr")
print(f"\n  Inlet: 1000 kg/hr  →  Gas: {gas_flow:.4e}  Liq: {liq_flow:.4e}")

r_gas = check_stream_properties(
    "Gas outlet (real flow)", gas_out, gas_out.getThermoSystem()
)
r_ghost = check_stream_properties(
    "Liquid outlet (GHOST flow)", liq_out, liq_out.getThermoSystem()
)


# ── Inspect ghost phase structure ──
divider("BUG 2 DETAIL: Ghost system phase structure")
lt = liq_out.getThermoSystem()
print(f"  Total moles: {lt.getNumberOfMoles():.4e}")
print(f"  System molar_mass: {lt.getMolarMass():.6f} (expected ~0.01702)")
print(f"  Number of phases: {lt.getNumberOfPhases()}")

for i in range(lt.getNumberOfPhases()):
    p = lt.getPhase(i)
    print(f"\n  Phase {i} ({p.getPhaseTypeName()}):")
    print(f"    moles: {p.getNumberOfMolesInPhase():.4e}")
    print(f"    molar_mass: {p.getMolarMass():.6f}")
    print(f"    Vm: {p.getMolarVolume():.4f}")
    print(f"    density(): {p.getDensity():.4f}")
    for j in range(p.getNumberOfComponents()):
        c = p.getComponent(j)
        print(f"      {str(c.getName()):>10s}: x={c.getx():.6f} (expected ~{COMP_DRY.get(str(c.getName()), '?')})")


# ══════════════════════════════════════════════════════════════════════════
# BUG 2 ISOLATION: What causes the garbage — stale beta or tiny-n EOS?
# ══════════════════════════════════════════════════════════════════════════
divider("BUG 2 ISOLATION: Stale beta vs tiny-n EOS residuals")
print("NeqSim Java test confirmed:")
print("  - initBeta() does NOT fix Cp/H (still huge)")
print("  - Collapsing to 1 phase fixes Z (1.986→0.993) but NOT Cp/H")
print("  - Rescaling to 1000 kg/hr DOES fix everything")
print("Verifying same behavior from Python via JPype...\n")

# Test 1: initBeta on ghost system
lt_test = liq_out.getThermoSystem()
print("── Before initBeta() ──")
z_before = lt_test.getZ()
try:
    h_before = lt_test.getEnthalpy("kJ/kg")
except Exception:
    h_before = "exception"
try:
    cp_before = lt_test.getCp("kJ/kgK")
except Exception:
    cp_before = "exception"
print(f"  Z={z_before}  H={h_before}  Cp={cp_before}")

print("\n── After initBeta() ──")
lt_test.initBeta()
z_after = lt_test.getZ()
try:
    h_after = lt_test.getEnthalpy("kJ/kg")
except Exception:
    h_after = "exception"
try:
    cp_after = lt_test.getCp("kJ/kgK")
except Exception:
    cp_after = "exception"
print(f"  Z={z_after}  H={h_after}  Cp={cp_after}")
print(f"  → initBeta changed Z? {z_before != z_after}")
print(f"  → initBeta changed H? {h_before != h_after}")

# Test 2: Collapse to single phase
print("\n── Collapse to single phase (phaseToSystem) ──")
try:
    one_phase = lt_test.phaseToSystem(0)
    one_phase.init(1)
    z_1p = one_phase.getZ()
    h_1p = one_phase.getEnthalpy("kJ/kg")
    cp_1p = one_phase.getCp("kJ/kgK")
    print(f"  Z={z_1p}  H={h_1p}  Cp={cp_1p}")
    print(f"  → Z fixed? {'YES' if abs(z_1p) < 2 else 'NO'}")
    print(f"  → H fixed? {'YES' if abs(h_1p) < 1e4 else 'NO'}")
    print(f"  → Cp fixed? {'YES' if abs(cp_1p) < 1e4 else 'NO'}")
except Exception as e:
    print(f"  phaseToSystem failed: {e}")
    one_phase = None

# Test 3: Rescale one-phase to normal flow
if one_phase is not None:
    print("\n── Rescale one-phase to 1000 kg/hr ──")
    try:
        rescaled = one_phase.clone()
        rescaled.setTotalFlowRate(1000.0, "kg/hr")
        rescaled.init(1)
        z_rs = rescaled.getZ()
        h_rs = rescaled.getEnthalpy("kJ/kg")
        cp_rs = rescaled.getCp("kJ/kgK")
        print(f"  Z={z_rs:.6f}  H={h_rs:.4f}  Cp={cp_rs:.4f}")
        print(f"  → All fixed? Z={'YES' if abs(z_rs) < 2 else 'NO'}, "
              f"H={'YES' if abs(h_rs) < 1e4 else 'NO'}, "
              f"Cp={'YES' if abs(cp_rs) < 1e4 else 'NO'}")
    except Exception as e:
        print(f"  Rescale failed: {e}")


# ══════════════════════════════════════════════════════════════════════════
# BUG 2 THRESHOLD: At what flow does the ghost state produce garbage?
# ══════════════════════════════════════════════════════════════════════════
divider("BUG 2 THRESHOLD: Ghost-state flow sweep")
print("Testing ghost state rescaled to different flows to find threshold.\n")

# Get ghost system and collapse to 1 phase for cleaner testing
lt_ghost = liq_out.getThermoSystem()

print("Testing on ghost system (2 degenerate phases, as-is from separator):")
print(f"  {'flow_kg_hr':>14s}  {'density':>12s}  {'H_kJ_kg':>14s}  {'Cp_kJ_kgK':>14s}  {'Z':>10s}  status")
print("  " + "-" * 80)

# Reference values from normal stream
ref_density = r_ref["density (kg/m3)"]["value"]
ref_h = r_ref["enthalpy (kJ/kg)"]["value"]
ref_cp = r_ref["cp (kJ/kgK)"]["value"]

flows = [1e3, 1e2, 1e1, 1e0, 1e-1, 1e-2, 1e-4, 1e-6, 1e-8, 1e-10, 1e-12, 1e-15, 1e-20, 1e-27]

for flow in flows:
    try:
        test_sys = lt_ghost.clone()
        test_sys.setTotalFlowRate(flow, "kg/hr")
        test_sys.init(0)
        test_sys.init(1)
    except Exception as e:
        print(f"  {flow:>14.0e}  INIT FAILED: {e}")
        continue

    try:
        d = test_sys.getDensity("kg/m3")
    except Exception:
        d = float("nan")
    try:
        h = test_sys.getEnthalpy("kJ/kg")
    except Exception:
        h = float("nan")
    try:
        cp = test_sys.getCp("kJ/kgK")
    except Exception:
        cp = float("nan")
    try:
        z = test_sys.getZ()
    except Exception:
        z = float("nan")

    # Check if values are reasonable (within 50% of reference for intensive props)
    d_ok = math.isfinite(d) and ref_density and abs(d) < 10 * abs(ref_density)
    h_ok = math.isfinite(h) and ref_h is not None and abs(h) < 1e4
    cp_ok = math.isfinite(cp) and ref_cp is not None and abs(cp) < 100
    z_ok = math.isfinite(z) and 0.01 < z < 5

    all_ok = d_ok and h_ok and cp_ok and z_ok
    status = "OK" if all_ok else "GARBAGE"
    flags = []
    if not d_ok:
        flags.append("d")
    if not h_ok:
        flags.append("H")
    if not cp_ok:
        flags.append("Cp")
    if not z_ok:
        flags.append("Z")

    print(
        f"  {flow:>14.0e}  {d:>12.4g}  {h:>14.4g}  {cp:>14.4g}  {z:>10.4g}  "
        f"[{status}{'  ← ' + ','.join(flags) if flags else ''}]"
    )


# ══════════════════════════════════════════════════════════════════════════
# BUG 2 THRESHOLD: Clean-state flow sweep (for comparison)
# ══════════════════════════════════════════════════════════════════════════
divider("COMPARISON: Clean-state (fresh thermo system) flow sweep")
print("Same composition but created fresh (not from separator ghost clone).\n")

print(f"  {'flow_kg_hr':>14s}  {'density':>12s}  {'H_kJ_kg':>14s}  {'Cp_kJ_kgK':>14s}  {'Z':>10s}  status")
print("  " + "-" * 80)

for flow in flows:
    try:
        s_test = make_stream(f"clean_{flow}", COMP_DRY, flow_kg_hr=max(flow, 1e-100))
        t_test = s_test.getThermoSystem()
    except Exception as e:
        print(f"  {flow:>14.0e}  MAKE FAILED: {e}")
        continue

    try:
        d = t_test.getDensity("kg/m3")
    except Exception:
        d = float("nan")
    try:
        h = t_test.getEnthalpy("kJ/kg")
    except Exception:
        h = float("nan")
    try:
        cp = t_test.getCp("kJ/kgK")
    except Exception:
        cp = float("nan")
    try:
        z = t_test.getZ()
    except Exception:
        z = float("nan")

    d_ok = math.isfinite(d) and ref_density and abs(d) < 10 * abs(ref_density)
    h_ok = math.isfinite(h) and abs(h) < 1e4
    cp_ok = math.isfinite(cp) and abs(cp) < 100
    z_ok = math.isfinite(z) and 0.01 < z < 5

    all_ok = d_ok and h_ok and cp_ok and z_ok
    status = "OK" if all_ok else "GARBAGE"
    flags = []
    if not d_ok:
        flags.append("d")
    if not h_ok:
        flags.append("H")
    if not cp_ok:
        flags.append("Cp")
    if not z_ok:
        flags.append("Z")

    print(
        f"  {flow:>14.0e}  {d:>12.4g}  {h:>14.4g}  {cp:>14.4g}  {z:>10.4g}  "
        f"[{status}{'  ← ' + ','.join(flags) if flags else ''}]"
    )


# ══════════════════════════════════════════════════════════════════════════
# APPROACH VALIDATION: Can we rescue ghost state by rescaling?
# ══════════════════════════════════════════════════════════════════════════
divider("APPROACH VALIDATION: Rescue ghost outlet via rescale")
print("If we detect near-zero flow on the ghost outlet, can we:")
print("1. Clone the thermo system")
print("2. Set flow to 1e-6 kg/hr (small but not tiny-n pathological)")
print("3. Re-init and extract intensive properties")
print("4. Report flow_rate=0 with valid intensive properties?")

lt_rescue = liq_out.getThermoSystem().clone()
lt_rescue.setTotalFlowRate(1e-6, "kg/hr")
lt_rescue.init(0)
lt_rescue.init(1)
lt_rescue.init(2)
lt_rescue.init(3)

print("\n── Rescued ghost system (1e-6 kg/hr) ──")
try:
    d_rescue = lt_rescue.getDensity("kg/m3")
    h_rescue = lt_rescue.getEnthalpy("kJ/kg")
    cp_rescue = lt_rescue.getCp("kJ/kgK")
    z_rescue = lt_rescue.getZ()
    print(f"  density:  {d_rescue:.4f} kg/m3")
    print(f"  enthalpy: {h_rescue:.4f} kJ/kg")
    print(f"  Cp:       {cp_rescue:.4f} kJ/kgK")
    print(f"  Z:        {z_rescue:.6f}")

    # Compare to reference
    print(f"\n── Reference (1000 kg/hr, same composition) ──")
    print(f"  density:  {ref_density} kg/m3")
    print(f"  enthalpy: {ref_h} kJ/kg")
    print(f"  Cp:       {ref_cp} kJ/kgK")

    if ref_density and ref_h and ref_cp:
        d_err = abs(d_rescue - ref_density) / abs(ref_density) if ref_density else 0
        h_err = abs(h_rescue - ref_h) / abs(ref_h) if ref_h else 0
        cp_err = abs(cp_rescue - ref_cp) / abs(ref_cp) if ref_cp else 0
        print(f"\n  Relative errors: density={d_err:.2e}, H={h_err:.2e}, Cp={cp_err:.2e}")
        all_close = d_err < 0.01 and h_err < 0.01 and cp_err < 0.01
        print(f"  All within 1%? {'YES — rescue works!' if all_close else 'NO — ghost state is too corrupted'}")
except Exception as e:
    print(f"  Rescue FAILED: {e}")


# ══════════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════════
divider("SUMMARY OF FINDINGS")

print("""
BUG 1: Source stream with flow_rate = 0.0
  Root cause: NeqSim's setEmptyFluid() sets totalNumberOfMoles = 0.0
  Failure:    getDensity("kg/m3") → NaN (phaseVol/totalVol = 0/0)
              getEnthalpy("kJ/kg") → ArithmeticException (explicit guard)
              getEnthalpy("J") → works! (no per-mass division)
  Fix proven: Setting 1e-10 kg/hr gives identical intensive props as 1000 kg/hr

BUG 2: Separator ghost-flow outlet (~1e-27 kg/hr)
  Root cause: getEmptySystemClone() divides moles by 1e30 → 2 degenerate phases
              EOS residual terms (1/(nV-B)^2 etc.) explode at ultra-small n
              NOT just stale beta — confirmed by Java diagnostic test
  Failure:    All intensive props are garbage but pass math.isfinite()
              Cp ~ 1e116, H ~ 1e60, speed_of_sound ~ 1e31
              Molar mass halved (0.0085 vs 0.0170) due to degenerate phases
  Fix:        Ghost outlet needs rescale to physically meaningful flow

ADDITIONAL ISSUE: Error message wording
  Current:  "Critical properties returned invalid values (inf/NaN): density"
  Problem:  "Critical" means Tc/Pc/ρc in chemical engineering
  Fix:      Use "Essential properties" or "Key properties"
""")
