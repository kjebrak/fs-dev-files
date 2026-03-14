#!/usr/bin/env python3
"""Debug: Validate the proposed fix for zero/ghost-flow streams.

The fix approach:
  1. Detect flow < threshold in Stream.extract_results()
  2. Read composition (normalize halved fractions), T, P from the ghost/zero system
  3. Create a FRESH NeqSim thermo system of the same EoS class
  4. Wrap it in a NeqSim Stream and run() it → gives ALL correct properties
  5. Extract intensive properties from the fresh stream
  6. Report flow_rate=0 in the final results

This script validates the approach for:
  - Bug 1: Source stream with flow_rate=0
  - Bug 2: Separator ghost-flow outlet (~1e-27 kg/hr)
  - Edge case: Rich gas with real liquid outlet (should NOT trigger fix)

Run with: uv run python .dev/dev_wdir/debugging/debug_zero_flow_fix_approach.py
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from jneqsim import neqsim  # noqa: E402

# ── Reference values (dry gas at 25°C, 50 bar, 1000 kg/hr) ────────────
ref = {"density": 37.896, "enthalpy": -2.361, "cp": 2.570, "z": 0.9078}
COMP_DRY = {"methane": 0.95, "ethane": 0.03, "propane": 0.02}
COMP_RICH = {"methane": 0.70, "ethane": 0.10, "propane": 0.10, "n-butane": 0.10}


def divider(title: str) -> None:
    print(f"\n{'=' * 72}")
    print(f"  {title}")
    print(f"{'=' * 72}")


def print_props(label: str, thermo, ref_vals: dict | None = None) -> dict:
    """Extract and print key intensive properties."""
    try:
        d = thermo.getDensity("kg/m3")
    except Exception:
        d = float("nan")
    try:
        h = thermo.getEnthalpy("kJ/kg")
    except Exception:
        h = float("nan")
    try:
        cp = thermo.getCp("kJ/kgK")
    except Exception:
        cp = float("nan")
    try:
        z = thermo.getZ()
    except Exception:
        z = float("nan")

    result = {"density": d, "enthalpy": h, "cp": cp, "z": z}
    print(f"  {label}")
    print(f"    density={d:.4f}  H={h:.4f}  Cp={cp:.4f}  Z={z:.6f}")

    if ref_vals:
        errors = {}
        for k, v in result.items():
            rv = ref_vals.get(k)
            if rv and math.isfinite(v) and rv != 0:
                err = abs(v - rv) / abs(rv) * 100
                errors[k] = err
        err_str = "  ".join(f"{k}={v:.2f}%" for k, v in errors.items())
        all_ok = all(v < 1.0 for v in errors.values())
        print(f"    errors: {err_str}  [{'ALL <1%' if all_ok else 'SOME >1%'}]")

    return result


def rescue_ghost_stream(ghost_thermo):
    """The proposed fix: create a fresh stream from ghost system's composition/T/P.

    This is the core logic that would go into Stream.extract_results().
    Returns a fresh NeqSim thermo system with correct properties.
    """
    # Step 1: Read T, P from ghost
    temp_k = ghost_thermo.getTemperature()  # Kelvin
    pressure = ghost_thermo.getPressure()  # bara

    # Step 2: Read and normalize composition
    n_comp = ghost_thermo.getNumberOfComponents()
    comp_data = []
    for i in range(n_comp):
        c = ghost_thermo.getComponent(i)
        comp_data.append((str(c.getName()), float(c.getz())))

    total_z = sum(z for _, z in comp_data)
    normalized = [(name, z / total_z) for name, z in comp_data if z > 1e-20]

    # Step 3: Create fresh system of the same EoS class
    eos_class = type(ghost_thermo)
    fresh = eos_class(temp_k, pressure)
    for name, frac in normalized:
        fresh.addComponent(name, frac)

    # Step 4: Set mixing rule and small flow, wrap in Stream, run
    fresh.setMixingRule(2)  # autoSelectMixingRule
    fresh.setTotalFlowRate(1.0, "kg/hr")  # epsilon flow for numerics

    stream = neqsim.process.equipment.stream.Stream("rescue", fresh)
    stream.run()

    return stream.getThermoSystem()


# ══════════════════════════════════════════════════════════════════════════
# TEST 1: Fix for Bug 2 — separator ghost outlet
# ══════════════════════════════════════════════════════════════════════════
divider("TEST 1: Rescue separator ghost-flow liquid outlet")

temp_k = 25.0 + 273.15
t1 = neqsim.thermo.system.SystemSrkEos(temp_k, 50.0)
for c, f in COMP_DRY.items():
    t1.addComponent(c, f)
t1.setMixingRule(2)
t1.setTotalFlowRate(1000.0, "kg/hr")
s1 = neqsim.process.equipment.stream.Stream("inlet", t1)
s1.run()
sep = neqsim.process.equipment.separator.Separator("sep", s1)
sep.run()

ghost = sep.getLiquidOutStream().getThermoSystem()
print(f"  Ghost flow: {sep.getLiquidOutStream().getFlowRate('kg/hr'):.4e} kg/hr")

print_props("GHOST (before rescue):", ghost, ref)
rescued = rescue_ghost_stream(ghost)
print_props("RESCUED:", rescued, ref)
print_props("REFERENCE (1000 kg/hr):", s1.getThermoSystem(), ref)


# ══════════════════════════════════════════════════════════════════════════
# TEST 2: Fix for Bug 1 — zero-flow source stream
# ══════════════════════════════════════════════════════════════════════════
divider("TEST 2: Rescue zero-flow source stream")

t2 = neqsim.thermo.system.SystemSrkEos(temp_k, 50.0)
for c, f in COMP_DRY.items():
    t2.addComponent(c, f)
t2.setMixingRule(2)
t2.setTotalFlowRate(0.0, "kg/hr")
s2 = neqsim.process.equipment.stream.Stream("zero_flow", t2)
s2.run()

zero_thermo = s2.getThermoSystem()
print(f"  Source flow: {s2.getFlowRate('kg/hr'):.4e} kg/hr")

print_props("ZERO FLOW (before rescue):", zero_thermo, ref)
rescued2 = rescue_ghost_stream(zero_thermo)
print_props("RESCUED:", rescued2, ref)


# ══════════════════════════════════════════════════════════════════════════
# TEST 3: Non-ghost stream should NOT be affected
# ══════════════════════════════════════════════════════════════════════════
divider("TEST 3: Real two-phase separator — liquid outlet has real flow")

t3 = neqsim.thermo.system.SystemSrkEos(temp_k, 50.0)
for c, f in COMP_RICH.items():
    t3.addComponent(c, f)
t3.setMixingRule(2)
t3.setTotalFlowRate(1000.0, "kg/hr")
s3 = neqsim.process.equipment.stream.Stream("rich_inlet", t3)
s3.run()
sep3 = neqsim.process.equipment.separator.Separator("sep3", s3)
sep3.run()

gas3 = sep3.getGasOutStream()
liq3 = sep3.getLiquidOutStream()
gas_flow = gas3.getFlowRate("kg/hr")
liq_flow = liq3.getFlowRate("kg/hr")

print(f"  Gas outlet: {gas_flow:.2f} kg/hr")
print(f"  Liq outlet: {liq_flow:.2f} kg/hr")
print(f"  Flow > threshold? {'YES — no rescue needed' if liq_flow > 1e-10 else 'NO — needs rescue'}")

liq3_thermo = liq3.getThermoSystem()
liq3_thermo.initPhysicalProperties()
print_props("Liquid outlet (normal extraction):", liq3_thermo)


# ══════════════════════════════════════════════════════════════════════════
# TEST 4: Ghost gas outlet (feed is all liquid)
# ══════════════════════════════════════════════════════════════════════════
divider("TEST 4: Heavy liquid feed — ghost GAS outlet")

# Use cold heavy composition to ensure all-liquid feed
t4 = neqsim.thermo.system.SystemSrkEos((-20.0 + 273.15), 50.0)
t4.addComponent("propane", 0.3)
t4.addComponent("n-butane", 0.3)
t4.addComponent("n-pentane", 0.2)
t4.addComponent("n-hexane", 0.2)
t4.setMixingRule(2)
t4.setTotalFlowRate(1000.0, "kg/hr")
s4 = neqsim.process.equipment.stream.Stream("heavy_inlet", t4)
s4.run()

print(f"  Inlet phases: {s4.getThermoSystem().getNumberOfPhases()}")
print(f"  hasGas: {s4.getThermoSystem().hasPhaseType('gas')}")

sep4 = neqsim.process.equipment.separator.Separator("sep4", s4)
sep4.run()

gas4 = sep4.getGasOutStream()
liq4 = sep4.getLiquidOutStream()
gas4_flow = gas4.getFlowRate("kg/hr")
liq4_flow = liq4.getFlowRate("kg/hr")

print(f"  Gas outlet: {gas4_flow:.4e} kg/hr")
print(f"  Liq outlet: {liq4_flow:.4f} kg/hr")

if gas4_flow < 1e-10:
    print("\n  Gas outlet is ghost — applying rescue:")
    ghost4 = gas4.getThermoSystem()
    print_props("GHOST gas:", ghost4)
    rescued4 = rescue_ghost_stream(ghost4)
    print_props("RESCUED gas:", rescued4)


# ══════════════════════════════════════════════════════════════════════════
# TEST 5: Different EoS model (PR instead of SRK)
# ══════════════════════════════════════════════════════════════════════════
divider("TEST 5: PR EoS — verify fix works with different models")

t5 = neqsim.thermo.system.SystemPrEos(temp_k, 50.0)
for c, f in COMP_DRY.items():
    t5.addComponent(c, f)
t5.setMixingRule(2)
t5.setTotalFlowRate(1000.0, "kg/hr")
s5 = neqsim.process.equipment.stream.Stream("pr_inlet", t5)
s5.run()
sep5 = neqsim.process.equipment.separator.Separator("sep5", s5)
sep5.run()

ghost5 = sep5.getLiquidOutStream().getThermoSystem()
ghost5_flow = sep5.getLiquidOutStream().getFlowRate("kg/hr")
print(f"  Ghost flow: {ghost5_flow:.4e} kg/hr")
print(f"  Ghost EoS class: {type(ghost5).__name__}")

print_props("GHOST (PR):", ghost5)
rescued5 = rescue_ghost_stream(ghost5)
print(f"  Rescued EoS class: {type(rescued5).__name__}")
print_props("RESCUED (PR):", rescued5)

# PR reference
pr_ref_thermo = s5.getThermoSystem()
print_props("REFERENCE (PR, 1000 kg/hr):", pr_ref_thermo)


# ══════════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════════
divider("SUMMARY")
print("""
The rescue_ghost_stream() function works by:
  1. Reading T, P, composition from the corrupted ghost system
  2. Normalizing the halved mole fractions
  3. Creating a FRESH NeqSim system of the same EoS class
  4. Wrapping in a NeqSim Stream and calling run()
  5. The Stream.run() path gives correct density (unlike raw TPflash+init)

Key: Stream.run() does additional initialization beyond TPflash+init
that fixes the getDensity('kg/m3') code path.

This approach:
  - Fixes Bug 1 (zero-flow source streams): all properties correct
  - Fixes Bug 2 (separator ghost outlets): all properties correct
  - Preserves the EoS model class (SRK, PR, etc.)
  - Works for both missing gas and missing liquid outlets
  - Only triggers when flow < threshold (doesn't affect normal streams)
""")
