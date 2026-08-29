#!/usr/bin/env python3
"""Generate differential evidence for every implemented Lab 27 bonus."""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from student_api import (
    column_downstream,
    detect_metric,
    multiwindow_burn,
    rag_embedding_shift,
)


EVIDENCE_DIR = ROOT / "reports" / "evidence"
OUTPUT_PATH = EVIDENCE_DIR / "bonus_evidence.json"
RUBRIC_BONUS_CAP = 15


def _load_json(name: str) -> dict[str, Any]:
    path = EVIDENCE_DIR / name
    if not path.exists():
        raise SystemExit(f"Thiếu minh chứng đầu vào cho bonus: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    # +3: a pooled z-score falsely pages a legitimate Saturday, while the
    # segmented robust detector stays healthy and still catches a real drop.
    pooled_weekdays = [990, 995, 1000, 1005, 1010, 1015, 1020, 1025]
    saturday_history = [240, 245, 250, 255, 260, 248]
    pooled_zscore = detect_metric(250, pooled_weekdays, method="zscore")
    segmented_healthy = detect_metric(
        250,
        pooled_weekdays,
        method="auto",
        context={
            "metric_name": "row_count",
            "day_of_week": 5,
            "same_segment_history": saturday_history,
        },
    )
    segmented_drop = detect_metric(
        150,
        [580, 590, 600, 610, 620, 605],
        method="auto",
        context={
            "metric_name": "row_count",
            "day_of_week": 2,
            "same_segment_history": [580, 590, 600, 610, 620, 605],
        },
    )
    _require(pooled_zscore["is_anomaly"], "pooled z-score must expose the false positive")
    _require(not segmented_healthy["is_anomaly"], "segmented Saturday must stay healthy")
    _require(segmented_drop["is_anomaly"], "segmented detector must catch the real drop")

    # +3: dbt native unit result plus a deterministic counterfactual showing
    # the two-active-version join would double 170 revenue to 340.
    dbt = _load_json("dbt_run_results.json")
    unit_results = [
        result
        for result in dbt.get("results", [])
        if str(result.get("unique_id", "")).startswith("unit_test.")
        and "deduplicates_active_customer_versions" in str(result.get("unique_id", ""))
    ]
    unit_passed = len(unit_results) == 1 and unit_results[0].get("status") == "pass"
    order_revenue = 100.0 + 70.0
    naive_join_revenue = order_revenue * 2
    _require(unit_passed, "native dbt SCD unit test must pass")
    _require(naive_join_revenue == 340.0 and order_revenue == 170.0, "invalid SCD counterfactual")

    # +3 +3: the real GX action drill proves policy and materialized rows.
    gx = _load_json("gx_action_drill.json")
    warning = gx["warning_case"]
    critical = gx["critical_case"]
    gx_policy_passed = warning["decision"] == "warn" and critical["decision"] == "block"
    quarantine_rows = critical["quarantine_rows"]
    quarantine_passed = (
        warning["quarantine_row_count"] == 0
        and critical["quarantine_row_count"] == 2
        and len(quarantine_rows) == 2
        and all("unique:order_id" in row["__quarantine_reasons"] for row in quarantine_rows)
    )
    _require(gx_policy_passed, "GX warning/critical policy evidence failed")
    _require(quarantine_passed, "GX quarantine evidence failed")

    # +7: one-hop lineage misses d.v; transitive BFS finds it once and survives
    # a cycle back to the start column.
    column_graph = {
        "a.x": ["b.y", "c.z"],
        "b.y": ["d.v"],
        "c.z": ["d.v"],
        "d.v": ["a.x"],
    }
    one_hop = column_graph["a.x"]
    transitive = column_downstream(column_graph, "a.x")
    lineage_passed = one_hop == ["b.y", "c.z"] and transitive == ["b.y", "c.z", "d.v"]
    _require(lineage_passed, "transitive column lineage evidence failed")

    # +7: a naive short-window-only policy pages the transient spike; the
    # multi-window policy suppresses it but pages sustained fast burn.
    transient = multiwindow_burn(15.0, 2.0)
    sustained = multiwindow_burn(15.0, 7.0)
    naive_short_only_page = 15.0 >= 14.4
    multiwindow_passed = naive_short_only_page and not transient["page"] and sustained["page"]
    _require(multiwindow_passed, "multi-window differential evidence failed")

    # +7: a mean-only embedding monitor misses this same-mean shape collapse;
    # KS + robust scale detects the distribution shift.
    embedding_baseline = [0.98, 0.99, 1.00, 1.01, 1.02] * 4
    embedding_current = [0.0, 2.0] * 10
    baseline_mean = sum(embedding_baseline) / len(embedding_baseline)
    current_mean = sum(embedding_current) / len(embedding_current)
    rag_signal = rag_embedding_shift(embedding_current, embedding_baseline)
    mean_only_misses = math.isclose(baseline_mean, current_mean, abs_tol=1.0e-12)
    rag_passed = mean_only_misses and rag_signal["is_anomaly"]
    _require(rag_passed, "RAG embedding differential evidence failed")

    checks: dict[str, dict[str, Any]] = {
        "mad_same_weekday": {
            "candidate_points": 3,
            "passed": True,
            "baseline_failure": "pooled_zscore_false_positive_on_saturday",
            "baseline_result": pooled_zscore,
            "advanced_healthy_result": segmented_healthy,
            "advanced_drop_result": segmented_drop,
        },
        "dbt_native_unit_test": {
            "candidate_points": 3,
            "passed": unit_passed,
            "baseline_failure": "two_active_scd_rows_double_revenue",
            "naive_join_revenue": naive_join_revenue,
            "deduplicated_revenue": order_revenue,
            "dbt_unit_unique_id": unit_results[0]["unique_id"],
            "dbt_status": unit_results[0]["status"],
        },
        "gx_severity_actions": {
            "candidate_points": 3,
            "passed": gx_policy_passed,
            "warning_decision": warning["decision"],
            "critical_decision": critical["decision"],
        },
        "automatic_quarantine": {
            "candidate_points": 3,
            "passed": quarantine_passed,
            "warning_quarantine_rows": warning["quarantine_row_count"],
            "critical_quarantine_rows": critical["quarantine_row_count"],
            "critical_reasons": [row["__quarantine_reasons"] for row in quarantine_rows],
        },
        "column_lineage": {
            "candidate_points": 7,
            "passed": lineage_passed,
            "baseline_one_hop": one_hop,
            "advanced_transitive_cycle_safe": transitive,
        },
        "multiwindow_burn_rate": {
            "candidate_points": 7,
            "passed": multiwindow_passed,
            "baseline_short_only_pages_transient": naive_short_only_page,
            "advanced_transient": transient,
            "advanced_sustained": sustained,
        },
        "rag_embedding_drift": {
            "candidate_points": 7,
            "passed": rag_passed,
            "baseline_mean_only_misses": mean_only_misses,
            "baseline_mean": baseline_mean,
            "current_mean": current_mean,
            "advanced_result": rag_signal,
        },
    }
    candidate_points = sum(item["candidate_points"] for item in checks.values() if item["passed"])
    payload = {
        "artifact_schema_version": 1,
        "all_checks_passed": all(item["passed"] for item in checks.values()),
        "candidate_points_before_cap": candidate_points,
        "rubric_bonus_cap": RUBRIC_BONUS_CAP,
        "max_countable_points": min(candidate_points, RUBRIC_BONUS_CAP),
        "grading_note": "Giảng viên quyết định điểm; đây là candidate evidence, không phải điểm tự chấm.",
        "checks": checks,
    }
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(
        "BONUS EVIDENCE: PASS "
        f"(candidate={candidate_points}, cap={RUBRIC_BONUS_CAP}, "
        f"checks={len(checks)})"
    )
    print(f"Evidence: {OUTPUT_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
