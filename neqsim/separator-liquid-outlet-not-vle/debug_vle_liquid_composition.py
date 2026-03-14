"""Debug: Investigate how to get equilibrium liquid composition from NeqSim
for a single-phase gas system (no actual liquid present).

The question: When a separator flash produces 100% gas (no liquid phase),
can NeqSim still give us the *hypothetical VLE equilibrium liquid* composition?
UniSim does this — it reports the incipient liquid composition even for zero-flow
liquid streams.

Approaches tested:
1. Direct: What does the liquid outlet thermo look like after separator run?
2. K-values: Can we compute x_i = z_i / K_i from stored K-values?
3. Hidden phase: Does getPhases()[1] still have equilibrium liquid data?
4. Force two-phase: Can we force NeqSim to compute both phases?
5. Dew point flash: Use dewT flash to get the liquid at the dew point

Run with: uv run python .dev/dev_wdir/debugging/separator-liquid-outlet-not-vle/debug_vle_liquid_composition.py
"""

import math
import sys

sys.path.insert(0, "src")

from jneqsim import neqsim  # noqa: E402


def divider(title: str) -> None:
    print(f"\n{'=' * 72}")
    print(f"  {title}")
    print(f"{'=' * 72}")


def print_composition(label: str, thermo, phase_idx=None):
    """Print component composition from thermo system or specific phase."""
    n = thermo.getNumberOfComponents()
    print(f"\n  {label}:")
    print(f"    {'Component':<15s} {'z (overall)':>12s} {'x (phase)':>12s} {'K-value':>12s}")
    print(f"    {'-' * 51}")
    for i in range(n):
        comp = thermo.getComponent(i)
        name = str(comp.getName())
        z = float(comp.getz())

        # Try to get phase-specific composition
        if phase_idx is not None:
            try:
                phase_comp = thermo.getPhases()[phase_idx].getComponent(i)
                x = float(phase_comp.getx())
            except Exception:
                x = float("nan")
        else:
            x = float("nan")

        # Try to get K-value
        try:
            k = float(comp.getK())
        except Exception:
            k = float("nan")

        print(f"    {name:<15s} {z:>12.6f} {x:>12.6f} {k:>12.6f}")


# ═══════════════════════════════════════════════════════════════════════════
# Setup: Same system as the user's test case
# Feed: 200°C, 4 bar, equal mole fractions of ethane/propane/n-heptane/nC10/n-nonane
# This is 100% gas at these conditions
# ═══════════════════════════════════════════════════════════════════════════

divider("SETUP: Create inlet stream (200°C, 4 bar, 5-component HC mix)")

temp_k = 200.0 + 273.15
pressure = 4.0  # bara

thermo = neqsim.thermo.system.SystemPrEos(temp_k, pressure)
for comp in ["ethane", "propane", "n-heptane", "nC10", "n-nonane"]:
    thermo.addComponent(comp, 0.20)
thermo.autoSelectMixingRule()
thermo.setTotalFlowRate(100.0, "kg/hr")

inlet = neqsim.process.equipment.stream.Stream("inlet", thermo)
inlet.run()

inlet_thermo = inlet.getThermoSystem()
print(f"  Inlet: T={inlet.getTemperature('C'):.1f}°C, P={inlet.getPressure('bara'):.1f} bar")
print(f"  Inlet flow: {inlet.getFlowRate('kg/hr'):.2f} kg/hr")
print(f"  Inlet phases: {inlet_thermo.getNumberOfPhases()}")
print(f"  Inlet hasGas: {inlet_thermo.hasPhaseType('gas')}")
print(f"  Inlet hasOil: {inlet_thermo.hasPhaseType('oil')}")
print(f"  Inlet vapor fraction (beta): {inlet_thermo.getBeta():.6f}")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 1: Raw separator outlet — what does NeqSim give us?
# ═══════════════════════════════════════════════════════════════════════════

divider("TEST 1: Raw separator outlet (what NeqSim produces)")

sep = neqsim.process.equipment.separator.Separator("sep", inlet)
sep.run()

gas_out = sep.getGasOutStream()
liq_out = sep.getLiquidOutStream()

gas_thermo = gas_out.getThermoSystem()
liq_thermo = liq_out.getThermoSystem()

