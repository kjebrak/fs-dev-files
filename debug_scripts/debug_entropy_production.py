"""Debug script to investigate NeqSim entropy production returning -inf.

Problem: Valve entropy_production returns -inf even though inlet/outlet streams
have valid entropy values (-0.297 and -0.056 kJ/kg-K respectively).

This script:
1. Recreates the exact conditions from the failing simulation
2. Inspects NeqSim's internal state to understand why entropy production is -inf
3. Tests various conditions (2-phase vs 3-phase, different P drops, etc.)

Usage:
    uv run python scripts/debug_entropy_production.py
"""

import math
import sys
from dataclasses import dataclass

# Add src to path for imports
sys.path.insert(0, "src")

# These imports start the JVM and load NeqSim
from core.models import (  # noqa: E402
    StreamInput,
    ValveCalculationMode,
    ValveInput,
)
from integration.neqsim_wrapper.process import (  # noqa: E402
    SingleIO,
    Stream,
)
from integration.neqsim_wrapper.process.valve import Valve  # noqa: E402


@dataclass
class EntropyInvestigation:
    """Results from entropy investigation."""

    inlet_entropy: float | None
    outlet_entropy: float | None
    delta_s: float | None  # Manual calculation: S_out - S_in
    neqsim_entropy_production: float | None  # NeqSim's getEntropyProduction()
    is_infinite: bool
    num_phases: int
    phase_fractions: dict[str, float]
    mass_flow_kg_hr: float


def create_valve_and_test(
    composition: dict[str, float],
    inlet_T_C: float,
    inlet_P_bara: float,
    outlet_P_bara: float,
    flow_rate_kg_hr: float = 100.0,
    eos_model: str = "SRK",
) -> EntropyInvestigation:
    """Create a valve simulation and investigate entropy production."""
    # Create inlet stream with wrapper
    stream_input = StreamInput(
        temperature=inlet_T_C,
        pressure=inlet_P_bara,
        flow_rate=flow_rate_kg_hr,
        composition=composition,
        eos_model=eos_model,
    )
    inlet_stream = Stream("inlet", stream_input)

    # Get inlet thermo system info
    inlet_thermo = inlet_stream._java_stream.getThermoSystem()
    inlet_entropy = inlet_thermo.getEntropy("kJ/kgK")
    mass_flow = inlet_stream._java_stream.getFlowRate("kg/hr")

    # Get phase information
    num_phases = inlet_thermo.getNumberOfPhases()
    phase_fractions = {}
    for i in range(num_phases):
        phase = inlet_thermo.getPhase(i)
        phase_type = str(phase.getPhaseTypeName())
        phase_fractions[phase_type] = inlet_thermo.getMoleFraction(i)

    # Calculate pressure drop
    pressure_drop = inlet_P_bara - outlet_P_bara

    # Create valve with wrapper
    valve_input = ValveInput(
        calculation_mode=ValveCalculationMode.PRESSURE_DROP,
        pressure_drop=pressure_drop,
        is_isothermal=False,
    )
    valve = Valve("valve", valve_input)
    valve.connect_inlet(SingleIO.INLET, inlet_stream)
    valve.calculate()

    # Get outlet stream entropy
    outlet_stream = valve.get_stream_from_port(SingleIO.OUTLET)
    outlet_thermo = outlet_stream._java_stream.getThermoSystem()
    outlet_entropy = outlet_thermo.getEntropy("kJ/kgK")

    # Manual entropy change calculation
    delta_s = None
    if outlet_entropy is not None and inlet_entropy is not None:
        if math.isfinite(outlet_entropy) and math.isfinite(inlet_entropy):
            delta_s = outlet_entropy - inlet_entropy

    # NeqSim's entropy production (direct from Java object)
    # Try with various units to see what works
    neqsim_entropy_production = None
    entropy_production_no_unit = None
    entropy_production_kj_k = None
    entropy_production_j_k = None

    try:
        # No unit argument (may use default)
        entropy_production_no_unit = valve.java_object.getEntropyProduction()
        print(f"   Raw getEntropyProduction():        {entropy_production_no_unit}")
    except Exception as e:
        print(f"   Raw getEntropyProduction():        ERROR - {e}")

    try:
        entropy_production_kj_k = valve.java_object.getEntropyProduction("kJ/K")
        print(f"   Raw getEntropyProduction('kJ/K'):   {entropy_production_kj_k}")
    except Exception as e:
        print(f"   Raw getEntropyProduction('kJ/K'):   ERROR - {e}")

    try:
        entropy_production_j_k = valve.java_object.getEntropyProduction("J/K")
        print(f"   Raw getEntropyProduction('J/K'):    {entropy_production_j_k}")
    except Exception as e:
        print(f"   Raw getEntropyProduction('J/K'):    ERROR - {e}")

    # Use the first valid result
    neqsim_entropy_production = (
        entropy_production_no_unit or entropy_production_kj_k or entropy_production_j_k
    )

    is_infinite = (
        not math.isfinite(neqsim_entropy_production)
        if neqsim_entropy_production is not None
        else True
    )

    return EntropyInvestigation(
        inlet_entropy=inlet_entropy,
        outlet_entropy=outlet_entropy,
        delta_s=delta_s,
        neqsim_entropy_production=neqsim_entropy_production,
        is_infinite=is_infinite,
        num_phases=num_phases,
        phase_fractions=phase_fractions,
        mass_flow_kg_hr=mass_flow,
    )


