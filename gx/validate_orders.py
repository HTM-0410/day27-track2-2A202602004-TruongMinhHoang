#!/usr/bin/env python3
"""Run the orders contract as a production-shaped GX Core 1.21 flow.

Configuration is ephemeral. Deterministic operational artifacts are written to
``reports/generated`` only when the CLI runs. Critical failures quarantine the
affected source rows and block; warning/info failures remain non-blocking.
"""
from __future__ import annotations

import hashlib
import json
import math
import numbers
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "data" / "incoming" / "orders.csv"
GENERATED_DIR = ROOT / "reports" / "generated"
EVIDENCE_PATH = GENERATED_DIR / "gx_orders_validation.json"
QUARANTINE_PATH = GENERATED_DIR / "gx_orders_critical_quarantine.csv"

SUITE_NAME = "orders_contract_suite"
DATA_SOURCE_NAME = "orders_pandas"
DATA_ASSET_NAME = "orders_dataframe"
BATCH_DEFINITION_NAME = "whole_orders"
VALIDATION_DEFINITION_NAME = "orders_validation_definition"
CHECKPOINT_NAME = "orders_reliability_checkpoint"

EXPECTED_COLUMNS = (
    "order_id",
    "customer_id",
    "amount",
    "currency",
    "status",
    "created_at",
    "updated_at",
)

SEVERITY_POLICY = {
    "critical": {"action": "quarantine", "pipeline": "block"},
    "warning": {"action": "warn", "pipeline": "continue"},
    "info": {"action": "observe", "pipeline": "continue"},
}

try:
    import great_expectations as gx
    from great_expectations.checkpoint import (
        ActionContext,
        CheckpointResult,
        ValidationAction,
    )
except ImportError as exc:  # Keep the module importable for local static tests.
    gx = None  # type: ignore[assignment]
    _GX_IMPORT_ERROR: ImportError | None = exc
else:
    _GX_IMPORT_ERROR = None


def _is_missing(value: Any) -> bool:
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _is_integer(value: Any) -> bool:
    return _is_missing(value) or (
        isinstance(value, numbers.Integral) and not isinstance(value, (bool, np.bool_))
    )


def _is_finite_number(value: Any) -> bool:
    if _is_missing(value):
        return True
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, numbers.Real):
        return False
    return math.isfinite(float(value))


def _is_string(value: Any) -> bool:
    return _is_missing(value) or isinstance(value, str)


def _column_or_missing(df: pd.DataFrame, column: str) -> pd.Series:
    if column in df.columns:
        return df[column]
    return pd.Series(pd.NA, index=df.index, dtype="object")


def _parse_utc(series: pd.Series) -> pd.Series:
    # Pandas 2.x needs format="mixed" for batches containing both Z and offsets.
    return pd.to_datetime(series, errors="coerce", utc=True, format="mixed")


def _epoch_seconds(parsed: pd.Series) -> pd.Series:
    result = pd.Series(np.nan, index=parsed.index, dtype="float64")
    valid = parsed.notna()
    if valid.any():
        result.loc[valid] = parsed.loc[valid].astype("int64") / 1_000_000_000
    return result


