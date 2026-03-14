#!/usr/bin/env python3
"""Debug: What happens when zero-flow streams feed into downstream equipment?

Tests whether NeqSim equipment (compressor, valve, mixer, another separator)
can handle a zero-flow or recovered-epsilon-flow inlet stream.

This validates our fix approach: after recovery, the stream has 1.0 kg/hr
epsilon flow. Does downstream equipment solve correctly? And what happens
if we pass the raw ghost stream (without recovery) into downstream equipment?

Run with: uv run python .dev/dev_wdir/debugging/zero_flow_and_ghost_flow_bug/debug_downstream_zero_flow.py
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "src"))

from jneqsim import neqsim  # noqa: E402


def divider(title: str) -> None:
    print(f"\n{'=' * 72}")
    print(f"  {title}")
    print(f"{'=' * 72}")


def check_prop(label: str, getter, unit: str = "") -> float | None:
    try:
        v = float(getter())
        is_ok = math.isfinite(v) and abs(v) < 1e10
        status = "OK" if is_ok else ("NaN" if math.isnan(v) else "BAD")
        print(f"  {label:40s} = {v:>15.6g} {unit:10s}  [{status}]")
        return v
    except Exception as e:
        print(f"  {label:40s} = EXCEPTION: {e}")
        return None


def make_ghost_separator():
    """Create a separator with single-phase liquid feed → ghost gas outlet."""
    temp_k = 10.0 + 273.15
    t = neqsim.thermo.system.SystemSrkEos(temp_k, 100.0)
    for name, frac in [
        ("methane", 0.1429), ("ethane", 0.1429), ("propane", 0.1429),
        ("i-butane", 0.1429), ("n-butane", 0.1429),
        ("i-pentane", 0.1429), ("n-pentane", 0.1429),
    ]:
        t.addComponent(name, frac)
    t.setMixingRule(2)
    t.setTotalFlowRate(100.0, "kg/hr")

    inlet = neqsim.process.equipment.stream.Stream("inlet", t)
    inlet.run()

    sep = neqsim.process.equipment.separator.Separator("sep", inlet)
    sep.run()

    return sep


def rescue_ghost_stream(ghost_thermo):
    """The validated fix: create fresh stream from ghost's composition/T/P."""
    temp_k = ghost_thermo.getTemperature()
    pressure = ghost_thermo.getPressure()

    n_comp = ghost_thermo.getNumberOfComponents()
    comp_data = []
    for i in range(n_comp):
        c = ghost_thermo.getComponent(i)
        comp_data.append((str(c.getName()), float(c.getz())))

    total_z = sum(z for _, z in comp_data)
    normalized = [(name, z / total_z) for name, z in comp_data if z > 1e-20]

    eos_class = type(ghost_thermo)
    fresh = eos_class(temp_k, pressure)
    for name, frac in normalized:
        fresh.addComponent(name, frac)
    fresh.setMixingRule(2)
    fresh.setTotalFlowRate(1.0, "kg/hr")  # epsilon flow

    stream = neqsim.process.equipment.stream.Stream("rescued", fresh)
    stream.run()
    return stream


sep = make_ghost_separator()
ghost_gas = sep.getGasOutStream()
ghost_flow = float(ghost_gas.getFlowRate("kg/hr"))
print(f"Ghost gas outlet flow: {ghost_flow:.4e} kg/hr")


# ══════════════════════════════════════════════════════════════════════════
# TEST 1: Feed ghost stream directly into a compressor (NO recovery)
# ══════════════════════════════════════════════════════════════════════════
divider("TEST 1: Ghost stream → Compressor (NO recovery)")
print("  What does NeqSim do if we feed the raw ghost stream into a compressor?")

