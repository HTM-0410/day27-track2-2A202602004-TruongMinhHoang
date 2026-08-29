#!/usr/bin/env python3
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
warehouse = ROOT / "warehouse"
warehouse.mkdir(parents=True, exist_ok=True)
for name in ["orders.csv", "customers.csv"]:
    src = ROOT / "data" / "incoming" / name
    dst = ROOT / "dbt_project" / "seeds" / name
    shutil.copy2(src, dst)
print("Synced incoming CSV files into dbt seeds.")
