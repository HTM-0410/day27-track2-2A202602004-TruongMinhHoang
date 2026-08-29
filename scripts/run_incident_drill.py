#!/usr/bin/env python3
"""Run a deterministic, non-destructive mystery-incident proxy.

The drill never mutates incoming/baseline data. It constructs two evidence-led
faults in memory: a partial orders ingestion and a stale KB publication batch.
The raw before/incident/recovery evidence is committed for report traceability.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from datetime import timedelta
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


EVIDENCE_PATH = ROOT / "reports" / "evidence" / "incident_drill.json"


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _freshness_failure(issues: list[dict[str, Any]]) -> dict[str, Any] | None:
    return next(
        (
            issue
            for issue in failed_issues(issues)
            if issue.get("check") == "freshness"
        ),
        None,
    )


def _row_count_signal(
    count: int,
    history: pd.DataFrame,
    weekday: int,
) -> dict[str, Any]:
    segment = history.loc[
        history["day_of_week"] == weekday, "row_count"
    ].tolist()
    return detect_anomaly(
        count,
        history["row_count"].tolist(),
        method="auto",
        context={
            "metric_name": "row_count",
            "day_of_week": weekday,
            "same_segment_history": segment,
        },
    )


def _document_signals(
    docs: list[dict[str, Any]], history: pd.DataFrame
) -> dict[str, Any]:
    contents = [str(doc.get("content", "")) for doc in docs]
    unit_norms = [1.0 if content.strip() else 0.0 for content in contents]
    return {
        "text_length": detect_text_length_shift(
            contents, history["mean_text_length"].tail(14).tolist()
        ),
        "embedding_norm": detect_embedding_norm_shift(
            unit_norms, history["embedding_norm_mean"].tail(14).tolist()
        ),
        "embedding_input": "unit_norm_proxy_for_nonempty_content",
    }


def main() -> None:
    paths = {
        "orders": ROOT / "data" / "baseline" / "orders.csv",
        "kb": ROOT / "data" / "baseline" / "kb_documents.jsonl",
        "history": ROOT / "data" / "history" / "metrics_history.csv",
        "lineage": ROOT / "data" / "baseline" / "lineage_graph.json",
        "orders_contract": ROOT / "contracts" / "orders_contract.yaml",
        "kb_contract": ROOT / "contracts" / "kb_contract.yaml",
        "config": ROOT / "lab_config.yaml",
    }
    orders = pd.read_csv(paths["orders"])
    docs = load_jsonl(paths["kb"])
    history = pd.read_csv(paths["history"])
    orders_contract = load_contract(paths["orders_contract"])
    kb_contract = load_contract(paths["kb_contract"])
    config = load_contract(paths["config"])

    latest_order = pd.to_datetime(
        orders["updated_at"], format="mixed", utc=True, errors="raise"
    ).max()
    latest_kb = pd.to_datetime(
        [doc["published_at"] for doc in docs],
        format="mixed",
        utc=True,
        errors="raise",
    ).max()
    # Five minutes after the newest source event makes both baseline datasets
    # healthy while keeping the drill clock stable across executions.
    reference = max(latest_order, latest_kb) + timedelta(minutes=5)
    weekday = reference.weekday()

    healthy_order_issues = validate_dataframe(
        orders, orders_contract, reference_time=reference
    )
    healthy_kb_issues = validate_dataframe(
        pd.DataFrame(docs), kb_contract, reference_time=reference
    )
    healthy_row_signal = _row_count_signal(len(orders), history, weekday)

    incident_orders = orders.iloc[: max(10, int(len(orders) * 0.25))].copy()
    incident_docs = copy.deepcopy(docs)
    for doc in incident_docs:
        published = pd.to_datetime(doc["published_at"], utc=True)
        doc["published_at"] = (published - timedelta(hours=3)).isoformat()

    incident_order_issues = validate_dataframe(
        incident_orders, orders_contract, reference_time=reference
    )
    incident_kb_issues = validate_dataframe(
        pd.DataFrame(incident_docs), kb_contract, reference_time=reference
    )
    incident_row_signal = _row_count_signal(
        len(incident_orders), history, weekday
    )
    incident_document_signals = _document_signals(incident_docs, history)

    lineage_payload = json.loads(paths["lineage"].read_text(encoding="utf-8"))
    graph = lineage_payload["dataset_lineage"]
    order_blast_radius = get_downstream_assets(graph, "raw_orders")
    kb_blast_radius = get_downstream_assets(graph, "kb_documents")

    revenue_target = float(config["slo"]["revenue_freshness"]["target"])
    rag_target = float(config["slo"]["rag_index_freshness"]["target"])
    critical_target = float(config["slo"]["critical_contract_pass"]["target"])
    revenue_slo = calculate_slo(revenue_target, bad_events=1, total_events=1)
    rag_slo = calculate_slo(rag_target, bad_events=1, total_events=1)
    critical_slo = calculate_slo(
        critical_target,
        bad_events=int(
            bool(failed_issues(incident_order_issues, "critical"))
            or bool(failed_issues(incident_kb_issues, "critical"))
        ),
        total_events=1,
    )

    # Explicit drill event counts, not production measurements.
    allowed = 1.0 - revenue_target
    short_window = {"bad_events": 4, "total_events": 20}
    long_window = {"bad_events": 10, "total_events": 100}
    short_burn = (
        short_window["bad_events"] / short_window["total_events"] / allowed
    )
    long_burn = long_window["bad_events"] / long_window["total_events"] / allowed
    burn_decision = evaluate_multiwindow_burn(
        short_window_burn=short_burn,
        long_window_burn=long_burn,
    )

    recovery_order_issues = validate_dataframe(
        orders, orders_contract, reference_time=reference
    )
    recovery_kb_issues = validate_dataframe(
        pd.DataFrame(docs), kb_contract, reference_time=reference
    )
    recovery_row_signal = _row_count_signal(len(orders), history, weekday)
    recovery_burn = evaluate_multiwindow_burn(
        short_window_burn=0.0, long_window_burn=0.0
    )

    evidence = {
        "artifact_schema_version": 1,
        "drill_type": "deterministic_in_memory_partial_ingestion_and_stale_kb",
        "reference_time_utc": reference.isoformat(),
        "input_hashes": {
            key: _hash(path) for key, path in paths.items()
        },
        "healthy": {
            "orders_rows": int(len(orders)),
            "orders_failed_contracts": failed_issues(healthy_order_issues),
            "kb_failed_contracts": failed_issues(healthy_kb_issues),
            "row_count_signal": healthy_row_signal,
        },
        "incident": {
            "what_happened": [
                "orders batch contains 25 percent of the healthy row volume",
                "KB published_at values lag the healthy source by three hours",
            ],
            "orders_rows": int(len(incident_orders)),
            "orders_volume_ratio": float(len(incident_orders) / len(orders)),
            "orders_failed_contracts": failed_issues(incident_order_issues),
            "kb_failed_contracts": failed_issues(incident_kb_issues),
            "kb_freshness_failure": _freshness_failure(incident_kb_issues),
            "row_count_signal": incident_row_signal,
            "rag_signals": incident_document_signals,
            "blast_radius": {
                "orders": order_blast_radius,
                "kb_documents": kb_blast_radius,
            },
            "slo": {
                "revenue_freshness": revenue_slo,
                "rag_index_freshness": rag_slo,
                "critical_contract_pass": critical_slo,
            },
            "multiwindow": {
                "source": "explicit_synthetic_drill_event_counts",
                "short_window": short_window,
                "long_window": long_window,
                "decision": burn_decision,
            },
        },
        "recovery": {
            "orders_failed_contracts": failed_issues(recovery_order_issues),
            "kb_failed_contracts": failed_issues(recovery_kb_issues),
            "row_count_signal": recovery_row_signal,
            "multiwindow": recovery_burn,
        },
    }

    # Acceptance invariants make this artifact reproducible evidence rather
    # than a self-authored success claim.
    assert not evidence["healthy"]["orders_failed_contracts"]
    assert not evidence["healthy"]["kb_failed_contracts"]
    assert healthy_row_signal["is_anomaly"] is False
    assert not failed_issues(incident_order_issues, "critical")
    assert incident_row_signal["is_anomaly"] is True
    assert evidence["incident"]["orders_volume_ratio"] == 0.25
    assert evidence["incident"]["kb_freshness_failure"] is not None
    assert burn_decision["page"] is True
    assert order_blast_radius[-1] == "ceo_revenue_dashboard"
    assert kb_blast_radius[-1] == "support_agent"
    assert not evidence["recovery"]["orders_failed_contracts"]
    assert not evidence["recovery"]["kb_failed_contracts"]
    assert recovery_row_signal["is_anomaly"] is False
    assert recovery_burn["page"] is False

    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_PATH.write_text(
        json.dumps(
            evidence,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print("Incident drill: PASS")
    print(f"Evidence: {EVIDENCE_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