try:
    comp1 = neqsim.process.equipment.compressor.Compressor("comp_ghost", ghost_gas)
    comp1.setOutletPressure(150.0)
    comp1.setIsentropicEfficiency(0.75)
    comp1.run()

    comp1_out = comp1.getOutletStream()
    print(f"\n  Compressor solved (no crash)")
    check_prop("outlet P [bara]", lambda: comp1_out.getPressure("bara"), "bara")
    check_prop("outlet T [C]", lambda: comp1_out.getTemperature("C"), "°C")
    check_prop("outlet flow [kg/hr]", lambda: comp1_out.getFlowRate("kg/hr"), "kg/hr")
    check_prop("power [W]", lambda: comp1.getPower(), "W")
    check_prop("outlet density [kg/m3]", lambda: comp1_out.getThermoSystem().getDensity("kg/m3"), "kg/m3")
    check_prop("outlet enthalpy [kJ/kg]", lambda: comp1_out.getThermoSystem().getEnthalpy("kJ/kg"), "kJ/kg")
except Exception as e:
    print(f"\n  Compressor FAILED: {e}")


# ══════════════════════════════════════════════════════════════════════════
# TEST 2: Feed RECOVERED stream into a compressor
# ══════════════════════════════════════════════════════════════════════════
divider("TEST 2: Recovered stream (1 kg/hr) → Compressor")
print("  After our fix, the stream has 1.0 kg/hr epsilon flow.")

rescued = rescue_ghost_stream(ghost_gas.getThermoSystem())
print(f"  Rescued stream flow: {rescued.getFlowRate('kg/hr'):.4f} kg/hr")

try:
    comp2 = neqsim.process.equipment.compressor.Compressor("comp_rescued", rescued)
    comp2.setOutletPressure(150.0)
    comp2.setIsentropicEfficiency(0.75)
    comp2.run()

    comp2_out = comp2.getOutletStream()
    print(f"\n  Compressor solved")
    check_prop("outlet P [bara]", lambda: comp2_out.getPressure("bara"), "bara")
    check_prop("outlet T [C]", lambda: comp2_out.getTemperature("C"), "°C")
    check_prop("outlet flow [kg/hr]", lambda: comp2_out.getFlowRate("kg/hr"), "kg/hr")
    check_prop("power [W]", lambda: comp2.getPower(), "W")
    check_prop("outlet density [kg/m3]", lambda: comp2_out.getThermoSystem().getDensity("kg/m3"), "kg/m3")
    check_prop("outlet enthalpy [kJ/kg]", lambda: comp2_out.getThermoSystem().getEnthalpy("kJ/kg"), "kJ/kg")
except Exception as e:
    print(f"\n  Compressor FAILED: {e}")


# ══════════════════════════════════════════════════════════════════════════
# TEST 3: Feed ghost stream into a valve (NO recovery)
# ══════════════════════════════════════════════════════════════════════════
divider("TEST 3: Ghost stream → Valve (NO recovery)")

try:
    valve1 = neqsim.process.equipment.valve.ThrottlingValve("valve_ghost", ghost_gas)
    valve1.setOutletPressure(50.0)
    valve1.run()

    valve1_out = valve1.getOutletStream()
    print(f"  Valve solved (no crash)")
    check_prop("outlet P [bara]", lambda: valve1_out.getPressure("bara"), "bara")
    check_prop("outlet T [C]", lambda: valve1_out.getTemperature("C"), "°C")
    check_prop("outlet flow [kg/hr]", lambda: valve1_out.getFlowRate("kg/hr"), "kg/hr")
    check_prop("outlet density [kg/m3]", lambda: valve1_out.getThermoSystem().getDensity("kg/m3"), "kg/m3")
except Exception as e:
    print(f"\n  Valve FAILED: {e}")


# ══════════════════════════════════════════════════════════════════════════
# TEST 4: Feed RECOVERED stream into a valve
# ══════════════════════════════════════════════════════════════════════════
divider("TEST 4: Recovered stream (1 kg/hr) → Valve")

rescued2 = rescue_ghost_stream(ghost_gas.getThermoSystem())