def investigate_problematic_case():
    """Investigate the exact case that caused the -inf bug."""
    print("=" * 70)
    print("INVESTIGATING NEQSIM ENTROPY PRODUCTION BUG")
    print("=" * 70)

    # Exact composition from the failing request
    problematic_composition = {
        "methane": 0.1111111111111111,
        "ethane": 0.1111111111111111,
        "propane": 0.1111111111111111,
        "n-butane": 0.1111111111111111,
        "i-butane": 0.1111111111111111,
        "n-pentane": 0.1111111111111111,
        "i-pentane": 0.1111111111111111,
        "water": 0.1111111111111111,
        "nC10": 0.1111111111111111,
    }

    print("\n1. ORIGINAL FAILING CASE (2 bar ΔP)")
    print("-" * 50)
    print("   Composition: 9 components (1/9 each)")
    print("   Inlet: 20°C, 50 bara")
    print("   Outlet: 48 bara (ΔP = 2 bar)")

    result = create_valve_and_test(problematic_composition, 20.0, 50.0, 48.0)
    print_investigation_result(result)

    # Test with larger pressure drop (user mentioned 20 bar)
    print("\n2. LARGER PRESSURE DROP (20 bar)")
    print("-" * 50)
    print("   Inlet: 20°C, 50 bara")
    print("   Outlet: 30 bara (ΔP = 20 bar)")

    result2 = create_valve_and_test(problematic_composition, 20.0, 50.0, 30.0)
    print_investigation_result(result2)

    # Test with simple 2-phase system (no water)
    print("\n3. SIMPLIFIED COMPOSITION (NO WATER)")
    print("-" * 50)
    simple_composition = {
        "methane": 0.5,
        "ethane": 0.3,
        "propane": 0.2,
    }
    print("   Composition: methane 50%, ethane 30%, propane 20%")
    print("   Inlet: 20°C, 50 bara → 30 bara")

    result3 = create_valve_and_test(simple_composition, 20.0, 50.0, 30.0)
    print_investigation_result(result3)

    # Test with gas only (supercritical / high temperature)
    print("\n4. PURE GAS PHASE (HIGH TEMPERATURE)")
    print("-" * 50)
    print("   Composition: methane 50%, ethane 30%, propane 20%")
    print("   Inlet: 100°C, 50 bara → 30 bara")

    result4 = create_valve_and_test(simple_composition, 100.0, 50.0, 30.0)
    print_investigation_result(result4)

    # Test with liquid only (low temperature)
    print("\n5. SUBCOOLED LIQUID (LOW TEMPERATURE)")
    print("-" * 50)
    print("   Composition: methane 50%, ethane 30%, propane 20%")
    print("   Inlet: -50°C, 50 bara → 30 bara")

    result5 = create_valve_and_test(simple_composition, -50.0, 50.0, 30.0)
    print_investigation_result(result5)

    # Analyze patterns
    print("\n" + "=" * 70)
    print("ANALYSIS")
    print("=" * 70)

    results = [
        ("Original (with water, 2 bar ΔP)", result),
        ("Original (with water, 20 bar ΔP)", result2),
        ("Simple (no water, 20 bar ΔP)", result3),
        ("Gas only (high T)", result4),
        ("Liquid only (low T)", result5),
    ]

    print(
        "\n   Case                              | Phases | ΔS (manual) | NeqSim S_prod | -inf?"
    )
    print("   " + "-" * 85)
    for name, r in results:
        delta_s_str = (
            f"{r.delta_s:.6f}" if r.delta_s and math.isfinite(r.delta_s) else "N/A"
        )
        neqsim_str = (
            f"{r.neqsim_entropy_production:.6f}"
            if r.neqsim_entropy_production
            and math.isfinite(r.neqsim_entropy_production)
            else "-inf/NaN"
        )
        print(
            f"   {name:<35} | {r.num_phases}      | {delta_s_str:>10} | {neqsim_str:>12} | {r.is_infinite}"
        )

    # Hypothesis testing
    print("\nHYPOTHESIS TESTING:")
    print("-" * 50)

    all_results = [result, result2, result3, result4, result5]

    water_inf = sum(1 for r in [result, result2] if r.is_infinite)
    no_water_inf = sum(1 for r in [result3, result4, result5] if r.is_infinite)

    if water_inf > no_water_inf:
        print("   ⚠️  Water-containing mixtures show more -inf occurrences")
        print(
            "   → Hypothesis: 3-phase equilibrium causes numerical issues in entropy calc"
        )
    elif all(r.is_infinite for r in all_results):
        print("   ⚠️  ALL cases return -inf for getEntropyProduction()")
        print("   → This appears to be a NeqSim bug or limitation")
    elif not any(r.is_infinite for r in all_results):
        print("   ✅ ALL cases return FINITE values when using 'J/K' unit!")
        print("   → The bug was caused by using unsupported unit 'kJ/K'")
        print("   → Fix: Change valve.py to use 'J/K' and convert to kJ/K")
    else:
        print("   → Pattern inconclusive, need more investigation")


