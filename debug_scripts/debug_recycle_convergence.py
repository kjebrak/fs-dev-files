#!/usr/bin/env python3
"""Debug script to trace recycle convergence issue.

This script creates a minimal recycle flowsheet and traces:
1. Stream states at each iteration
2. Property values before/after updates
3. Enthalpy calculations for the mixer
4. Temperature changes through the loop

Run with: uv run python scripts/debug_recycle_convergence.py
"""

import logging
import sys
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from jneqsim import neqsim

from core.models.compressor import CompressorInput
from core.models.flowsheet_graph import Edge, FlowsheetGraph, Node, NodeType
from core.models.recycle import RecycleInput
from core.models.splitter import SplitterInput
from core.models.stream import StreamInput
from core.process_builder import ProcessBuilder
from integration.neqsim_wrapper.process.stream import Stream

# Setup logging
logging.basicConfig(level=logging.DEBUG, format="%(name)s - %(message)s")
logger = logging.getLogger(__name__)


def create_recycle_flowsheet() -> FlowsheetGraph:
    """Create the test recycle flowsheet.

    Feed (25°C, 1 bara) → Mixer → Compressor (3 bara) → Splitter → Product (80%)
                            ↑                              ↓
                          Recycle ←──────────────── Recycle In (20%)
    """
    nodes = [
        Node(
            id="feed",
            type=NodeType.STREAM,
            input_data=StreamInput(
                temperature=25.0,
                pressure=1.0,
                flow_rate=1000.0,
                composition={"methane": 0.8, "ethane": 0.2},
                eos_model="SRK",
            ),
        ),
        Node(id="mixer", type=NodeType.MIXER, input_data=None),
        Node(id="s_mixed", type=NodeType.STREAM, input_data=None),
        Node(
            id="compressor",
            type=NodeType.COMPRESSOR,
            input_data=CompressorInput(
                calculation_mode="outlet_pressure",
                outlet_pressure=3.0,
                isentropic_efficiency=0.75,
            ),
        ),
        Node(id="s_comp", type=NodeType.STREAM, input_data=None),
        Node(
            id="splitter",
            type=NodeType.SPLITTER,
            input_data=SplitterInput(fraction_outlet_1=0.8, fraction_outlet_2=0.2),
        ),
        Node(id="s_product", type=NodeType.STREAM, input_data=None),
        Node(id="s_rec_in", type=NodeType.STREAM, input_data=None),  # Tear stream
        Node(
            id="recycle",
            type=NodeType.RECYCLE,
            input_data=RecycleInput(
                max_iterations=10,  # Low for debugging
                temperature_tolerance_c=0.1,
                pressure_tolerance_bara=0.01,
                flow_tolerance_fraction=0.001,
                composition_tolerance_fraction=0.001,
                smoothing_factor=0.3,
            ),
        ),
        Node(id="s_rec_out", type=NodeType.STREAM, input_data=None),  # Guess stream
    ]

    edges = [
        Edge(id="e1", source="feed", target="mixer", source_handle="outlet", target_handle="inlet_1"),
        Edge(id="e2", source="mixer", target="s_mixed", source_handle="outlet", target_handle="inlet"),
        Edge(id="e3", source="s_mixed", target="compressor", source_handle="outlet", target_handle="inlet"),
        Edge(id="e4", source="compressor", target="s_comp", source_handle="outlet", target_handle="inlet"),
        Edge(id="e5", source="s_comp", target="splitter", source_handle="outlet", target_handle="inlet"),
        Edge(id="e6", source="splitter", target="s_product", source_handle="outlet_1", target_handle="inlet"),
        Edge(id="e7", source="splitter", target="s_rec_in", source_handle="outlet_2", target_handle="inlet"),
        Edge(id="e8", source="s_rec_in", target="recycle", source_handle="outlet", target_handle="inlet"),
        Edge(id="e9", source="recycle", target="s_rec_out", source_handle="outlet", target_handle="inlet"),
        Edge(id="e10", source="s_rec_out", target="mixer", source_handle="outlet", target_handle="inlet_2"),
    ]

    return FlowsheetGraph(nodes=nodes, edges=edges, name="Recycle Debug")


def get_stream_state(stream: Stream, label: str) -> dict:
    """Extract and print stream state."""
    if not stream or not stream._java_stream:
        return {"label": label, "error": "No Java stream"}

    js = stream._java_stream
    fluid = js.getFluid()

    # Get composition
    comp = {}
    for i in range(fluid.getNumberOfComponents()):
        c = fluid.getComponent(i)
        comp[c.getName()] = c.getz()

    state = {
        "label": label,
        "temperature_C": js.getTemperature("C"),
        "pressure_bara": js.getPressure("bara"),
        "flow_kg_hr": js.getFlowRate("kg/hr"),
        "enthalpy_J": fluid.getEnthalpy(),
        "composition": comp,
    }

    return state