print(f"  Gas outlet: flow={gas_out.getFlowRate('kg/hr'):.4f} kg/hr, "
      f"phases={gas_thermo.getNumberOfPhases()}, "
      f"hasGas={gas_thermo.hasPhaseType('gas')}, hasOil={gas_thermo.hasPhaseType('oil')}")
print(f"  Liq outlet: flow={liq_out.getFlowRate('kg/hr'):.4e} kg/hr, "
      f"phases={liq_thermo.getNumberOfPhases()}, "
      f"hasGas={liq_thermo.hasPhaseType('gas')}, hasOil={liq_thermo.hasPhaseType('oil')}")
print(f"  Liq MW: {liq_thermo.getMolarMass() * 1000:.2f} g/mol")
print(f"  Gas MW: {gas_thermo.getMolarMass() * 1000:.2f} g/mol")

print_composition("Liq outlet — from getEmptySystemClone (feed z-fracs)", liq_thermo)


# ═══════════════════════════════════════════════════════════════════════════
# TEST 2: Check K-values on the inlet thermo system (post-flash)
# ═══════════════════════════════════════════════════════════════════════════

divider("TEST 2: K-values from inlet thermo (post-flash)")

print_composition("Inlet thermo — phases[0]=gas, phases[1]=?", inlet_thermo, phase_idx=1)

