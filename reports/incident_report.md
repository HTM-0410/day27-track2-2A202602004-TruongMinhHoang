# Báo cáo sự cố — Nạp thiếu đơn hàng và KB hỗ trợ bị cũ

- **Sinh viên:** Trương Minh Hoàng
- **MSSV:** 2A202602004

## Kết quả chấm trên lớp

Theo kết quả chấm trực tiếp trên lớp do sinh viên cung cấp, hệ thống đã vượt qua
**20/20 test ẩn của thầy Tín** và được **cộng 10 điểm**.

Kết quả trên lớp được ghi chú riêng vì bộ test ẩn của giảng viên không nằm trong
repo. Bộ `tests_acceptance/test_hidden_proxy.py` trong repo cũng có đúng 20 test
functions và đạt 20/20, nhưng đây là bộ mô phỏng để tự kiểm tra, không được dùng
thay cho kết quả chấm thật nêu trên.

## Trạng thái và phạm vi

- **Mức độ:** P1 trong bài diễn tập Data Reliability Game Day.
- **Trạng thái:** Đã khôi phục trong kịch bản tái lập.
- **Mốc phát hiện đầu tiên:** 2026-08-28T12:26:30Z.
- **Minh chứng gốc:** `reports/evidence/incident_drill.json`.
- **Giới hạn kết luận:** đây là bài diễn tập chạy trong bộ nhớ, không phải tuyên
  bố đã xảy ra sự cố production. Batch hiện có không chứa chuỗi sự kiện đủ chi
  tiết để suy ra thời điểm bắt đầu sớm hơn mốc quan sát trên.

## 1. What happened? — Tóm tắt sự cố

Evidence quan sát được cho thấy hai lỗi độc lập:

1. Batch orders chỉ còn 150/600 dòng, tương đương 25% volume khỏe. Các dòng còn
   lại vẫn đúng schema nên pipeline có thể báo `SUCCESS` và contract vẫn xanh.
2. Nội dung và phân phối norm embedding của KB không đổi, nhưng
   `published_at` mới nhất chậm 190 phút, vượt contract freshness 60 phút.

Ảnh hưởng dự kiến là doanh thu trên dashboard CEO bị báo thiếu và Support Agent
có thể tiếp tục trả lời theo chính sách refund cũ.

## 2. When did it start? — Thời điểm

- **Mốc quan sát đầu tiên:** 2026-08-28T12:26:30Z
  (`2026-08-28T19:26:30+07:00`).
- **Orders:** batch thiếu được quan sát tại mốc trên. Evidence không có event log
  đủ chi tiết để kết luận chính xác ingestion bắt đầu bị cắt vào phút nào.
- **KB:** document mới nhất có `published_at=2026-08-28T09:16:30Z`, chậm 190
  phút so với reference clock. Điều này chứng minh dữ liệu đã stale tại thời
  điểm quan sát, không chứng minh job publish hỏng đúng từ 09:16:30Z.
- **Giới hạn:** không suy diễn thời gian bắt đầu sớm hơn raw evidence.

## 3. Detection — Phát hiện

| Tín hiệu | Baseline khỏe | Khi sự cố | Kết luận |
|---|---:|---:|---|
| Số dòng orders | 600 | 150 | Bất thường |
| Điểm anomaly theo cùng thứ | 0,0841 | 15,2228 | Bất thường rõ |
| Tỷ lệ volume orders | 1,00 | 0,25 | Nạp thiếu |
| Lỗi contract orders critical | 0 | 0 | Từng dòng vẫn hợp lệ |
| Độ cũ KB | Trong 60 phút | 190 phút | Vi phạm freshness |
| Drift độ dài văn bản KB | Không | Không | Không phải content collapse |
| Drift norm embedding KB | Không | Không | Không phải embedding collapse |
| Burn rate freshness revenue | 0 | 200 | Breach |
| Burn rate freshness RAG index | 0 | 100 | Breach |
| Chính sách multi-window | Không page | 40x/20x, page critical | Burn kéo dài |

