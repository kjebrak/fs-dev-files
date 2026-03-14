"""Debug script to compare stagnation handling strategies.

Tests the hypothesis that reducing alpha (more smoothing) helps convergence
when stagnation is detected, instead of disabling Wegstein entirely.

Approach:
1. Run with default alpha=0.3 and fallback ENABLED (current behavior)
2. Run with default alpha=0.3 and fallback DISABLED (no handling - baseline)
3. Run with lower alpha=0.15 and fallback DISABLED (simulates adaptive smoothing)
4. Run with lower alpha=0.10 and fallback DISABLED (more aggressive smoothing)

Usage:
    uv run python scripts/debug_adaptive_smoothing.py
"""

import logging
import sys
from dataclasses import dataclass
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core.iterative_solver import IterativeSolver
from core.process_builder import ProcessBuilder
from tests.integration.conftest import build_nested_recycle_graph

logging.basicConfig(
    level=logging.WARNING,  # Reduce noise
    format="%(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class RunResult:
    """Result of a single simulation run."""

    name: str
    alpha: float
    fallback_enabled: bool
    converged: bool
    iterations: int
    final_tolerance_ratios: dict[str, float]  # recycle_id -> final ratio
    fallback_triggered: dict[str, bool]  # recycle_id -> triggered


def run_simulation(
    name: str,
    alpha: float,
    fallback_enabled: bool,
    max_iterations: int = 100,
    wegstein_enabled: bool = True,
) -> RunResult:
    """Run nested recycle with specified settings."""
    graph = build_nested_recycle_graph(
        inner_recycle_config={
            "max_iterations": max_iterations,
            "fallback_to_direct_substitution": fallback_enabled,
            "smoothing_factor": alpha,
            "wegstein_enabled": wegstein_enabled,
        },
        outer_recycle_config={
            "max_iterations": max_iterations,
            "fallback_to_direct_substitution": fallback_enabled,
            "smoothing_factor": alpha,
            "wegstein_enabled": wegstein_enabled,
        },
    )

    builder = ProcessBuilder()
    artifacts = builder.build(graph)
    solver = IterativeSolver()

    container = solver.solve(artifacts)

    # Get results
    per_recycle = solver._recycle_controller.get_per_recycle_results()

    final_ratios = {}
    fallback_triggered = {}
    for recycle_id, data in per_recycle.items():
        history = data.get("iteration_history", [])
        if history:
            final_ratios[recycle_id] = history[-1].get("tolerance_ratio", float("inf"))
        else:
            final_ratios[recycle_id] = float("inf")
        fallback_triggered[recycle_id] = data.get("wegstein_fallback_triggered", False)

    converged = container.recycle_converged
    iterations = container.recycle_iteration_count or max_iterations

    return RunResult(
        name=name,
        alpha=alpha,
        fallback_enabled=fallback_enabled,
        converged=converged,
        iterations=iterations,
        final_tolerance_ratios=final_ratios,
        fallback_triggered=fallback_triggered,
    )


def print_iteration_trace(name: str, alpha: float, fallback: bool, max_iter: int = 100):
    """Print detailed iteration trace for a specific configuration."""
    print(f"\n{'='*70}")
    print(f"TRACE: {name}")
    print(f"  alpha={alpha}, fallback_enabled={fallback}")
    print("=" * 70)

    graph = build_nested_recycle_graph(
        inner_recycle_config={
            "max_iterations": max_iter,
            "fallback_to_direct_substitution": fallback,
            "smoothing_factor": alpha,
        },
        outer_recycle_config={
            "max_iterations": max_iter,
            "fallback_to_direct_substitution": fallback,
            "smoothing_factor": alpha,
        },
    )

    builder = ProcessBuilder()
    artifacts = builder.build(graph)
    solver = IterativeSolver()

    container = solver.solve(artifacts)

    per_recycle = solver._recycle_controller.get_per_recycle_results()

    # Print header
    print(f"\n{'Iter':<6} ", end="")
    for recycle_id in sorted(per_recycle.keys()):
        print(f"{recycle_id:<25} ", end="")
    print("Notes")
    print("-" * 80)

    # Find max iterations
    max_iters = max(
        len(data.get("iteration_history", [])) for data in per_recycle.values()
    )

    # Print each iteration
    for i in range(1, min(max_iters + 1, 60)):  # Cap at 60 iterations for readability
        print(f"{i:<6} ", end="")
        notes = []

        for recycle_id in sorted(per_recycle.keys()):
            data = per_recycle[recycle_id]
            history = data.get("iteration_history", [])

            if i <= len(history):
                h = history[i - 1]
                ratio = h.get("tolerance_ratio", 0)
                limiting = h.get("limiting", "?")
                conv = "✓" if h.get("converged") else " "

                # Check for fallback
                fb_iter = data.get("wegstein_fallback_iteration")
                if fb_iter == i:
                    notes.append(f"FALLBACK:{recycle_id}")

                print(f"{conv} {ratio:6.2f} ({limiting:<4}) ", end="")
            else:
                print(f"  {'--':>6} {'':>6} ", end="")

        print(" ".join(notes))

    # Summary
    print("-" * 80)
    converged = container.recycle_converged
    iterations = container.recycle_iteration_count or max_iter
    print(f"Result: {'CONVERGED' if converged else 'FAILED'} in {iterations} iterations")

    for recycle_id, data in per_recycle.items():
        fb = data.get("wegstein_fallback_triggered", False)
        fb_iter = data.get("wegstein_fallback_iteration")
        if fb:
            print(f"  {recycle_id}: Fallback triggered at iteration {fb_iter}")


def main():
    """Compare different stagnation handling approaches."""
    print("=" * 70)
    print("ADAPTIVE SMOOTHING CONCEPT VALIDATION")
    print("=" * 70)
    print("\nHypothesis: Reducing alpha (more smoothing) when stagnation is detected")
    print("should help convergence without the discontinuity of disabling Wegstein.\n")

    # Run different configurations
    # Note: smoothing_factor has min=0.1 validation constraint
    # Format: (name, alpha, fallback_enabled, wegstein_enabled)
    configs = [
        ("Current (fallback ON)", 0.3, True, True),
        ("No handling (Wegstein ON)", 0.3, False, True),
        ("Lower alpha=0.15 (Weg ON)", 0.15, False, True),
        ("Pure DS alpha=0.3 (Weg OFF)", 0.3, False, False),
        ("Pure DS alpha=0.5 (Weg OFF)", 0.5, False, False),
        ("Pure DS alpha=0.7 (Weg OFF)", 0.7, False, False),
    ]

    results = []
    for name, alpha, fallback, wegstein in configs:
        result = run_simulation(name, alpha, fallback, wegstein_enabled=wegstein)
        results.append(result)

    # Summary table
    print("\n" + "=" * 90)
    print("SUMMARY")
    print("=" * 90)
    print(
        f"{'Configuration':<30} {'Alpha':<8} {'Fallback':<10} "
        f"{'Converged':<12} {'Iterations':<12}"
    )
    print("-" * 90)

    for r in results:
        print(
            f"{r.name:<30} {r.alpha:<8.2f} {str(r.fallback_enabled):<10} "
            f"{str(r.converged):<12} {r.iterations:<12}"
        )

    print("-" * 90)

    # Analysis
    print("\nANALYSIS:")
    current = results[0]
    no_handling = results[1]

    if current.converged:
        print(f"  - Current approach (fallback) converges in {current.iterations} iterations")
    else:
        print(f"  - Current approach (fallback) FAILED after {current.iterations} iterations")

    if no_handling.converged:
        print(f"  - Without any handling, converges in {no_handling.iterations} iterations")
    else:
        print(f"  - Without any handling, FAILS - stagnation handling is needed")

    # Check if lower alpha helps
    for r in results[2:]:
        if r.converged and (not no_handling.converged or r.iterations < no_handling.iterations):
            print(f"  - alpha={r.alpha} HELPS: converges in {r.iterations} iterations")
        elif r.converged:
            print(f"  - alpha={r.alpha} converges in {r.iterations} iterations (similar)")
        else:
            print(f"  - alpha={r.alpha} still fails")

    # Show detailed trace for most interesting cases
    print("\n" + "=" * 70)
    print("DETAILED TRACES")
    print("=" * 70)

    # Show current behavior
    print_iteration_trace("Current (fallback ON)", 0.3, True, max_iter=60)

    # Show lower alpha if it helps
    lower_alpha_result = results[2]  # alpha=0.15
    if lower_alpha_result.converged:
        print_iteration_trace("Lower alpha=0.15 (no fallback)", 0.15, False, max_iter=60)


if __name__ == "__main__":
    main()
