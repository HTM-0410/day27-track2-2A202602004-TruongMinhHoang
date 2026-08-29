# Ma trận yêu cầu — triển khai — minh chứng

Ma trận này tách riêng code, raw evidence và nội dung báo cáo. Public tests xanh
không được xem là bằng chứng duy nhất.

| Hạng mục rubric | Triển khai | Minh chứng tái lập |
|---|---|---|
| Baseline và hiểu hệ thống (5) | Orders đi qua `stg_orders → fct_daily_revenue → CEO dashboard`; KB đi qua `kb_active_docs → rag_index → support_agent`. Tín hiệu tin cậy gồm contract, freshness, anomaly, drift, lineage và SLO. | `healthy_baseline_metrics.json`, `verification_summary.json` |
| Data contract (10) | Required/null/unique/accepted/range, strict type, datetime nhiều timezone, UTC freshness, severity/action | Public tests và 20 hidden-proxy cases trong JUnit |
| GX/equivalent flow (10) | GX 1.21 Suite → ValidationDefinition → Checkpoint → Action; warning tiếp tục, critical block/quarantine | `gx_healthy_validation.json`, `gx_action_drill.json` |
| dbt và transformation correctness (10) | Generic/singular tests, fail-closed currency chưa có FX, SCD dedupe và native unit test | `dbt_run_results.json`: 23 results đều pass/success |
| Anomaly detection (15) | Z-score explicit; auto dùng same-weekday + MAD; lọc non-finite; xử lý zero-MAD | Hidden-proxy cases 6–8; incident 600→150; recovery false |
| Lineage và blast radius (15) | BFS transitive, chống cycle/diamond cho dataset và column | Hidden-proxy cases 15–17; incident đi tới CEO dashboard và Support Agent |
| SLI/SLO/error budget (10) | Validate input, normalized burn math, boundary và multi-window AND policy | Public SLO tests, proxy cases 11–14, incident burn/page |
| Mystery incident RCA (15) | Drill không phá dữ liệu cho partial ingestion + stale KB, có hypotheses, mitigation và recovery assertions | `incident_drill.json`, `incident_report.md` |
| Incident report (5) | Đủ what/when/root cause/blast radius/mitigation/recovery/prevention và giới hạn kết luận | `reports/incident_report.md` |
| Giải thích/defend solution (5) | Bảy quyết định theo hypothesis → proposal → evidence → decision; giải thích data test khác unit test | `reports/agent_log.md` |

## Minh chứng bắt buộc và nguồn gốc

| Tệp | Nội dung |
|---|---|
| `reports/evidence/pytest_results.xml` | Raw JUnit của public + acceptance tests |
| `reports/evidence/healthy_baseline_metrics.json` | Baseline clean-room, UTC clock, input SHA-256, contract/anomaly/RAG/SLO |
| `reports/evidence/gx_healthy_validation.json` | Toàn bộ expectation result của GX baseline khỏe |
| `reports/evidence/gx_action_drill.json` | Warning/critical decisions và hai rows bị quarantine |
| `reports/evidence/dbt_run_results.json` | Raw dbt run result cho models, data tests, seeds và unit test |
| `reports/evidence/incident_drill.json` | Before/incident/recovery, lineage và SLO |
| `reports/evidence/verification_summary.json` | Tổng hợp kết quả và SHA-256 của từng raw artifact |

## Minh chứng bonus kỹ thuật

Rubric giới hạn tổng bonus tối đa 15; giảng viên là người quyết định điểm.

| Bonus | Minh chứng |
|---|---|
| MAD/same-weekday (+3) | Seasonal và outlier tests; incident volume |
| dbt native unit test (+3) | Hai active SCD versions không thể nhân 170 revenue |
| GX severity/actions (+3) | Warning tiếp tục, critical block |
| Automatic quarantine (+3) | Duplicate-key quarantine đúng hai rows |
| Column lineage (+7) | Transitive diamond/cycle test |
| Multi-window burn (+7) | Spike ngắn không page, paired sustained burn page |
| RAG embedding/token drift (+7) | Stable/collapse/shift tests và baseline signals |

Không tuyên bố đã triển khai Soda, Elementary hoặc OpenLineage.

## Lệnh xác minh chuẩn trên Windows

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts\verify_lab.py
git diff --check
```

Verifier tự chạy reset/baseline/GX/dbt trong clean-room tạm để không thay đổi
source data đã track. GNU Make/MinGW trên checkout có đường dẫn tiếng Việt có
thể lỗi trước khi đọc Makefile, vì vậy Python verifier là lệnh chuẩn.
