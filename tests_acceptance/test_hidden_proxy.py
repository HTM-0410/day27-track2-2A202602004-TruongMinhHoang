"""Hard, deterministic proxy for the instructor-side hidden evaluation.

The cases in this module intentionally exercise only the nine stable functions
exported by ``student_api.py``.  They do not depend on repository sample data,
the wall clock, or implementation-specific detector names.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
import yaml

from student_api import (
    column_downstream,
    detect_distribution,
    detect_metric,
    downstream_assets,
    multiwindow_burn,
    rag_embedding_shift,
    rag_length_shift,
    slo_status,
    validate_orders,
)


ANOMALY_KEYS = {"is_anomaly", "score", "method", "reason"}


@pytest.fixture
def fixed_contract_path(tmp_path: Path) -> Path:
    """Contract with an explicit clock so freshness tests are reproducible."""
    contract = {
        "dataset": "orders",
        "owner": "acceptance-test",
        "freshness": {
            "column": "updated_at",
            "max_delay_minutes": 30,
            "reference_time": "2026-08-28T10:30:00Z",
            "severity": "warning",
            "action": "warn",
        },
        "columns": {
            "order_id": {
                "type": "integer",
                "required": True,
                "unique": True,
                "severity": "critical",
                "action": "block",
            },
            "customer_id": {
                "type": "string",
                "required": True,
                "severity": "critical",
                "action": "quarantine",
            },
            "amount": {
                "type": "number",
                "required": True,
                "min": 0,
                "severity": "critical",
                "action": "block",
            },
            "currency": {
                "type": "string",
                "required": True,
                "accepted_values": ["USD", "VND"],
                "severity": "critical",
                "action": "block",
            },
            "status": {
                "type": "string",
                "required": True,
                "accepted_values": ["pending", "completed", "refunded", "cancelled"],
                "severity": "warning",
                "action": "warn",
            },
            "created_at": {
                "type": "datetime",
                "required": True,
                "severity": "critical",
                "action": "block",
            },
            "updated_at": {
                "type": "datetime",
                "required": True,
                "severity": "critical",
                "action": "block",
            },
        },
    }
    path = tmp_path / "orders_contract.yaml"
    path.write_text(yaml.safe_dump(contract, sort_keys=False), encoding="utf-8")
    return path


@pytest.fixture
def healthy_orders() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "order_id": 101,
                "customer_id": "C001",
                "amount": 12.5,
                "currency": "USD",
                "status": "completed",
                "created_at": "2026-08-28T10:00:00Z",
                "updated_at": "2026-08-28T10:10:00Z",
            },
            {
                "order_id": 102,
                "customer_id": "C002",
                "amount": 7.0,
                "currency": "VND",
                "status": "pending",
                "created_at": "2026-08-28T10:05:00Z",
                "updated_at": "2026-08-28T10:15:00Z",
            },
        ]
    )


def _failed(issues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [issue for issue in issues if issue.get("passed") is False]


def _matching(
    issues: list[dict[str, Any]],
    *,
    check_contains: str,
    column: str | None,
    passed: bool | None = None,
) -> list[dict[str, Any]]:
    matches = [
        issue
        for issue in issues
        if check_contains in str(issue.get("check", "")).lower()
        and issue.get("column") == column
        and (passed is None or issue.get("passed") is passed)
    ]
    assert matches, (
        f"No issue matched check~={check_contains!r}, column={column!r}, "
        f"passed={passed!r}. Got: {issues!r}"
    )
    return matches


def _assert_action(issue: dict[str, Any], expected: str) -> None:
    assert str(issue.get("action", "")).lower() == expected


def _assert_anomaly_shape(result: dict[str, Any]) -> None:
    assert ANOMALY_KEYS <= result.keys()
    assert type(result["is_anomaly"]) is bool
    assert isinstance(result["score"], float) and not isinstance(result["score"], bool)
    assert isinstance(result["method"], str) and result["method"]
    assert isinstance(result["reason"], str) and result["reason"]


# 1/20
def test_contract_healthy_emits_type_and_freshness_checks_and_is_json_serializable(
    fixed_contract_path: Path, healthy_orders: pd.DataFrame
) -> None:
    issues = validate_orders(healthy_orders, fixed_contract_path)

    assert not _failed(issues)
    for column in healthy_orders.columns:
        _matching(issues, check_contains="type", column=column, passed=True)
    _matching(issues, check_contains="freshness", column="updated_at", passed=True)
    json.dumps(issues, allow_nan=False)

    nullable = healthy_orders.astype(
        {"order_id": "Int64", "amount": "Float64", "customer_id": "string"}
    )
    assert not _failed(validate_orders(nullable, fixed_contract_path))


# 2/20
def test_contract_missing_critical_column_preserves_severity_and_declared_action(
    fixed_contract_path: Path, healthy_orders: pd.DataFrame
) -> None:
    issues = validate_orders(healthy_orders.drop(columns=["order_id"]), fixed_contract_path)
    missing = _matching(
        issues, check_contains="required", column="order_id", passed=False
    )[0]

    assert missing["severity"] == "critical"
    _assert_action(missing, "block")


# 3/20
def test_contract_rejects_numeric_strings_as_type_drift(
    fixed_contract_path: Path, healthy_orders: pd.DataFrame
) -> None:
    drifted = healthy_orders.copy()
    drifted["order_id"] = drifted["order_id"].astype(str)
    drifted["amount"] = drifted["amount"].map(str)

    issues = validate_orders(drifted, fixed_contract_path)

    _matching(issues, check_contains="type", column="order_id", passed=False)
    _matching(issues, check_contains="type", column="amount", passed=False)

    nonfinite = healthy_orders.copy()
    nonfinite["amount"] = [float("inf"), float("nan")]
    issues = validate_orders(nonfinite, fixed_contract_path)
    _matching(issues, check_contains="type", column="amount", passed=False)
    _matching(issues, check_contains="not_null", column="amount", passed=False)
    _matching(issues, check_contains="range", column="amount", passed=False)

    booleans = healthy_orders.copy()
    booleans["order_id"] = booleans["order_id"].astype(object)
    booleans["amount"] = booleans["amount"].astype(object)
    booleans.loc[0, "order_id"] = True
    booleans.loc[0, "amount"] = False
    issues = validate_orders(booleans, fixed_contract_path)
    _matching(issues, check_contains="type", column="order_id", passed=False)
    _matching(issues, check_contains="type", column="amount", passed=False)


# 4/20
def test_contract_uses_fixed_reference_time_for_stale_freshness_warning(
    fixed_contract_path: Path, healthy_orders: pd.DataFrame
) -> None:
    stale = healthy_orders.copy()
    stale["updated_at"] = ["2026-08-28T07:50:00Z", "2026-08-28T08:00:00Z"]

    issues = validate_orders(stale, fixed_contract_path)
    freshness = _matching(
        issues, check_contains="freshness", column="updated_at", passed=False
    )[0]

    assert freshness["severity"] == "warning"
    _assert_action(freshness, "warn")

    future = healthy_orders.copy()
    future["updated_at"] = ["2026-08-28T10:40:00Z", "2026-08-28T17:41:00+07:00"]
    _matching(
        validate_orders(future, fixed_contract_path),
        check_contains="freshness",
        column="updated_at",
        passed=False,
    )


# 5/20
def test_contract_reports_multiple_rules_without_losing_per_rule_policy(
    fixed_contract_path: Path, healthy_orders: pd.DataFrame
) -> None:
    broken = healthy_orders.copy()
    broken.loc[1, "order_id"] = broken.loc[0, "order_id"]
    broken.loc[0, "customer_id"] = None
    broken.loc[0, "amount"] = -50.0
    broken.loc[0, "status"] = "unknown"

    issues = validate_orders(broken, fixed_contract_path)
    unique = _matching(issues, check_contains="unique", column="order_id", passed=False)[0]
    null = _matching(issues, check_contains="not_null", column="customer_id", passed=False)[0]
    amount = _matching(issues, check_contains="range", column="amount", passed=False)[0]
    status = _matching(
        issues, check_contains="accepted", column="status", passed=False
    )[0]

    assert unique["severity"] == amount["severity"] == "critical"
    assert null["severity"] == "critical"
    assert status["severity"] == "warning"
    _assert_action(unique, "block")
    _assert_action(null, "quarantine")
    _assert_action(status, "warn")


# 6/20
def test_anomaly_auto_uses_same_segment_history_to_avoid_seasonal_false_positive() -> None:
    mixed_history = [1000.0] * 30 + [100.0] * 2
    weekend_history = [95.0, 100.0, 105.0, 98.0, 102.0, 101.0]

    result = detect_metric(
        100.0,
        mixed_history,
        method="auto",
        context={
            "metric_name": "row_count",
            "day_of_week": 6,
            "same_segment_history": weekend_history,
        },
    )

    _assert_anomaly_shape(result)
    assert result["is_anomaly"] is False


# 7/20
def test_anomaly_auto_detects_change_when_robust_scale_is_zero() -> None:
    result = detect_metric(40.0, [100.0] * 8, method="auto")

    _assert_anomaly_shape(result)
    assert result["is_anomaly"] is True
    assert result["score"] > 0

    for invalid in [float("nan"), float("inf")]:
        result = detect_metric(invalid, [100.0] * 8, method="auto")
        _assert_anomaly_shape(result)
        assert result["is_anomaly"] is True
        assert math.isfinite(result["score"])


# 8/20
def test_anomaly_auto_is_not_masked_by_one_extreme_history_outlier() -> None:
    history = [100.0, 101.0, 99.0, 100.0, 102.0, 98.0, 100.0, 1000.0]

    result = detect_metric(180.0, (value for value in history), method="auto")

    _assert_anomaly_shape(result)
    assert result["is_anomaly"] is True
    assert result["score"] > 0


# 9/20
def test_distribution_detects_large_shape_shift_even_when_means_match() -> None:
    baseline = [9.0, 10.0, 11.0] * 40
    current = [0.0] * 60 + [20.0] * 60

    result = detect_distribution(
        (value for value in current), (value for value in baseline)
    )

    _assert_anomaly_shape(result)
    assert sum(current) / len(current) == pytest.approx(sum(baseline) / len(baseline))
    assert result["is_anomaly"] is True
    assert result["score"] > 0


# 10/20
def test_distribution_does_not_flag_the_same_distribution_in_different_order() -> None:
    baseline = [1.0, 2.0, 3.0, 5.0, 8.0, 13.0] * 10
    current = list(reversed(baseline))

    result = detect_distribution(current, baseline)

    _assert_anomaly_shape(result)
    assert result["is_anomaly"] is False

    invalid = detect_distribution([float("nan"), float("inf")], baseline)
    _assert_anomaly_shape(invalid)
    assert invalid["is_anomaly"] is True
    assert math.isfinite(invalid["score"])


# 11/20
def test_slo_exact_budget_boundary_is_not_a_breach() -> None:
    result = slo_status(0.99, bad_events=1, total_events=100)

    assert result["allowed_bad_rate"] == pytest.approx(0.01)
    assert result["actual_bad_rate"] == pytest.approx(0.01)
    assert result["burn_rate"] == pytest.approx(1.0)
    assert result["remaining_error_budget_fraction"] == pytest.approx(0.0)
    assert result["breached"] is False


# 12/20
def test_slo_rejects_invalid_targets_and_event_counts() -> None:
    invalid_calls = [
        (0.0, 0, 1),
        (1.0, 0, 1),
        (-0.1, 0, 1),
        (0.99, -1, 10),
        (0.99, 1, -1),
        (0.99, 11, 10),
        (float("nan"), 0, 1),
        (float("inf"), 0, 1),
        (0.99, 1.5, 10),
        (0.99, 1, 10.0),
    ]

    for target, bad_events, total_events in invalid_calls:
        with pytest.raises(ValueError):
            slo_status(target, bad_events, total_events)


# 13/20
def test_multiwindow_short_spike_without_long_burn_does_not_page() -> None:
    result = multiwindow_burn(short_window_burn=20.0, long_window_burn=1.0)

    assert result["page"] is False
    assert isinstance(result["severity"], str) and result["severity"]
    assert isinstance(result["reason"], str) and result["reason"]
    with pytest.raises(ValueError):
        multiwindow_burn(-1.0, 1.0)
    with pytest.raises(ValueError):
        multiwindow_burn(float("nan"), 1.0)


# 14/20
def test_multiwindow_sustained_fast_burn_pages_with_actionable_severity() -> None:
    result = multiwindow_burn(short_window_burn=20.0, long_window_burn=20.0)

    assert result["page"] is True
    assert str(result["severity"]).lower() not in {"", "info", "none", "ok"}
    assert isinstance(result["reason"], str) and result["reason"]


# 15/20
def test_downstream_assets_returns_bfs_order_and_deduplicates_a_diamond() -> None:
    graph = {
        "A": ["B", "C"],
        "B": ["D"],
        "C": ["D", "E"],
        "D": ["F"],
        "E": ["F"],
    }

    assert downstream_assets(graph, "A") == ["B", "C", "D", "E", "F"]


# 16/20
def test_downstream_assets_handles_cycles_without_returning_start() -> None:
    graph = {"A": ["B"], "B": ["C"], "C": ["A", "D"], "D": ["B"]}

    result = downstream_assets(graph, "A")

    assert result == ["B", "C", "D"]
    assert "A" not in result
    assert downstream_assets(graph, "missing") == []
    with pytest.raises((TypeError, ValueError)):
        downstream_assets({"A": "BC"}, "A")
    with pytest.raises((TypeError, ValueError)):
        downstream_assets({"A": None}, "A")
    with pytest.raises((TypeError, ValueError)):
        downstream_assets(["A", "B"], "A")  # type: ignore[arg-type]
    assert downstream_assets({"dataset_lineage": graph}, "A") == ["B", "C", "D"]


# 17/20
def test_column_downstream_is_transitive_deduplicated_and_cycle_safe() -> None:
    graph = {
        "raw.amount": ["stg.amount_usd", "audit.raw_amount"],
        "stg.amount_usd": ["mart.daily_revenue"],
        "audit.raw_amount": ["mart.daily_revenue"],
        "mart.daily_revenue": ["dashboard.revenue"],
        "dashboard.revenue": ["stg.amount_usd"],
    }

    assert column_downstream(graph, "raw.amount") == [
        "stg.amount_usd",
        "audit.raw_amount",
        "mart.daily_revenue",
        "dashboard.revenue",
    ]


# 18/20
def test_rag_length_metric_distinguishes_stable_batch_from_length_collapse() -> None:
    baseline_batch_means = [38.0, 39.0, 40.0, 41.0, 42.0, 40.0, 39.0]
    stable_texts = [" ".join(["token"] * 40) for _ in range(5)]
    collapsed_texts = ["two words", "tiny text", "very short"]

    stable = rag_length_shift(stable_texts, baseline_batch_means)
    shifted = rag_length_shift(collapsed_texts, baseline_batch_means)

    _assert_anomaly_shape(stable)
    _assert_anomaly_shape(shifted)
    assert stable["is_anomaly"] is False
    assert shifted["is_anomaly"] is True

    unicode_text = " ".join(["chính_sách"] * 40)
    single_document = rag_length_shift(unicode_text, baseline_batch_means)
    empty_batch = rag_length_shift([], baseline_batch_means)
    assert single_document["document_count"] == 1
    assert single_document["is_anomaly"] is False
    assert empty_batch["is_anomaly"] is True


# 19/20
def test_rag_embedding_metric_does_not_flag_stable_norms() -> None:
    baseline = [0.97, 0.99, 1.00, 1.01, 1.03, 1.00, 0.98]
    current = [0.99, 1.00, 1.01, 1.00]

    result = rag_embedding_shift(
        (value for value in current), (value for value in baseline)
    )

    _assert_anomaly_shape(result)
    assert result["is_anomaly"] is False


# 20/20
def test_rag_embedding_metric_detects_large_norm_shift() -> None:
    baseline = [0.97, 0.99, 1.00, 1.01, 1.03, 1.00, 0.98]
    current = [2.40, 2.50, 2.60, 2.55]

    result = rag_embedding_shift(current, baseline)

    _assert_anomaly_shape(result)
    assert result["is_anomaly"] is True
    assert result["score"] > 0

    for invalid_current in [[], [float("nan"), float("inf")]]:
        invalid = rag_embedding_shift(invalid_current, baseline)
        _assert_anomaly_shape(invalid)
        assert invalid["is_anomaly"] is True
        assert math.isfinite(invalid["score"])
