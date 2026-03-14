#!/usr/bin/env python3
"""Debug: What happens when each equipment type receives a true zero-flow inlet?

Unlike debug_downstream_zero_flow.py (which tests ghost streams FROM separator),
this script tests a SOURCE STREAM with flow_rate=0 fed directly into each
equipment type. This tells us what NeqSim does when the inlet truly has zero
moles, and whether we need equipment-specific short-circuit logic.

Key questions:
1. Does each equipment crash, produce garbage, or handle zero flow gracefully?
2. For separator: does it still flash and produce meaningful phase compositions?
3. For mixer: what if ONE inlet is zero and the other is real?
4. What do outlet streams look like after equipment runs on zero-flow inlet?

Run with: uv run python .dev/dev_wdir/debugging/zero_flow_and_ghost_flow_bug/debug_all_equipment_zero_flow_inlet.py
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "src"))

from jneqsim import neqsim  # noqa: E402


def divider(title: str) -> None:
    print(f"\n{'=' * 76}")
    print(f"  {title}")
    print(f"{'=' * 76}")


def check_prop(label: str, getter, unit: str = "") -> float | None:
    try:
        v = float(getter())
        is_ok = math.isfinite(v) and abs(v) < 1e10
        status = "OK" if is_ok else ("NaN" if math.isnan(v) else "BAD")
        print(f"  {label:45s} = {v:>15.6g} {unit:10s}  [{status}]")
        return v
    except Exception as e:
        print(f"  {label:45s} = EXCEPTION: {e}")
        return None


def make_zero_flow_gas_stream(name: str = "zero_gas"):
    """Create a gas stream with flow_rate=0."""
    t = neqsim.thermo.system.SystemSrkEos(25.0 + 273.15, 50.0)
    t.addComponent("methane", 0.90)
    t.addComponent("ethane", 0.06)
    t.addComponent("propane", 0.04)
    t.setMixingRule(2)
    t.setTotalFlowRate(0.0, "kg/hr")
    s = neqsim.process.equipment.stream.Stream(name, t)
    s.run()
    return s


def make_real_gas_stream(name: str = "real_gas", flow: float = 100.0):
    """Create a real gas stream with normal flow."""
    t = neqsim.thermo.system.SystemSrkEos(25.0 + 273.15, 50.0)
    t.addComponent("methane", 0.90)
    t.addComponent("ethane", 0.06)
    t.addComponent("propane", 0.04)
    t.setMixingRule(2)
    t.setTotalFlowRate(flow, "kg/hr")
    s = neqsim.process.equipment.stream.Stream(name, t)
    s.run()
    return s


def make_zero_flow_liquid_stream(name: str = "zero_liq"):
    """Create a liquid stream with flow_rate=0."""
    t = neqsim.thermo.system.SystemSrkEos(25.0 + 273.15, 50.0)
    t.addComponent("water", 1.0)
    t.setMixingRule(2)
    t.setTotalFlowRate(0.0, "kg/hr")
    s = neqsim.process.equipment.stream.Stream(name, t)
    s.run()
    return s


def make_zero_flow_twophase_stream(name: str = "zero_2ph"):
    """Create a two-phase stream (at normal flow) but with flow=0."""
    t = neqsim.thermo.system.SystemSrkEos(25.0 + 273.15, 50.0)
    t.addComponent("methane", 0.70)
    t.addComponent("ethane", 0.10)
    t.addComponent("propane", 0.10)
    t.addComponent("n-butane", 0.10)
    t.setMixingRule(2)
    t.setTotalFlowRate(0.0, "kg/hr")
    s = neqsim.process.equipment.stream.Stream(name, t)
    s.run()
    return s


def inspect_stream(label: str, stream) -> None:
    """Print key properties of a stream."""
    print(f"\n  --- {label} ---")
    check_prop("flow [kg/hr]", lambda: stream.getFlowRate("kg/hr"), "kg/hr")
    check_prop("T [C]", lambda: stream.getTemperature("C"), "°C")
    check_prop("P [bara]", lambda: stream.getPressure("bara"), "bara")
    thermo = stream.getThermoSystem()
    check_prop("density [kg/m3]", lambda: thermo.getDensity("kg/m3"), "kg/m3")
    check_prop("enthalpy [kJ/kg]", lambda: thermo.getEnthalpy("kJ/kg"), "kJ/kg")
    check_prop("Cp [kJ/kgK]", lambda: thermo.getCp("kJ/kgK"), "kJ/kgK")
    check_prop("Z factor", lambda: thermo.getZ())
    check_prop("molar mass [kg/kmol]", lambda: thermo.getMolarMass() * 1000, "kg/kmol")

    # Composition
    n_comp = thermo.getNumberOfComponents()
    print(f"  Composition ({n_comp} components):")
    for i in range(n_comp):
        c = thermo.getComponent(i)
        name = str(c.getName())
        z = float(c.getz())
        print(f"    {name:20s}  z = {z:.6f}")


def inspect_stream_brief(label: str, stream) -> None:
    """Print brief properties of a stream."""
    flow = check_prop(f"{label} flow [kg/hr]", lambda: stream.getFlowRate("kg/hr"), "kg/hr")
    check_prop(f"{label} T [C]", lambda: stream.getTemperature("C"), "°C")
    check_prop(f"{label} P [bara]", lambda: stream.getPressure("bara"), "bara")
    thermo = stream.getThermoSystem()
    check_prop(f"{label} density [kg/m3]", lambda: thermo.getDensity("kg/m3"), "kg/m3")
    return flow


# ════════════════════════════════════════════════════════════════════════════
# Baseline: What does the zero-flow source stream look like?
# ════════════════════════════════════════════════════════════════════════════
divider("BASELINE: Zero-flow gas source stream")
zero_gas = make_zero_flow_gas_stream("baseline")
inspect_stream("Zero-flow gas source", zero_gas)

print("\n  Key: flow=0 means NeqSim called setEmptyFluid().")
print("  Density and enthalpy may be NaN or ArithmeticException.")


# ════════════════════════════════════════════════════════════════════════════
# TEST 1: COMPRESSOR with zero-flow inlet
# ════════════════════════════════════════════════════════════════════════════
divider("TEST 1: COMPRESSOR ← zero-flow gas inlet")
try:
    s1 = make_zero_flow_gas_stream("comp_inlet")
    comp = neqsim.process.equipment.compressor.Compressor("comp1", s1)
    comp.setOutletPressure(100.0)
    comp.setIsentropicEfficiency(0.75)
    comp.run()

    print("  Compressor solved (no crash)")
    check_prop("power [W]", lambda: comp.getPower(), "W")
    check_prop("polytropic efficiency", lambda: comp.getPolytropicEfficiency())
    check_prop("isentropic efficiency", lambda: comp.getIsentropicEfficiency())

    out = comp.getOutletStream()
    inspect_stream_brief("outlet", out)

    # Check if outlet pressure matches spec
    out_p = float(out.getPressure("bara"))
    print(f"\n  Specified outlet P: 100.0 bara")
    print(f"  Actual outlet P:   {out_p:.4f} bara")
    print(f"  Did compressor change pressure? {'YES' if abs(out_p - 50.0) > 0.1 else 'NO'}")
except Exception as e:
    print(f"  COMPRESSOR FAILED: {e}")


# ════════════════════════════════════════════════════════════════════════════
# TEST 2: VALVE with zero-flow inlet
# ════════════════════════════════════════════════════════════════════════════
divider("TEST 2: VALVE ← zero-flow gas inlet")
try:
    s2 = make_zero_flow_gas_stream("valve_inlet")
    valve = neqsim.process.equipment.valve.ThrottlingValve("valve1", s2)
    valve.setOutletPressure(20.0)
    valve.run()

    print("  Valve solved (no crash)")
    out = valve.getOutletStream()
    inspect_stream_brief("outlet", out)

    out_p = float(out.getPressure("bara"))
    print(f"\n  Specified outlet P: 20.0 bara")
    print(f"  Actual outlet P:   {out_p:.4f} bara")
    print(f"  Did valve change pressure? {'YES' if abs(out_p - 50.0) > 0.1 else 'NO'}")
except Exception as e:
    print(f"  VALVE FAILED: {e}")


# ════════════════════════════════════════════════════════════════════════════
# TEST 3: PUMP with zero-flow inlet (liquid)
# ════════════════════════════════════════════════════════════════════════════
divider("TEST 3: PUMP ← zero-flow liquid inlet")
try:
    s3 = make_zero_flow_liquid_stream("pump_inlet")
    pump = neqsim.process.equipment.pump.Pump("pump1", s3)
    pump.setOutletPressure(100.0)
    pump.run()

    print("  Pump solved (no crash)")
    check_prop("power [W]", lambda: pump.getPower(), "W")

    out = pump.getOutletStream()
    inspect_stream_brief("outlet", out)

    out_p = float(out.getPressure("bara"))
    print(f"\n  Specified outlet P: 100.0 bara")
    print(f"  Actual outlet P:   {out_p:.4f} bara")
except Exception as e:
    print(f"  PUMP FAILED: {e}")


# ════════════════════════════════════════════════════════════════════════════
# TEST 4: SEPARATOR with zero-flow inlet (would-be two-phase composition)
# ════════════════════════════════════════════════════════════════════════════
divider("TEST 4: SEPARATOR ← zero-flow two-phase-composition inlet")
print("  Key question: Does NeqSim still flash and assign phase compositions?")
try:
    s4 = make_zero_flow_twophase_stream("sep_inlet")
    print(f"  Inlet flow: {s4.getFlowRate('kg/hr'):.4e} kg/hr")

    sep = neqsim.process.equipment.separator.Separator("sep1", s4)
    sep.run()

    print("  Separator solved (no crash)")

    gas_out = sep.getGasOutStream()
    liq_out = sep.getLiquidOutStream()

    print("\n  --- Gas outlet ---")
    gas_flow = check_prop("gas flow [kg/hr]", lambda: gas_out.getFlowRate("kg/hr"), "kg/hr")
    check_prop("gas T [C]", lambda: gas_out.getTemperature("C"), "°C")
    check_prop("gas P [bara]", lambda: gas_out.getPressure("bara"), "bara")

    gas_thermo = gas_out.getThermoSystem()
    check_prop("gas density [kg/m3]", lambda: gas_thermo.getDensity("kg/m3"), "kg/m3")
    n_comp = gas_thermo.getNumberOfComponents()
    total_z = 0.0
    print(f"  Gas composition ({n_comp} components):")
    for i in range(n_comp):
        c = gas_thermo.getComponent(i)
        z = float(c.getz())
        total_z += z
        print(f"    {str(c.getName()):20s}  z = {z:.6f}")
    print(f"  Sum of z-fractions: {total_z:.6f}")

    print("\n  --- Liquid outlet ---")
    liq_flow = check_prop("liq flow [kg/hr]", lambda: liq_out.getFlowRate("kg/hr"), "kg/hr")
    check_prop("liq T [C]", lambda: liq_out.getTemperature("C"), "°C")
    check_prop("liq P [bara]", lambda: liq_out.getPressure("bara"), "bara")

    liq_thermo = liq_out.getThermoSystem()
    check_prop("liq density [kg/m3]", lambda: liq_thermo.getDensity("kg/m3"), "kg/m3")
    total_z_liq = 0.0
    print(f"  Liquid composition ({liq_thermo.getNumberOfComponents()} components):")
    for i in range(liq_thermo.getNumberOfComponents()):
        c = liq_thermo.getComponent(i)
        z = float(c.getz())
        total_z_liq += z
        print(f"    {str(c.getName()):20s}  z = {z:.6f}")
    print(f"  Sum of z-fractions: {total_z_liq:.6f}")

    print(f"\n  SEPARATOR SUMMARY:")
    print(f"  Gas outlet flow:    {float(gas_out.getFlowRate('kg/hr')):.4e} kg/hr")
    print(f"  Liquid outlet flow: {float(liq_out.getFlowRate('kg/hr')):.4e} kg/hr")
    print(f"  Gas z-sum:          {total_z:.6f} (expect ~0.5 if ghost, ~1.0 if clean)")
    print(f"  Liquid z-sum:       {total_z_liq:.6f}")

except Exception as e:
    print(f"  SEPARATOR FAILED: {e}")


# ════════════════════════════════════════════════════════════════════════════
# TEST 4b: SEPARATOR with zero-flow SINGLE-PHASE inlet (all gas)
# ════════════════════════════════════════════════════════════════════════════
divider("TEST 4b: SEPARATOR ← zero-flow single-phase gas inlet")
print("  What if inlet is single-phase gas at zero flow?")
try:
    s4b = make_zero_flow_gas_stream("sep_inlet_gas")
    sep4b = neqsim.process.equipment.separator.Separator("sep4b", s4b)
    sep4b.run()

    print("  Separator solved (no crash)")
    gas_out = sep4b.getGasOutStream()
    liq_out = sep4b.getLiquidOutStream()

    gas_flow = float(gas_out.getFlowRate("kg/hr"))
    liq_flow = float(liq_out.getFlowRate("kg/hr"))
    print(f"  Gas outlet flow:    {gas_flow:.4e} kg/hr")
    print(f"  Liquid outlet flow: {liq_flow:.4e} kg/hr")

    gas_thermo = gas_out.getThermoSystem()
    check_prop("gas density [kg/m3]", lambda: gas_thermo.getDensity("kg/m3"), "kg/m3")

    liq_thermo = liq_out.getThermoSystem()
    check_prop("liq density [kg/m3]", lambda: liq_thermo.getDensity("kg/m3"), "kg/m3")
except Exception as e:
    print(f"  SEPARATOR FAILED: {e}")


# ════════════════════════════════════════════════════════════════════════════
# TEST 5: MIXER with zero-flow + real-flow inlets
# ════════════════════════════════════════════════════════════════════════════
divider("TEST 5: MIXER ← zero-flow + real-flow inlets")
print("  Partial zero-flow: one inlet zero, one inlet real.")
try:
    s5_zero = make_zero_flow_gas_stream("mix_zero_in")
    s5_real = make_real_gas_stream("mix_real_in", flow=100.0)

    mixer = neqsim.process.equipment.mixer.Mixer("mix1")
    mixer.addStream(s5_zero)
    mixer.addStream(s5_real)
    mixer.run()

    out = mixer.getOutletStream()
    print("  Mixer solved (no crash)")
    inspect_stream_brief("outlet", out)
except Exception as e:
    print(f"  MIXER FAILED: {e}")


# ════════════════════════════════════════════════════════════════════════════
# TEST 5b: MIXER with ALL zero-flow inlets
# ════════════════════════════════════════════════════════════════════════════
divider("TEST 5b: MIXER ← ALL zero-flow inlets")
try:
    s5b_z1 = make_zero_flow_gas_stream("mix_z1")
    s5b_z2 = make_zero_flow_gas_stream("mix_z2")

    mixer2 = neqsim.process.equipment.mixer.Mixer("mix2")
    mixer2.addStream(s5b_z1)
    mixer2.addStream(s5b_z2)
    mixer2.run()

    out = mixer2.getOutletStream()
    print("  Mixer solved (no crash)")
    inspect_stream_brief("outlet", out)

    out_thermo = out.getThermoSystem()
    check_prop("outlet enthalpy [kJ/kg]", lambda: out_thermo.getEnthalpy("kJ/kg"), "kJ/kg")
    check_prop("outlet Cp [kJ/kgK]", lambda: out_thermo.getCp("kJ/kgK"), "kJ/kgK")
except Exception as e:
    print(f"  MIXER FAILED: {e}")


# ════════════════════════════════════════════════════════════════════════════
# TEST 6: SPLITTER with zero-flow inlet
# ════════════════════════════════════════════════════════════════════════════
divider("TEST 6: SPLITTER ← zero-flow gas inlet")
try:
    s6 = make_zero_flow_gas_stream("split_inlet")
    splitter = neqsim.process.equipment.splitter.Splitter("split1", s6, 2)
    splitter.setSplitFactors([0.6, 0.4])
    splitter.run()

    print("  Splitter solved (no crash)")
    out1 = splitter.getSplitStream(0)
    out2 = splitter.getSplitStream(1)

    inspect_stream_brief("outlet_1", out1)
    inspect_stream_brief("outlet_2", out2)
except Exception as e:
    print(f"  SPLITTER FAILED: {e}")


# ════════════════════════════════════════════════════════════════════════════
# TEST 7: HEATER (simulated via stream heater) with zero-flow inlet
# ════════════════════════════════════════════════════════════════════════════
divider("TEST 7: HEATER ← zero-flow gas inlet")
try:
    s7 = make_zero_flow_gas_stream("heater_inlet")
    heater = neqsim.process.equipment.heatexchanger.Heater("heater1", s7)
    heater.setOutTemperature(80.0 + 273.15)
    heater.run()

    print("  Heater solved (no crash)")
    check_prop("duty [W]", lambda: heater.getDuty(), "W")

    out = heater.getOutletStream()
    inspect_stream_brief("outlet", out)

    out_t = float(out.getTemperature("C"))
    print(f"\n  Specified outlet T: 80.0 °C")
    print(f"  Actual outlet T:   {out_t:.4f} °C")
except Exception as e:
    print(f"  HEATER FAILED: {e}")


# ════════════════════════════════════════════════════════════════════════════
# TEST 8: COOLER (heater with lower T) with zero-flow inlet
# ════════════════════════════════════════════════════════════════════════════
divider("TEST 8: COOLER ← zero-flow gas inlet")
try:
    s8 = make_zero_flow_gas_stream("cooler_inlet")
    cooler = neqsim.process.equipment.heatexchanger.Cooler("cooler1", s8)
    cooler.setOutTemperature(5.0 + 273.15)
    cooler.run()

    print("  Cooler solved (no crash)")
    check_prop("duty [W]", lambda: cooler.getDuty(), "W")

    out = cooler.getOutletStream()
    inspect_stream_brief("outlet", out)

    out_t = float(out.getTemperature("C"))
    print(f"\n  Specified outlet T: 5.0 °C")
    print(f"  Actual outlet T:   {out_t:.4f} °C")
except Exception as e:
    print(f"  COOLER FAILED: {e}")


# ════════════════════════════════════════════════════════════════════════════
# REFERENCE: Same equipment with real flow (for comparison)
# ════════════════════════════════════════════════════════════════════════════
divider("REFERENCE: Compressor with real flow (100 kg/hr)")
try:
    s_ref = make_real_gas_stream("ref_gas", 100.0)
    comp_ref = neqsim.process.equipment.compressor.Compressor("comp_ref", s_ref)
    comp_ref.setOutletPressure(100.0)
    comp_ref.setIsentropicEfficiency(0.75)
    comp_ref.run()

    check_prop("power [W]", lambda: comp_ref.getPower(), "W")
    out_ref = comp_ref.getOutletStream()
    inspect_stream_brief("outlet", out_ref)
except Exception as e:
    print(f"  REFERENCE FAILED: {e}")


# ════════════════════════════════════════════════════════════════════════════
# SUMMARY
# ════════════════════════════════════════════════════════════════════════════
divider("SUMMARY")
print("""
Key questions to answer from results above:

1. COMPRESSOR:  Does it crash? What's the power? Does outlet P change?
2. VALVE:       Does it crash? Does outlet P change?
3. PUMP:        Does it crash? What's the power?
4. SEPARATOR:   Does it flash? Are outlet compositions different from inlet?
                Does z-sum = 0.5 (ghost) or 1.0 (clean)?
4b. SEPARATOR (single-phase): Both outlets ghost?
5. MIXER (partial): Does real inlet dominate? Is outlet correct?
5b. MIXER (all zero): Does it crash? What state is outlet?
6. SPLITTER:    Does it crash? Do split factors apply?
7. HEATER:      Does it crash? Does outlet T match spec? What's the duty?
8. COOLER:      Does it crash? Does outlet T match spec? What's the duty?

DESIGN IMPLICATIONS:
- If equipment doesn't crash but produces garbage → need short-circuit guard
- If separator still flashes at zero flow → can let it run, Layer 1 handles outlets
- If mixer works with partial zero → only guard when ALL inlets are zero
- If heater/cooler changes T at zero flow → may not need guard (or may need it
  to prevent misleading duty values)
""")
