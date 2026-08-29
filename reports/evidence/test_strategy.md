# Chiến lược mô phỏng hidden test

## Tiêu chuẩn chấp nhận

Hidden evaluator có 20 cases và chỉ import chín hàm trong `student_api.py`.
Trước khi sửa implementation, bài lab chốt một benchmark proxy độc lập gồm
**đúng 20 test functions** trong
`tests_acceptance/test_hidden_proxy.py`.

Benchmark kiểm tra invariant hành vi, không khóa cứng tên thuật toán nội bộ hoặc
giá trị sát threshold. Freshness contract dùng `reference_time` cố định nên
không phụ thuộc sample data, network hoặc wall clock.

## Danh sách nhóm ca kiểm thử

| Ca | Phạm vi | Điều kiện khó |
|---:|---|---|
| 1–5 | Contract | Healthy JSON shape, thiếu critical column, numeric-string drift, fixed-clock freshness, nhiều severity/action |
| 6–8 | Metric anomaly | Weekday/weekend, zero-MAD, outlier, giảm tương đối, `known_event`, drop-only |
| 9–10 | Distribution | Cùng mean nhưng khác shape; cùng distribution khác thứ tự |
| 11–14 | SLO | Đúng boundary budget, input sai, transient và sustained burn |
| 15–17 | Lineage | BFS diamond, cycle và column lineage transitive |
| 18–20 | RAG | Stable/collapsed length và stable/shifted embedding norm |

Các assertion đối kháng bên trong 20 cases còn phủ NaN/Infinity, adjacency sai
kiểu, start node không tồn tại, một Unicode string phải được hiểu là một
document, empty RAG batch và JSON serialization với `allow_nan=False`.

## Vì sao bộ test khó

- Giá trị ép kiểu được vẫn có thể là schema drift.
- Weekend khỏe sẽ trông bất thường nếu so với weekday history.
- Một outlier lịch sử có thể che anomaly thật của mean/std.
- Baseline phân tán rộng có thể che một cú giảm tương đối nghiêm trọng.
- Metric sản lượng tăng khỏe không được báo như một cú giảm; sự kiện đã biết
  cần được suppress nhưng vẫn để lại method có thể audit.
- Cùng mean không đồng nghĩa cùng distribution.
- Short-window spike không được page nếu long window không xác nhận.
- Diamond/cycle graph làm hỏng traversal đệ quy ngây thơ.
- Telemetry rỗng hoặc non-finite không được âm thầm trả `score=NaN`.

## Khả năng phân biệt

- Stable API hoàn chỉnh: **20 pass / 0 fail**.
- Hai GX integration tests riêng xác minh warning-continue và
  critical-block/quarantine.
- Raw kết quả cuối được ghi trong
  `reports/evidence/pytest_results.xml`.

Lệnh tái lập:

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest tests_public tests_acceptance -q
```