def prepare_validation_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Add helper columns used by portable Pandas Expectations."""
    frame = df.copy()
    order_id = _column_or_missing(frame, "order_id")
    amount = _column_or_missing(frame, "amount")
    customer_id = _column_or_missing(frame, "customer_id")
    currency = _column_or_missing(frame, "currency")
    status = _column_or_missing(frame, "status")
    created_at = _column_or_missing(frame, "created_at")
    updated_at = _column_or_missing(frame, "updated_at")

    frame["__order_id_type_valid"] = order_id.map(_is_integer)
    frame["__amount_type_valid"] = amount.map(_is_finite_number)
    frame["__customer_id_type_valid"] = customer_id.map(_is_string)
    frame["__currency_type_valid"] = currency.map(_is_string)
    frame["__status_type_valid"] = status.map(_is_string)

    amount_numeric = pd.to_numeric(amount, errors="coerce").astype("float64")
    frame["__amount_numeric"] = amount_numeric.where(np.isfinite(amount_numeric))

    created_parsed = _parse_utc(created_at)
    updated_parsed = _parse_utc(updated_at)
    frame["__created_at_valid"] = created_at.isna() | created_parsed.notna()
    frame["__updated_at_valid"] = updated_at.isna() | updated_parsed.notna()
    frame["__updated_at_epoch_seconds"] = _epoch_seconds(updated_parsed)
    return frame


def _add_reason(
    reasons: list[list[str]], mask: pd.Series | np.ndarray | list[bool], reason: str
) -> None:
    values = np.asarray(mask, dtype=bool)
    for position in np.flatnonzero(values):
        reasons[int(position)].append(reason)


def critical_quarantine_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Return source rows that violate a critical rule, with stable reasons."""
    reasons: list[list[str]] = [[] for _ in range(len(df))]
    all_rows = np.ones(len(df), dtype=bool)

    for column in (
        "order_id",
        "customer_id",
        "amount",
        "currency",
        "created_at",
        "updated_at",
    ):
        if column not in df.columns:
            _add_reason(reasons, all_rows, f"missing_required_column:{column}")
        else:
            _add_reason(reasons, df[column].isna(), f"not_null:{column}")

    if "order_id" in df.columns:
        order_id = df["order_id"]
        _add_reason(reasons, ~order_id.map(_is_integer), "type:order_id")
        _add_reason(
            reasons,
            order_id.notna() & order_id.duplicated(keep=False),
            "unique:order_id",
        )

    if "customer_id" in df.columns:
        _add_reason(reasons, ~df["customer_id"].map(_is_string), "type:customer_id")

    if "amount" in df.columns:
        amount = df["amount"]
        numeric = pd.to_numeric(amount, errors="coerce")
        type_invalid = ~amount.map(_is_finite_number)
        nonfinite = numeric.notna() & ~np.isfinite(numeric.astype(float))
        _add_reason(reasons, type_invalid | nonfinite, "type:amount")
        _add_reason(reasons, numeric.notna() & (numeric < 0), "range:amount")

    if "currency" in df.columns:
        currency = df["currency"]
        _add_reason(reasons, ~currency.map(_is_string), "type:currency")
        _add_reason(
            reasons,
            currency.notna() & ~currency.isin(["USD", "VND"]),
            "accepted_values:currency",
        )

    for column in ("created_at", "updated_at"):
        if column in df.columns:
            values = df[column]
            parsed = _parse_utc(values)
            _add_reason(reasons, values.notna() & parsed.isna(), f"datetime:{column}")

    selected = np.asarray([bool(items) for items in reasons], dtype=bool)
    positions = np.flatnonzero(selected)
    quarantined = df.iloc[positions].copy()
    quarantined.insert(0, "__source_row_number", positions + 2)
    quarantined["__quarantine_reasons"] = [
        ";".join(reasons[position]) for position in positions
    ]
    return quarantined


def _severity_name(value: Any) -> str | None:
    if value is None:
        return None
    text = str(getattr(value, "value", value)).lower()
    for severity in ("critical", "warning", "info"):
        if severity in text:
            return severity
    return text


def _expectation_config_dict(config: Any) -> dict[str, Any]:
    if config is None:
        return {}
    if hasattr(config, "to_json_dict"):
        return dict(config.to_json_dict())
    if isinstance(config, dict):
        return dict(config)
    return {}


def _iter_validation_results(checkpoint_result: Any):
    for suite_result in getattr(checkpoint_result, "run_results", {}).values():
        for result in getattr(suite_result, "results", []) or []:
            yield result


def _failed_severity(result: Any) -> str | None:
    if bool(getattr(result, "success", False)):
        return None
    exception_info = getattr(result, "exception_info", None) or {}
    if exception_info.get("raised_exception"):
        return "critical"
    config = _expectation_config_dict(getattr(result, "expectation_config", None))
    return _severity_name(config.get("severity")) or "critical"


def maximum_failure_severity(checkpoint_result: Any) -> str | None:
    order = {None: -1, "info": 0, "warning": 1, "critical": 2}
    maximum: str | None = None
    for result in _iter_validation_results(checkpoint_result):
        severity = _failed_severity(result)
        if order.get(severity, 2) > order.get(maximum, -1):
            maximum = severity
    return maximum