def print_state(state: dict, indent: int = 2):
    """Pretty print a state dict."""
    prefix = " " * indent
    print(f"{prefix}{state['label']}:")
    if "error" in state:
        print(f"{prefix}  ERROR: {state['error']}")
        return

    print(f"{prefix}  T={state['temperature_C']:.4f}°C, P={state['pressure_bara']:.4f} bara")
    print(f"{prefix}  Flow={state['flow_kg_hr']:.2f} kg/hr, H={state['enthalpy_J']:.2f} J")
    print(f"{prefix}  Composition: {state['composition']}")


def debug_manual_iteration():
    """Manually step through recycle iterations to trace the issue."""
    print("\n" + "=" * 80)
    print("MANUAL RECYCLE ITERATION DEBUG")
    print("=" * 80)

    # Build the flowsheet
    flowsheet = create_recycle_flowsheet()
    builder = ProcessBuilder()
    artifacts = builder.build(flowsheet)

    # Get key streams and units
    feed = artifacts.stream_by_node["feed"]
    mixer = artifacts.unit_by_node["mixer"]
    s_mixed = artifacts.stream_by_node["s_mixed"]
    compressor = artifacts.unit_by_node["compressor"]
    s_comp = artifacts.stream_by_node["s_comp"]
    splitter = artifacts.unit_by_node["splitter"]
    s_product = artifacts.stream_by_node["s_product"]
    s_rec_in = artifacts.stream_by_node["s_rec_in"]  # Tear stream
    recycle = artifacts.unit_by_node["recycle"]
    s_rec_out = artifacts.stream_by_node["s_rec_out"]  # Guess stream

    print("\n--- Initial State ---")
    print_state(get_stream_state(feed, "Feed"))
    print_state(get_stream_state(s_rec_out, "Recycle Out (guess)"))

    # Store previous iteration values for Wegstein
    prev_guess_T = None
    prev_tear_T = None

    for iteration in range(30):
        print(f"\n{'='*60}")
        print(f"ITERATION {iteration + 1}")
        print(f"{'='*60}")

        # Capture guess stream state BEFORE running
        guess_before = get_stream_state(s_rec_out, f"Guess Before Iter {iteration + 1}")
        print("\n--- Before Flowsheet Pass ---")
        print_state(guess_before)

        # Check enthalpy of inlet streams to mixer
        print("\n--- Mixer Inlet Enthalpies ---")
        feed_fluid = feed._java_stream.getFluid()
        feed_fluid.init(3)  # Recalculate properties
        rec_fluid = s_rec_out._java_stream.getFluid()
        rec_fluid.init(3)

        print(f"  Feed: T={feed_fluid.getTemperature('C'):.4f}°C, H={feed_fluid.getEnthalpy():.2f} J")
        print(f"  Rec Out: T={rec_fluid.getTemperature('C'):.4f}°C, H={rec_fluid.getEnthalpy():.2f} J")
        print(f"  Total H = {feed_fluid.getEnthalpy() + rec_fluid.getEnthalpy():.2f} J")

        # Run Mixer
        print("\n--- Running Mixer ---")
        mixer.calculate()
        mixed_state = get_stream_state(s_mixed, "Mixed Stream")
        print_state(mixed_state)

        # Run Compressor
        print("\n--- Running Compressor ---")
        compressor.calculate()
        comp_state = get_stream_state(s_comp, "Compressor Out")
        print_state(comp_state)

        # Run Splitter
        print("\n--- Running Splitter ---")
        splitter.calculate()
        prod_state = get_stream_state(s_product, "Product (80%)")
        tear_state = get_stream_state(s_rec_in, "Tear Stream (20%)")
        print_state(prod_state)
        print_state(tear_state)

        # Convergence check
        T_diff = abs(tear_state["temperature_C"] - guess_before["temperature_C"])
        P_diff = abs(tear_state["pressure_bara"] - guess_before["pressure_bara"])

        print(f"\n--- Convergence Check ---")
        print(f"  Temperature diff: {T_diff:.6f}°C (tolerance: 0.1°C)")
        print(f"  Pressure diff: {P_diff:.6f} bara (tolerance: 0.01 bara)")

        if T_diff < 0.1 and P_diff < 0.01:
            print("\n*** CONVERGED! ***")
            break

        # Update guess stream using direct substitution with smoothing
        print(f"\n--- Updating Guess Stream (smoothing=0.3) ---")
        alpha = 0.3

        # Current values
        T_old = guess_before["temperature_C"]
        P_old = guess_before["pressure_bara"]
        F_old = guess_before["flow_kg_hr"]

        # New values from tear stream
        T_new = tear_state["temperature_C"]
        P_new = tear_state["pressure_bara"]
        F_new = tear_state["flow_kg_hr"]

        # Smoothed values
        T_next = alpha * T_new + (1 - alpha) * T_old
        P_next = alpha * P_new + (1 - alpha) * P_old
        F_next = alpha * F_new + (1 - alpha) * F_old

        print(f"  T: {T_old:.4f} -> {T_new:.4f}, smoothed: {T_next:.4f}")
        print(f"  P: {P_old:.4f} -> {P_new:.4f}, smoothed: {P_next:.4f}")
        print(f"  F: {F_old:.4f} -> {F_new:.4f}, smoothed: {F_next:.4f}")

        # Apply updates to guess stream
        js = s_rec_out._java_stream
        print(f"\n--- Setting Properties on Java Stream ---")
        print(f"  Before set: T={js.getTemperature('C'):.4f}°C")

        js.setTemperature(T_next, "C")
        print(f"  After setTemperature: T={js.getTemperature('C'):.4f}°C")

        js.setPressure(P_next, "bara")
        print(f"  After setPressure: P={js.getPressure('bara'):.4f} bara")

        js.setFlowRate(F_next, "kg/hr")
        print(f"  After setFlowRate: F={js.getFlowRate('kg/hr'):.4f} kg/hr")

        # Update composition with smoothing
        fluid = js.getFluid()
        for i in range(fluid.getNumberOfComponents()):
            comp = fluid.getComponent(i)
            name = comp.getName()
            z_old = guess_before["composition"].get(name, 0)
            z_new = tear_state["composition"].get(name, 0)
            z_next = alpha * z_new + (1 - alpha) * z_old
            comp.setz(z_next)

        print(f"\n--- Before stream.run() ---")
        print(f"  T={js.getTemperature('C'):.4f}°C, P={js.getPressure('bara'):.4f} bara")
        print(f"  Fluid T={js.getFluid().getTemperature('C'):.4f}°C")

        # THIS IS THE KEY - what happens when we call run()?
        print(f"\n--- Calling stream.run() (TPflash) ---")
        js.run()

        print(f"\n--- After stream.run() ---")
        print(f"  T={js.getTemperature('C'):.4f}°C, P={js.getPressure('bara'):.4f} bara")
        print(f"  Fluid T={js.getFluid().getTemperature('C'):.4f}°C")
        print(f"  Fluid H={js.getFluid().getEnthalpy():.2f} J")

        # Final state check
        guess_after = get_stream_state(s_rec_out, f"Guess After Update {iteration + 1}")
        print_state(guess_after)

        # Check if temperature changed during run()
        T_before_run = T_next
        T_after_run = guess_after["temperature_C"]
        if abs(T_before_run - T_after_run) > 0.001:
            print(f"\n  *** WARNING: Temperature changed during run()! ***")
            print(f"  *** Expected: {T_before_run:.4f}°C, Got: {T_after_run:.4f}°C ***")
            print(f"  *** Difference: {T_before_run - T_after_run:.6f}°C ***")

        prev_guess_T = T_old
        prev_tear_T = T_new

    print("\n" + "=" * 80)
    print("DEBUG COMPLETE")
    print("=" * 80)


