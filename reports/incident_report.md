# Báo cáo sự cố — Nạp thiếu đơn hàng và KB hỗ trợ bị cũ

## Trạng thái và phạm vi

- **Mức độ:** P1 trong bài diễn tập Data Reliability Game Day.
- **Trạng thái:** Đã khôi phục trong kịch bản tái lập.
- **Mốc phát hiện đầu tiên:** 2026-08-28T12:26:30Z.
- **Minh chứng gốc:** `reports/evidence/incident_drill.json`.
- **Giới hạn kết luận:** đây là bài diễn tập chạy trong bộ nhớ, không phải tuyên
  bố đã xảy ra sự cố production. Batch hiện có không chứa chuỗi sự kiện đủ chi
  tiết để suy ra thời điểm bắt đầu sớm hơn mốc quan sát trên.

## Tóm tắt

Bài diễn tập tạo hai lỗi độc lập:

1. Batch orders chỉ còn 150/600 dòng, tương đương 25% volume khỏe. Các dòng còn
   lại vẫn đúng schema nên pipeline có thể báo `SUCCESS` và contract vẫn xanh.
2. Nội dung và phân phối norm embedding của KB không đổi, nhưng
   `published_at` mới nhất chậm 190 phút, vượt contract freshness 60 phút.

Ảnh hưởng dự kiến là doanh thu trên dashboard CEO bị báo thiếu và Support Agent
có thể tiếp tục trả lời theo chính sách refund cũ.

## Phát hiện

| Tín hiệu | Baseline khỏe | Khi sự cố | Kết luận |
|---|---:|---:|---|
| Số dòng orders | 600 | 150 | Bất thường |
| Điểm MAD theo cùng thứ | 0,000 | 9,339 | Bất thường rõ |
| Tỷ lệ volume orders | 1,00 | 0,25 | Nạp thiếu |
| Lỗi contract orders critical | 0 | 0 | Từng dòng vẫn hợp lệ |
| Độ cũ KB | Trong 60 phút | 190 phút | Vi phạm freshness |
| Drift độ dài văn bản KB | Không | Không | Không phải content collapse |
| Drift norm embedding KB | Không | Không | Không phải embedding collapse |
| Burn rate freshness revenue | 0 | 200 | Breach |
| Burn rate freshness RAG index | 0 | 100 | Breach |
| Chính sách multi-window | Không page | Page critical | Burn kéo dài |

Thiết kế này thể hiện defense in depth: contract deterministic bắt timestamp
cũ, còn MAD theo cùng thứ bắt lỗi volume mà không cần rule cố định
`row_count == N`.

## Nguyên nhân gốc

Minh chứng hỗ trợ hai nguyên nhân:

- **Orders:** luồng ingestion bị cắt/ngừng trước `raw_orders`; batch vẫn đúng
  cấu trúc nhưng chỉ còn một phần tư volume kỳ vọng theo cùng thứ.
- **KB:** bước publish/index bị trễ; phân phối content ổn định nhưng timestamp
  nguồn đã cũ 190 phút.

Các giả thuyết bị loại:

- **Schema/enum/amount orders sai:** không phù hợp vì không có contract critical
  nào thất bại.
- **Mẫu traffic cuối tuần hợp lệ:** không phù hợp vì detector đã so với lịch sử
  cùng thứ mà vẫn trả score 9,339.
- **KB content hoặc embedding bị collapse:** không phù hợp vì cả hai drift
  signal đều false, chỉ freshness thất bại.

## Phạm vi ảnh hưởng

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

## Giảm thiểu

1. Dừng publish partition revenue/dashboard bị ảnh hưởng và nạp lại orders từ
   batch nguồn đầy đủ gần nhất.
2. Không quarantine 150 orders chỉ vì volume thấp vì từng dòng vẫn hợp lệ.
   Quarantine chỉ áp dụng cho lỗi row-level critical như duplicate key, sai
   type, amount âm hoặc currency không hợp lệ.
3. Giữ RAG index tốt gần nhất, publish lại policy mới, validate KB contract rồi
   mới swap index.
4. Thông báo cho owner dashboard và Support Agent theo lineage ở trên.

## Khôi phục và xác minh

Recovery replay dùng lại 600 orders đầy đủ và KB baseline mới:

- [x] Contract orders và KB không còn check thất bại.
- [x] Row-count anomaly theo cùng thứ trở về false, score 0,000.
- [x] GX baseline khỏe trả `PASS`.
- [x] GX warning tiếp tục pipeline; duplicate-key critical trả exit code khác 0
      và quarantine đúng hai dòng lỗi.
- [x] dbt build đạt **23/23**, không warning/error, gồm native SCD unit test.
- [x] Multi-window không page trên cửa sổ recovery.
- [x] Lineage đi tới đúng CEO dashboard và Support Agent.

Recovery không xóa phần error budget đã tiêu thụ. `Không page` chỉ có nghĩa
các cửa sổ recovery mới đã khỏe.

## Bảo vệ logic transformation

`not_null` và `unique` là data test trên dữ liệu đã materialize; chúng không
chứng minh logic transformation đúng. Native dbt unit test cung cấp hai active
SCD versions của cùng customer, hai completed orders (100 + 70), một pending
order và một inactive history row. Kỳ vọng chính xác là 2 orders và 170 revenue,
qua đó làm lộ lỗi join nhân doanh thu mà generic tests có thể bỏ sót.

Mart chỉ công bố doanh thu USD. Singular test riêng chặn completed order khác
USD cho tới khi có nguồn FX rate được quản trị; model đồng thời lọc USD nên
không âm thầm cộng VND như USD trong khi build đang fail.

## Hành động phòng ngừa

| Hành động | Owner | Hạn | Lý do |
|---|---|---|---|
| Gate publish bằng robust volume theo cùng thứ | Commerce Data | 2026-08-31 | Bắt partial load vẫn đúng schema |
| Lưu GX Suite/Checkpoint/Action và quarantine evidence | Data Reliability | 2026-08-31 | Biến severity thành hành động thực |
| Enforce một active SCD version/customer | Analytics Engineering | 2026-09-01 | Ngăn nhân doanh thu |
| Chỉ page khi cả short và long window cùng burn | SRE | 2026-09-01 | Giảm false positive ngắn hạn |
| Gate RAG index swap bằng KB freshness contract | Support AI | 2026-09-02 | Ngăn trả policy cũ |
| Lưu SHA-256 input và UTC reference clock | Data Platform | 2026-09-02 | RCA và recovery tái lập được |
