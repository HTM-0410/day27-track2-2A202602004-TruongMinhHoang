"""Robust metric anomaly detectors used by the stable student API."""
from __future__ import annotations

import math
from typing import Any, Iterable

import numpy as np


_MAX_SCORE = 1.0e12


def _finite_values(values: Iterable[float]) -> tuple[np.ndarray, int]:
    try:
        raw = np.asarray(list(values), dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("history must contain numeric values") from exc
    finite = raw[np.isfinite(raw)]
    return finite, int(raw.size - finite.size)


def _current_value(current: float) -> float | None:
    try:
        value = float(current)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _invalid_current(method: str, current: Any) -> dict[str, Any]:
    return {
        "is_anomaly": True,
        "score": float(_MAX_SCORE),
        "method": method,
        "reason": f"invalid_current={current!r}",
    }


def _zero_spread_score(current: float, center: float) -> float:
    # A one-percent noise floor avoids paging on tiny float jitter while still
    # detecting meaningful deviation from a historically constant metric.
    scale = max(abs(center) * 0.01, 1.0e-9)
    return min(abs(current - center) / scale, _MAX_SCORE)


def zscore_detector(
    current: float, history: Iterable[float], threshold: float = 3.0
) -> dict[str, Any]:
    value = _current_value(current)
    if value is None:
        return _invalid_current("zscore", current)
    values, dropped = _finite_values(history)
    if values.size < 3:
        return {
            "is_anomaly": False,
            "score": 0.0,
            "method": "zscore",
            "reason": (
                f"insufficient_history; finite_count={values.size}; "
                f"dropped_nonfinite={dropped}"
            ),
        }
    mean = float(np.mean(values))
    std = float(np.std(values))
    score = (
        _zero_spread_score(value, mean)
        if std <= np.finfo(float).eps * max(1.0, abs(mean))
        else min(abs(value - mean) / std, _MAX_SCORE)
    )
    return {
        "is_anomaly": bool(score > threshold),
        "score": float(score),
        "method": "zscore",
        "reason": (
            f"mean={mean:.6g}; std={std:.6g}; threshold={threshold:.6g}; "
            f"finite_count={values.size}; dropped_nonfinite={dropped}"
        ),
    }


def mad_detector(
    current: float, history: Iterable[float], threshold: float = 3.5
) -> dict[str, Any]:
    value = _current_value(current)
    if value is None:
        return _invalid_current("mad", current)
    values, dropped = _finite_values(history)
    if values.size < 5:
        return {
            "is_anomaly": False,
            "score": 0.0,
            "method": "mad",
            "reason": (
                f"insufficient_history; finite_count={values.size}; "
                f"dropped_nonfinite={dropped}"
            ),
        }
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    score = (
        _zero_spread_score(value, median)
        if mad <= np.finfo(float).eps * max(1.0, abs(median))
        else min(0.67448975 * abs(value - median) / mad, _MAX_SCORE)
    )
    return {
        "is_anomaly": bool(score > threshold),
        "score": float(score),
        "method": "mad",
        "reason": (
            f"median={median:.6g}; mad={mad:.6g}; threshold={threshold:.6g}; "
            f"finite_count={values.size}; dropped_nonfinite={dropped}"
        ),
    }


def detect_anomaly(
    current: float,
    history: Iterable[float],
    *,
    method: str = "auto",
    threshold: float = 3.0,
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Detect a point anomaly and preserve explicit algorithms.

    Auto mode prefers a caller-provided same-segment baseline (for example the
    same weekday), then applies robust MAD when at least five finite values are
    available. Known events are retained as evidence; they do not erase the
    underlying data anomaly.
    """
    normalized_method = str(method).lower()
    if normalized_method == "zscore":
        return zscore_detector(current, history, threshold=threshold)
    if normalized_method == "mad":
        return mad_detector(current, history)
    if normalized_method != "auto":
        raise ValueError(f"Unsupported method: {method}")

    base_history = list(history)
    baseline_source = "history"
    if context:
        segment = context.get("same_segment_history")
        if segment is not None:
            segment_values = list(segment)
            finite_segment, _ = _finite_values(segment_values)
            if finite_segment.size >= 3:
                base_history = segment_values
                baseline_source = "same_segment_history"

    finite, _ = _finite_values(base_history)
    if finite.size >= 5:
        result = mad_detector(current, base_history, threshold=3.5)
        algorithm = "mad"
    else:
        result = zscore_detector(current, base_history, threshold=threshold)
        algorithm = "zscore"

    result["method"] = f"auto:{algorithm}"
    annotations = [f"baseline_source={baseline_source}"]
    if context:
        if context.get("metric_name") is not None:
            annotations.append(f"metric_name={context['metric_name']}")
        if context.get("day_of_week") is not None:
            annotations.append(f"day_of_week={context['day_of_week']}")
        if context.get("known_event") is not None:
            annotations.append(f"known_event={context['known_event']}")
    result["reason"] = f"{result['reason']}; " + "; ".join(annotations)
    result["is_anomaly"] = bool(result["is_anomaly"])
    result["score"] = float(result["score"])
    return result