def _write_quarantine(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    df.to_csv(
        temporary,
        index=False,
        lineterminator="\n",
        na_rep="<NULL>",
        float_format="%.12g",
    )
    temporary.replace(path)


if gx is not None:

    class QuarantineCriticalRowsAction(ValidationAction):
        """GX 1.21 Action that materializes critical source rows."""

        type: Literal["quarantine_critical_rows"] = "quarantine_critical_rows"
        source_path: str
        quarantine_path: str

        def run(
            self,
            checkpoint_result: CheckpointResult,
            action_context: ActionContext | None = None,
        ) -> dict[str, Any]:
            del action_context
            raw = pd.read_csv(Path(self.source_path))
            critical_failed = maximum_failure_severity(checkpoint_result) == "critical"
            quarantined = critical_quarantine_rows(raw) if critical_failed else raw.iloc[0:0].copy()

            # A failed GX execution is critical even if no row rule can identify
            # it. Fail closed and retain the whole batch for investigation.
            if critical_failed and quarantined.empty and not raw.empty:
                quarantined = raw.copy()
                quarantined.insert(0, "__source_row_number", np.arange(len(raw)) + 2)
                quarantined["__quarantine_reasons"] = "gx_critical_execution_failure"
            elif "__source_row_number" not in quarantined.columns:
                quarantined.insert(0, "__source_row_number", pd.Series(dtype="int64"))
                quarantined["__quarantine_reasons"] = pd.Series(dtype="object")

            target = Path(self.quarantine_path)
            _write_quarantine(quarantined, target)
            return {
                "critical_failure": critical_failed,
                "quarantined_rows": int(len(quarantined)),
                "quarantine_path": str(target),
            }

else:

    class QuarantineCriticalRowsAction:  # pragma: no cover - dependency fallback
        def __init__(self, *_: Any, **__: Any) -> None:
            raise RuntimeError("Great Expectations 1.21.0 is required")


def _rule_meta(rule_id: str, severity: str, source_column: str | None = None) -> dict[str, Any]:
    policy = SEVERITY_POLICY[severity]
    meta: dict[str, Any] = {
        "rule_id": rule_id,
        "on_failure": policy["action"],
        "pipeline_effect": policy["pipeline"],
    }
    if source_column:
        meta["source_column"] = source_column
    return meta


def build_expectation_suite(context: Any, freshness_cutoff: pd.Timestamp) -> Any:
    """Create and register the orders Expectation Suite."""
    assert gx is not None
    suite = gx.ExpectationSuite(name=SUITE_NAME)

    def add(expectation: Any) -> None:
        suite.add_expectation(expectation)

    common = {"catch_exceptions": True}
    add(
        gx.expectations.ExpectTableColumnsToMatchSet(
            column_set=list(EXPECTED_COLUMNS),
            exact_match=False,
            severity="critical",
            meta=_rule_meta("orders.required_columns", "critical"),
            **common,
        )
    )
    add(
        gx.expectations.ExpectTableRowCountToBeBetween(
            min_value=1,
            severity="critical",
            meta=_rule_meta("orders.nonempty_batch", "critical"),
            **common,
        )
    )

    column_severities = {
        "order_id": "critical",
        "customer_id": "critical",
        "amount": "critical",
        "currency": "critical",
        "status": "warning",
        "created_at": "critical",
        "updated_at": "critical",
    }
    for column, severity in column_severities.items():
        add(
            gx.expectations.ExpectColumnValuesToNotBeNull(
                column=column,
                severity=severity,
                meta=_rule_meta(f"orders.{column}.not_null", severity, column),
                **common,
            )
        )

    specifications = [
        gx.expectations.ExpectColumnValuesToBeUnique(
            column="order_id",
            severity="critical",
            meta=_rule_meta("orders.order_id.unique", "critical", "order_id"),
            **common,
        ),
        gx.expectations.ExpectColumnValuesToBeInSet(
            column="__order_id_type_valid",
            value_set=[True],
            severity="critical",
            meta=_rule_meta("orders.order_id.integer", "critical", "order_id"),
            **common,
        ),
        gx.expectations.ExpectColumnValuesToBeInSet(
            column="__customer_id_type_valid",
            value_set=[True],
            severity="critical",
            meta=_rule_meta("orders.customer_id.string", "critical", "customer_id"),
            **common,
        ),
        gx.expectations.ExpectColumnValuesToBeInSet(
            column="__amount_type_valid",
            value_set=[True],
            severity="critical",
            meta=_rule_meta("orders.amount.number", "critical", "amount"),
            **common,
        ),
        gx.expectations.ExpectColumnValuesToBeBetween(
            column="__amount_numeric",
            min_value=0,
            severity="critical",
            meta=_rule_meta("orders.amount.nonnegative", "critical", "amount"),
            **common,
        ),
        gx.expectations.ExpectColumnValuesToBeInSet(
            column="__currency_type_valid",
            value_set=[True],
            severity="critical",
            meta=_rule_meta("orders.currency.string", "critical", "currency"),
            **common,
        ),
        gx.expectations.ExpectColumnValuesToBeInSet(
            column="currency",
            value_set=["USD", "VND"],
            severity="critical",
            meta=_rule_meta("orders.currency.accepted", "critical", "currency"),
            **common,
        ),
        gx.expectations.ExpectColumnValuesToBeInSet(
            column="__status_type_valid",
            value_set=[True],
            severity="warning",
            meta=_rule_meta("orders.status.string", "warning", "status"),
            **common,
        ),
        gx.expectations.ExpectColumnValuesToBeInSet(
            column="status",
            value_set=["pending", "completed", "refunded", "cancelled"],
            severity="warning",
            meta=_rule_meta("orders.status.accepted", "warning", "status"),
            **common,
        ),
        gx.expectations.ExpectColumnValuesToBeInSet(
            column="__created_at_valid",
            value_set=[True],
            severity="critical",
            meta=_rule_meta("orders.created_at.datetime", "critical", "created_at"),
            **common,
        ),
        gx.expectations.ExpectColumnValuesToBeInSet(
            column="__updated_at_valid",
            value_set=[True],
            severity="critical",
            meta=_rule_meta("orders.updated_at.datetime", "critical", "updated_at"),
            **common,
        ),
        gx.expectations.ExpectColumnMaxToBeBetween(
            column="__updated_at_epoch_seconds",
            min_value=float(freshness_cutoff.timestamp()),
            severity="warning",
            meta=_rule_meta("orders.updated_at.freshness_30m", "warning", "updated_at"),
            **common,
        ),
    ]
    for expectation in specifications:
        add(expectation)
    return context.suites.add(suite)


def build_checkpoint(context: Any, freshness_cutoff: pd.Timestamp) -> Any:
    """Register Data Source, Suite, Validation Definition, and Checkpoint."""
    assert gx is not None
    data_source = context.data_sources.add_pandas(DATA_SOURCE_NAME)
    asset = data_source.add_dataframe_asset(name=DATA_ASSET_NAME)
    batch_definition = asset.add_batch_definition_whole_dataframe(BATCH_DEFINITION_NAME)
    suite = build_expectation_suite(context, freshness_cutoff)
    validation_definition = context.validation_definitions.add(
        gx.ValidationDefinition(
            name=VALIDATION_DEFINITION_NAME,
            data=batch_definition,
            suite=suite,
        )
    )
    checkpoint = gx.Checkpoint(
        name=CHECKPOINT_NAME,
        validation_definitions=[validation_definition],
        actions=[
            QuarantineCriticalRowsAction(
                name="quarantine_critical_orders",
                source_path=str(SOURCE_PATH),
                quarantine_path=str(QUARANTINE_PATH),
            )
        ],
        result_format={
            "result_format": "COMPLETE",
            "partial_unexpected_count": 20,
            "unexpected_index_column_names": ["order_id"],
        },
    )
    return context.checkpoints.add(checkpoint)


def _json_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, numbers.Real):
        numeric = float(value)
        return numeric if math.isfinite(numeric) else str(numeric)
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_json_scalar(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_scalar(item) for key, item in value.items()}
    return str(value)