try:
    valve2 = neqsim.process.equipment.valve.ThrottlingValve("valve_rescued", rescued2)
    valve2.setOutletPressure(50.0)
    valve2.run()

    valve2_out = valve2.getOutletStream()
    print(f"  Valve solved")
    check_prop("outlet P [bara]", lambda: valve2_out.getPressure("bara"), "bara")
    check_prop("outlet T [C]", lambda: valve2_out.getTemperature("C"), "°C")
    check_prop("outlet flow [kg/hr]", lambda: valve2_out.getFlowRate("kg/hr"), "kg/hr")
    check_prop("outlet density [kg/m3]", lambda: valve2_out.getThermoSystem().getDensity("kg/m3"), "kg/m3")
except Exception as e:
    print(f"\n  Valve FAILED: {e}")


# ══════════════════════════════════════════════════════════════════════════
# TEST 5: Zero-flow source stream → Compressor (the Bug 1 path)
# ══════════════════════════════════════════════════════════════════════════
divider("TEST 5: Zero-flow source stream → Compressor")
print("  User creates source with flow=0, connects to compressor.")

t5 = neqsim.thermo.system.SystemSrkEos(25.0 + 273.15, 50.0)
t5.addComponent("methane", 0.95)
t5.addComponent("ethane", 0.03)
t5.addComponent("propane", 0.02)
t5.setMixingRule(2)
t5.setTotalFlowRate(0.0, "kg/hr")

s5 = neqsim.process.equipment.stream.Stream("zero_source", t5)
s5.run()
print(f"  Source flow: {s5.getFlowRate('kg/hr'):.4e} kg/hr")

try:
    comp5 = neqsim.process.equipment.compressor.Compressor("comp_zero", s5)
    comp5.setOutletPressure(100.0)
    comp5.setIsentropicEfficiency(0.75)
    comp5.run()

    comp5_out = comp5.getOutletStream()
    print(f"\n  Compressor solved (no crash)")
    check_prop("outlet P [bara]", lambda: comp5_out.getPressure("bara"), "bara")
    check_prop("outlet T [C]", lambda: comp5_out.getTemperature("C"), "°C")
    check_prop("outlet flow [kg/hr]", lambda: comp5_out.getFlowRate("kg/hr"), "kg/hr")
    check_prop("power [W]", lambda: comp5.getPower(), "W")
    check_prop("outlet density [kg/m3]", lambda: comp5_out.getThermoSystem().getDensity("kg/m3"), "kg/m3")
except Exception as e:
    print(f"\n  Compressor FAILED: {e}")


# ══════════════════════════════════════════════════════════════════════════
# TEST 6: Recovered zero-flow source → Compressor
# ══════════════════════════════════════════════════════════════════════════
divider("TEST 6: Recovered zero-flow source (1 kg/hr) → Compressor")

rescued6 = rescue_ghost_stream(s5.getThermoSystem())
print(f"  Rescued stream flow: {rescued6.getFlowRate('kg/hr'):.4f} kg/hr")

try:
    comp6 = neqsim.process.equipment.compressor.Compressor("comp_rescued_zero", rescued6)
    comp6.setOutletPressure(100.0)
    comp6.setIsentropicEfficiency(0.75)
    comp6.run()

    comp6_out = comp6.getOutletStream()
    print(f"\n  Compressor solved")
    check_prop("outlet P [bara]", lambda: comp6_out.getPressure("bara"), "bara")
    check_prop("outlet T [C]", lambda: comp6_out.getTemperature("C"), "°C")
    check_prop("outlet flow [kg/hr]", lambda: comp6_out.getFlowRate("kg/hr"), "kg/hr")
    check_prop("power [W]", lambda: comp6.getPower(), "W")
    check_prop("outlet density [kg/m3]", lambda: comp6_out.getThermoSystem().getDensity("kg/m3"), "kg/m3")
    check_prop("outlet enthalpy [kJ/kg]", lambda: comp6_out.getThermoSystem().getEnthalpy("kJ/kg"), "kJ/kg")
