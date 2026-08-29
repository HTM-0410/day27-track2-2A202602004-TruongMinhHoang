from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "latest_metrics.json"
HISTORY = ROOT / "data" / "history" / "metrics_history.csv"

st.set_page_config(page_title="Data Reliability Lab", layout="wide")
st.title("Data Reliability Game Day")
st.caption("Data-quality, anomaly, lineage and SLO signals for incident decisions.")

if not REPORT.exists():
    st.warning("Run `make baseline` first to generate reports/latest_metrics.json")
    st.stop()

report = json.loads(REPORT.read_text(encoding="utf-8"))

c1, c2, c3, c4 = st.columns(4)
c1.metric("Orders rows", report["orders_rows"])
c2.metric(
    "Orders freshness (min)",
    "unknown"
    if report.get("orders_freshness_minutes") is None
    else f"{report['orders_freshness_minutes']:.1f}",
)
c3.metric(
    "KB freshness (min)",
    "unknown"
    if report.get("kb_freshness_minutes") is None
    else f"{report['kb_freshness_minutes']:.1f}",
)
c4.metric(
    "Critical failures",
    report.get("critical_contract_failure_count", report["critical_contract_failures"]),
)

s1, s2, s3 = st.columns(3)
contract_slo = report["contract_slo"]
s1.metric(
    "Contract SLO",
    f"{contract_slo['target'] * 100:.2f}%",
    delta=f"burn {contract_slo['burn_rate']:.2f}x",
    delta_color="inverse",
)
s2.metric(
    "Error budget remaining",
    f"{contract_slo['remaining_error_budget_fraction'] * 100:.1f}%",
)
multiwindow = report.get("multiwindow_burn_signal", {})
s3.metric(
    "Multi-window page",
    "YES" if multiwindow.get("page") else "NO",
    delta=str(multiwindow.get("severity", "unknown")),
    delta_color="inverse" if multiwindow.get("page") else "normal",
)

st.subheader("Current signals")
st.json({
    "row_count_anomaly": report["row_count_anomaly"],
    "kb_text_length_signal": report["kb_text_length_signal"],
    "kb_embedding_signal": report.get("kb_embedding_signal"),
    "multiwindow_burn_signal": multiwindow,
})

failed_issues = report.get("failed_contract_issues", {})
if any(failed_issues.values()):
    st.warning("Contract failures need triage according to severity/action.")
    st.json(failed_issues)
else:
    st.success("No failed orders or KB contract checks in this run.")

history = pd.read_csv(HISTORY)
st.subheader("Historical row count")
st.line_chart(history.set_index("date")[["row_count"]])

st.subheader("Example blast radius")
st.write("stg_orders -> " + " -> ".join(report["sample_blast_radius_from_stg_orders"]))

st.subheader("Owners and response")
st.table(
    pd.DataFrame(
        [
            {
                "asset": "orders / fct_daily_revenue",
                "owner": "commerce-data",
                "response": "hold publish, validate, retry ingestion",
            },
            {
                "asset": "kb_documents / rag_index",
                "owner": "support-ai",
                "response": "keep last-good index, republish, validate, swap",
            },
        ]
    )
)
st.caption(
    "Input hashes and the single UTC reference clock are preserved in "
    "reports/latest_metrics.json for RCA."
)