Thiết kế này thể hiện defense in depth: contract deterministic bắt timestamp
cũ, còn MAD theo cùng thứ bắt lỗi volume mà không cần rule cố định
`row_count == N`.

## 4. Root cause — Nguyên nhân gốc có khả năng cao

Minh chứng hỗ trợ hai nguyên nhân:

- **Orders:** luồng ingestion bị cắt/ngừng trước `raw_orders`; batch vẫn đúng
  cấu trúc nhưng chỉ còn một phần tư volume kỳ vọng theo cùng thứ.
- **KB:** bước publish/index bị trễ; phân phối content ổn định nhưng timestamp
  nguồn đã cũ 190 phút.

Các giả thuyết bị loại:

- **Schema/enum/amount orders sai:** không phù hợp vì không có contract critical
  nào thất bại.
- **Mẫu traffic cuối tuần hợp lệ:** không phù hợp vì detector đã so với lịch sử
  cùng thứ mà vẫn trả score 15,2228 và relative drop 75%.
- **KB content hoặc embedding bị collapse:** không phù hợp vì cả hai drift
  signal đều false, chỉ freshness thất bại.

Không có extractor log hoặc scheduler event trong artifact, nên hai nguyên nhân
trên là kết luận có xác suất cao nhất từ signal, không phải khẳng định tuyệt đối
về component vật lý đã hỏng.

## 5. Blast radius — Phạm vi ảnh hưởng

```text
raw_orders
└─ stg_orders
   └─ fct_daily_revenue
      └─ ceo_revenue_dashboard

kb_documents
└─ kb_active_docs
   └─ rag_index
      └─ support_agent
```

Hai nhánh revenue và support độc lập, vì vậy phải giảm thiểu và xác minh riêng.

## 6. Mitigation — Giảm thiểu

1. Dừng publish partition revenue/dashboard bị ảnh hưởng và nạp lại orders từ
   batch nguồn đầy đủ gần nhất.
2. Không quarantine 150 orders chỉ vì volume thấp vì từng dòng vẫn hợp lệ.
   Quarantine chỉ áp dụng cho lỗi row-level critical như duplicate key, sai
   type, amount âm hoặc currency không hợp lệ.
3. Giữ RAG index tốt gần nhất, publish lại policy mới, validate KB contract rồi
   mới swap index.
4. Thông báo cho owner dashboard và Support Agent theo lineage ở trên.

## 7. Recovery verification — Khôi phục và xác minh

Recovery replay dùng lại 600 orders đầy đủ và KB baseline mới:

- [x] Contract orders và KB không còn check thất bại.
- [x] Row-count anomaly theo cùng thứ trở về false, score 0,0841.
- [x] GX baseline khỏe trả `PASS`.
- [x] GX warning tiếp tục pipeline; duplicate-key critical trả exit code khác 0
      và quarantine đúng hai dòng lỗi.
- [x] dbt build đạt **23/23**, không warning/error, gồm native SCD unit test.
- [x] Multi-window không page trên cửa sổ recovery.
- [x] Lineage đi tới đúng CEO dashboard và Support Agent.

Recovery không xóa phần error budget đã tiêu thụ. `Không page` chỉ có nghĩa
các cửa sổ recovery mới đã khỏe.

## 8. Bảo vệ logic transformation

`not_null` và `unique` là data test trên dữ liệu đã materialize; chúng không
chứng minh logic transformation đúng. Native dbt unit test cung cấp hai active
SCD versions của cùng customer, hai completed orders (100 + 70), một pending
order và một inactive history row. Kỳ vọng chính xác là 2 orders và 170 revenue,
qua đó làm lộ lỗi join nhân doanh thu mà generic tests có thể bỏ sót.

Mart chỉ công bố doanh thu USD. Singular test riêng chặn completed order khác
USD cho tới khi có nguồn FX rate được quản trị; model đồng thời lọc USD nên
không âm thầm cộng VND như USD trong khi build đang fail.