except Exception as e:
    print(f"\n  Compressor FAILED: {e}")


# ══════════════════════════════════════════════════════════════════════════
# TEST 7: Ghost stream into a mixer (mixed with real stream)
# ══════════════════════════════════════════════════════════════════════════
divider("TEST 7: Ghost gas + real liquid → Mixer")
print("  What happens when ghost gas is mixed with the real liquid outlet?")

real_liq = sep.getLiquidOutStream()
print(f"  Real liquid flow: {real_liq.getFlowRate('kg/hr'):.4f} kg/hr")
print(f"  Ghost gas flow: {ghost_flow:.4e} kg/hr")

try:
    mixer = neqsim.process.equipment.mixer.Mixer("mix")
    mixer.addStream(ghost_gas)
    mixer.addStream(real_liq)
    mixer.run()

    mix_out = mixer.getOutletStream()
    print(f"\n  Mixer solved")
    check_prop("outlet T [C]", lambda: mix_out.getTemperature("C"), "°C")
    check_prop("outlet P [bara]", lambda: mix_out.getPressure("bara"), "bara")
    check_prop("outlet flow [kg/hr]", lambda: mix_out.getFlowRate("kg/hr"), "kg/hr")
    check_prop("outlet density [kg/m3]", lambda: mix_out.getThermoSystem().getDensity("kg/m3"), "kg/m3")
except Exception as e:
    print(f"\n  Mixer FAILED: {e}")


# ══════════════════════════════════════════════════════════════════════════
# IMPORTANT: What does the DOWNSTREAM stream look like after equipment
# processes a recovered (epsilon) flow?
# ══════════════════════════════════════════════════════════════════════════
divider("TEST 8: Chain test — rescued → compressor → what flow does outlet have?")
print("  If we recover to 1 kg/hr, does the compressor outlet also have 1 kg/hr?")
print("  This matters because our fix reports flow=0 but the actual NeqSim stream")
print("  has epsilon flow — downstream equipment will propagate that epsilon flow.")

rescued8 = rescue_ghost_stream(ghost_gas.getThermoSystem())
comp8 = neqsim.process.equipment.compressor.Compressor("comp_chain", rescued8)
comp8.setOutletPressure(150.0)
comp8.setIsentropicEfficiency(0.75)
comp8.run()

comp8_out = comp8.getOutletStream()
print(f"\n  Compressor inlet flow:  {rescued8.getFlowRate('kg/hr'):.6f} kg/hr")
print(f"  Compressor outlet flow: {comp8_out.getFlowRate('kg/hr'):.6f} kg/hr")
print(f"  Compressor power:       {comp8.getPower():.6f} W")

# Now extract from the outlet — is IT also a clean stream?
out_thermo = comp8_out.getThermoSystem()
out_thermo.initPhysicalProperties()
print()
check_prop("outlet density [kg/m3]", lambda: out_thermo.getDensity("kg/m3"), "kg/m3")
check_prop("outlet enthalpy [kJ/kg]", lambda: out_thermo.getEnthalpy("kJ/kg"), "kJ/kg")
check_prop("outlet Cp [kJ/kgK]", lambda: out_thermo.getCp("kJ/kgK"), "kJ/kgK")
check_prop("outlet Z", lambda: out_thermo.getZ())


# ══════════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════════
divider("SUMMARY")
print("""
Key questions answered:
1. Can NeqSim equipment handle ghost streams? → Check tests above
2. Can NeqSim equipment handle recovered (epsilon) streams? → Check tests above
3. Does the fix propagate correctly through equipment chains? → Check test 8
4. Is our approach of recovering at extraction time sufficient,
   or do we need to recover BEFORE downstream equipment runs?

If downstream equipment crashes or produces garbage with ghost streams,
we may need to recover the stream BEFORE it feeds into the next unit,
not just at extraction time. This would change the fix location from
Stream.extract_results() to earlier in the solver pipeline.
""")