def expectation_evidence(checkpoint_result: Any) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for result in _iter_validation_results(checkpoint_result):
        config = _expectation_config_dict(getattr(result, "expectation_config", None))
        kwargs = config.get("kwargs") or {}
        meta = config.get("meta") or {}
        details = getattr(result, "result", None) or {}
        exception_info = getattr(result, "exception_info", None) or {}
        configured = _severity_name(config.get("severity")) or "critical"
        effective = _failed_severity(result) or configured
        policy = SEVERITY_POLICY.get(effective, SEVERITY_POLICY["critical"])
        evidence.append(
            {
                "action_on_failure": policy["action"],
                "column": kwargs.get("column"),
                "effective_severity": effective,
                "exception_raised": bool(exception_info.get("raised_exception", False)),
                "expectation_type": config.get("type") or config.get("expectation_type"),
                "observed_value": _json_scalar(details.get("observed_value")),
                "pipeline_effect": policy["pipeline"],
                "rule_id": meta.get("rule_id"),
                "success": bool(getattr(result, "success", False)),
                "unexpected_count": int(details.get("unexpected_count", 0) or 0),
            }
        )
    return sorted(
        evidence,
        key=lambda item: (
            str(item.get("rule_id") or ""),
            str(item.get("expectation_type") or ""),
        ),
    )