def debug_wegstein_values():
    """Debug the actual Wegstein q values being calculated.

    This version uses Wegstein only for Temperature (as in the converging version).
    """
    print("\n" + "=" * 80)
    print("WEGSTEIN PARAMETER DEBUG (T only, direct sub for P/F)")
    print("=" * 80)

    # Build the flowsheet
    flowsheet = create_recycle_flowsheet()
    builder = ProcessBuilder()
    artifacts = builder.build(flowsheet)

    # Get key streams and units
    mixer = artifacts.unit_by_node["mixer"]
    compressor = artifacts.unit_by_node["compressor"]
    splitter = artifacts.unit_by_node["splitter"]
    s_rec_in = artifacts.stream_by_node["s_rec_in"]  # Tear stream
    s_rec_out = artifacts.stream_by_node["s_rec_out"]  # Guess stream

    alpha = 0.3
    q_min, q_max = -5.0, 0.0
    accel_delay = 2

    # Track history for Wegstein
    prev_guess_T = None
    prev_tear_T = None

    print("\nIter | T_old  | T_new  | dx_cur | dx_prev|   q    | wegstein | T_next |")
    print("-" * 80)

    for iteration in range(40):
        # Get current guess state
        js_guess = s_rec_out._java_stream
        T_old = js_guess.getTemperature("C")

        # Run flowsheet
        mixer.calculate()
        compressor.calculate()
        splitter.calculate()

        # Get tear state
        js_tear = s_rec_in._java_stream
        T_new = js_tear.getTemperature("C")

        # Calculate Wegstein update
        dx_new = T_new - T_old

        if iteration < accel_delay or prev_guess_T is None:
            # Direct substitution with smoothing
            T_next = alpha * T_new + (1 - alpha) * T_old
            q = "N/A"
            wegstein = "N/A"
            dx_prev = "N/A"
            print(f"{iteration + 1:4d} | {T_old:6.2f} | {T_new:6.2f} | {dx_new:6.2f} | {'N/A':>6} | {'DS':>6} | {'N/A':>8} | {T_next:6.2f} |")
        else:
            # Wegstein acceleration
            dx_prev = prev_tear_T - prev_guess_T
            denom = dx_new - dx_prev

            if abs(denom) < 1e-10:
                q = 0.0
            else:
                q = dx_new / denom

            # Bound q
            q_bounded = max(q_min, min(q_max, q))

            # Wegstein update
            if abs(1 - q_bounded) < 1e-10:
                wegstein = T_new
            else:
                wegstein = T_old - q_bounded * dx_new / (1 - q_bounded)

            # Apply smoothing
            T_next = alpha * wegstein + (1 - alpha) * T_old

            print(f"{iteration + 1:4d} | {T_old:6.2f} | {T_new:6.2f} | {dx_new:6.2f} | {dx_prev:6.2f} | {q_bounded:6.3f} | {wegstein:8.2f} | {T_next:6.2f} |")

        # Check convergence
        T_diff = abs(T_new - T_old)
        if T_diff < 0.1:
            print(f"\n*** CONVERGED at iteration {iteration + 1}! ***")
            break

        # Update guess stream
        js_guess.setTemperature(T_next, "C")
        # Also update P and F with simple smoothing for consistency
        P_next = alpha * js_tear.getPressure("bara") + (1 - alpha) * js_guess.getPressure("bara")
        F_next = alpha * js_tear.getFlowRate("kg/hr") + (1 - alpha) * js_guess.getFlowRate("kg/hr")
        js_guess.setPressure(P_next, "bara")
        js_guess.setFlowRate(F_next, "kg/hr")

        # Update composition
        fluid_guess = js_guess.getFluid()
        fluid_tear = js_tear.getFluid()
        for i in range(fluid_guess.getNumberOfComponents()):
            comp_guess = fluid_guess.getComponent(i)
            comp_tear = fluid_tear.getComponent(i)
            z_next = alpha * comp_tear.getz() + (1 - alpha) * comp_guess.getz()
            comp_guess.setz(z_next)

        js_guess.run()

        # Store for next iteration
        prev_guess_T = T_old
        prev_tear_T = T_new