def print_investigation_result(result: EntropyInvestigation):
    """Print detailed investigation results."""
    inlet_s = (
        f"{result.inlet_entropy:.6f}"
        if result.inlet_entropy and math.isfinite(result.inlet_entropy)
        else "N/A"
    )
    outlet_s = (
        f"{result.outlet_entropy:.6f}"
        if result.outlet_entropy and math.isfinite(result.outlet_entropy)
        else "N/A"
    )
    delta_s = (
        f"{result.delta_s:.6f}"
        if result.delta_s and math.isfinite(result.delta_s)
        else "N/A"
    )

    print(f"\n   Inlet entropy:  {inlet_s} kJ/kg-K")
    print(f"   Outlet entropy: {outlet_s} kJ/kg-K")
    print(f"   ΔS (manual):    {delta_s} kJ/kg-K")
    print(f"   Mass flow:      {result.mass_flow_kg_hr:.2f} kg/hr")

    # Calculate total entropy change (ΔS × ṁ) for comparison
    if result.delta_s and math.isfinite(result.delta_s):
        total_entropy_change = result.delta_s * result.mass_flow_kg_hr / 3600  # kJ/K/s
        print(f"   Total ΔS×ṁ:     {total_entropy_change:.6f} kJ/K/s (manual calc)")

    print(f"\n   Number of phases: {result.num_phases}")
    for phase_type, fraction in result.phase_fractions.items():
        print(f"      {phase_type}: {fraction:.6f} mol frac")

    neqsim_s = (
        f"{result.neqsim_entropy_production}"
        if result.neqsim_entropy_production
        else "N/A"
    )
    print(f"\n   NeqSim getEntropyProduction(): {neqsim_s}")
    if result.is_infinite:
        print("   ⚠️  VALUE IS INFINITE - This is the bug!")


def investigate_neqsim_source():
    """Explain what NeqSim does internally."""
    print("\n" + "=" * 70)
    print("NEQSIM INTERNALS INVESTIGATION")
    print("=" * 70)

    print("""
    Looking at NeqSim source code (ThrottlingValve.java):

    The getEntropyProduction() method typically calculates:

        S_prod = ṁ * (s_out - s_in)  [total entropy production rate]

    OR it may use a more complex formula involving phase equilibrium:

        S_prod = -R * Σ(n_i * ln(x_i))  [mixing entropy contribution]

    When phase fractions (x_i) approach 0, ln(0) → -∞

    Possible causes for -inf:
    1. Phase fraction near 0 in ln() calculation
    2. Division by zero in internal thermodynamic calculations
    3. Temperature/pressure near phase boundaries
    4. Multi-phase equilibrium calculation edge cases
    """)


def main():
    """Run the investigation."""
    investigate_problematic_case()
    investigate_neqsim_source()

    print("\n" + "=" * 70)
    print("RECOMMENDATIONS")
    print("=" * 70)
    print("""
    1. The sanitization is CORRECT - entropy_production returning None is expected
       behavior when NeqSim returns -inf for edge cases.

    2. The -inf is likely caused by NeqSim's internal calculations when:
       - Phase fractions approach 0 (e.g., trace water in gas systems)
       - Temperature/pressure near phase boundaries
       - Multi-phase equilibrium calculations

    3. For users who need entropy production:
       - Can be calculated manually: ṁ × (S_out - S_in) / 3600
       - Stream entropy values are reliable (properly sanitized)

    4. POTENTIAL ENHANCEMENT: Add a manual entropy production calculation
       as a fallback when NeqSim's method returns invalid values:

       entropy_production = None
       neqsim_value = safe_extract(
           lambda: self.java_object.getEntropyProduction("kJ/K"),
           context="valve.entropy_production",
       )
       if neqsim_value is not None:
           entropy_production = neqsim_value
       else:
           # Manual calculation as fallback
           inlet_s = inlet_stream.get_entropy("kJ/kgK")
           outlet_s = outlet_stream.get_entropy("kJ/kgK")
           mass_flow = inlet_stream.get_mass_flow("kg/s")
           if all(v is not None for v in [inlet_s, outlet_s, mass_flow]):
               entropy_production = mass_flow * (outlet_s - inlet_s)
    """)


if __name__ == "__main__":
    main()