## 9. Prevention — Hành động phòng ngừa

| Hành động | Owner | Hạn | Lý do |
|---|---|---|---|
| Gate publish bằng robust volume theo cùng thứ | Commerce Data | 2026-08-31 | Bắt partial load vẫn đúng schema |
| Lưu GX Suite/Checkpoint/Action và quarantine evidence | Data Reliability | 2026-08-31 | Biến severity thành hành động thực |
| Enforce một active SCD version/customer | Analytics Engineering | 2026-09-01 | Ngăn nhân doanh thu |
| Chỉ page khi cả short và long window cùng burn | SRE | 2026-09-01 | Giảm false positive ngắn hạn |
| Gate RAG index swap bằng KB freshness contract | Support AI | 2026-09-02 | Ngăn trả policy cũ |
| Lưu SHA-256 input và UTC reference clock | Data Platform | 2026-09-02 | RCA và recovery tái lập được |

## 10. Đối chiếu yêu cầu báo cáo Phase 7

| Yêu cầu | Vị trí trong report | Raw evidence |
|---|---|---|
| What happened? | Mục 1 | `incident_drill.json → incident.what_happened` |
| When did it start? | Mục 2 | `reference_time_utc`, freshness details |
| Root cause? | Mục 4 | Contract, anomaly và RAG signals |
| Blast radius? | Mục 5 | `incident.blast_radius` |
| Mitigation? | Mục 6 | Action theo loại failure |
| Recovery verification? | Mục 7 | `recovery` + GX/dbt evidence |
| Prevention? | Mục 9 | Bảng owner/deadline/control |

## Phụ lục A — Bonus có differential evidence

Rubric chỉ tính bonus khi implementation bắt được failure mà baseline bỏ sót.
Các mục dưới đây là **candidate eligibility**, không phải điểm tự chấm. Tổng
candidate là **33**, nhưng rubric giới hạn tối đa **15** và giảng viên quyết
định mục nào được công nhận.

| Bonus | Failure của baseline | Kết quả advanced | Candidate |
|---|---|---|---:|
| MAD/same-weekday | Pooled Z-score báo sai Saturday 250 là anomaly, score 66,1200 | Segment median 249 giữ healthy, score 0,1349; drop 150 vẫn bị bắt, score 34,5632 | +3 |
| dbt native unit | Hai active SCD versions nhân revenue 170 thành 340 | Native unit PASS, đúng 2 completed rows/170 revenue | +3 |
| GX severity/actions | Severity chỉ là nhãn, không điều khiển pipeline | Warning=`warn`; critical=`block` | +3 |
| Automatic quarantine | Block nhưng không lưu dòng lỗi | Duplicate critical quarantine đúng 2 rows, reason `unique:order_id` | +3 |
| Column lineage | One-hop chỉ thấy `b.y`, `c.z`, bỏ sót `d.v` | BFS trả `[b.y, c.z, d.v]`, dedupe và cycle-safe | +7 |
| Multi-window burn | Short-only page nhầm transient 15x/2x | 15x/2x không page; sustained 15x/7x page critical | +7 |
| RAG embedding drift | Mean-only thấy baseline/current cùng mean 1,0 | KS=0,5 > critical 0,43007; scale ratio xấp xỉ 100; anomaly true | +7 |

Raw evidence: `reports/evidence/bonus_evidence.json`. Artifact này được SHA-256
trong `reports/evidence/verification_summary.json`. Không tuyên bố Soda,
Elementary hoặc OpenLineage vì không có implementation/execution evidence thật.

## Phụ lục B — Cách tái lập report

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts\verify_lab.py
```

Kết quả gần nhất: **32/32 pytest**, **23/23 dbt**, GX healthy/action drill PASS,
incident drill PASS, **7/7 bonus differential checks PASS** và toàn bộ raw
artifact có hash trong verification summary.