def debug_wegstein_all_properties():
    """Debug Wegstein applied to ALL properties (T, P, F) like actual solver.

    This should reproduce the non-convergence issue.
    """
    print("\n" + "=" * 80)
    print("WEGSTEIN DEBUG (ALL properties T/P/F like actual solver)")
    print("=" * 80)

    # Build the flowsheet
    flowsheet = create_recycle_flowsheet()
    builder = ProcessBuilder()
    artifacts = builder.build(flowsheet)

    # Get key streams and units
    mixer = artifacts.unit_by_node["mixer"]
    compressor = artifacts.unit_by_node["compressor"]
    splitter = artifacts.unit_by_node["splitter"]
    s_rec_in = artifacts.stream_by_node["s_rec_in"]
    s_rec_out = artifacts.stream_by_node["s_rec_out"]

    alpha = 0.3
    q_min, q_max = -5.0, 0.0
    accel_delay = 2

    # Track history for Wegstein - ALL properties
    prev_old = None  # (T, P, F) tuple from last iteration
    prev_new = None

    print("\nIter | T_old  | T_new  | T_diff | P_diff | F_diff |")
    print("-" * 60)

    for iteration in range(60):
        # Get current guess state
        js_guess = s_rec_out._java_stream
        T_old = js_guess.getTemperature("C")
        P_old = js_guess.getPressure("bara")
        F_old = js_guess.getFlowRate("kg/hr")

        # Run flowsheet
        mixer.calculate()
        compressor.calculate()
        splitter.calculate()

        # Get tear state
        js_tear = s_rec_in._java_stream
        T_new = js_tear.getTemperature("C")
        P_new = js_tear.getPressure("bara")
        F_new = js_tear.getFlowRate("kg/hr")

        T_diff = abs(T_new - T_old)
        P_diff = abs(P_new - P_old)
        F_diff = abs(F_new - F_old) / max(F_old, 1e-10)

        print(f"{iteration + 1:4d} | {T_old:6.2f} | {T_new:6.2f} | {T_diff:6.2f} | {P_diff:6.4f} | {F_diff:6.4f} |")

        # Check convergence
        if T_diff < 1.0 and P_diff < 0.05 and F_diff < 0.01:
            print(f"\n*** CONVERGED at iteration {iteration + 1}! ***")
            break

        # Calculate updates for ALL properties using Wegstein
        if iteration < accel_delay or prev_old is None:
            # Direct substitution with smoothing
            T_next = alpha * T_new + (1 - alpha) * T_old
            P_next = alpha * P_new + (1 - alpha) * P_old
            F_next = alpha * F_new + (1 - alpha) * F_old
        else:
            # Wegstein for each property
            T_prev_old, P_prev_old, F_prev_old = prev_old
            T_prev_new, P_prev_new, F_prev_new = prev_new

            def wegstein_update(x_old, x_new, x_prev_old, x_prev_new):
                dx_new = x_new - x_old
                dx_prev = x_prev_new - x_prev_old
                denom = dx_new - dx_prev
                if abs(denom) < 1e-10:
                    q = 0.0
                else:
                    q = dx_new / denom
                q = max(q_min, min(q_max, q))
                if abs(1 - q) < 1e-10:
                    wegstein_val = x_new
                else:
                    wegstein_val = x_old - q * dx_new / (1 - q)
                return alpha * wegstein_val + (1 - alpha) * x_old

            T_next = wegstein_update(T_old, T_new, T_prev_old, T_prev_new)
            P_next = wegstein_update(P_old, P_new, P_prev_old, P_prev_new)
            F_next = wegstein_update(F_old, F_new, F_prev_old, F_prev_new)

        # Update guess stream with ALL properties
        js_guess.setTemperature(T_next, "C")
        js_guess.setPressure(P_next, "bara")
        js_guess.setFlowRate(F_next, "kg/hr")

        # Update composition
        fluid_guess = js_guess.getFluid()
        fluid_tear = js_tear.getFluid()
        for i in range(fluid_guess.getNumberOfComponents()):
            comp_guess = fluid_guess.getComponent(i)
            comp_tear = fluid_tear.getComponent(i)
            z_next = alpha * comp_tear.getz() + (1 - alpha) * comp_guess.getz()
            comp_guess.setz(z_next)

        js_guess.run()

        # Store for next iteration
        prev_old = (T_old, P_old, F_old)
        prev_new = (T_new, P_new, F_new)

    # Final state
    print(f"\nFinal: T_guess={js_guess.getTemperature('C'):.4f}, T_tear={js_tear.getTemperature('C'):.4f}")
    print(f"       T_diff={abs(js_guess.getTemperature('C') - js_tear.getTemperature('C')):.4f}")


