# Agent log

- Sinh viên: Trương Minh Hoàng
- MSSV: 2A202602004
- Kết quả trên lớp do sinh viên cung cấp: hệ thống vượt qua 20/20 test ẩn của
  thầy Tín và được cộng 10 điểm.
- Lưu ý: 20 test ẩn của thầy không nằm trong repo. Bộ 20 hidden-proxy trong repo
  là bộ mô phỏng riêng và cũng đạt 20/20.

Em hỏi agent theo từng phần, tự chọn phương án rồi chạy test để kiểm tra. Em
không giao cho agent làm cả bài trong một prompt.

## 1. Contract

- Em hỏi: Contract có bắt được numeric string và timestamp cũ không?
- Agent trả lời: Nên kiểm tra type chặt, đổi thời gian về UTC và thêm freshness.
- Em chọn: Không tự cast dữ liệu sai type vì sẽ che schema drift.
- Test: Type drift, missing column, freshness và severity/action đều pass trong
  tests_acceptance/test_hidden_proxy.py.
- Có sửa lại fixture test dùng ngày cố định vì để lâu nó tự thành stale.

## 2. Anomaly

- Em hỏi: Z-score báo sai ở weekend và history có variance bằng 0 thì sửa thế
  nào?
- Agent trả lời: Dùng history cùng segment, MAD, relative drop và giữ Z-score
  cho API cũ.
- Em chọn: Auto dùng weekday/weekend, MAD + Z-score + relative drop 50%.
  row_count chỉ cảnh báo chiều giảm; known_event thì suppress.
- Test: Saturday khỏe không bị báo, 600 xuống 150 bị bắt, history toàn 100 mà
  current 101 vẫn bị bắt, planned maintenance không page.
- Evidence: reports/evidence/bonus_evidence.json và incident_drill.json.

## 3. Distribution drift

- Em hỏi: Hai batch cùng mean nhưng khác shape thì detector mean-ratio có bỏ
  sót không?
- Agent trả lời: Có. Nên thêm two-sample KS và so location/scale.
- Em chọn: Dùng KS + robust effect, không thêm thư viện ML.
- Test: Batch 0/20 có cùng mean với baseline vẫn bị bắt; chỉ đảo thứ tự thì
  không bị báo.
- Kết quả nằm ở hidden-proxy 9–10.

## 4. Bộ test khó

- Em hỏi: Public test chỉ có 10 ca thì kiểm tra hidden test bằng cách nào?
- Agent trả lời: Tạo 20 test theo behavior của stable API.
- Em chọn: Contract 5, anomaly 3, distribution 2, SLO 4, lineage 3 và RAG 3.
  GX để thành test riêng.
- Test: Tổng suite 32/32 pass, riêng hidden-proxy là 20/20.
- Evidence: reports/evidence/pytest_results.xml.

## 5. GX action

- Em hỏi: Chỉ ghi severity trong kết quả đã đủ chưa?
- Agent trả lời: Chưa. Warning phải được đi tiếp, critical phải block và
  quarantine dòng lỗi.
- Em chọn: Dùng GX Checkpoint và custom Action, không chỉ in log.
- Test: Warning trả warn, exit 0, quarantine 0 dòng. Duplicate critical trả
  block, exit khác 0 và quarantine đúng 2 dòng.
- Evidence: reports/evidence/gx_action_drill.json.

## 6. dbt unit test

- Em hỏi: Hai active customer versions có thể nhân doanh thu nhưng generic
  test vẫn xanh. Nên test thế nào?
- Agent trả lời: Tạo fixture có hai active versions, hai completed orders và
  expected revenue 170 thay vì 340.
- Em chọn: Viết native dbt unit test và chọn active version mới nhất trước khi
  join.
- Lần chạy đầu lỗi valid_to rỗng và cú pháp cũ. Em sửa rồi chạy lại.
- Kết quả: dbt 23/23 pass. Evidence ở
  reports/evidence/dbt_run_results.json.

## 7. Cách tạo evidence

- Em hỏi: Chạy reset sẽ sửa source data, có cách nào tạo evidence mà không
  đụng dữ liệu gốc không?
- Agent trả lời: Chạy trong clean-room tạm, dùng chung UTC clock và lưu SHA-256.
- Em chọn: Dùng scripts/verify_lab.py làm lệnh kiểm tra chính trên Windows.
- Test: Verifier chạy pytest, baseline, GX, dbt, incident và bonus.
- Kết quả: PASS; hash nằm trong reports/evidence/verification_summary.json.

## 8. Bonus

- Em hỏi: Chỉ liệt kê bonus đã làm thì có đủ không?
- Agent trả lời: Không. Phải chỉ ra baseline bỏ sót gì và phần nâng cấp xử lý
  được gì.
- Em chọn: Chứng minh 7 mục đã có code thật: MAD/same-weekday, dbt unit, GX
  action, quarantine, column lineage, multi-window và RAG drift.
- Em không chọn thêm Soda, Elementary hoặc OpenLineage vì chưa có execution
  evidence và rubric chỉ tính tối đa 15 điểm.
- Test: scripts/run_bonus_evidence.py trả 7/7 pass. Candidate trước cap là 33,
  cap là 15; giảng viên quyết định điểm.
- Evidence: reports/evidence/bonus_evidence.json.