def build_evidence(
    checkpoint_result: Any, raw_df: pd.DataFrame, source_path: Path
) -> dict[str, Any]:
    expectations = expectation_evidence(checkpoint_result)
    maximum = maximum_failure_severity(checkpoint_result)
    critical_failed = maximum == "critical"
    quarantined = critical_quarantine_rows(raw_df) if critical_failed else raw_df.iloc[0:0]
    quarantine_count = len(raw_df) if critical_failed and quarantined.empty and not raw_df.empty else len(quarantined)
    decision = "block" if critical_failed else ("warn" if maximum else "pass")
    return {
        "artifact_schema_version": 1,
        "checkpoint": CHECKPOINT_NAME,
        "data_asset": DATA_ASSET_NAME,
        "data_source": DATA_SOURCE_NAME,
        "decision": decision,
        "expectation_suite": SUITE_NAME,
        "expectations": expectations,
        "gx_version": getattr(gx, "__version__", "1.21.0"),
        "maximum_failure_severity": maximum,
        "quarantine": {
            "path": QUARANTINE_PATH.relative_to(ROOT).as_posix(),
            "row_count": int(quarantine_count),
        },
        "severity_policy": SEVERITY_POLICY,
        "source": {
            "path": source_path.relative_to(ROOT).as_posix(),
            "row_count": int(len(raw_df)),
            "sha256": hashlib.sha256(source_path.read_bytes()).hexdigest(),
        },
        "validation_definition": VALIDATION_DEFINITION_NAME,
    }


def _write_json(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
        allow_nan=False,
    ) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(serialized, encoding="utf-8")
    temporary.replace(path)


def run(reference_time: pd.Timestamp | None = None) -> int:
    if gx is None:
        print(
            "Great Expectations 1.21.0 is required for this validation flow. "
            "Run: pip install -r requirements.txt",
            file=sys.stderr,
        )
        if _GX_IMPORT_ERROR is not None:
            print(f"Import error: {_GX_IMPORT_ERROR}", file=sys.stderr)
        return 2
    if not SOURCE_PATH.exists():
        print(f"Orders input not found: {SOURCE_PATH}", file=sys.stderr)
        return 2

    raw_df = pd.read_csv(SOURCE_PATH)
    validation_df = prepare_validation_frame(raw_df)
    now = reference_time or pd.Timestamp.now(tz="UTC")
    if now.tzinfo is None:
        now = now.tz_localize("UTC")
    else:
        now = now.tz_convert("UTC")
    context = gx.get_context(mode="ephemeral")
    checkpoint = build_checkpoint(context, now - timedelta(minutes=30))
    checkpoint_result = checkpoint.run(batch_parameters={"dataframe": validation_df})

    evidence = build_evidence(checkpoint_result, raw_df, SOURCE_PATH)
    _write_json(evidence, EVIDENCE_PATH)
    for item in evidence["expectations"]:
        state = "PASS" if item["success"] else "FAIL"
        print(
            f"{item['rule_id'] or item['expectation_type']:<42} "
            f"{state:<4} severity={item['effective_severity']} "
            f"action={item['action_on_failure']}"
        )

    decision = evidence["decision"]
    if decision == "block":
        print(f"\nGX result: FAIL_CRITICAL; quarantined={evidence['quarantine']['row_count']}")
    elif decision == "warn":
        print("\nGX result: PASS_WITH_WARNINGS")
    else:
        print("\nGX result: PASS")
    print(f"Evidence: {EVIDENCE_PATH.relative_to(ROOT)}")
    print(f"Quarantine: {QUARANTINE_PATH.relative_to(ROOT)}")
    return 1 if decision == "block" else 0


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