def debug_convergence_pattern():
    """Run many iterations and just track key convergence metrics."""
    print("\n" + "=" * 80)
    print("CONVERGENCE PATTERN DEBUG (50 iterations)")
    print("=" * 80)

    # Build the flowsheet
    flowsheet = create_recycle_flowsheet()
    builder = ProcessBuilder()
    artifacts = builder.build(flowsheet)

    # Get key streams and units
    feed = artifacts.stream_by_node["feed"]
    mixer = artifacts.unit_by_node["mixer"]
    compressor = artifacts.unit_by_node["compressor"]
    splitter = artifacts.unit_by_node["splitter"]
    s_rec_in = artifacts.stream_by_node["s_rec_in"]  # Tear stream
    s_rec_out = artifacts.stream_by_node["s_rec_out"]  # Guess stream

    alpha = 0.3
    convergence_data = []

    print("\nIter |  T_guess  |  T_tear  |  T_diff  |  F_guess  |  F_tear  |")
    print("-" * 70)

    for iteration in range(50):
        # Get current guess state
        js_guess = s_rec_out._java_stream
        T_guess = js_guess.getTemperature("C")
        P_guess = js_guess.getPressure("bara")
        F_guess = js_guess.getFlowRate("kg/hr")

        # Run flowsheet
        mixer.calculate()
        compressor.calculate()
        splitter.calculate()

        # Get tear state
        js_tear = s_rec_in._java_stream
        T_tear = js_tear.getTemperature("C")
        P_tear = js_tear.getPressure("bara")
        F_tear = js_tear.getFlowRate("kg/hr")

        T_diff = abs(T_tear - T_guess)
        P_diff = abs(P_tear - P_guess)

        print(f"{iteration + 1:4d} | {T_guess:9.4f} | {T_tear:8.4f} | {T_diff:8.4f} | {F_guess:9.2f} | {F_tear:8.2f} |")

        convergence_data.append({
            "iter": iteration + 1,
            "T_guess": T_guess,
            "T_tear": T_tear,
            "T_diff": T_diff,
            "F_guess": F_guess,
            "F_tear": F_tear,
        })

        # Check convergence
        if T_diff < 0.1 and P_diff < 0.01:
            print(f"\n*** CONVERGED at iteration {iteration + 1}! ***")
            break

        # Update guess stream (direct substitution with smoothing)
        T_next = alpha * T_tear + (1 - alpha) * T_guess
        P_next = alpha * P_tear + (1 - alpha) * P_guess
        F_next = alpha * F_tear + (1 - alpha) * F_guess

        js_guess.setTemperature(T_next, "C")
        js_guess.setPressure(P_next, "bara")
        js_guess.setFlowRate(F_next, "kg/hr")

        # Update composition
        fluid_guess = js_guess.getFluid()
        fluid_tear = js_tear.getFluid()
        for i in range(fluid_guess.getNumberOfComponents()):
            comp_guess = fluid_guess.getComponent(i)
            comp_tear = fluid_tear.getComponent(i)
            z_next = alpha * comp_tear.getz() + (1 - alpha) * comp_guess.getz()
            comp_guess.setz(z_next)

        js_guess.run()

    # Final analysis
    print("\n" + "-" * 70)
    if len(convergence_data) >= 10:
        last_10 = convergence_data[-10:]
        T_diffs = [d["T_diff"] for d in last_10]
        print(f"Last 10 T_diff values: {[f'{t:.4f}' for t in T_diffs]}")
        print(f"T_diff trend (last - first): {T_diffs[-1] - T_diffs[0]:.6f}")

        if abs(T_diffs[-1] - T_diffs[0]) < 0.001:
            print("*** CONVERGENCE APPEARS TO BE PLATEAUING! ***")
            print(f"*** Final T_diff = {T_diffs[-1]:.4f}°C ***")
        elif T_diffs[-1] < T_diffs[0]:
            print("Convergence still improving")


