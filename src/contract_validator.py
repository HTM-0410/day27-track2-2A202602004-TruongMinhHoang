"""Deterministic, severity-aware dataframe contract validation.

The validator deliberately does not coerce numeric strings into numbers: a
batch can be numerically coercible and still represent a schema/type drift.
Datetime strings are accepted because CSV is an input format for this lab.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from numbers import Integral, Real
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


_ACTION_BY_SEVERITY = {
    "critical": "block",
    "warning": "warn",
    "info": "observe",
}


def _issue(
    check: str,
    *,
    column: str | None,
    severity: str,
    passed: bool,
    details: str,
    action: str | None = None,
) -> dict[str, Any]:
    normalized_severity = str(severity).lower()
    return {
        "check": check,
        "column": column,
        "severity": normalized_severity,
        "action": action or _ACTION_BY_SEVERITY.get(normalized_severity, "warn"),
        "passed": bool(passed),
        "details": str(details),
    }


def load_contract(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        payload = yaml.safe_load(f)
    if not isinstance(payload, dict):
        raise ValueError("contract must be a YAML mapping")
    return payload


def _parse_datetimes(series: pd.Series) -> pd.Series:
    """Parse mixed ISO-8601 values into a single UTC dtype."""
    try:
        return pd.to_datetime(series, format="mixed", utc=True, errors="coerce")
    except TypeError:  # Compatibility with older supported pandas releases.
        return pd.to_datetime(series, utc=True, errors="coerce")


def _valid_type_mask(series: pd.Series, declared_type: str) -> pd.Series:
    non_null = series.notna()
    kind = declared_type.strip().lower()

    if kind in {"datetime", "timestamp"}:
        return ~non_null | _parse_datetimes(series).notna()

    def valid_scalar(value: Any) -> bool:
        if pd.isna(value):
            return True
        if kind in {"integer", "int"}:
            return isinstance(value, Integral) and not isinstance(value, bool)
        if kind in {"number", "numeric", "float"}:
            return (
                isinstance(value, Real)
                and not isinstance(value, bool)
                and math.isfinite(float(value))
            )
        if kind in {"string", "str"}:
            return isinstance(value, str)
        if kind in {"boolean", "bool"}:
            return isinstance(value, bool)
        return False

    return series.map(valid_scalar).astype(bool)


def _reference_timestamp(
    df: pd.DataFrame,
    freshness: dict[str, Any],
    explicit: datetime | pd.Timestamp | str | None,
) -> pd.Timestamp:
    # A declared/ascribed clock makes contract tests deterministic. Production
    # callers can omit it and get the real UTC wall clock.
    candidate = explicit
    if candidate is None:
        candidate = freshness.get("reference_time")
    if candidate is None:
        candidate = df.attrs.get("validation_time")
    if candidate is None:
        candidate = datetime.now(timezone.utc)
    parsed = pd.to_datetime(candidate, utc=True, errors="coerce")
    if pd.isna(parsed):
        raise ValueError("freshness reference_time must be a valid datetime")
    return pd.Timestamp(parsed)


def validate_dataframe(
    df: pd.DataFrame,
    contract: dict[str, Any],
    *,
    reference_time: datetime | pd.Timestamp | str | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(df, pd.DataFrame):
        raise TypeError("df must be a pandas DataFrame")
    if not isinstance(contract, dict):
        raise TypeError("contract must be a mapping")

    issues: list[dict[str, Any]] = []
    columns = contract.get("columns", contract.get("fields", {}))
    if not isinstance(columns, dict):
        raise ValueError("contract columns/fields must be a mapping")

    for column, raw_rules in columns.items():
        if not isinstance(raw_rules, dict):
            raise ValueError(f"rules for {column!r} must be a mapping")
        rules = raw_rules
        severity = str(rules.get("severity", "warning")).lower()
        action = rules.get("action")
        required = bool(rules.get("required", False))

        if column not in df.columns:
            if required:
                issues.append(
                    _issue(
                        "required_column",
                        column=column,
                        severity=severity,
                        action=action,
                        passed=False,
                        details=f"Missing required column: {column}",
                    )
                )
            continue

        series = df[column]

        if required:
            null_count = int(series.isna().sum())
            issues.append(
                _issue(
                    "not_null",
                    column=column,
                    severity=severity,
                    action=action,
                    passed=(null_count == 0),
                    details=f"null_count={null_count}",
                )
            )

        if rules.get("unique"):
            duplicate_mask = series.notna() & series.duplicated(keep=False)
            duplicate_count = int(duplicate_mask.sum())
            issues.append(
                _issue(
                    "unique",
                    column=column,
                    severity=severity,
                    action=action,
                    passed=(duplicate_count == 0),
                    details=f"duplicate_rows={duplicate_count}",
                )
            )

        declared_type = rules.get("type")
        if declared_type is not None:
            type_mask = _valid_type_mask(series, str(declared_type))
            invalid_type_count = int((series.notna() & ~type_mask).sum())
            issues.append(
                _issue(
                    "type",
                    column=column,
                    severity=severity,
                    action=action,
                    passed=(invalid_type_count == 0),
                    details=(
                        f"expected={declared_type}; "
                        f"invalid_count={invalid_type_count}"
                    ),
                )
            )

        accepted = rules.get("accepted_values")
        if accepted is not None:
            if isinstance(accepted, (str, bytes)) or not isinstance(accepted, list):
                raise ValueError(f"accepted_values for {column!r} must be a list")
            invalid_mask = series.notna() & ~series.isin(accepted)
            invalid_count = int(invalid_mask.sum())
            issues.append(
                _issue(
                    "accepted_values",
                    column=column,
                    severity=severity,
                    action=action,
                    passed=(invalid_count == 0),
                    details=f"invalid_count={invalid_count}; accepted={accepted}",
                )
            )

        if "min" in rules or "max" in rules:
            numeric = pd.to_numeric(series, errors="coerce")
            finite = numeric.map(lambda value: pd.isna(value) or math.isfinite(float(value)))
            invalid = series.notna() & (numeric.isna() | ~finite)
            if "min" in rules:
                invalid |= numeric < rules["min"]
            if "max" in rules:
                invalid |= numeric > rules["max"]
            invalid_count = int(invalid.fillna(False).sum())
            issues.append(
                _issue(
                    "range",
                    column=column,
                    severity=severity,
                    action=action,
                    passed=(invalid_count == 0),
                    details=f"invalid_count={invalid_count}",
                )
            )

        if "min_length" in rules:
            invalid = series.notna() & series.map(
                lambda value: not isinstance(value, str)
                or len(value.strip()) < int(rules["min_length"])
            )
            invalid_count = int(invalid.sum())
            issues.append(
                _issue(
                    "min_length",
                    column=column,
                    severity=severity,
                    action=action,
                    passed=(invalid_count == 0),
                    details=(
                        f"min_length={int(rules['min_length'])}; "
                        f"invalid_count={invalid_count}"
                    ),
                )
            )

    freshness = contract.get("freshness")
    if freshness is not None:
        if not isinstance(freshness, dict):
            raise ValueError("contract freshness must be a mapping")
        column = freshness.get("column")
        severity = str(freshness.get("severity", "warning")).lower()
        action = freshness.get("action")
        max_delay = float(freshness.get("max_delay_minutes", 0))
        max_future = float(freshness.get("max_future_minutes", 5))

        if not column or column not in df.columns:
            issues.append(
                _issue(
                    "freshness",
                    column=str(column) if column else None,
                    severity=severity,
                    action=action,
                    passed=False,
                    details="freshness column is missing",
                )
            )
        elif max_delay < 0 or max_future < 0:
            raise ValueError("freshness delays must be non-negative")
        else:
            parsed = _parse_datetimes(df[column])
            latest = parsed.max()
            reference = _reference_timestamp(df, freshness, reference_time)
            if pd.isna(latest):
                passed = False
                details = "no valid freshness timestamps"
            else:
                delay_minutes = float((reference - latest).total_seconds() / 60.0)
                passed = -max_future <= delay_minutes <= max_delay
                details = (
                    f"latest={latest.isoformat()}; reference={reference.isoformat()}; "
                    f"delay_minutes={delay_minutes:.3f}; "
                    f"max_delay_minutes={max_delay:.3f}; "
                    f"max_future_minutes={max_future:.3f}"
                )
            issues.append(
                _issue(
                    "freshness",
                    column=str(column),
                    severity=severity,
                    action=action,
                    passed=passed,
                    details=details,
                )
            )

    return issues


def failed_issues(
    issues: list[dict[str, Any]], min_severity: str | None = None
) -> list[dict[str, Any]]:
    failed = [issue for issue in issues if not issue.get("passed", False)]
    if min_severity is None:
        return failed
    order = {"info": 0, "warning": 1, "critical": 2}
    normalized = min_severity.lower()
    if normalized not in order:
        raise ValueError(f"unknown severity: {min_severity}")
    threshold = order[normalized]
    return [
        issue
        for issue in failed
        if order.get(str(issue.get("severity", "warning")).lower(), 1) >= threshold
    ]
