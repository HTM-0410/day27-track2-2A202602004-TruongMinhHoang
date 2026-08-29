"""Dependency-free two-sample distribution drift detection."""
from __future__ import annotations

import math
from typing import Any, Iterable

import numpy as np


_MAX_SCORE = 1.0e12


def _finite_array(values: Iterable[float], name: str) -> tuple[np.ndarray, int]:
    try:
        raw = np.asarray(list(values), dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain numeric values") from exc
    finite = raw[np.isfinite(raw)]
    return finite, int(raw.size - finite.size)


def _ks_statistic(left: np.ndarray, right: np.ndarray) -> float:
    points = np.sort(np.unique(np.concatenate([left, right])))
    left_sorted = np.sort(left)
    right_sorted = np.sort(right)
    left_cdf = np.searchsorted(left_sorted, points, side="right") / left.size
    right_cdf = np.searchsorted(right_sorted, points, side="right") / right.size
    return float(np.max(np.abs(left_cdf - right_cdf)))


def _robust_scale(values: np.ndarray) -> float:
    median = float(np.median(values))
    mad_sigma = 1.4826 * float(np.median(np.abs(values - median)))
    q25, q75 = np.quantile(values, [0.25, 0.75])
    iqr_sigma = float(q75 - q25) / 1.349
    return max(mad_sigma, iqr_sigma, abs(median) * 0.01, 1.0e-9)


def detect_distribution_shift(
    current_values: Iterable[float],
    baseline_values: Iterable[float],
    *,
    ratio_threshold: float = 3.0,
) -> dict[str, Any]:
    """Detect location, scale, and shape drift without SciPy.

    The normalized two-sample KS component catches equal-mean shape changes,
    while robust location/scale effects retain power on small extreme samples.
    """
    cur, dropped_cur = _finite_array(current_values, "current_values")
    base, dropped_base = _finite_array(baseline_values, "baseline_values")
    if cur.size == 0 or base.size == 0:
        invalid_current = cur.size == 0 and (base.size > 0 or dropped_cur > 0)
        return {
            "is_anomaly": bool(invalid_current),
            "score": float(_MAX_SCORE if invalid_current else 0.0),
            "method": "ks+robust_effect",
            "reason": (
                f"empty_input; current_count={cur.size}; baseline_count={base.size}; "
                f"dropped_current={dropped_cur}; dropped_baseline={dropped_base}"
            ),
        }

    base_median = float(np.median(base))
    cur_median = float(np.median(cur))
    base_scale = _robust_scale(base)
    cur_scale = _robust_scale(cur)
    location_effect = min(abs(cur_median - base_median) / base_scale, _MAX_SCORE)
    scale_ratio = min(max(cur_scale / base_scale, base_scale / cur_scale), _MAX_SCORE)

    ks = _ks_statistic(cur, base)
    effective_n = cur.size * base.size / (cur.size + base.size)
    ks_critical = 1.36 / math.sqrt(effective_n)
    ks_normalized = min(ks / ks_critical, _MAX_SCORE)

    # ratio_threshold remains part of the starter signature. It now controls a
    # robust scale-ratio gate; the location threshold is deliberately strong.
    location_anomaly = location_effect >= 5.0
    scale_anomaly = scale_ratio >= max(float(ratio_threshold), 1.0)
    shape_anomaly = ks_normalized > 1.0
    is_anomaly = location_anomaly or scale_anomaly or shape_anomaly
    score = max(location_effect / 5.0, scale_ratio / max(ratio_threshold, 1.0), ks_normalized)

    return {
        "is_anomaly": bool(is_anomaly),
        "score": float(min(score, _MAX_SCORE)),
        "method": "ks+robust_effect",
        "reason": (
            f"ks={ks:.6g}; ks_critical={ks_critical:.6g}; "
            f"location_effect={location_effect:.6g}; scale_ratio={scale_ratio:.6g}; "
            f"current_count={cur.size}; baseline_count={base.size}; "
            f"dropped_current={dropped_cur}; dropped_baseline={dropped_base}"
        ),
        "ks_statistic": float(ks),
        "location_effect": float(location_effect),
        "scale_ratio": float(scale_ratio),
    }