def debug_enthalpy_update():
    """Debug whether enthalpy is properly updated when temperature changes."""
    print("\n" + "=" * 80)
    print("ENTHALPY UPDATE DEBUG")
    print("=" * 80)

    # Create a simple stream
    thermo = neqsim.thermo.system.SystemSrkEos(298.15, 1.0)  # 25°C, 1 bara
    thermo.addComponent("methane", 0.8)
    thermo.addComponent("ethane", 0.2)
    thermo.setMixingRule(2)
    thermo.setTotalFlowRate(1000.0, "kg/hr")

    stream = neqsim.process.equipment.stream.Stream("test", thermo)
    stream.run()

    print("\n--- Initial State ---")
    print(f"  T = {stream.getTemperature('C'):.4f}°C")
    print(f"  P = {stream.getPressure('bara'):.4f} bara")
    print(f"  H = {stream.getFluid().getEnthalpy():.2f} J")

    # Change temperature to 100°C
    print("\n--- Setting Temperature to 100°C ---")
    stream.setTemperature(100.0, "C")
    print(f"  After setTemperature: T = {stream.getTemperature('C'):.4f}°C")
    print(f"  Fluid T = {stream.getFluid().getTemperature('C'):.4f}°C")
    print(f"  H (before run) = {stream.getFluid().getEnthalpy():.2f} J")

    # Run to perform flash
    print("\n--- Calling stream.run() ---")
    stream.run()
    print(f"  After run: T = {stream.getTemperature('C'):.4f}°C")
    print(f"  Fluid T = {stream.getFluid().getTemperature('C'):.4f}°C")
    print(f"  H (after run) = {stream.getFluid().getEnthalpy():.2f} J")

    # Check that temperature is preserved
    if abs(stream.getTemperature("C") - 100.0) > 0.001:
        print(f"\n  *** WARNING: Temperature not preserved after run()! ***")
        print(f"  *** Expected: 100.0°C, Got: {stream.getTemperature('C'):.4f}°C ***")
    else:
        print("\n  Temperature preserved correctly after run()")


