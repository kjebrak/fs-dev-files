#!/usr/bin/env python3
"""Debug: Resolve open questions from plan review for zero/ghost-flow recovery.

This script answers critical implementation questions identified during the plan
review, so we can commit to specific approaches BEFORE writing production code.

Open questions tested:
  Q1: Does setTotalFlowRate(0) after separator flash corrupt compositions?
      → Determines whether we use option (a) set-flow-to-zero or option (b) flag-based
  Q2: Can we read the mixing rule from an existing thermo system?
      → Determines hardcode-2 vs read-dynamically
  Q3: Does type(thermo) constructor work for all EoS classes we support?
      → Validates the EoS preservation approach
  Q4: What happens when ALL z-fractions are below the 1e-20 filter threshold?
      → Validates the empty-component-list guard
  Q5: Does setTotalFlowRate(1e-20) preserve compositions without corruption?
      → Sub-threshold alternative if Q1 shows corruption
  Q6: Can we read z-fractions from a zero-flow (setEmptyFluid) system?
      → Validates that recovery can read composition from Bug 1 systems

Run with: uv run python .dev/dev_wdir/debugging/zero_flow_and_ghost_flow_bug/debug_plan_open_questions.py
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "src"))

from jneqsim import neqsim  # noqa: E402

COMP_DRY = {"methane": 0.95, "ethane": 0.03, "propane": 0.02}
COMP_RICH = {"methane": 0.70, "ethane": 0.10, "propane": 0.10, "n-butane": 0.10}


def divider(title: str) -> None:
    print(f"\n{'=' * 72}")
    print(f"  {title}")
    print(f"{'=' * 72}")


def print_composition(label: str, thermo) -> dict:
    """Read and print component z-fractions from a thermo system."""
    n_comp = thermo.getNumberOfComponents()
    comp_data = {}
    total_z = 0.0
    for i in range(n_comp):
        c = thermo.getComponent(i)
        name = str(c.getName())
        z = float(c.getz())
        comp_data[name] = z
        total_z += z
    print(f"  {label}")
    for name, z in comp_data.items():
        print(f"    {name}: z = {z:.6e}")
    print(f"    SUM(z) = {total_z:.6e}")
    return comp_data


def print_props(label: str, thermo) -> dict:
    """Extract and print key intensive properties."""
    props = {}
    for prop_name, getter in [
        ("density", lambda: thermo.getDensity("kg/m3")),
        ("enthalpy", lambda: thermo.getEnthalpy("kJ/kg")),
        ("cp", lambda: thermo.getCp("kJ/kgK")),
        ("z_factor", lambda: thermo.getZ()),
    ]:
        try:
            v = float(getter())
        except Exception:
            v = float("nan")
        props[prop_name] = v
    print(f"  {label}")
    print(f"    density={props['density']:.4f}  H={props['enthalpy']:.4f}  "
          f"Cp={props['cp']:.4f}  Z={props['z_factor']:.6f}")
    return props


def compare_props(label: str, test: dict, ref: dict) -> bool:
    """Compare test vs reference properties, return True if all match within 0.01%."""
    all_ok = True
    diffs = []
    for k in ref:
        rv = ref[k]
        tv = test.get(k, float("nan"))
        if rv != 0 and math.isfinite(rv) and math.isfinite(tv):
            err = abs(tv - rv) / abs(rv) * 100
            diffs.append(f"{k}={err:.4f}%")
            if err > 0.01:
                all_ok = False
        elif not math.isfinite(tv):
            diffs.append(f"{k}=NaN/Inf!")
            all_ok = False
    result = "PASS" if all_ok else "FAIL"
    print(f"  {label}: {' '.join(diffs)} [{result}]")
    return all_ok


# ══════════════════════════════════════════════════════════════════════════
# Q1: Does setTotalFlowRate(0) after separator flash corrupt compositions?
# ══════════════════════════════════════════════════════════════════════════
divider("Q1: setTotalFlowRate(0) after separator flash — composition integrity")

temp_k = 25.0 + 273.15

# Create a real two-phase separator at normal flow to get correct phase compositions
t1 = neqsim.thermo.system.SystemSrkEos(temp_k, 50.0)
for c, f in COMP_RICH.items():
    t1.addComponent(c, f)
t1.setMixingRule(2)
t1.setTotalFlowRate(1000.0, "kg/hr")
s1 = neqsim.process.equipment.stream.Stream("rich_inlet", t1)
s1.run()
sep1 = neqsim.process.equipment.separator.Separator("sep1", s1)
sep1.run()

# Capture reference compositions BEFORE setting flow to 0
gas1 = sep1.getGasOutStream()
liq1 = sep1.getLiquidOutStream()

gas_comp_before = print_composition("Gas outlet BEFORE setTotalFlowRate(0):", gas1.getThermoSystem())
liq_comp_before = print_composition("Liquid outlet BEFORE setTotalFlowRate(0):", liq1.getThermoSystem())
gas_flow_before = float(gas1.getFlowRate("kg/hr"))
liq_flow_before = float(liq1.getFlowRate("kg/hr"))
print(f"\n  Gas flow before: {gas_flow_before:.4f} kg/hr")
print(f"  Liq flow before: {liq_flow_before:.4f} kg/hr")

# Now set flow to 0 on both outlets
gas1.getThermoSystem().setTotalFlowRate(0.0, "kg/hr")
liq1.getThermoSystem().setTotalFlowRate(0.0, "kg/hr")

gas_comp_after = print_composition("\nGas outlet AFTER setTotalFlowRate(0):", gas1.getThermoSystem())
liq_comp_after = print_composition("Liquid outlet AFTER setTotalFlowRate(0):", liq1.getThermoSystem())

gas_flow_after = float(gas1.getFlowRate("kg/hr"))
liq_flow_after = float(liq1.getFlowRate("kg/hr"))
print(f"\n  Gas flow after: {gas_flow_after:.4e} kg/hr")
print(f"  Liq flow after: {liq_flow_after:.4e} kg/hr")

# Check if compositions survived
q1_gas_ok = all(
    abs(gas_comp_before[k] - gas_comp_after[k]) < 1e-10
    for k in gas_comp_before
)
q1_liq_ok = all(
    abs(liq_comp_before[k] - liq_comp_after[k]) < 1e-10
    for k in liq_comp_before
)
print(f"\n  Gas compositions preserved? {'YES' if q1_gas_ok else 'NO — CORRUPTED!'}")
print(f"  Liq compositions preserved? {'YES' if q1_liq_ok else 'NO — CORRUPTED!'}")

# Also check: can we read totalNumberOfMoles after setTotalFlowRate(0)?
try:
    gas_moles = float(gas1.getThermoSystem().getTotalNumberOfMoles())
    print(f"  Gas totalNumberOfMoles after: {gas_moles:.4e}")
except Exception as e:
    print(f"  Gas totalNumberOfMoles after: ERROR — {e}")

# Check if setEmptyFluid() was called internally
# (signature: totalNumberOfMoles becomes 0, component moles become 0)
try:
    gas_c0_moles = float(gas1.getThermoSystem().getComponent(0).getNumberOfMolesInPhase())
    print(f"  Gas component[0] moles in phase: {gas_c0_moles:.4e}")
except Exception as e:
    print(f"  Gas component[0] moles: ERROR — {e}")

# Try to recover from this state with our rescue approach
print("\n  Attempting rescue from post-setTotalFlowRate(0) state...")
gas_thermo_zero = gas1.getThermoSystem()
comp_data_rescue = []
n_comp = gas_thermo_zero.getNumberOfComponents()
total_z_rescue = 0.0
for i in range(n_comp):
    c = gas_thermo_zero.getComponent(i)
    name = str(c.getName())
    z = float(c.getz())
    comp_data_rescue.append((name, z))
    total_z_rescue += z
print(f"  z-fractions after setTotalFlowRate(0): sum={total_z_rescue:.6e}")
for name, z in comp_data_rescue:
    print(f"    {name}: {z:.6e}")

# Q1 VERDICT
q1_verdict = q1_gas_ok and q1_liq_ok and total_z_rescue > 0.5
print(f"\n  >>> Q1 VERDICT: Option (a) setTotalFlowRate(0) is "
      f"{'SAFE — compositions preserved' if q1_verdict else 'UNSAFE — use option (b) or sub-threshold flow'}")


# ══════════════════════════════════════════════════════════════════════════
# Q5: Does setTotalFlowRate(1e-20) work as alternative?
# ══════════════════════════════════════════════════════════════════════════
divider("Q5: setTotalFlowRate(1e-20) as alternative — composition + flow check")

# Create fresh separator for clean test
t5 = neqsim.thermo.system.SystemSrkEos(temp_k, 50.0)
for c, f in COMP_RICH.items():
    t5.addComponent(c, f)
t5.setMixingRule(2)
t5.setTotalFlowRate(1000.0, "kg/hr")
s5 = neqsim.process.equipment.stream.Stream("rich5", t5)
s5.run()
sep5 = neqsim.process.equipment.separator.Separator("sep5", s5)
sep5.run()

gas5 = sep5.getGasOutStream()
gas_comp_5_before = print_composition("Gas BEFORE setTotalFlowRate(1e-20):", gas5.getThermoSystem())

# Set sub-threshold flow
gas5.getThermoSystem().setTotalFlowRate(1e-20, "kg/hr")

gas_comp_5_after = print_composition("Gas AFTER setTotalFlowRate(1e-20):", gas5.getThermoSystem())
gas5_flow_after = float(gas5.getFlowRate("kg/hr"))
print(f"  Gas flow after: {gas5_flow_after:.4e} kg/hr")
print(f"  Below threshold (1e-10)? {'YES' if gas5_flow_after < 1e-10 else 'NO'}")

q5_ok = all(
    abs(gas_comp_5_before[k] - gas_comp_5_after[k]) < 1e-10
    for k in gas_comp_5_before
)
print(f"  Compositions preserved? {'YES' if q5_ok else 'NO'}")
print(f"  >>> Q5 VERDICT: Sub-threshold flow (1e-20) is "
      f"{'SAFE' if q5_ok and gas5_flow_after < 1e-10 else 'UNSAFE or not below threshold'}")


# ══════════════════════════════════════════════════════════════════════════
# Q2: Can we read the mixing rule from an existing thermo system?
# ══════════════════════════════════════════════════════════════════════════
divider("Q2: Reading mixing rule from existing thermo system")

test_systems = {
    "SRK": neqsim.thermo.system.SystemSrkEos(temp_k, 50.0),
    "PR": neqsim.thermo.system.SystemPrEos(temp_k, 50.0),
}

for eos_name, sys_obj in test_systems.items():
    for c, f in COMP_DRY.items():
        sys_obj.addComponent(c, f)
    sys_obj.setMixingRule(2)

    # Try various methods to read mixing rule back
    methods_to_try = [
        ("getMixingRule()", lambda s=sys_obj: s.getMixingRule()),
        ("getMixingRuleNumber()", lambda s=sys_obj: s.getMixingRuleNumber()),
        ("getPhase(0).getMixingRule()", lambda s=sys_obj: s.getPhase(0).getMixingRule()),
        ("getPhase(0).getMixingRuleName()", lambda s=sys_obj: s.getPhase(0).getMixingRuleName() if hasattr(s.getPhase(0), 'getMixingRuleName') else "N/A"),
        ("getMixingRuleType()", lambda s=sys_obj: s.getMixingRuleType() if hasattr(sys_obj, 'getMixingRuleType') else "N/A"),
    ]

    print(f"\n  {eos_name} EoS (mixing rule set to 2):")
    for method_name, method_fn in methods_to_try:
        try:
            result = method_fn()
            print(f"    {method_name} = {result} (type: {type(result).__name__})")
        except Exception as e:
            print(f"    {method_name} = ERROR: {type(e).__name__}: {e}")

# Also check on a ghost system (from separator empty outlet)
print("\n  Ghost system (from separator empty outlet):")
t2_ghost = neqsim.thermo.system.SystemSrkEos(temp_k, 50.0)
for c, f in COMP_DRY.items():
    t2_ghost.addComponent(c, f)
t2_ghost.setMixingRule(2)
t2_ghost.setTotalFlowRate(1000.0, "kg/hr")
s2_ghost = neqsim.process.equipment.stream.Stream("ghost_src", t2_ghost)
s2_ghost.run()
sep2_ghost = neqsim.process.equipment.separator.Separator("sep_ghost", s2_ghost)
sep2_ghost.run()
ghost_thermo = sep2_ghost.getLiquidOutStream().getThermoSystem()

for method_name, method_fn in [
    ("getMixingRule()", lambda: ghost_thermo.getMixingRule()),
    ("getMixingRuleNumber()", lambda: ghost_thermo.getMixingRuleNumber()),
    ("getPhase(0).getMixingRule()", lambda: ghost_thermo.getPhase(0).getMixingRule()),
]:
    try:
        result = method_fn()
        print(f"    {method_name} = {result} (type: {type(result).__name__})")
    except Exception as e:
        print(f"    {method_name} = ERROR: {type(e).__name__}: {e}")


# ══════════════════════════════════════════════════════════════════════════
# Q3: Does type(thermo) constructor work for all EoS classes?
# ══════════════════════════════════════════════════════════════════════════
divider("Q3: type(thermo)(T, P) constructor for all supported EoS classes")

eos_classes = [
    ("SystemSrkEos", neqsim.thermo.system.SystemSrkEos),
    ("SystemPrEos", neqsim.thermo.system.SystemPrEos),
    ("SystemSrkCPAstatoil", neqsim.thermo.system.SystemSrkCPAstatoil),
    ("SystemUMRPRUMCEos", neqsim.thermo.system.SystemUMRPRUMCEos),
    ("SystemPrEos1978", neqsim.thermo.system.SystemPrEos1978),
    ("SystemSrkMathiasCopeman", neqsim.thermo.system.SystemSrkMathiasCopeman),
    ("SystemSrkPenelouxEos", neqsim.thermo.system.SystemSrkPenelouxEos),
]

for eos_name, eos_cls in eos_classes:
    print(f"\n  Testing {eos_name}:")

    # Create original system
    try:
        original = eos_cls(temp_k, 50.0)
        for c, f in COMP_DRY.items():
            original.addComponent(c, f)
        original.setMixingRule(2)
        original.setTotalFlowRate(1000.0, "kg/hr")

        # Get type and reconstruct
        reconstructed_cls = type(original)
        reconstructed = reconstructed_cls(temp_k, 50.0)
        for c, f in COMP_DRY.items():
            reconstructed.addComponent(c, f)
        reconstructed.setMixingRule(2)
        reconstructed.setTotalFlowRate(1.0, "kg/hr")

        # Wrap in stream and run
        stream_orig = neqsim.process.equipment.stream.Stream("orig", original)
        stream_orig.run()
        stream_recon = neqsim.process.equipment.stream.Stream("recon", reconstructed)
        stream_recon.run()

        # Compare properties
        orig_props = print_props(f"Original ({eos_name}):", stream_orig.getThermoSystem())
        recon_props = print_props(f"Reconstructed ({eos_name}):", stream_recon.getThermoSystem())
        compare_props(f"Comparison", recon_props, orig_props)

        print(f"    type(original).__name__ = {type(original).__name__}")
        print(f"    type(reconstructed).__name__ = {type(reconstructed).__name__}")
        print(f"    Same class? {type(original) == type(reconstructed)}")

    except Exception as e:
        print(f"    FAILED: {type(e).__name__}: {e}")


# ══════════════════════════════════════════════════════════════════════════
# Q4: What happens with empty component list after z-fraction filtering?
# ══════════════════════════════════════════════════════════════════════════
divider("Q4: Empty component list after z-fraction filtering")

# Simulate a pathologically corrupted system where all z-fractions are tiny
print("  Creating a thermo system and manually simulating all-tiny z-fractions...")

# We can't easily create a system with z < 1e-20 through normal API,
# so we simulate the filtering logic directly
test_comp_data = [
    ("methane", 1e-30),
    ("ethane", 1e-31),
    ("propane", 1e-32),
]

total_z = sum(z for _, z in test_comp_data)
print(f"  Input z-fractions: {test_comp_data}")
print(f"  Total z: {total_z:.4e}")

# Apply our filter
filtered = [(name, z) for name, z in test_comp_data if z > 1e-20]
print(f"  After filtering z > 1e-20: {filtered}")
print(f"  Empty? {len(filtered) == 0}")

if not filtered:
    print("  >>> Guard needed: must raise ValueError before creating empty system")

    # What happens if we try to create a system with no components?
    try:
        bad_sys = neqsim.thermo.system.SystemSrkEos(temp_k, 50.0)
        # Don't add any components
        bad_sys.setMixingRule(2)
        bad_sys.setTotalFlowRate(1.0, "kg/hr")
        print(f"  System with no components: nComp={bad_sys.getNumberOfComponents()}")
        bad_stream = neqsim.process.equipment.stream.Stream("bad", bad_sys)
        bad_stream.run()
        print("  Stream.run() succeeded (unexpected!)")
    except Exception as e:
        print(f"  Stream.run() with no components: {type(e).__name__}: {e}")

print("\n  >>> Q4 VERDICT: Must add guard — if `not normalized: raise ValueError(...)`")


# ══════════════════════════════════════════════════════════════════════════
# Q6: Can we read z-fractions from a zero-flow (setEmptyFluid) system?
# ══════════════════════════════════════════════════════════════════════════
divider("Q6: Reading z-fractions from zero-flow source (setEmptyFluid) system")

t6 = neqsim.thermo.system.SystemSrkEos(temp_k, 50.0)
for c, f in COMP_DRY.items():
    t6.addComponent(c, f)
t6.setMixingRule(2)
t6.setTotalFlowRate(0.0, "kg/hr")

print("  After setTotalFlowRate(0):")
comp_data_6 = print_composition("Zero-flow source system:", t6)

# Also check after wrapping in Stream and running
s6 = neqsim.process.equipment.stream.Stream("zero_src", t6)
s6.run()
print("\n  After Stream.run():")
comp_data_6_run = print_composition("Zero-flow source after run():", s6.getThermoSystem())

# Check if compositions are correct (should be the original values)
q6_ok = True
for comp_name, orig_frac in COMP_DRY.items():
    read_frac = comp_data_6.get(comp_name, 0.0)
    if abs(read_frac - orig_frac) > 1e-6:
        print(f"  MISMATCH: {comp_name} expected {orig_frac}, got {read_frac}")
        q6_ok = False

print(f"\n  >>> Q6 VERDICT: Reading z-fractions from zero-flow system "
      f"{'WORKS — compositions preserved' if q6_ok else 'FAILS — compositions corrupted!'}")


# ══════════════════════════════════════════════════════════════════════════
# BONUS: Test the full separator epsilon-flash approach end-to-end
# ══════════════════════════════════════════════════════════════════════════
divider("BONUS: Full epsilon-flash separator approach end-to-end")

print("  Simulating: zero-flow inlet → epsilon-flash separator → set outlets to 0")

# Read "inlet" state (simulate reading from a zero-flow inlet)
# Use dry gas composition at 25°C, 50 bar
inlet_comp = COMP_DRY.copy()
inlet_t_k = temp_k
inlet_p = 50.0

# Step 1: Create fresh system at epsilon flow
fresh = neqsim.thermo.system.SystemSrkEos(inlet_t_k, inlet_p)
for c, f in inlet_comp.items():
    fresh.addComponent(c, f)
fresh.setMixingRule(2)
fresh.setTotalFlowRate(1.0, "kg/hr")

# Step 2: Run separator at epsilon flow
temp_stream = neqsim.process.equipment.stream.Stream("temp_inlet", fresh)
temp_stream.run()
temp_sep = neqsim.process.equipment.separator.Separator("temp_sep", temp_stream)
temp_sep.run()

# Step 3: Check outlet states
gas_out = temp_sep.getGasOutStream()
liq_out = temp_sep.getLiquidOutStream()

gas_flow_eps = float(gas_out.getFlowRate("kg/hr"))
liq_flow_eps = float(liq_out.getFlowRate("kg/hr"))
print(f"\n  Epsilon separator gas flow: {gas_flow_eps:.4e} kg/hr")
print(f"  Epsilon separator liq flow: {liq_flow_eps:.4e} kg/hr")
print(f"  Gas is ghost? {gas_flow_eps < 1e-10}")
print(f"  Liq is ghost? {liq_flow_eps < 1e-10}")

print_composition("\n  Gas outlet composition:", gas_out.getThermoSystem())
print_composition("  Liq outlet composition:", liq_out.getThermoSystem())

# Step 4: Now set both to zero (or sub-threshold) based on Q1/Q5 results
# Try the recommended approach first
print("\n  Setting both outlets to zero flow...")
gas_out.getThermoSystem().setTotalFlowRate(0.0, "kg/hr")
liq_out.getThermoSystem().setTotalFlowRate(0.0, "kg/hr")

# Verify compositions survived
gas_comp_final = print_composition("\n  Gas after zero-flow:", gas_out.getThermoSystem())
liq_comp_final = print_composition("  Liq after zero-flow:", liq_out.getThermoSystem())

# Test rescue from this state
print("\n  Rescuing gas outlet from zero-flow state...")
gas_thermo_final = gas_out.getThermoSystem()
n_comp_g = gas_thermo_final.getNumberOfComponents()
comp_rescue = []
for i in range(n_comp_g):
    c = gas_thermo_final.getComponent(i)
    name_r = str(c.getName())
    z_r = float(c.getz())
    comp_rescue.append((name_r, z_r))
total_z_r = sum(z for _, z in comp_rescue)
normalized_r = [(name, z / total_z_r) for name, z in comp_rescue if z > 1e-20]

print(f"  Components after rescue filtering: {len(normalized_r)}")
print(f"  z-fraction sum before normalization: {total_z_r:.6e}")

if normalized_r:
    fresh_rescue = neqsim.thermo.system.SystemSrkEos(inlet_t_k, inlet_p)
    for name, frac in normalized_r:
        fresh_rescue.addComponent(name, frac)
    fresh_rescue.setMixingRule(2)
    fresh_rescue.setTotalFlowRate(1.0, "kg/hr")
    stream_rescue = neqsim.process.equipment.stream.Stream("rescue", fresh_rescue)
    stream_rescue.run()

    print_props("  Rescued gas properties:", stream_rescue.getThermoSystem())
    print("  >>> BONUS VERDICT: Full epsilon-flash + zero-flow + rescue pipeline WORKS")
else:
    print("  >>> BONUS VERDICT: FAILED — no components after filtering!")


# ══════════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════════
divider("SUMMARY OF FINDINGS")
print("""
Review the output above for each question's verdict.
Key decisions to make based on results:

  Q1: If SAFE → use option (a) setTotalFlowRate(0) in separator flash
      If UNSAFE → use option (b) flag-based, or Q5's sub-threshold flow

  Q2: If mixing rule is readable → consider reading dynamically
      If not readable → hardcode 2 (acceptable, matches our codebase)

  Q3: If all EoS classes work → type(thermo)(T, P) approach is confirmed
      If any fail → need EoS class lookup table instead

  Q4: Always add empty-component guard (defensive programming)

  Q5: Fallback for Q1 if setTotalFlowRate(0) corrupts

  Q6: If compositions readable → zero-flow source recovery works
      If not → need alternative approach for Bug 1

  BONUS: End-to-end validation of the full separator flash pipeline
""")
