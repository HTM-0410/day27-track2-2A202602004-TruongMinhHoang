from __future__ import annotations

import math
from numbers import Integral, Real
from typing import Any


def _finite_real(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ValueError(f"{name} must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be a finite number")
    return result


def _event_count(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, Integral):
        raise ValueError(f"{name} must be an integer")
    return int(value)


def calculate_slo(target: float, bad_events: int, total_events: int) -> dict[str, Any]:
    normalized_target = _finite_real(target, "target")
    bad = _event_count(bad_events, "bad_events")
    total = _event_count(total_events, "total_events")
    if not 0.0 < normalized_target < 1.0:
        raise ValueError("target must be between 0 and 1 (exclusive)")
    if bad < 0 or total < 0 or bad > total:
        raise ValueError("invalid event counts")

    allowed_bad_rate = 1.0 - normalized_target
    if total == 0:
        return {
            "target": normalized_target,
            "actual_bad_rate": 0.0,
            "allowed_bad_rate": allowed_bad_rate,
            "burn_rate": 0.0,
            "remaining_error_budget_fraction": 1.0,
            "breached": False,
        }

    actual_bad_rate = bad / total
    burn_rate = actual_bad_rate / allowed_bad_rate
    tolerance = 1.0e-12 * max(1.0, abs(burn_rate))
    return {
        "target": normalized_target,
        "actual_bad_rate": float(actual_bad_rate),
        "allowed_bad_rate": float(allowed_bad_rate),
        "burn_rate": float(burn_rate),
        "remaining_error_budget_fraction": float(max(0.0, 1.0 - burn_rate)),
        "breached": bool(burn_rate > 1.0 + tolerance),
    }


def evaluate_multiwindow_burn(
    *,
    short_window_burn: float,
    long_window_burn: float,
    policy: str = "sre_multiwindow_v1",
) -> dict[str, Any]:
    """Evaluate two corroborating windows to suppress transient spikes.

    A fast-burn page requires both a very hot short window and a sustained long
    window. A lower paired threshold emits a warning page. Single-window spikes
    remain visible in the reason but never page.
    """
    short = _finite_real(short_window_burn, "short_window_burn")
    long = _finite_real(long_window_burn, "long_window_burn")
    if short < 0 or long < 0:
        raise ValueError("burn rates must be non-negative")

    fast_short, fast_long = 14.4, 6.0
    slow_short, slow_long = 6.0, 3.0
    if short >= fast_short and long >= fast_long:
        page = True
        severity = "critical"
        reason = "sustained_fast_burn"
    elif short >= slow_short and long >= slow_long:
        page = True
        severity = "warning"
        reason = "sustained_burn"
    elif short >= fast_short or long >= fast_long:
        page = False
        severity = "warning"
        reason = "single_window_spike"
    else:
        page = False
        severity = "info"
        reason = "within_multiwindow_policy"

    return {
        "page": bool(page),
        "severity": severity,
        "reason": reason,
        "policy": policy,
        "short_window_burn": float(short),
        "long_window_burn": float(long),
        "thresholds": {
            "fast": {"short": fast_short, "long": fast_long},
            "slow": {"short": slow_short, "long": slow_long},
        },
    }
