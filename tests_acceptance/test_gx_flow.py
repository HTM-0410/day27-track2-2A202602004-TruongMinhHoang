"""Integration checks for the GX Checkpoint/Action operational behavior."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

pytest.importorskip("great_expectations")

from gx import validate_orders as gx_orders


REFERENCE_TIME = pd.Timestamp("2026-08-28T10:30:00Z")


def _orders(*, duplicate: bool = False, warning_only: bool = False) -> pd.DataFrame:
    rows = [
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
    return pd.DataFrame(rows)


def _run_in_tmp(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, frame: pd.DataFrame
) -> tuple[int, dict, pd.DataFrame]:
    source = tmp_path / "orders.csv"
    evidence = tmp_path / "reports" / "gx_orders_validation.json"
    quarantine = tmp_path / "reports" / "gx_orders_critical_quarantine.csv"
    frame.to_csv(source, index=False)

    monkeypatch.setattr(gx_orders, "ROOT", tmp_path)
    monkeypatch.setattr(gx_orders, "SOURCE_PATH", source)
    monkeypatch.setattr(gx_orders, "GENERATED_DIR", evidence.parent)
    monkeypatch.setattr(gx_orders, "EVIDENCE_PATH", evidence)
    monkeypatch.setattr(gx_orders, "QUARANTINE_PATH", quarantine)

    exit_code = gx_orders.run(reference_time=REFERENCE_TIME)
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    quarantined = pd.read_csv(quarantine)
    return exit_code, payload, quarantined


def test_gx_warning_continues_without_quarantining_rows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    exit_code, evidence, quarantined = _run_in_tmp(
        monkeypatch, tmp_path, _orders(warning_only=True)
    )

    assert exit_code == 0
    assert evidence["decision"] == "warn"
    assert evidence["maximum_failure_severity"] == "warning"
    assert quarantined.empty


def test_gx_critical_failure_blocks_and_quarantines_exact_bad_rows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    exit_code, evidence, quarantined = _run_in_tmp(
        monkeypatch, tmp_path, _orders(duplicate=True)
    )

    assert exit_code == 1
    assert evidence["decision"] == "block"
    assert evidence["maximum_failure_severity"] == "critical"
    assert len(quarantined) == 2
    assert quarantined["__quarantine_reasons"].str.contains(
        "unique:order_id", regex=False
    ).all()
