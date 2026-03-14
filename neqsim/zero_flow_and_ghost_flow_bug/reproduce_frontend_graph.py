#!/usr/bin/env python3
"""Reproduce the exact frontend separator graph to identify which outlet triggers the bug.

Uses the same composition, T, P, and topology as the user's web app test graph.
Feed: equal-fraction C1-nC5 mixture at 10°C, 100 bar, 100 kg/hr.

Run with: uv run python .dev/dev_wdir/debugging/zero_flow_and_ghost_flow_bug/reproduce_frontend_graph.py
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent / "src"))

from jneqsim import neqsim  # noqa: E402

COMP = {
    "methane": 0.14285714285714282,
    "ethane": 0.14285714285714282,
    "propane": 0.14285714285714282,
    "i-butane": 0.14285714285714282,
    "n-butane": 0.14285714285714282,
    "i-pentane": 0.14285714285714282,
    "n-pentane": 0.14285714285714282,
}

TEMP_C = 10.0
PRESSURE_BARA = 100.0
FLOW_KG_HR = 100.0


def divider(title: str) -> None:
    print(f"\n{'=' * 72}")
    print(f"  {title}")
    print(f"{'=' * 72}")


def check_property(label: str, getter, unit: str = "") -> tuple[str, float | None]:
    """Try to extract a property, report result and status."""
    try:
        v = getter()
        v_float = float(v)
        is_fin = math.isfinite(v_float)
        is_nan = math.isnan(v_float)
        status = "OK" if is_fin and abs(v_float) < 1e10 else ("NaN" if is_nan else ("inf" if not is_fin else "GARBAGE"))
        print(f"  {label:35s} = {v_float:>15.6g} {unit:10s}  [{status}]")
        return status, v_float
    except Exception as e:
        print(f"  {label:35s} = EXCEPTION: {e}")
        return "EXCEPTION", None


# ─── Build the system ───────────────────────────────────────────────────
divider("FEED STREAM (your exact frontend graph)")
print(f"  T={TEMP_C}°C, P={PRESSURE_BARA} bara, flow={FLOW_KG_HR} kg/hr")
print(f"  Components: {list(COMP.keys())}")
print(f"  Equal fractions: {list(COMP.values())[0]:.6f}")

temp_k = TEMP_C + 273.15
thermo = neqsim.thermo.system.SystemSrkEos(temp_k, PRESSURE_BARA)
for name, frac in COMP.items():
    thermo.addComponent(name, frac)
thermo.setMixingRule(2)
thermo.setTotalFlowRate(FLOW_KG_HR, "kg/hr")

inlet = neqsim.process.equipment.stream.Stream("inlet", thermo)
inlet.run()

inlet_thermo = inlet.getThermoSystem()
n_phases = inlet_thermo.getNumberOfPhases()
has_gas = inlet_thermo.hasPhaseType("gas")
has_oil = inlet_thermo.hasPhaseType("oil")
has_aq = inlet_thermo.hasPhaseType("aqueous")

print(f"\n  Inlet phases: {n_phases}")
print(f"  hasGas={has_gas}, hasOil={has_oil}, hasAqueous={has_aq}")

# ─── Separator ──────────────────────────────────────────────────────────
divider("SEPARATOR")
sep = neqsim.process.equipment.separator.Separator("sep", inlet)
sep.run()

gas_stream = sep.getGasOutStream()
liq_stream = sep.getLiquidOutStream()

gas_flow = float(gas_stream.getFlowRate("kg/hr"))
liq_flow = float(liq_stream.getFlowRate("kg/hr"))

print(f"  Gas outlet flow: {gas_flow:.6e} kg/hr")
print(f"  Liq outlet flow: {liq_flow:.6e} kg/hr")

THRESHOLD = 1e-10
gas_is_ghost = gas_flow < THRESHOLD
liq_is_ghost = liq_flow < THRESHOLD

print(f"\n  Gas is ghost? {gas_is_ghost}")
print(f"  Liq is ghost? {liq_is_ghost}")

# ─── Inspect ghost outlet(s) ───────────────────────────────────────────
for label, stream, is_ghost in [
    ("GAS OUTLET", gas_stream, gas_is_ghost),
    ("LIQUID OUTLET", liq_stream, liq_is_ghost),
]:
    divider(f"{label} {'(GHOST)' if is_ghost else '(REAL)'}")

    t = stream.getThermoSystem()
    t.initPhysicalProperties()

    flow = float(stream.getFlowRate("kg/hr"))
    print(f"  Flow: {flow:.6e} kg/hr")
    print(f"  Phases: {t.getNumberOfPhases()}")
    print(f"  hasGas={t.hasPhaseType('gas')}, hasOil={t.hasPhaseType('oil')}")

    print("\n  --- Mix-level properties (what Stream.extract_results() checks) ---")
    check_property("temperature [C]", lambda: t.getTemperature("C"), "°C")
    check_property("pressure [bara]", lambda: t.getPressure("bara"), "bara")
    check_property("density [kg/m3]", lambda: t.getDensity("kg/m3"), "kg/m3")
    check_property("enthalpy [kJ/kg]", lambda: t.getEnthalpy("kJ/kg"), "kJ/kg")
    check_property("entropy [kJ/kgK]", lambda: t.getEntropy("kJ/kgK"), "kJ/kgK")
    check_property("Cp [kJ/kgK]", lambda: t.getCp("kJ/kgK"), "kJ/kgK")
    check_property("Cv [kJ/kgK]", lambda: t.getCv("kJ/kgK"), "kJ/kgK")
    check_property("Z factor", lambda: t.getZ())
    check_property("molar mass [g/mol]", lambda: t.getMolarMass() * 1000, "g/mol")
    check_property("viscosity [cP]", lambda: t.getViscosity("cP"), "cP")
    check_property("thermal cond [W/mK]", lambda: t.getThermalConductivity("W/mK"), "W/mK")
    check_property("speed of sound [m/s]", lambda: t.getSoundSpeed(), "m/s")
    check_property("JT coeff", lambda: t.getJouleThomsonCoefficient())

    # Check mass/molar flow (these are critical props)
    check_property("mass flow [kg/hr]", lambda: stream.getFlowRate("kg/hr"), "kg/hr")
    check_property("molar rate [mol/s]", lambda: stream.getMolarRate(), "mol/s")

    if is_ghost:
        print("\n  --- Ghost composition analysis ---")
        n_comp = t.getNumberOfComponents()
        total_z = 0.0
        for i in range(n_comp):
            c = t.getComponent(i)
            z = float(c.getz())
            total_z += z
            print(f"    {str(c.getName()):15s}  z={z:.8f}")
        print(f"    {'TOTAL':15s}  z={total_z:.8f}  (should be 1.0, ghost halves to ~0.5)")


# ─── Summary ────────────────────────────────────────────────────────────
divider("ANALYSIS")

if not gas_is_ghost and not liq_is_ghost:
    print("""
  Both outlets have real flow — this composition at 10°C / 100 bar is TWO-PHASE.
  No ghost outlets are produced, so neither Bug 1 nor Bug 2 triggers.

  The error you saw ("Critical properties returned invalid values") was likely
  from a DIFFERENT configuration (different T/P/composition that makes the feed
  single-phase), or from a zero-flow source stream elsewhere in the graph.
""")
elif liq_is_ghost:
    print("""
  Liquid outlet is GHOST — this is Bug 2.
  Ghost density passes isfinite() but is wrong (halved).
  Ghost enthalpy/Cp are astronomically wrong but also pass isfinite().
  The error "Critical properties returned invalid values (inf/NaN): density"
  would only trigger if density returns NaN — check the values above.
""")
elif gas_is_ghost:
    print("""
  Gas outlet is GHOST — this is Bug 2 (gas variant).
  Same ghost-flow corruption applies to the gas outlet.
""")
