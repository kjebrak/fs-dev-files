"""Debug: Separator outlet with zero/near-zero flow.

Reproduces the bug where a separator produces an outlet phase with negligible
flow, causing NeqSim to return inf/NaN for density and other properties.

NeqSim internally uses getEmptySystemClone() which scales all moles by 1e-30,
leading to numerical instability in property calculations.

Investigation questions:
1. At what compositions does one outlet flow become zero?
2. Which properties fail (inf/NaN) when flow is near-zero?
3. Does NeqSim's hasPhaseType() still report the phase as existing?
4. What does the thermo system look like for a near-zero phase?

IMPORTANT: NeqSim constructors take temperature in KELVIN, not Celsius!
"""

import math
import sys

sys.path.insert(0, "src")

from jneqsim import neqsim  # noqa: E402


def check_properties(label: str, stream, thermo) -> list[str]:
    """Check all properties on a stream/thermo, return list of failed ones."""
    failed = []
    print(f"\n── {label}: Property Extraction ──")

    basic_props = {
        "flow_rate (kg/hr)": lambda: stream.getFlowRate("kg/hr"),
        "temperature (C)": lambda: stream.getTemperature("C"),
        "pressure (bara)": lambda: stream.getPressure("bara"),
        "density (kg/m3)": lambda: thermo.getDensity("kg/m3"),
        "molar_mass (kg/mol)": lambda: thermo.getMolarMass(),
        "molar_volume": lambda: thermo.getMolarVolume(),
        "enthalpy (kJ/kg)": lambda: thermo.getEnthalpy("kJ/kg"),
        "entropy (kJ/kgK)": lambda: thermo.getEntropy("kJ/kgK"),
        "z_factor": lambda: thermo.getZ(),
        "cp (kJ/kgK)": lambda: thermo.getCp("kJ/kgK"),
        "cv (kJ/kgK)": lambda: thermo.getCv("kJ/kgK"),
        "numberOfMoles": lambda: thermo.getNumberOfMoles(),
        "numberOfPhases": lambda: thermo.getNumberOfPhases(),
    }

    for name, getter in basic_props.items():
        try:
            value = getter()
            is_valid = value is not None and (
                isinstance(value, int) or math.isfinite(value)
            )
            status = "OK" if is_valid else "INVALID"
            if not is_valid:
                failed.append(name)
            print(f"  {name:30s} = {str(value):<25s} [{status}]")
        except Exception as e:
            failed.append(name)
            print(f"  {name:30s} = EXCEPTION: {e}")

    # Transport properties
    print(f"  {'--- transport ---':30s}")
    try:
        thermo.initPhysicalProperties()
        transport_props = {
            "viscosity (cP)": lambda: thermo.getViscosity("cP"),
            "thermal_cond (W/mK)": lambda: thermo.getThermalConductivity("W/mK"),
            "speed_of_sound": lambda: thermo.getSoundSpeed(),
        }
        for name, getter in transport_props.items():
            try:
                value = getter()
                is_valid = value is not None and math.isfinite(value)
                status = "OK" if is_valid else "INVALID"
                if not is_valid:
                    failed.append(name)
                print(f"  {name:30s} = {str(value):<25s} [{status}]")
            except Exception as e:
                failed.append(name)
                print(f"  {name:30s} = EXCEPTION: {e}")
    except Exception as e:
        failed.append("initPhysicalProperties")
        print(f"  initPhysicalProperties FAILED: {e}")

    return failed


def run_separator_test(
    label: str,
    composition: dict[str, float],
    temp_c: float = 25.0,
    pressure_bar: float = 50.0,
    flow_kg_hr: float = 1000.0,
):
    """Run a separator and check both outlets."""
    print("\n" + "=" * 70)
    print(f"  {label}")
    print(f"  T={temp_c}°C, P={pressure_bar} bar, Flow={flow_kg_hr} kg/hr")
    print(f"  Composition: {composition}")
    print("=" * 70)

    temp_k = temp_c + 273.15
    thermo = neqsim.thermo.system.SystemSrkEos(temp_k, pressure_bar)
    for comp, frac in composition.items():
        thermo.addComponent(comp, frac)
    thermo.setMixingRule(2)
    thermo.setTotalFlowRate(flow_kg_hr, "kg/hr")

    inlet = neqsim.process.equipment.stream.Stream("inlet", thermo)
    inlet.run()

    sep = neqsim.process.equipment.separator.Separator("sep", inlet)
    sep.run()

    gas_out = sep.getGasOutStream()
    liq_out = sep.getLiquidOutStream()

    gas_flow = gas_out.getFlowRate("kg/hr")
    liq_flow = liq_out.getFlowRate("kg/hr")

    print(f"\n  Inlet:   {inlet.getFlowRate('kg/hr'):>12.4f} kg/hr  T={inlet.getTemperature('C'):.1f}°C")
    print(f"  Gas out: {gas_flow:>12.6e} kg/hr")
    print(f"  Liq out: {liq_flow:>12.6e} kg/hr")
    print(f"  Balance: {gas_flow + liq_flow:>12.4f} kg/hr")

    gas_thermo = gas_out.getThermoSystem()
    liq_thermo = liq_out.getThermoSystem()

    print(f"\n  Gas thermo: phases={gas_thermo.getNumberOfPhases()}, "
          f"hasGas={gas_thermo.hasPhaseType('gas')}, hasOil={gas_thermo.hasPhaseType('oil')}")
    print(f"  Liq thermo: phases={liq_thermo.getNumberOfPhases()}, "
          f"hasGas={liq_thermo.hasPhaseType('gas')}, hasOil={liq_thermo.hasPhaseType('oil')}")

    gas_failed = check_properties("Gas Outlet", gas_out, gas_thermo)
    liq_failed = check_properties("Liquid Outlet", liq_out, liq_thermo)

    return {
        "gas_flow": gas_flow,
        "liq_flow": liq_flow,
        "gas_failed": gas_failed,
        "liq_failed": liq_failed,
    }


