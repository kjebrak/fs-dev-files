#!/usr/bin/env python3
"""Debug: What does getBeta() return for single-phase liquid vs single-phase gas?

Run with: uv run python .dev/dev_wdir/debug_scripts/debug_getBeta_single_phase.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

from core.models.eos_models import EoSModel
from core.models.stream import StreamInput
from integration.neqsim_wrapper.process import Stream


def inspect_stream(label: str, stream: Stream) -> None:
    js = stream._java_stream
    thermo = js.getThermoSystem()

    n_phases = thermo.getNumberOfPhases()
    phase_names = [str(thermo.getPhase(i).getPhaseTypeName()) for i in range(n_phases)]

    beta = thermo.getBeta()

    print(f"\n--- {label} ---")
    print(f"  T={js.getTemperature('C'):.1f}°C, P={js.getPressure('bara'):.1f} bara")
    print(f"  Phases ({n_phases}): {phase_names}")
    print(f"  getBeta() = {beta}")
    print(f"  getNumberOfPhases() = {n_phases}")

    # Try phase-specific queries
    for ptype in ["gas", "oil", "aqueous"]:
        try:
            frac = thermo.getPhaseFraction(ptype, "mole")
            print(f"  getPhaseFraction('{ptype}', 'mole') = {frac}")
        except Exception as e:
            print(f"  getPhaseFraction('{ptype}', 'mole') -> ERROR: {e}")

    # Check if there's a hasPhaseType method
    try:
        has_gas = thermo.hasPhaseType("gas")
        has_oil = thermo.hasPhaseType("oil")
        print(f"  hasPhaseType('gas') = {has_gas}")
        print(f"  hasPhaseType('oil') = {has_oil}")
    except Exception as e:
        print(f"  hasPhaseType() -> ERROR: {e}")


# Case 1: Subcooled liquid (the problematic case)
print("=" * 60)
print("Case 1: Heavy C1-C5 mix at 10°C, 50 bara (subcooled liquid)")
print("=" * 60)
s1 = Stream(
    name="subcooled",
    stream_input=StreamInput(
        temperature=10.0, pressure=50.0, flow_rate=100.0,
        composition={
            "methane": 1/7, "ethane": 1/7, "propane": 1/7,
            "n-butane": 1/7, "i-butane": 1/7,
            "n-pentane": 1/7, "i-pentane": 1/7,
        },
        property_package_ref="default",
    ),
    eos_model=EoSModel.SRK,
)
inspect_stream("Subcooled liquid", s1)


# Case 2: Pure methane at 100°C, 10 bara (definitely gas)
print("\n" + "=" * 60)
print("Case 2: Pure methane at 100°C, 10 bara (superheated gas)")
print("=" * 60)
s2 = Stream(
    name="gas",
    stream_input=StreamInput(
        temperature=100.0, pressure=10.0, flow_rate=100.0,
        composition={"methane": 1.0},
        property_package_ref="default",
    ),
    eos_model=EoSModel.SRK,
)
inspect_stream("Superheated gas", s2)


# Case 3: Two-phase mixture
print("\n" + "=" * 60)
print("Case 3: C1-C4 mix at 20°C, 50 bara (two-phase)")
print("=" * 60)
s3 = Stream(
    name="twophase",
    stream_input=StreamInput(
        temperature=20.0, pressure=50.0, flow_rate=100.0,
        composition={"methane": 0.7, "ethane": 0.1, "propane": 0.1, "n-butane": 0.1},
        property_package_ref="default",
    ),
    eos_model=EoSModel.SRK,
)
inspect_stream("Two-phase", s3)


# Case 4: Same heavy mix but at conditions that should vaporize it
print("\n" + "=" * 60)
print("Case 4: Same C1-C5 mix at 200°C, 5 bara (should be gas)")
print("=" * 60)
s4 = Stream(
    name="vaporized",
    stream_input=StreamInput(
        temperature=200.0, pressure=5.0, flow_rate=100.0,
        composition={
            "methane": 1/7, "ethane": 1/7, "propane": 1/7,
            "n-butane": 1/7, "i-butane": 1/7,
            "n-pentane": 1/7, "i-pentane": 1/7,
        },
        property_package_ref="default",
    ),
    eos_model=EoSModel.SRK,
)
inspect_stream("Vaporized heavy mix", s4)
