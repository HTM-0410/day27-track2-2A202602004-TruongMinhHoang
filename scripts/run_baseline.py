#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from observability.anomaly import detect_anomaly
from observability.lineage import get_downstream_assets
from observability.rag_metrics import (
    detect_embedding_norm_shift,
    detect_text_length_shift,
)
from observability.slo import calculate_slo, evaluate_multiwindow_burn
from src.contract_validator import failed_issues, load_contract, validate_dataframe
from src.io_utils import load_jsonl


def _file_evidence(paths: dict[str, Path]) -> dict[str, dict[str, Any]]:
    """Hash the exact local inputs used by this run without recording content."""
    evidence: dict[str, dict[str, Any]] = {}
    for name, path in paths.items():
        payload = path.read_bytes()
        evidence[name] = {
            "path": path.relative_to(ROOT).as_posix(),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
        }
    return evidence


def _freshness_minutes(values: pd.Series, reference_time: datetime) -> float | None:
    parsed = pd.to_datetime(values, format="mixed", utc=True, errors="coerce")
    latest = parsed.max()
    if pd.isna(latest):
        return None
    return float((pd.Timestamp(reference_time) - latest).total_seconds() / 60.0)


def _kb_embedding_norms(docs: list[dict[str, Any]]) -> tuple[list[Any], str]:
    """Use supplied norms when present, otherwise a documented unit-norm proxy.

    Normalized embedding models produce vectors with norm approximately one.
    The proxy keeps the lab offline and still detects empty-document collapse;
    it is not a semantic embedding-drift measurement.
    """
    values: list[Any] = []
    supplied = 0
    for doc in docs:
        if "embedding_norm" in doc:
            values.append(doc["embedding_norm"])
            supplied += 1
        else:
            values.append(1.0 if str(doc.get("content", "")).strip() else 0.0)
    if supplied == len(docs) and docs:
        source = "document_embedding_norm"
    elif supplied:
        source = "mixed_document_norm_and_unit_norm_proxy"
    else:
        source = "unit_norm_proxy_for_nonempty_content"
    return values, source


def _multiwindow_contract_signal(
    history: pd.DataFrame,
    *,
    target: float,
    current_contract_burn: float,
) -> dict[str, Any]:
    """Build a reproducible two-window signal from the available local history.

    The lab history has no historical contract-check event stream, so its
    ``null_rate`` is explicitly labeled as a proxy rather than presented as a
    measured contract SLI.
    """
    rates = pd.to_numeric(
        history.get("null_rate", pd.Series(dtype=float)), errors="coerce"
    )
    rates = rates.dropna()
    allowed_bad_rate = 1.0 - target
    short_rates = rates.tail(3)
    long_rates = rates.tail(14)
    historical_short_burn = (
        float(short_rates.median()) / allowed_bad_rate if len(short_rates) else 0.0
    )
    historical_long_burn = (
        float(long_rates.median()) / allowed_bad_rate if len(long_rates) else 0.0
    )
    short_burn = max(float(current_contract_burn), historical_short_burn)
    result = evaluate_multiwindow_burn(
        short_window_burn=short_burn,
        long_window_burn=historical_long_burn,
    )
    result.update(
        {
            "source": "current_contract_check_plus_historical_null_rate_proxy",
            "short_window_points": int(len(short_rates)),
            "long_window_points": int(len(long_rates)),
            "historical_short_burn": float(historical_short_burn),
            "current_contract_burn": float(current_contract_burn),
        }
    )
    return result


