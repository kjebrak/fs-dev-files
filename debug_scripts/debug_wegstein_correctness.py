"""Debug script to verify Wegstein implementation correctness.

Tests:
1. Simple 1D fixed-point problem where Wegstein SHOULD help
2. Our implementation vs textbook formula
3. Comparison with pure direct substitution and adaptive damping

If Wegstein is correct, it should accelerate convergence on simple problems.
If it doesn't help even on simple problems, the implementation is wrong.
"""

import math
from dataclasses import dataclass


# =============================================================================
# Test Problem: Simple 1D Fixed-Point Iteration
# =============================================================================
# Find x such that x = g(x) where g(x) = cos(x)
# Solution: x ≈ 0.739085 (Dottie number)
#
# This is a classic test case for fixed-point acceleration methods.
# Direct substitution converges slowly. Wegstein should accelerate it.


def g(x: float) -> float:
    """Fixed-point function: g(x) = cos(x)."""
    return math.cos(x)


@dataclass
class IterationResult:
    """Result of a convergence test."""
    method: str
    converged: bool
    iterations: int
    final_x: float
    final_error: float
    history: list[float]


def direct_substitution(x0: float, tol: float = 1e-8, max_iter: int = 100) -> IterationResult:
    """Pure direct substitution: x_{n+1} = g(x_n)."""
    x = x0
    history = [x]

    for i in range(max_iter):
        x_new = g(x)
        error = abs(x_new - x)
        history.append(x_new)

        if error < tol:
            return IterationResult("Direct Substitution", True, i + 1, x_new, error, history)

        x = x_new

    return IterationResult("Direct Substitution", False, max_iter, x, error, history)


def direct_substitution_damped(
    x0: float, alpha: float = 0.5, tol: float = 1e-8, max_iter: int = 100
) -> IterationResult:
    """Direct substitution with damping: x_{n+1} = alpha * g(x_n) + (1-alpha) * x_n."""
    x = x0
    history = [x]

    for i in range(max_iter):
        g_x = g(x)
        x_new = alpha * g_x + (1 - alpha) * x
        error = abs(x_new - x)
        history.append(x_new)

        if error < tol:
            return IterationResult(f"DS Damped (α={alpha})", True, i + 1, x_new, error, history)

        x = x_new

    return IterationResult(f"DS Damped (α={alpha})", False, max_iter, x, error, history)


def wegstein_textbook(
    x0: float, q_min: float = -5.0, q_max: float = 1.0, tol: float = 1e-8, max_iter: int = 100
) -> IterationResult:
    """Textbook Wegstein method.

    Standard formula:
        q = (g(x_n) - g(x_{n-1})) / (x_n - x_{n-1}) - 1
        Actually: slope = dg/dx ≈ (g_n - g_{n-1}) / (x_n - x_{n-1})
        q = slope / (slope - 1)

    Then: x_{n+1} = (1-q) * g(x_n) + q * x_n
    Or equivalently: x_{n+1} = x_n + (g(x_n) - x_n) / (1 - slope)
    """
    x = x0
    x_prev = None
    g_prev = None
    history = [x]

    for i in range(max_iter):
        g_x = g(x)

        if x_prev is None or abs(x - x_prev) < 1e-12:
            # First iteration or no change: use direct substitution
            x_new = g_x
        else:
            # Calculate slope approximation
            slope = (g_x - g_prev) / (x - x_prev)

            if abs(slope - 1) < 1e-10:
                # Avoid division by zero
                x_new = g_x
            else:
                # Wegstein formula: x_{n+1} = x_n + (g(x_n) - x_n) / (1 - slope)
                x_new = x + (g_x - x) / (1 - slope)

                # Equivalent to bounded q form:
                # q = slope / (slope - 1)  # Note: different from our impl!
                # q = max(q_min, min(q_max, q))
                # x_new = q * x + (1 - q) * g_x

        error = abs(x_new - x)
        history.append(x_new)

        if error < tol:
            return IterationResult("Wegstein (textbook)", True, i + 1, x_new, error, history)

        x_prev = x
        g_prev = g_x
        x = x_new

    return IterationResult("Wegstein (textbook)", False, max_iter, x, error, history)


def wegstein_our_impl(
    x0: float,
    alpha: float = 0.3,
    q_min: float = -5.0,
    q_max: float = 0.0,  # Note: our bounds are [-5, 0]
    tol: float = 1e-8,
    max_iter: int = 100,
    accel_delay: int = 2,
) -> IterationResult:
    """Our Wegstein implementation (from iterative_solver.py).

    This replicates our exact implementation to test correctness.
    """
    x = x0  # x_old
    prev_old = None
    prev_new = None
    history = [x]

    for i in range(max_iter):
        x_old = x
        x_new = g(x_old)  # What we get from running the system

        # Determine if Wegstein should be used
        use_wegstein = (
            i >= accel_delay
            and prev_old is not None
            and prev_new is not None
        )

        if not use_wegstein:
            # Direct substitution with smoothing
            x_next = alpha * x_new + (1 - alpha) * x_old
        else:
            # Wegstein acceleration (OUR FORMULA)
            x_prev_old = prev_old
            x_prev_new = prev_new

            dx_new = x_new - x_old
            dx_prev = x_prev_new - x_prev_old

            denom = dx_new - dx_prev
            if abs(denom) < 1e-10:
                q = 0.0
            else:
                q = dx_new / denom

            # Bound q
            q = max(q_min, min(q_max, q))

            # Wegstein update
            if abs(1 - q) < 1e-10:
                wegstein_val = x_new
            else:
                wegstein_val = x_old - q * dx_new / (1 - q)

            # Apply smoothing to Wegstein result
            x_next = alpha * wegstein_val + (1 - alpha) * x_old

        error = abs(x_next - x_old)
        history.append(x_next)

        if error < tol:
            return IterationResult(f"Our Wegstein (α={alpha})", True, i + 1, x_next, error, history)

        prev_old = x_old
        prev_new = x_new
        x = x_next

    return IterationResult(f"Our Wegstein (α={alpha})", False, max_iter, x, error, history)


