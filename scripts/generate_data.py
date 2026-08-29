#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]


def parse_as_of(value: str) -> datetime:
    """Parse an ISO-8601 generation clock and normalize it to UTC."""
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--as-of must be an ISO-8601 datetime") from exc
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("--as-of must include a timezone")
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


def generate(
    rows: int, days: int, seed: int, as_of: datetime | None = None
) -> None:
    random.seed(seed)
    rng = np.random.default_rng(seed)
    baseline_dir = ROOT / "data" / "baseline"
    history_dir = ROOT / "data" / "history"
    baseline_dir.mkdir(parents=True, exist_ok=True)
    history_dir.mkdir(parents=True, exist_ok=True)

    now = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc).replace(
        microsecond=0
    )

    # Customers with a small amount of SCD history, but exactly one active row per customer.
    customers = []
    countries = ["VN", "SG", "US", "AU"]
    tiers = ["basic", "silver", "gold"]
    n_customers = max(60, rows // 8)
    for i in range(1, n_customers + 1):
        cid = f"C{i:04d}"
        country = random.choice(countries)
        tier = random.choices(tiers, weights=[0.55, 0.30, 0.15])[0]
        if i % 11 == 0:
            customers.append({
                "customer_id": cid,
                "country": country,
                "tier": "basic",
                "is_active": False,
                "valid_from": (now - timedelta(days=500)).isoformat(),
                "valid_to": (now - timedelta(days=120)).isoformat(),
            })
        customers.append({
            "customer_id": cid,
            "country": country,
            "tier": tier,
            "is_active": True,
            "valid_from": (now - timedelta(days=120)).isoformat(),
            "valid_to": "",
        })
    pd.DataFrame(customers).to_csv(baseline_dir / "customers.csv", index=False)

    # Today's order batch.
    order_rows = []
    statuses = ["completed", "completed", "completed", "pending", "refunded", "cancelled"]
    for i in range(rows):
        updated = now - timedelta(minutes=random.randint(4, 25))
        created = updated - timedelta(minutes=random.randint(1, 180))
        order_rows.append({
            "order_id": 100000 + i,
            "customer_id": f"C{random.randint(1, n_customers):04d}",
            "amount": round(float(max(3.0, rng.lognormal(mean=4.05, sigma=0.55))), 2),
            "currency": "USD",
            "status": random.choice(statuses),
            "created_at": created.isoformat(),
            "updated_at": updated.isoformat(),
        })
    pd.DataFrame(order_rows).to_csv(baseline_dir / "orders.csv", index=False)

    kb_docs = [
        {
            "doc_id": "refund-policy",
            "version": 4,
            "effective_at": (now - timedelta(days=8)).isoformat(),
            "published_at": (now - timedelta(minutes=12)).isoformat(),
            "source_uri": "policy/refund-v4.pdf",
            "content": "Customers may request a refund within 7 days of delivery when the order satisfies the refund conditions.",
        },
        {
            "doc_id": "shipping-policy",
            "version": 7,
            "effective_at": (now - timedelta(days=20)).isoformat(),
            "published_at": (now - timedelta(minutes=15)).isoformat(),
            "source_uri": "policy/shipping-v7.pdf",
            "content": "Standard domestic shipping usually arrives within three to five business days after fulfillment.",
        },
        {
            "doc_id": "account-recovery",
            "version": 3,
            "effective_at": (now - timedelta(days=35)).isoformat(),
            "published_at": (now - timedelta(minutes=9)).isoformat(),
            "source_uri": "support/account-recovery-v3.md",
            "content": "Account recovery requires identity verification and a one-time code sent to a previously verified channel.",
        },
        {
            "doc_id": "priority-support",
            "version": 2,
            "effective_at": (now - timedelta(days=50)).isoformat(),
            "published_at": (now - timedelta(minutes=18)).isoformat(),
            "source_uri": "support/priority-v2.md",
            "content": "Priority incidents are triaged immediately and routed to the on-call support owner with an explicit severity.",
        },
        {
            "doc_id": "privacy-policy",
            "version": 5,
            "effective_at": (now - timedelta(days=15)).isoformat(),
            "published_at": (now - timedelta(minutes=20)).isoformat(),
            "source_uri": "policy/privacy-v5.pdf",
            "content": "Personal data is processed only for approved purposes and is retained according to the documented retention schedule.",
        },
    ]
    with open(baseline_dir / "kb_documents.jsonl", "w", encoding="utf-8") as f:
        for row in kb_docs:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    # Metric history. Weekend traffic is intentionally lower to motivate seasonality handling.
    metrics = []
    base_count = rows
    for offset in range(days, 0, -1):
        d = (now - timedelta(days=offset)).date()
        weekday = d.weekday()  # 0=Mon
        if weekday >= 5:
            expected = base_count * 0.43
        else:
            expected = base_count * 1.0
        row_count = int(max(20, rng.normal(expected, max(8, expected * 0.04))))
        metrics.append({
            "date": d.isoformat(),
            "day_of_week": weekday,
            "row_count": row_count,
            "null_rate": round(float(max(0, rng.normal(0.004, 0.0015))), 5),
            "avg_amount": round(float(rng.normal(70, 4.5)), 2),
            "mean_text_length": round(float(rng.normal(15, 1.2)), 2),
            "embedding_norm_mean": round(float(rng.normal(1.0, 0.025)), 4),
        })
    pd.DataFrame(metrics).to_csv(history_dir / "metrics_history.csv", index=False)

    lineage = {
        "dataset_lineage": {
            "raw_orders": ["stg_orders"],
            "raw_customers": ["stg_customers"],
            "stg_orders": ["fct_daily_revenue"],
            "stg_customers": ["fct_daily_revenue"],
            "fct_daily_revenue": ["ceo_revenue_dashboard"],
            "kb_documents": ["kb_active_docs"],
            "kb_active_docs": ["rag_index"],
            "rag_index": ["support_agent"],
        },
        "column_lineage": {
            "raw_orders.amount": ["stg_orders.amount_usd"],
            "stg_orders.amount_usd": ["fct_daily_revenue.daily_revenue"],
            "fct_daily_revenue.daily_revenue": ["ceo_revenue_dashboard.revenue"],
            "kb_documents.content": ["kb_active_docs.content"],
            "kb_active_docs.content": ["rag_index.embedding"],
            "rag_index.embedding": ["support_agent.answer"],
        },
    }
    with open(baseline_dir / "lineage_graph.json", "w", encoding="utf-8") as f:
        json.dump(lineage, f, indent=2)

    print(f"Generated {rows} orders, {n_customers} customers, {days} days of metric history.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", type=int, default=600)
    parser.add_argument("--days", type=int, default=42)
    parser.add_argument("--seed", type=int, default=27)
    parser.add_argument(
        "--as-of",
        type=parse_as_of,
        default=None,
        help="fixed ISO-8601 UTC-aware clock for bit-for-bit reproducible data",
    )
    args = parser.parse_args()
    generate(args.rows, args.days, args.seed, args.as_of)