def main() -> None:
    # Capture one UTC clock for every time-sensitive check in this run.
    reference_time = datetime.now(timezone.utc)
    input_paths = {
        "orders": ROOT / "data" / "incoming" / "orders.csv",
        "customers": ROOT / "data" / "incoming" / "customers.csv",
        "kb_documents": ROOT / "data" / "incoming" / "kb_documents.jsonl",
        "metrics_history": ROOT / "data" / "history" / "metrics_history.csv",
        "orders_contract": ROOT / "contracts" / "orders_contract.yaml",
        "kb_contract": ROOT / "contracts" / "kb_contract.yaml",
        "lab_config": ROOT / "lab_config.yaml",
        "lineage_graph": ROOT / "data" / "baseline" / "lineage_graph.json",
    }
    input_hashes = _file_evidence(input_paths)

    orders = pd.read_csv(input_paths["orders"])
    history = pd.read_csv(input_paths["metrics_history"])
    docs = load_jsonl(input_paths["kb_documents"])

    orders_contract = load_contract(input_paths["orders_contract"])
    kb_contract = load_contract(input_paths["kb_contract"])
    order_issues = validate_dataframe(
        orders, orders_contract, reference_time=reference_time
    )
    kb_issues = validate_dataframe(
        pd.DataFrame(docs), kb_contract, reference_time=reference_time
    )
    order_failed = failed_issues(order_issues)
    kb_failed = failed_issues(kb_issues)
    order_critical_failed = failed_issues(order_issues, min_severity="critical")
    kb_critical_failed = failed_issues(kb_issues, min_severity="critical")
    all_critical_failed = order_critical_failed + kb_critical_failed

    current_dow = reference_time.weekday()
    same_segment_history = history.loc[
        history["day_of_week"] == current_dow, "row_count"
    ].tail(8).tolist()
    row_history = history["row_count"].tail(28).tolist()
    row_result = detect_anomaly(
        len(orders),
        row_history,
        method="auto",
        context={
            "metric_name": "row_count",
            "day_of_week": current_dow,
            "same_segment_history": same_segment_history,
        },
    )

    orders_freshness_minutes = (
        _freshness_minutes(orders["updated_at"], reference_time)
        if "updated_at" in orders.columns
        else None
    )
    kb_frame = pd.DataFrame(docs)
    kb_freshness_minutes = (
        _freshness_minutes(kb_frame["published_at"], reference_time)
        if "published_at" in kb_frame.columns
        else None
    )
    text_result = detect_text_length_shift(
        [d.get("content", "") for d in docs],
        history["mean_text_length"].tail(14).tolist(),
    )
    current_embedding_norms, embedding_norm_source = _kb_embedding_norms(docs)
    embedding_result = detect_embedding_norm_shift(
        current_embedding_norms,
        history["embedding_norm_mean"].tail(14).tolist(),
    )

    config = load_contract(input_paths["lab_config"])
    contract_target = float(config["slo"]["critical_contract_pass"]["target"])
    bad = 1 if all_critical_failed else 0
    contract_slo = calculate_slo(contract_target, bad_events=bad, total_events=1)
    multiwindow_signal = _multiwindow_contract_signal(
        history,
        target=contract_target,
        current_contract_burn=contract_slo["burn_rate"],
    )

    with open(input_paths["lineage_graph"], "r", encoding="utf-8") as f:
        lineage = json.load(f)["dataset_lineage"]
    blast_radius = get_downstream_assets(lineage, "stg_orders")

    report = {
        "report_schema_version": 2,
        "timestamp": reference_time.isoformat(),
        "reference_time_utc": reference_time.isoformat(),
        "input_hashes": input_hashes,
        "orders_rows": int(len(orders)),
        "failed_contract_checks": len(order_failed),
        "critical_contract_failures": len(order_critical_failed),
        "failed_contract_issues": {
            "orders": order_failed,
            "kb_documents": kb_failed,
        },
        "failed_contract_issue_count": int(len(order_failed) + len(kb_failed)),
        "critical_contract_failure_count": int(len(all_critical_failed)),
        "row_count_anomaly": row_result,
        "row_count_same_segment_points": int(len(same_segment_history)),
        "freshness_minutes": orders_freshness_minutes,
        "orders_freshness_minutes": orders_freshness_minutes,
        "kb_freshness_minutes": kb_freshness_minutes,
        "kb_text_length_signal": text_result,
        "kb_embedding_signal": embedding_result,
        "kb_embedding_norm_source": embedding_norm_source,
        "contract_slo": contract_slo,
        "multiwindow_burn_signal": multiwindow_signal,
        "sample_blast_radius_from_stg_orders": blast_radius,
    }
    out = ROOT / "reports" / "latest_metrics.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            report,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        ),
        encoding="utf-8",
    )

    print("=== DATA RELIABILITY BASELINE ===")
    print(f"reference UTC            : {reference_time.isoformat()}")
    print(f"orders rows              : {len(orders)}")
    print(f"orders contract failures : {len(order_failed)}")
    print(f"KB contract failures     : {len(kb_failed)}")
    print(f"critical contract fails  : {len(all_critical_failed)}")
    print(f"row-count anomaly        : {row_result['is_anomaly']} ({row_result['method']}, score={row_result['score']:.2f})")
    print(f"orders freshness minutes : {orders_freshness_minutes if orders_freshness_minutes is not None else 'unknown'}")
    print(f"KB freshness minutes     : {kb_freshness_minutes if kb_freshness_minutes is not None else 'unknown'}")
    print(f"KB length anomaly        : {text_result['is_anomaly']}")
    print(f"KB embedding anomaly     : {embedding_result['is_anomaly']} ({embedding_norm_source})")
    print(f"multiwindow page         : {multiwindow_signal['page']} ({multiwindow_signal['severity']})")
    print(f"sample blast radius      : {', '.join(blast_radius)}")
    print(f"report                    : {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