def adaptive_damping(
    x0: float, tol: float = 1e-8, max_iter: int = 100
) -> IterationResult:
    """Adaptive damping like eCalc - adjusts beta based on oscillation detection."""
    x = x0
    beta = 1.0
    delta_prev = None
    flip_counter = 0
    stable_counter = 0
    history = [x]

    EPSILON_LARGE = 0.01
    OSCILL_FLIP_REQ = 2
    STEP_DOWN = 0.5
    STEP_UP = 1.5
    BETA_MIN = 0.10
    BETA_MAX = 1.00
    STABLE_ITERS = 3

    for i in range(max_iter):
        g_x = g(x)
        delta = g_x - x

        large_step = abs(delta) > EPSILON_LARGE * abs(x) if x != 0 else abs(delta) > EPSILON_LARGE
        flip = delta_prev is not None and (delta * delta_prev) < 0.0

        # Oscillation detection
        if flip and large_step:
            flip_counter += 1
        else:
            flip_counter = 0

        # Beta adaptation
        if flip_counter >= OSCILL_FLIP_REQ:
            beta = max(BETA_MIN, beta * STEP_DOWN)
            stable_counter = 0
        else:
            stable_counter += 1
            if stable_counter >= STABLE_ITERS:
                beta = min(BETA_MAX, beta * STEP_UP)
                stable_counter = 0

        x_new = x + beta * delta
        error = abs(x_new - x)
        history.append(x_new)

        if error < tol:
            return IterationResult("Adaptive Damping", True, i + 1, x_new, error, history)

        delta_prev = delta
        x = x_new

    return IterationResult("Adaptive Damping", False, max_iter, x, error, history)


def print_result(result: IterationResult, show_history: bool = False):
    """Print iteration result."""
    status = "✓ CONVERGED" if result.converged else "✗ FAILED"
    print(f"  {result.method:<30} {status:<15} {result.iterations:>4} iters  x={result.final_x:.8f}")

    if show_history and len(result.history) <= 20:
        print(f"    History: {[f'{x:.4f}' for x in result.history]}")


def main():
    print("=" * 80)
    print("WEGSTEIN CORRECTNESS TEST")
    print("=" * 80)
    print("\nTest Problem: Find x where x = cos(x)")
    print("Known solution: x ≈ 0.73908513 (Dottie number)")
    print()

    x0 = 0.5  # Starting point

    print(f"Starting point: x0 = {x0}")
    print()

    # Run all methods
    results = [
        direct_substitution(x0),
        direct_substitution_damped(x0, alpha=0.5),
        direct_substitution_damped(x0, alpha=0.3),
        wegstein_textbook(x0),
        wegstein_our_impl(x0, alpha=1.0),  # No smoothing
        wegstein_our_impl(x0, alpha=0.5),
        wegstein_our_impl(x0, alpha=0.3),  # Our default
        adaptive_damping(x0),
    ]

    print("-" * 80)
    print(f"{'Method':<30} {'Status':<15} {'Iters':>6}  Result")
    print("-" * 80)

    for result in results:
        print_result(result)

    print("-" * 80)

    # Analysis
    print("\nANALYSIS:")

    ds_result = results[0]
    textbook_result = results[3]
    our_result = results[6]  # alpha=0.3

    print(f"\n1. Direct Substitution baseline: {ds_result.iterations} iterations")

    if textbook_result.converged and textbook_result.iterations < ds_result.iterations:
        print(f"2. Textbook Wegstein: {textbook_result.iterations} iterations (FASTER - acceleration works!)")
    else:
        print(f"2. Textbook Wegstein: {textbook_result.iterations} iterations (NOT faster)")

    if our_result.converged:
        if our_result.iterations < ds_result.iterations:
            print(f"3. Our Wegstein: {our_result.iterations} iterations (FASTER)")
        elif our_result.iterations > ds_result.iterations:
            print(f"3. Our Wegstein: {our_result.iterations} iterations (SLOWER - something wrong!)")
        else:
            print(f"3. Our Wegstein: {our_result.iterations} iterations (same)")
    else:
        print(f"3. Our Wegstein: FAILED to converge!")

    # Show detailed comparison of Wegstein formulas
    print("\n" + "=" * 80)
    print("DETAILED FORMULA COMPARISON")
    print("=" * 80)

    print("\nTextbook Wegstein formula:")
    print("  slope = (g(x_n) - g(x_{n-1})) / (x_n - x_{n-1})")
    print("  x_{n+1} = x_n + (g(x_n) - x_n) / (1 - slope)")

    print("\nOur Wegstein formula:")
    print("  dx_new = x_new - x_old = g(x_old) - x_old")
    print("  dx_prev = x_prev_new - x_prev_old = g(x_prev_old) - x_prev_old")
    print("  q = dx_new / (dx_new - dx_prev)")
    print("  wegstein_val = x_old - q * dx_new / (1 - q)")
    print("  x_next = alpha * wegstein_val + (1 - alpha) * x_old")

    print("\nKey difference:")
    print("  - Textbook: Uses slope = dg/dx")
    print("  - Ours: Uses q = dx_new/(dx_new - dx_prev) which is DIFFERENT")
    print("  - Ours: Also applies smoothing AFTER Wegstein (double damping?)")


if __name__ == "__main__":
    main()
