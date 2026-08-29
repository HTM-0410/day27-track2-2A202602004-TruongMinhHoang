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
| `reports/evidence/bonus_evidence.json` | Differential proof baseline → advanced cho toàn bộ bonus đã triển khai |
| `reports/evidence/verification_summary.json` | Tổng hợp kết quả và SHA-256 của từng raw artifact |

## Minh chứng bonus kỹ thuật

Rubric giới hạn tổng bonus tối đa 15; giảng viên là người quyết định điểm.
Verifier hiện xác minh **7/7 candidate checks**, tổng candidate trước cap là
**33**, nhưng chỉ tối đa **15** điểm được tính. Con số này là eligibility có
minh chứng, không phải tự chấm điểm.

| Bonus | Baseline failure được chứng minh | Advanced evidence |
|---|---|---|
| MAD/same-weekday (+3) | Pooled Z-score báo sai Saturday khỏe | Segmented MAD giữ healthy, vẫn bắt volume drop |
| dbt native unit test (+3) | Hai active SCD versions làm 170 thành 340 | Native unit PASS với đúng 2 rows/170 |
| GX severity/actions (+3) | Severity chỉ là nhãn, chưa điều khiển pipeline | Warning=`warn`, critical=`block` |
| Automatic quarantine (+3) | Critical fail không materialize dòng lỗi | Đúng hai duplicate rows, có reason |
| Column lineage (+7) | One-hop bỏ sót downstream `d.v` | BFS transitive, dedupe diamond, chống cycle |
| Multi-window burn (+7) | Short-only page nhầm transient spike | 15x/2x không page; 15x/7x page critical |
| RAG embedding/token drift (+7) | Mean-only bỏ sót hai distribution cùng mean | KS + robust scale phát hiện shape collapse |

Raw differential artifact: `reports/evidence/bonus_evidence.json`.

### Phân tích chi tiết từng bonus

#### 1. MAD / same-weekday anomaly — candidate +3

**Failure của baseline.** Z-score trên một pooled history chỉ gồm các ngày có
volume khoảng 990–1.025 đánh dấu Saturday có 250 rows là anomaly. Kết quả
baseline là `is_anomaly=true`, score **66,1200**. Đây là false positive vì
Saturday phải được so với Saturday history, không phải weekday history.

**Cách xử lý.** `method="auto"` ưu tiên `same_segment_history`; nếu caller không
cung cấp segment thì dùng `day_of_week` để chọn cluster weekday/weekend. Detector
kết hợp MAD, Z-score và mức giảm tương đối. Với metric sản lượng, policy
`drop_only` không page khi volume tăng; `known_event` được suppress nhưng method
và reason vẫn được giữ để audit.

**Kết quả.** Saturday current 250 trên segment median 249 trả
`is_anomaly=false`, score **0,1349**. Ca giảm thật current 150 trên segment median
602,5 trả `is_anomaly=true`, score **34,5632**. Zero-MAD, outlier, relative drop
và known-event còn được kiểm tra trong hidden-proxy cases 6–8.

**Code/minh chứng:** `observability/anomaly.py`,
`tests_acceptance/test_hidden_proxy.py`, `bonus_evidence.json` →
`checks.mad_same_weekday`.

#### 2. dbt native unit test — candidate +3

**Failure của baseline.** Generic test như `not_null`, `unique` hoặc kiểm tra
aggregate không âm vẫn có thể xanh khi dimension có hai active SCD versions.
Naive join nhân mỗi order với hai customer rows, làm revenue đúng **170** thành
**340**.

**Cách xử lý.** Model chọn deterministic latest active customer version trước
khi join fact. Native dbt unit fixture cố ý có hai active versions, một inactive
version, hai completed orders và một pending order.

**Kết quả.** Unit test
`fct_daily_revenue_deduplicates_active_customer_versions` PASS, output đúng
`completed_order_rows=2` và `daily_revenue=170`. Toàn bộ dbt build đạt 23/23.

**Code/minh chứng:** `dbt_project/models/marts/unit_tests.yml`,
`dbt_project/models/marts/fct_daily_revenue.sql`, `dbt_run_results.json`,
`bonus_evidence.json` → `checks.dbt_native_unit_test`.

#### 3. GX severity/actions — candidate +3

**Failure của baseline.** Nếu severity chỉ là text trong validation result thì
pipeline không biết nên tiếp tục hay dừng. Một warning có thể chặn nhầm batch;
một critical failure có thể vẫn publish dữ liệu lỗi.

**Cách xử lý.** GX Suite → ValidationDefinition → Checkpoint → custom Action
ánh xạ warning thành `warn/continue`, critical thành `block/quarantine`.

**Kết quả.** Warning case trả decision `warn`, không quarantine và pipeline tiếp
tục. Duplicate critical case trả decision `block`. Đây là action chạy thật,
không phải log mô tả thủ công.

