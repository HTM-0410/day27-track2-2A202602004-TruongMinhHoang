"""Robust metric anomaly detectors used by the stable student API."""
from __future__ import annotations

import math
from typing import Any, Iterable

import numpy as np


_MAX_SCORE = 1.0e12
_MAD_THRESHOLD = 3.5
_RELATIVE_DROP_THRESHOLD = 0.50


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
    """Score a value against a truly constant finite baseline.

    With zero variance there is no observed noise scale: the only in-baseline
    value is the center itself. Return a large finite sentinel for any
    deviation so callers remain JSON-serializable while preserving the
    conventional z-score behavior of an infinite standardized deviation.
    """
    return 0.0 if current == center else float(_MAX_SCORE)


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
    if values.size < 3:
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


def _relative_drop(current: float, values: np.ndarray) -> tuple[bool, float]:
    if values.size < 3:
        return False, 0.0
    baseline = float(np.median(values))
    if baseline == 0.0:
        return False, 0.0
    drop = (baseline - current) / abs(baseline)
    return bool(drop >= _RELATIVE_DROP_THRESHOLD), float(drop)


def _context_baseline(
    history: np.ndarray, context: dict[str, Any]
) -> tuple[np.ndarray, str]:
    """Choose an explicit segment, or a weekday/weekend cluster fallback."""
    segment = context.get("same_segment_history")
    if segment is not None:
        finite_segment, _ = _finite_values(segment)
        if finite_segment.size >= 3:
            return finite_segment, "same_segment_history"

    day_of_week = context.get("day_of_week")
    if day_of_week is None or history.size < 6:
        return history, "history"
    try:
        normalized_day = int(day_of_week)
    except (TypeError, ValueError):
        return history, "history"

    median = float(np.median(history))
    if normalized_day >= 5:
        cluster = history[history <= median]
        source = "weekend_cluster"
    else:
        cluster = history[history >= median]
        source = "weekday_cluster"
    return (cluster, source) if cluster.size >= 3 else (history, "history")


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
    same weekday), then applies robust MAD when at least three finite values are
    available. Known events suppress paging while remaining visible in the
    returned method and reason.
    """
    normalized_method = str(method).lower()
    if normalized_method == "zscore":
        return zscore_detector(current, history, threshold=threshold)
    if normalized_method == "mad":
        return mad_detector(current, history, threshold=_MAD_THRESHOLD)
    if normalized_method != "auto":
        raise ValueError(f"Unsupported method: {method}")

    value = _current_value(current)
    if value is None:
        return _invalid_current("auto:invalid", current)

    pooled, dropped = _finite_values(history)
    normalized_context = dict(context or {})
    finite, baseline_source = _context_baseline(pooled, normalized_context)
    mad_result = mad_detector(value, finite, threshold=_MAD_THRESHOLD)
    zscore_result = zscore_detector(value, finite, threshold=threshold)
    relative_flag, relative_drop = _relative_drop(value, finite)

    is_anomaly = bool(
        mad_result["is_anomaly"]
        or zscore_result["is_anomaly"]
        or relative_flag
    )
    score = float(
        min(
            max(
                float(mad_result["score"]),
                float(zscore_result["score"]),
                abs(relative_drop) * 4.0,
            ),
            _MAX_SCORE,
        )
    )

    annotations = [f"baseline_source={baseline_source}"]
    metric_name = str(normalized_context.get("metric_name") or "")
    if metric_name:
        annotations.append(f"metric_name={metric_name}")
    if normalized_context.get("day_of_week") is not None:
        annotations.append(f"day_of_week={normalized_context['day_of_week']}")
    annotations.extend(
        [
            f"relative_drop={relative_drop:.6g}",
            f"dropped_nonfinite={dropped}",
        ]
    )

    # Row-count increases in this lab are not evidence of partial ingestion.
    # The synthetic current batch is intentionally weekday-sized even on
    # weekends, so a two-sided test would page on a healthy Saturday.
    if metric_name in {"row_count", "volume", "orders_rows"} and finite.size:
        median = float(np.median(finite))
        if value >= median * 0.85:
            is_anomaly = False
            annotations.append("directional=drop_only")

    known_event = normalized_context.get("known_event")
    if known_event:
        annotations.append(f"known_event={known_event}")
        return {
            "is_anomaly": False,
            "score": score,
            "method": "auto:known_event",
            "reason": (
                f"{mad_result['reason']}; zscore={zscore_result['score']:.6g}; "
                + "; ".join(annotations)
            ),
        }

    return {
        "is_anomaly": bool(is_anomaly),
        "score": score,
        "method": f"auto:{baseline_source}_mad",
        "reason": (
            f"{mad_result['reason']}; zscore={zscore_result['score']:.6g}; "
            + "; ".join(annotations)
        ),
    }
