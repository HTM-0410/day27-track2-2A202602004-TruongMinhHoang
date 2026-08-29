#!/usr/bin/env python3
from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data" / "baseline"
INCOMING = ROOT / "data" / "incoming"
HISTORY = ROOT / "data" / "history" / "metrics_history.csv"


def shift_dataframe_timestamps(
    df: pd.DataFrame,
    columns: list[str],
    target_age_minutes: int = 5,
    *,
    reference_time: datetime | None = None,
) -> pd.DataFrame:
    parsed: list[pd.Series] = []
    for col in columns:
        if col in df.columns:
            parsed.append(
                pd.to_datetime(df[col], format="mixed", utc=True, errors="coerce")
            )
    latest_values = [series.max() for series in parsed if series.notna().any()]
    if not latest_values:
        return df
    latest = max(latest_values)
    now = reference_time or datetime.now(timezone.utc)
    target = pd.Timestamp(now - timedelta(minutes=target_age_minutes))
    delta = target - latest
    for col in columns:
        if col in df.columns:
            s = pd.to_datetime(
                df[col], format="mixed", utc=True, errors="coerce"
            )
            df[col] = (s + delta).dt.strftime("%Y-%m-%dT%H:%M:%S%z")
    return df


def weekday_median_row_count(
    history: pd.DataFrame, weekday: int, fallback: int
) -> tuple[int, int]:
    """Return a deterministic healthy count and the supporting point count."""
    if "day_of_week" not in history.columns or "row_count" not in history.columns:
        return fallback, 0
    day = pd.to_numeric(history["day_of_week"], errors="coerce")
    counts = pd.to_numeric(history["row_count"], errors="coerce")
    segment = counts.loc[day == weekday].dropna()
    if segment.empty:
        return fallback, 0
    target = int(round(float(segment.median())))
    if target <= 0:
        raise ValueError(f"weekday median row_count must be positive, got {target}")
    return target, int(len(segment))


def resize_orders(orders: pd.DataFrame, target_rows: int) -> pd.DataFrame:
    """Resize deterministically while preserving the order primary-key contract."""
    if target_rows <= 0:
        raise ValueError("target_rows must be positive")
    if orders.empty:
        raise ValueError("cannot resize an empty baseline orders dataset")
    if "order_id" not in orders.columns:
        raise ValueError("baseline orders dataset is missing order_id")
    if target_rows <= len(orders):
        return orders.iloc[:target_rows].copy().reset_index(drop=True)

    additional = target_rows - len(orders)
    repeats = (additional + len(orders) - 1) // len(orders)
    extra = pd.concat([orders] * repeats, ignore_index=True).iloc[:additional].copy()

    numeric_ids = pd.to_numeric(orders["order_id"], errors="coerce")
    numeric_primary_key = bool(
        numeric_ids.notna().all() and ((numeric_ids % 1) == 0).all()
    )
    if numeric_primary_key:
        first_new_id = int(numeric_ids.max()) + 1
        extra["order_id"] = range(first_new_id, first_new_id + additional)
    else:
        existing = {str(value) for value in orders["order_id"]}
        generated: list[str] = []
        suffix = 1
        while len(generated) < additional:
            candidate = f"RESET-{suffix:08d}"
            if candidate not in existing:
                generated.append(candidate)
                existing.add(candidate)
            suffix += 1
        extra["order_id"] = generated

    return pd.concat([orders, extra], ignore_index=True)


def main() -> None:
    reference_time = datetime.now(timezone.utc)
    INCOMING.mkdir(parents=True, exist_ok=True)
    orders = pd.read_csv(BASE / "orders.csv")
    history = pd.read_csv(HISTORY)
    target_rows, segment_points = weekday_median_row_count(
        history, reference_time.weekday(), fallback=len(orders)
    )
    orders = resize_orders(orders, target_rows)
    orders = shift_dataframe_timestamps(
        orders,
        ["created_at", "updated_at"],
        target_age_minutes=5,
        reference_time=reference_time,
    )
    orders.to_csv(INCOMING / "orders.csv", index=False)

    shutil.copy2(BASE / "customers.csv", INCOMING / "customers.csv")

    docs = []
    with open(BASE / "kb_documents.jsonl", "r", encoding="utf-8") as f:
        for line in f:
            docs.append(json.loads(line))
    # Re-anchor publish times so the starter dataset is always fresh when class runs.
    for i, doc in enumerate(docs):
        doc["published_at"] = (
            reference_time - timedelta(minutes=10 + i * 2)
        ).isoformat()
    with open(INCOMING / "kb_documents.jsonl", "w", encoding="utf-8") as f:
        for row in docs:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # Keep dbt seeds synchronized with current incoming data.
    seeds = ROOT / "dbt_project" / "seeds"
    seeds.mkdir(parents=True, exist_ok=True)
    shutil.copy2(INCOMING / "orders.csv", seeds / "orders.csv")
    shutil.copy2(INCOMING / "customers.csv", seeds / "customers.csv")

    metrics = ROOT / "reports" / "latest_metrics.json"
    if metrics.exists():
        metrics.unlink()
    print("Lab reset to a healthy baseline.")
    print(f"Reference UTC: {reference_time.isoformat()}")
    print(
        "Orders row target: "
        f"{target_rows} (UTC weekday={reference_time.weekday()}, "
        f"history_points={segment_points})"
    )


if __name__ == "__main__":
    main()