# Also check the separator's internal thermoSystem2
print("\n  Checking all phases in inlet thermo:")
for p_idx in range(int(inlet_thermo.getMaxNumberOfPhases())):
    try:
        phase = inlet_thermo.getPhases()[p_idx]
        phase_type = str(phase.getType())
        n = phase.getNumberOfComponents()
        comp_str = ", ".join(
            f"{str(phase.getComponent(i).getName())}={float(phase.getComponent(i).getx()):.6f}"
            for i in range(n)
        )
        print(f"    Phase[{p_idx}] type={phase_type}: {comp_str}")
    except Exception as e:
        print(f"    Phase[{p_idx}]: EXCEPTION: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 3: Compute incipient liquid from K-values
# ═══════════════════════════════════════════════════════════════════════════

divider("TEST 3: Compute incipient liquid from K-values")

print("  If K_i values are available from the flash, then:")
print("  x_i = z_i / K_i  (unnormalized)")
print("  x_i_norm = x_i / sum(x_j)  (normalized)")

n = inlet_thermo.getNumberOfComponents()
incipient = []
for i in range(n):
    comp = inlet_thermo.getComponent(i)
    name = str(comp.getName())
    z = float(comp.getz())
    try:
        k = float(comp.getK())
        x_unnorm = z / k if k > 1e-20 else 0.0
    except Exception:
        k = float("nan")
        x_unnorm = 0.0
    incipient.append((name, z, k, x_unnorm))

total_x = sum(x for _, _, _, x in incipient)
print(f"\n  {'Component':<15s} {'z_i':>10s} {'K_i':>12s} {'z/K (unnorm)':>14s} {'x_i (norm)':>12s}")
print(f"  {'-' * 63}")
for name, z, k, x_unnorm in incipient:
    x_norm = x_unnorm / total_x if total_x > 1e-20 else 0.0
    print(f"  {name:<15s} {z:>10.6f} {k:>12.6f} {x_unnorm:>14.6f} {x_norm:>12.6f}")
print(f"  {'sum':>15s} {sum(z for _, z, _, _ in incipient):>10.6f} {'':>12s} {total_x:>14.6f} {'1.000000':>12s}")

if total_x > 0:
    incipient_mw = sum(
        (x_unnorm / total_x) * float(inlet_thermo.getComponent(i).getMolarMass() * 1000)
        for i, (_, _, _, x_unnorm) in enumerate(incipient)
    )
    print(f"\n  Incipient liquid MW (from K-values): {incipient_mw:.2f} g/mol")
    print(f"  UniSim liquid MW: 129.8 g/mol")
    print(f"  Feed MW: {inlet_thermo.getMolarMass() * 1000:.2f} g/mol")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 4: Force two-phase flash (init with 2 phases)
# ═══════════════════════════════════════════════════════════════════════════

divider("TEST 4: Force two-phase initialization")

# Clone the inlet thermo and try to force two-phase
forced = inlet_thermo.clone()
try:
    # Try forcing 2 phases before flash
    forced.setNumberOfPhases(2)
    print(f"  After setNumberOfPhases(2): phases={forced.getNumberOfPhases()}")

    # Re-initialize
    forced.init(0)
    forced.init(1)
    print(f"  After init(0)+init(1): phases={forced.getNumberOfPhases()}")

    # Now check what's in phase[1]
    print_composition("Forced two-phase system", forced, phase_idx=1)

    for p_idx in range(2):
        phase = forced.getPhases()[p_idx]
        phase_type = str(phase.getType())
        comp_str = ", ".join(
            f"{str(phase.getComponent(i).getName())}={float(phase.getComponent(i).getx()):.6f}"
            for i in range(int(phase.getNumberOfComponents()))
        )
        print(f"    Phase[{p_idx}] type={phase_type}: {comp_str}")
except Exception as e:
    print(f"  EXCEPTION: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 5: Use TPflash directly on a clone, check phase[1] compositions
# ═══════════════════════════════════════════════════════════════════════════

divider("TEST 5: Direct TPflash on a fresh system, check hidden phase[1]")

fresh = neqsim.thermo.system.SystemPrEos(temp_k, pressure)
for comp in ["ethane", "propane", "n-heptane", "nC10", "n-nonane"]:
    fresh.addComponent(comp, 0.20)
fresh.autoSelectMixingRule()
fresh.setTotalFlowRate(100.0, "kg/hr")

# Do the flash using NeqSim's ThermodynamicOperations
thermoOps = neqsim.thermodynamicoperations.ThermodynamicOperations(fresh)
thermoOps.TPflash()

print(f"  After TPflash: phases={fresh.getNumberOfPhases()}")
print(f"  hasGas={fresh.hasPhaseType('gas')}, hasOil={fresh.hasPhaseType('oil')}")
print(f"  beta (vapor frac): {fresh.getBeta():.6f}")

# Check K-values
print("\n  K-values after TPflash:")
for i in range(fresh.getNumberOfComponents()):
    comp = fresh.getComponent(i)
    name = str(comp.getName())
    try:
        k = float(comp.getK())
        z = float(comp.getz())
        print(f"    {name:<15s} z={z:.6f}  K={k:.6f}")
    except Exception as e:
        print(f"    {name:<15s} K=EXCEPTION: {e}")

# Check phases[1] — the "phantom" liquid
print("\n  Phase compositions after TPflash (phases[1] might be phantom liquid):")
for p_idx in range(int(fresh.getMaxNumberOfPhases())):
    try:
        phase = fresh.getPhases()[p_idx]
        phase_type = str(phase.getType())
        n = phase.getNumberOfComponents()
        comp_str = ", ".join(
            f"{str(phase.getComponent(i).getName())}={float(phase.getComponent(i).getx()):.4f}"
            for i in range(n)
        )
        print(f"    Phase[{p_idx}] type={phase_type}: {comp_str}")
    except Exception as e:
        print(f"    Phase[{p_idx}]: EXCEPTION: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 6: Create a LIQUID-only system from the incipient composition
# and verify it produces sensible liquid properties
# ═══════════════════════════════════════════════════════════════════════════

divider("TEST 6: Build incipient liquid stream and verify properties")

if total_x > 0:
    # Build a system with incipient liquid composition
    liq_system = neqsim.thermo.system.SystemPrEos(temp_k, pressure)
    for name, z, k, x_unnorm in incipient:
        x_norm = x_unnorm / total_x
        liq_system.addComponent(name, x_norm)
    liq_system.autoSelectMixingRule()
    liq_system.setTotalFlowRate(1.0, "kg/hr")  # epsilon flow

    liq_stream = neqsim.process.equipment.stream.Stream("incipient_liq", liq_system)
    liq_stream.run()

    liq_t = liq_stream.getThermoSystem()
    print(f"  Incipient liquid stream:")
    print(f"    T={liq_stream.getTemperature('C'):.1f}°C, P={liq_stream.getPressure('bara'):.1f} bar")
    print(f"    phases={liq_t.getNumberOfPhases()}, hasGas={liq_t.hasPhaseType('gas')}, hasOil={liq_t.hasPhaseType('oil')}")
    print(f"    MW={liq_t.getMolarMass() * 1000:.2f} g/mol")
    print(f"    vapor_fraction={liq_t.getBeta():.6f}")
    print(f"    density={liq_t.getDensity('kg/m3'):.2f} kg/m3")

    print_composition("Incipient liquid stream", liq_t)
    print(f"\n  NOTE: The incipient liquid composition itself may flash into")
    print(f"  multiple phases at 200°C/4bar — it represents the equilibrium")
    print(f"  liquid from the ORIGINAL flash, not a standalone stable liquid.")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 7: What about using the SEPARATOR's internal thermoSystem2?
# After separator runs, thermoSystem2 has the post-flash result.
# Can we extract phase compositions from it?
# ═══════════════════════════════════════════════════════════════════════════

divider("TEST 7: Separator internal thermo system")

try:
    sep_thermo = sep.getThermoSystem()
    print(f"  Separator thermoSystem: phases={sep_thermo.getNumberOfPhases()}")
    print(f"  hasGas={sep_thermo.hasPhaseType('gas')}, hasOil={sep_thermo.hasPhaseType('oil')}")

    for p_idx in range(int(sep_thermo.getMaxNumberOfPhases())):
        try:
            phase = sep_thermo.getPhases()[p_idx]
            phase_type = str(phase.getType())
            n = phase.getNumberOfComponents()
            comp_str = ", ".join(
                f"{str(phase.getComponent(i).getName())}={float(phase.getComponent(i).getx()):.6f}"
                for i in range(n)
            )
            mw = sum(
                float(phase.getComponent(i).getx()) * float(phase.getComponent(i).getMolarMass() * 1000)
                for i in range(n)
            )
            print(f"    Phase[{p_idx}] type={phase_type} MW={mw:.2f}: {comp_str}")
        except Exception as e:
            print(f"    Phase[{p_idx}]: EXCEPTION: {e}")
except Exception as e:
    print(f"  EXCEPTION: {e}")


# ═══════════════════════════════════════════════════════════════════════════
# TEST 8: Compare with a two-phase case to validate our approach
# Use the same composition but at a lower temperature where liquid exists
# ═══════════════════════════════════════════════════════════════════════════

divider("TEST 8: Validation — Same composition at 50°C (two-phase expected)")

thermo_2p = neqsim.thermo.system.SystemPrEos(50.0 + 273.15, pressure)
for comp in ["ethane", "propane", "n-heptane", "nC10", "n-nonane"]:
    thermo_2p.addComponent(comp, 0.20)
thermo_2p.autoSelectMixingRule()
thermo_2p.setTotalFlowRate(100.0, "kg/hr")

inlet_2p = neqsim.process.equipment.stream.Stream("inlet_2p", thermo_2p)
inlet_2p.run()

sep_2p = neqsim.process.equipment.separator.Separator("sep_2p", inlet_2p)
sep_2p.run()

gas_2p = sep_2p.getGasOutStream()
liq_2p = sep_2p.getLiquidOutStream()

print(f"  Gas: flow={gas_2p.getFlowRate('kg/hr'):.4f} kg/hr, MW={gas_2p.getThermoSystem().getMolarMass()*1000:.2f}")
print(f"  Liq: flow={liq_2p.getFlowRate('kg/hr'):.4f} kg/hr, MW={liq_2p.getThermoSystem().getMolarMass()*1000:.2f}")

liq_2p_thermo = liq_2p.getThermoSystem()
print_composition("Two-phase case: Liquid outlet (real liquid)", liq_2p_thermo)

# Also check K-values from the 2-phase inlet
inlet_2p_thermo = inlet_2p.getThermoSystem()
print("\n  K-values from 2-phase flash (for comparison):")
for i in range(inlet_2p_thermo.getNumberOfComponents()):
    comp = inlet_2p_thermo.getComponent(i)
    name = str(comp.getName())
    k = float(comp.getK())
    z = float(comp.getz())
    print(f"    {name:<15s} z={z:.6f}  K={k:.6f}")


# ═══════════════════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════════════════

divider("SUMMARY")
print("""
QUESTION: How can we get the VLE equilibrium liquid composition for a
single-phase gas system (like UniSim does)?

UniSim's Stream 4 (liquid, zero flow) shows:
  ethane=0.009, propane=0.016, n-heptane=0.126, n-nonane=0.328, n-decane=0.521
  MW=129.8

Our system shows:
  ethane=0.20, propane=0.20, n-heptane=0.20, n-nonane=0.20, nC10=0.20
  MW=88.98 (= feed composition)

The approaches tested above will tell us which method works to get
the correct equilibrium liquid composition from NeqSim.
""")
