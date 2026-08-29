#!/usr/bin/env python3
"""Sinh minh chứng GX warning/critical/quarantine bằng Checkpoint thật."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from gx import validate_orders as gx_orders


REFERENCE_TIME = pd.Timestamp("2026-08-28T10:30:00Z")
EVIDENCE_PATH = ROOT / "reports" / "evidence" / "gx_action_drill.json"


def _orders(*, duplicate: bool = False, warning_only: bool = False) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "order_id": 1,
                "customer_id": "C1",
                "amount": 10.0,
                "currency": "USD",
                "status": "unknown" if warning_only else "completed",
                "created_at": "2026-08-28T10:20:00Z",
                "updated_at": "2026-08-28T10:25:00Z",
            },
            {
                "order_id": 1 if duplicate else 2,
                "customer_id": "C2",
                "amount": 20.0,
                "currency": "USD",
                "status": "pending",
                "created_at": "2026-08-28T10:21:00Z",
                "updated_at": "2026-08-28T10:26:00Z",
            },
        ]
    )


def _run_case(case_root: Path, frame: pd.DataFrame) -> dict[str, Any]:
    case_root.mkdir(parents=True, exist_ok=True)
    source = case_root / "orders.csv"
    generated = case_root / "generated"
    evidence_path = generated / "gx_orders_validation.json"
    quarantine_path = generated / "gx_orders_critical_quarantine.csv"
    frame.to_csv(source, index=False, lineterminator="\n")

    names = (
        "ROOT",
        "SOURCE_PATH",
        "GENERATED_DIR",
        "EVIDENCE_PATH",
        "QUARANTINE_PATH",
    )
    original = {name: getattr(gx_orders, name) for name in names}
    try:
        gx_orders.ROOT = case_root
        gx_orders.SOURCE_PATH = source
        gx_orders.GENERATED_DIR = generated
        gx_orders.EVIDENCE_PATH = evidence_path
        gx_orders.QUARANTINE_PATH = quarantine_path
        with contextlib.redirect_stdout(io.StringIO()):
            exit_code = gx_orders.run(reference_time=REFERENCE_TIME)
    finally:
        for name, value in original.items():
            setattr(gx_orders, name, value)

    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    quarantine = pd.read_csv(quarantine_path)
    failed_rules = [
        item["rule_id"]
        for item in evidence["expectations"]
        if not item["success"]
    ]
    return {
        "exit_code": int(exit_code),
        "decision": evidence["decision"],
        "maximum_failure_severity": evidence["maximum_failure_severity"],
        "failed_rules": failed_rules,
        "quarantine_row_count": int(len(quarantine)),
        "quarantine_rows": json.loads(quarantine.to_json(orient="records")),
        "checkpoint_evidence": evidence,
    }


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="day27-gx-action-") as temp:
        temp_root = Path(temp)
        warning = _run_case(
            temp_root / "warning",
            _orders(warning_only=True),
        )
        critical = _run_case(
            temp_root / "critical",
            _orders(duplicate=True),
        )

    assert warning["exit_code"] == 0
    assert warning["decision"] == "warn"
    assert warning["maximum_failure_severity"] == "warning"
    assert warning["quarantine_row_count"] == 0
    assert critical["exit_code"] == 1
    assert critical["decision"] == "block"
    assert critical["maximum_failure_severity"] == "critical"
    assert critical["quarantine_row_count"] == 2
    assert all(
        "unique:order_id" in row["__quarantine_reasons"]
        for row in critical["quarantine_rows"]
    )

    payload = {
        "artifact_schema_version": 1,
        "reference_time_utc": REFERENCE_TIME.isoformat(),
        "warning_case": warning,
        "critical_case": critical,
        "acceptance": {
            "warning_continues_without_quarantine": True,
            "critical_blocks_pipeline": True,
            "duplicate_rows_quarantined": 2,
        },
    }
    EVIDENCE_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE_PATH.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print("Minh chứng GX Action: PASS")
    print(f"Tệp: {EVIDENCE_PATH.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
