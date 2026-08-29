# Danh mục minh chứng Lab 27

Các tệp trong thư mục này được sinh bằng code thật, không ghi tay số liệu.

| Tệp | Nguồn sinh | Mục đích |
|---|---|---|
| `pytest_results.xml` | `scripts/verify_lab.py` | Raw JUnit cho public và acceptance tests |
| `healthy_baseline_metrics.json` | clean-room `scripts/run_baseline.py` | Contract, anomaly, freshness, RAG, SLO và SHA-256 input |
| `gx_healthy_validation.json` | clean-room GX Checkpoint | Kết quả từng expectation của baseline khỏe |
| `gx_action_drill.json` | `scripts/run_gx_action_drill.py` | Warning/critical decision và quarantine |
| `dbt_run_results.json` | clean-room `dbt build` | Raw status models, seeds, data tests và unit test |
| `incident_drill.json` | `scripts/run_incident_drill.py` | Before/incident/recovery, blast radius và SLO |
| `verification_summary.json` | `scripts/verify_lab.py` | Tổng hợp PASS và hash toàn bộ raw evidence |
| `test_strategy.md` | Thiết kế acceptance | Giải thích đúng 20 hidden-proxy cases |

Lệnh sinh lại toàn bộ:

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts\verify_lab.py
```

Verifier chạy healthy flow trong thư mục tạm để không sửa các file
`data/incoming`, `data/baseline`, `data/history` và dbt seeds trong
checkout chính.
