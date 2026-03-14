"""Debug: Compare Wegstein formulas step by step.

This traces through the exact calculations to find the bug.
"""

import math


def g(x: float) -> float:
    """Fixed-point function: g(x) = cos(x)."""
    return math.cos(x)


def trace_textbook_wegstein(x0: float, num_iters: int = 10):
    """Trace textbook Wegstein step by step."""
    print("=" * 80)
    print("TEXTBOOK WEGSTEIN TRACE")
    print("Formula: x_{n+1} = x_n + (g(x_n) - x_n) / (1 - slope)")
    print("         where slope = (g(x_n) - g(x_{n-1})) / (x_n - x_{n-1})")
    print("=" * 80)

    x = x0
    x_prev = None
    g_prev = None

    for i in range(num_iters):
        g_x = g(x)
        residual = g_x - x

        if x_prev is None:
            # First iteration: direct substitution
            x_new = g_x
            print(f"Iter {i}: x={x:.6f}, g(x)={g_x:.6f}, residual={residual:.6f}")
            print(f"         [First iter: DS] x_new={x_new:.6f}")
        else:
            # Wegstein
            slope = (g_x - g_prev) / (x - x_prev)
            accel_factor = 1 / (1 - slope)
            x_new = x + residual * accel_factor

            print(f"Iter {i}: x={x:.6f}, g(x)={g_x:.6f}, residual={residual:.6f}")
            print(f"         slope={slope:.6f}, accel_factor={accel_factor:.6f}")
            print(f"         x_new = {x:.6f} + {residual:.6f} * {accel_factor:.6f} = {x_new:.6f}")

        if abs(x_new - x) < 1e-8:
            print(f"\n*** CONVERGED at iter {i} ***")
            break

        x_prev = x
        g_prev = g_x
        x = x_new
        print()


def trace_our_wegstein(x0: float, alpha: float = 0.3, num_iters: int = 10):
    """Trace our Wegstein step by step."""
    print("=" * 80)
    print(f"OUR WEGSTEIN TRACE (alpha={alpha})")
    print("Formula: q = dx_new / (dx_new - dx_prev)")
    print("         wegstein_val = x_old - q * dx_new / (1 - q)")
    print("         x_next = alpha * wegstein_val + (1-alpha) * x_old")
    print("=" * 80)

    x = x0  # x_old
    prev_old = None
    prev_new = None
    accel_delay = 2
    q_min, q_max = -5.0, 0.0

    for i in range(num_iters):
        x_old = x
        x_new = g(x_old)  # What we get

        use_wegstein = (i >= accel_delay and prev_old is not None and prev_new is not None)

        print(f"Iter {i}: x_old={x_old:.6f}, x_new=g(x_old)={x_new:.6f}")

        if not use_wegstein:
            # Direct substitution with smoothing
            x_next = alpha * x_new + (1 - alpha) * x_old
            print(f"         [DS] x_next = {alpha}*{x_new:.6f} + {1-alpha}*{x_old:.6f} = {x_next:.6f}")
        else:
            # Wegstein
            dx_new = x_new - x_old
            dx_prev = prev_new - prev_old

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

            x_next = alpha * wegstein_val + (1 - alpha) * x_old

            print(f"         dx_new={dx_new:.6f}, dx_prev={dx_prev:.6f}")
            print(f"         q={q:.6f} (before clamp: {dx_new/denom if abs(denom)>1e-10 else 'div0'})")
            print(f"         wegstein_val = {x_old:.6f} - {q:.6f}*{dx_new:.6f}/(1-{q:.6f}) = {wegstein_val:.6f}")
            print(f"         x_next = {alpha}*{wegstein_val:.6f} + {1-alpha}*{x_old:.6f} = {x_next:.6f}")

        if abs(x_next - x_old) < 1e-8:
            print(f"\n*** CONVERGED at iter {i} ***")
            break

        prev_old = x_old
        prev_new = x_new
        x = x_next
        print()


def trace_our_wegstein_no_smooth(x0: float, num_iters: int = 10):
    """Trace our Wegstein WITHOUT post-smoothing to isolate the formula bug."""
    print("=" * 80)
    print("OUR WEGSTEIN TRACE (NO SMOOTHING - alpha=1.0)")
    print("This isolates the core formula to find the bug")
    print("=" * 80)

    x = x0
    prev_old = None
    prev_new = None
    accel_delay = 2
    q_min, q_max = -5.0, 0.0

    for i in range(num_iters):
        x_old = x
        x_new = g(x_old)

        use_wegstein = (i >= accel_delay and prev_old is not None and prev_new is not None)

        print(f"Iter {i}: x_old={x_old:.6f}, g(x_old)={x_new:.6f}")

        if not use_wegstein:
            x_next = x_new  # Pure DS
            print(f"         [DS] x_next = {x_next:.6f}")
        else:
            dx_new = x_new - x_old
            dx_prev = prev_new - prev_old

            denom = dx_new - dx_prev
            q_raw = dx_new / denom if abs(denom) > 1e-10 else 0
            q = max(q_min, min(q_max, q_raw))

            wegstein_val = x_old - q * dx_new / (1 - q) if abs(1-q) > 1e-10 else x_new

            print(f"         dx_new={dx_new:.6f}, dx_prev={dx_prev:.6f}")
            print(f"         q_raw={q_raw:.6f}, q_clamped={q:.6f}")
            print(f"         wegstein_val = {wegstein_val:.6f}")

            x_next = wegstein_val  # No smoothing!

        if abs(x_next - x_old) < 1e-8:
            print(f"\n*** CONVERGED at iter {i} ***")
            break

        prev_old = x_old
        prev_new = x_new
        x = x_next
        print()


if __name__ == "__main__":
    x0 = 0.5

    print(f"\nStarting point: x0 = {x0}")
    print(f"Target solution: x ≈ 0.73908513\n")

    trace_textbook_wegstein(x0, num_iters=10)
    print("\n" * 2)
    trace_our_wegstein_no_smooth(x0, num_iters=10)
    print("\n" * 2)
    trace_our_wegstein(x0, alpha=0.3, num_iters=10)