def debug_mixer_enthalpy_balance():
    """Debug the mixer enthalpy balance calculation."""
    print("\n" + "=" * 80)
    print("MIXER ENTHALPY BALANCE DEBUG")
    print("=" * 80)

    # Create two streams at different temperatures
    # Stream 1: 25°C, 1 bara, 800 kg/hr (Feed)
    thermo1 = neqsim.thermo.system.SystemSrkEos(298.15, 1.0)
    thermo1.addComponent("methane", 0.8)
    thermo1.addComponent("ethane", 0.2)
    thermo1.setMixingRule(2)
    thermo1.setTotalFlowRate(800.0, "kg/hr")

    stream1 = neqsim.process.equipment.stream.Stream("feed", thermo1)
    stream1.run()

    # Stream 2: 100°C, 3 bara, 200 kg/hr (Recycle guess)
    thermo2 = neqsim.thermo.system.SystemSrkEos(373.15, 3.0)
    thermo2.addComponent("methane", 0.8)
    thermo2.addComponent("ethane", 0.2)
    thermo2.setMixingRule(2)
    thermo2.setTotalFlowRate(200.0, "kg/hr")

    stream2 = neqsim.process.equipment.stream.Stream("recycle", thermo2)
    stream2.run()

    print("\n--- Stream States ---")
    print(f"  Feed: T={stream1.getTemperature('C'):.4f}°C, P={stream1.getPressure('bara'):.4f} bara")
    print(f"         Flow={stream1.getFlowRate('kg/hr'):.2f} kg/hr, H={stream1.getFluid().getEnthalpy():.2f} J")
    print(f"  Recycle: T={stream2.getTemperature('C'):.4f}°C, P={stream2.getPressure('bara'):.4f} bara")
    print(f"           Flow={stream2.getFlowRate('kg/hr'):.2f} kg/hr, H={stream2.getFluid().getEnthalpy():.2f} J")

    # Create mixer and add streams
    mixer = neqsim.process.equipment.mixer.Mixer("test_mixer")
    mixer.addStream(stream1)
    mixer.addStream(stream2)

    print("\n--- Running Mixer ---")
    mixer.run()

    mixed = mixer.getOutletStream()
    print(f"  Mixed: T={mixed.getTemperature('C'):.4f}°C, P={mixed.getPressure('bara'):.4f} bara")
    print(f"         Flow={mixed.getFlowRate('kg/hr'):.2f} kg/hr, H={mixed.getFluid().getEnthalpy():.2f} J")

    # Calculate expected temperature (enthalpy-weighted)
    H_total = stream1.getFluid().getEnthalpy() + stream2.getFluid().getEnthalpy()
    print(f"\n--- Enthalpy Balance ---")
    print(f"  H_feed = {stream1.getFluid().getEnthalpy():.2f} J")
    print(f"  H_recycle = {stream2.getFluid().getEnthalpy():.2f} J")
    print(f"  H_total = {H_total:.2f} J")
    print(f"  H_mixed = {mixed.getFluid().getEnthalpy():.2f} J")
    print(f"  Enthalpy balance error = {abs(H_total - mixed.getFluid().getEnthalpy()):.2f} J")


