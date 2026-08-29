from __future__ import annotations

from typing import Any, Iterable

import numpy as np

from observability.anomaly import detect_anomaly
from observability.distribution import detect_distribution_shift


def _text_batch(texts: Iterable[str] | str) -> list[Any]:
    # A single string is one document, not an iterable of characters.
    if isinstance(texts, str):
        return [texts]
    if texts is None:
        return []
    return list(texts)


def approximate_token_lengths(texts: Iterable[str] | str) -> list[int]:
    lengths: list[int] = []
    for value in _text_batch(texts):
        if value is None:
            lengths.append(0)
        else:
            lengths.append(len(str(value).split()))
    return lengths


def detect_text_length_shift(
    current_texts: Iterable[str] | str,
    baseline_batch_means: Iterable[float],
    *,
    threshold: float = 3.0,
) -> dict[str, Any]:
    lengths = approximate_token_lengths(current_texts)
    current_mean = float(np.mean(lengths)) if lengths else 0.0
    result = detect_anomaly(
        current_mean,
        baseline_batch_means,
        method="auto",
        context={"metric_name": "mean_text_length"},
        threshold=threshold,
    )
    result["metric"] = "mean_text_length"
    result["current_mean"] = float(current_mean)
    result["document_count"] = int(len(lengths))
    return result


def detect_embedding_norm_shift(
    current_norms: Iterable[float], baseline_norms: Iterable[float]
) -> dict[str, Any]:
    """Detect embedding-space collapse or drift from precomputed vector norms."""
    current = list(current_norms)
    baseline = list(baseline_norms)
    result = detect_distribution_shift(current, baseline)

    finite_current: list[float] = []
    invalid_current = 0
    for value in current:
        try:
            number = float(value)
        except (TypeError, ValueError):
            invalid_current += 1
            continue
        if np.isfinite(number) and number >= 0:
            finite_current.append(number)
        else:
            invalid_current += 1

    if baseline and not finite_current:
        result = {
            "is_anomaly": True,
            "score": 1.0e12,
            "method": "ks+robust_effect",
            "reason": "missing_or_invalid_current_embeddings",
        }
    elif invalid_current:
        result["is_anomaly"] = True
        result["score"] = float(max(float(result["score"]), 1.0))
        result["reason"] = (
            f"{result['reason']}; invalid_current_norms={invalid_current}"
        )

    result["is_anomaly"] = bool(result["is_anomaly"])
    result["score"] = float(result["score"])
    result["metric"] = "embedding_norm_distribution"
    result["current_count"] = int(len(current))
    result["baseline_count"] = int(len(baseline))
    return result