**Code/minh chứng:** `gx/validate_orders.py`,
`tests_acceptance/test_gx_flow.py`, `gx_action_drill.json`,
`bonus_evidence.json` → `checks.gx_severity_actions`.

#### 4. Automatic quarantine — candidate +3

**Failure của baseline.** Block pipeline nhưng không materialize dòng lỗi khiến
on-call phải dò lại toàn bộ batch và không có row-level evidence để sửa.

**Cách xử lý.** Custom GX Action xác định chính xác vị trí dòng vi phạm, ghi
source row number và `__quarantine_reasons` vào quarantine CSV. Warning không
tạo quarantine rows.

**Kết quả.** Drill duplicate key quarantine đúng **2 rows**, cả hai có reason
`unique:order_id`; warning case có **0 rows**. Test còn kiểm tra critical exit 1
và warning exit 0.

**Code/minh chứng:** `gx/validate_orders.py`,
`scripts/run_gx_action_drill.py`, `gx_action_drill.json`,
`bonus_evidence.json` → `checks.automatic_quarantine`.

#### 5. Column-level transitive lineage — candidate +7

**Failure của baseline.** One-hop traversal từ `a.x` chỉ thấy `b.y` và `c.z`, bỏ
sót downstream `d.v`. Diamond graph có thể trả `d.v` hai lần; cycle từ `d.v`
quay về `a.x` có thể gây lặp vô hạn.

**Cách xử lý.** BFS dùng queue và visited set, giữ thứ tự deterministic, loại
duplicate và không trả lại start node. Cùng engine được dùng cho dataset lineage
và column lineage.

**Kết quả.** Advanced output là `[b.y, c.z, d.v]`; `d.v` xuất hiện đúng một lần
và cycle kết thúc an toàn. Incident drill dùng lineage để xác định CEO dashboard
và Support Agent trong blast radius.

**Code/minh chứng:** `observability/lineage.py`, hidden-proxy cases 15–17,
`incident_drill.json`, `bonus_evidence.json` → `checks.column_lineage`.

#### 6. Multi-window burn-rate — candidate +7

**Failure của baseline.** Policy chỉ nhìn short window sẽ page ngay khi burn rate
15x dù long window mới 2x. Đây là transient spike, gây alert fatigue.

**Cách xử lý.** Fast page cần short ≥14,4x **và** long ≥6x; slow page cần short
≥6x **và** long ≥3x. Một cửa sổ vượt ngưỡng vẫn được ghi nhận là warning nhưng
không page.

**Kết quả.** Ca 15x/2x trả `page=false`, reason `single_window_spike`. Ca
15x/7x trả `page=true`, severity `critical`, reason `sustained_fast_burn`.

**Code/minh chứng:** `observability/slo.py`, hidden-proxy cases 11–14,
`incident_drill.json`, `bonus_evidence.json` →
`checks.multiwindow_burn_rate`.

#### 7. RAG embedding drift — candidate +7

**Failure của baseline.** Baseline norms quanh 1,0 và current bimodal 0,0/2,0
đều có mean **1,0**. Mean-only monitor kết luận không đổi dù embedding space đã
collapse thành hai cực.

**Cách xử lý.** Detector kết hợp two-sample KS với robust location/scale effect;
đồng thời fail closed khi current embeddings rỗng, invalid hoặc non-finite.

**Kết quả.** Mean-only bỏ sót, nhưng advanced detector trả
`is_anomaly=true`, KS **0,5** lớn hơn critical **0,43007**, scale ratio xấp xỉ
**100**. Hidden-proxy cases 18–20 còn kiểm tra stable norms và large norm shift.

**Code/minh chứng:** `observability/rag_metrics.py`,
`observability/distribution.py`, hidden-proxy cases 18–20,
`bonus_evidence.json` → `checks.rag_embedding_drift`.

### Kết luận bonus và giới hạn tuyên bố

- Có **7/7 candidate bonus checks PASS** với tổng candidate **33** trước cap.
- Theo rubric, tổng bonus được tính không vượt quá **15**.
- Các con số trên là eligibility có raw evidence, không phải điểm tự chấm;
  giảng viên quyết định mục nào được chấp nhận.
- `bonus_evidence.json` được hash trong `verification_summary.json`, vì vậy thay
  đổi raw artifact sẽ làm evidence chain không còn khớp.
- Không tuyên bố Soda Data Contract, Elementary OSS hoặc OpenLineage vì repo
  không có triển khai và execution evidence thật cho ba mục này.

Tái lập riêng bonus:

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts\run_bonus_evidence.py
```

Tái lập toàn bộ lab và evidence chain:

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts\verify_lab.py
```

## Lệnh xác minh chuẩn trên Windows

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts\verify_lab.py
git diff --check
```

Verifier tự chạy reset/baseline/GX/dbt trong clean-room tạm để không thay đổi
source data đã track. GNU Make/MinGW trên checkout có đường dẫn tiếng Việt có
thể lỗi trước khi đọc Makefile, vì vậy Python verifier là lệnh chuẩn.