def debug_actual_solver():
    """Test with the actual IterativeSolver to compare behavior."""
    print("\n" + "=" * 80)
    print("ACTUAL ITERATIVE SOLVER DEBUG")
    print("=" * 80)

    from core.iterative_solver import IterativeSolver

    # Build the flowsheet with higher max_iterations and tight tolerance
    nodes = [
        Node(
            id="feed",
            type=NodeType.STREAM,
            input_data=StreamInput(
                temperature=25.0,
                pressure=1.0,
                flow_rate=1000.0,
                composition={"methane": 0.8, "ethane": 0.2},
                eos_model="SRK",
            ),
        ),
        Node(id="mixer", type=NodeType.MIXER, input_data=None),
        Node(id="s_mixed", type=NodeType.STREAM, input_data=None),
        Node(
            id="compressor",
            type=NodeType.COMPRESSOR,
            input_data=CompressorInput(
                calculation_mode="outlet_pressure",
                outlet_pressure=3.0,
                isentropic_efficiency=0.75,
            ),
        ),
        Node(id="s_comp", type=NodeType.STREAM, input_data=None),
        Node(
            id="splitter",
            type=NodeType.SPLITTER,
            input_data=SplitterInput(fraction_outlet_1=0.8, fraction_outlet_2=0.2),
        ),
        Node(id="s_product", type=NodeType.STREAM, input_data=None),
        Node(id="s_rec_in", type=NodeType.STREAM, input_data=None),
        Node(
            id="recycle",
            type=NodeType.RECYCLE,
            input_data=RecycleInput(
                max_iterations=100,
                temperature_tolerance_c=1.0,  # Same as frontend
                pressure_tolerance_bara=0.05,
                flow_tolerance_fraction=0.01,
                composition_tolerance_fraction=0.01,
                smoothing_factor=0.3,
                wegstein_bounds=[-5, 0],
                accel_delay=2,
            ),
        ),
        Node(id="s_rec_out", type=NodeType.STREAM, input_data=None),
    ]

    edges = [
        Edge(id="e1", source="feed", target="mixer", source_handle="outlet", target_handle="inlet_1"),
        Edge(id="e2", source="mixer", target="s_mixed", source_handle="outlet", target_handle="inlet"),
        Edge(id="e3", source="s_mixed", target="compressor", source_handle="outlet", target_handle="inlet"),
        Edge(id="e4", source="compressor", target="s_comp", source_handle="outlet", target_handle="inlet"),
        Edge(id="e5", source="s_comp", target="splitter", source_handle="outlet", target_handle="inlet"),
        Edge(id="e6", source="splitter", target="s_product", source_handle="outlet_1", target_handle="inlet"),
        Edge(id="e7", source="splitter", target="s_rec_in", source_handle="outlet_2", target_handle="inlet"),
        Edge(id="e8", source="s_rec_in", target="recycle", source_handle="outlet", target_handle="inlet"),
        Edge(id="e9", source="recycle", target="s_rec_out", source_handle="outlet", target_handle="inlet"),
        Edge(id="e10", source="s_rec_out", target="mixer", source_handle="outlet", target_handle="inlet_2"),
    ]

    flowsheet = FlowsheetGraph(nodes=nodes, edges=edges, name="Recycle Debug")

    # Build
    builder = ProcessBuilder()
    artifacts = builder.build(flowsheet)

    # Solve with actual solver
    solver = IterativeSolver(timeout_seconds=120.0)
    container = solver.solve(artifacts)

    # Print results
    print(f"\nConverged: {container.recycle_converged}")
    print(f"Iterations: {container.recycle_iteration_count}")

    print("\nIteration history:")
    for rec in container.recycle_iterations:
        print(f"  Iter {rec['iteration']}: error={rec['error']:.6f}, converged={rec['converged']}")

    # Get final recycle node results
    recycle = artifacts.unit_by_node["recycle"]
    print(f"\nRecycle results:")
    print(f"  Temperature error: {recycle._temperature_error_c:.4f}°C (tolerance: 1.0°C)")
    print(f"  Pressure error: {recycle._pressure_error_bara:.4f} bara (tolerance: 0.05 bara)")
    print(f"  Flow error: {recycle._flow_error_fraction:.6f} (tolerance: 0.01)")

    # Get final stream states
    s_rec_in = artifacts.stream_by_node["s_rec_in"]
    s_rec_out = artifacts.stream_by_node["s_rec_out"]
    print(f"\nFinal stream states:")
    print(f"  Tear stream (s_rec_in): T={s_rec_in._java_stream.getTemperature('C'):.4f}°C")
    print(f"  Guess stream (s_rec_out): T={s_rec_out._java_stream.getTemperature('C'):.4f}°C")
    print(f"  Temperature difference: {abs(s_rec_in._java_stream.getTemperature('C') - s_rec_out._java_stream.getTemperature('C')):.4f}°C")


if __name__ == "__main__":
    print("=" * 80)
    print("RECYCLE CONVERGENCE DEBUGGING SCRIPT")
    print("=" * 80)

    # Test with actual IterativeSolver (should now converge after fix!)
    debug_actual_solver()

    # Optional tests (uncomment as needed):
    # debug_wegstein_values()  # Wegstein on T only - always worked
    # debug_wegstein_all_properties()  # OLD bug: Wegstein on all - doesn't converge
    # debug_enthalpy_update()
    # debug_mixer_enthalpy_balance()
    # debug_convergence_pattern()  # Direct substitution only
    # debug_manual_iteration()