# ── Scenario 1: Dry gas — expect mostly gas, little/no liquid ─────────────
r1 = run_separator_test(
    "SCENARIO 1: Dry gas (95% CH4) at 25°C, 50 bar",
    {"methane": 0.95, "ethane": 0.03, "propane": 0.02},
)

# ── Scenario 2: Rich gas — expect both phases ────────────────────────────
r2 = run_separator_test(
    "SCENARIO 2: Rich gas (70% CH4 + heavies) at 25°C, 50 bar",
    {"methane": 0.70, "ethane": 0.10, "propane": 0.10, "n-butane": 0.10},
)

# ── Scenario 3: Pure methane — definitely single phase at 25°C, 50 bar ───
r3 = run_separator_test(
    "SCENARIO 3: Pure methane at 25°C, 50 bar (supercritical!)",
    {"methane": 1.0},
)

# ── Scenario 4: Pure methane at lower pressure (definitely gas) ───────────
r4 = run_separator_test(
    "SCENARIO 4: Pure methane at 25°C, 10 bar (gas phase)",
    {"methane": 1.0},
    pressure_bar=10.0,
)

# ── Scenario 5: Light gas at low pressure (the frontend test case?) ───────
r5 = run_separator_test(
    "SCENARIO 5: Light gas at 20°C, 30 bar",
    {"methane": 0.85, "ethane": 0.10, "propane": 0.05},
    temp_c=20.0,
    pressure_bar=30.0,
)

# ── Scenario 6: Cold rich gas — ensures liquid phase exists ───────────────
r6 = run_separator_test(
    "SCENARIO 6: Rich gas at -20°C, 50 bar (cold = more liquid)",
    {"methane": 0.70, "ethane": 0.10, "propane": 0.10, "n-butane": 0.10},
    temp_c=-20.0,
)


# ── Threshold sweep ──────────────────────────────────────────────────────
print("\n\n" + "=" * 70)
print("  THRESHOLD SWEEP: CH4 fraction at 25°C, 50 bar")
print("=" * 70)
print(f"  {'CH4':>5s}  {'gas_flow':>14s}  {'liq_flow':>14s}  {'gas_dens':>12s}  {'liq_dens':>12s}  {'status':>8s}")
print("  " + "-" * 75)

ch4_fracs = [0.50, 0.60, 0.70, 0.80, 0.90, 0.95, 0.98, 0.99, 1.00]
for ch4 in ch4_fracs:
    remaining = 1.0 - ch4
    temp_k = 25.0 + 273.15
    t = neqsim.thermo.system.SystemSrkEos(temp_k, 50.0)
    t.addComponent("methane", ch4)
    if remaining > 1e-10:
        t.addComponent("ethane", remaining * 0.4)
        t.addComponent("propane", remaining * 0.3)
        t.addComponent("n-butane", remaining * 0.3)
    t.setMixingRule(2)
    t.setTotalFlowRate(1000.0, "kg/hr")

    s = neqsim.process.equipment.stream.Stream(f"s_{ch4}", t)
    s.run()
    sep = neqsim.process.equipment.separator.Separator(f"sep_{ch4}", s)
    sep.run()

    g = sep.getGasOutStream()
    l = sep.getLiquidOutStream()
    gf = g.getFlowRate("kg/hr")
    lf = l.getFlowRate("kg/hr")

    try:
        gd = g.getThermoSystem().getDensity("kg/m3")
        gd_ok = math.isfinite(gd)
    except Exception:
        gd = float("nan")
        gd_ok = False

    try:
        ld = l.getThermoSystem().getDensity("kg/m3")
        ld_ok = math.isfinite(ld)
    except Exception:
        ld = float("nan")
        ld_ok = False

    status = "OK" if (gd_ok and ld_ok) else "BROKEN"
    broken_side = ""
    if not gd_ok:
        broken_side += " gas"
    if not ld_ok:
        broken_side += " liq"

    print(f"  {ch4:5.2f}  {gf:>14.6e}  {lf:>14.6e}  {gd:>12.4f}  {ld:>12.4f}  [{status}{broken_side}]")


# ── Summary ───────────────────────────────────────────────────────────────
print("\n\n" + "=" * 70)
print("  SUMMARY")
print("=" * 70)

all_results = [
    ("Scenario 1 (dry gas)", r1),
    ("Scenario 2 (rich gas)", r2),
    ("Scenario 3 (pure CH4 supercrit)", r3),
    ("Scenario 4 (pure CH4 low P)", r4),
    ("Scenario 5 (light gas 30 bar)", r5),
    ("Scenario 6 (cold rich gas)", r6),
]

for label, r in all_results:
    gas_status = f"{len(r['gas_failed'])} failures" if r["gas_failed"] else "OK"
    liq_status = f"{len(r['liq_failed'])} failures" if r["liq_failed"] else "OK"
    zero_gas = " [ZERO GAS]" if r["gas_flow"] < 1e-10 else ""
    zero_liq = " [ZERO LIQ]" if r["liq_flow"] < 1e-10 else ""
    print(f"  {label:40s}  gas={gas_status:12s}{zero_gas}  liq={liq_status:12s}{zero_liq}")
    if r["gas_failed"]:
        print(f"    Gas failures: {r['gas_failed']}")
    if r["liq_failed"]:
        print(f"    Liq failures: {r['liq_failed']}")
