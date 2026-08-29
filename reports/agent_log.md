# Nhật ký quyết định khi sử dụng AI Agent

Nhật ký chỉ lưu các quyết định kỹ thuật quan trọng theo đúng chuỗi
**giả thuyết → đề xuất → kiểm thử/minh chứng → chấp nhận/từ chối/chỉnh sửa**,
không sao chép toàn bộ hội thoại.

## Quyết định 1 — Contract phải bắt được type drift và freshness

- **Giả thuyết:** numeric string, giá trị không hữu hạn và timestamp cũ có thể
  qua starter validator dù schema nguồn đã drift.
- **Đề xuất của agent:** dùng strict type mask, parse datetime nhiều timezone về
  UTC, thêm freshness clock và ánh xạ severity → action.
- **Kiểm thử/minh chứng:** các ca contract trong
  `tests_acceptance/test_hidden_proxy.py`; kết quả raw trong
  `reports/evidence/pytest_results.xml`.
- **Kết luận:** **Chấp nhận sau khi chỉnh sửa.**
- **Lý do:** fixture public ban đầu hard-code ngày 28/08 nên tự stale theo thời
  gian. Fixture được chuyển sang thời gian tương đối; production validator
  không có nhánh nhận diện hoặc bỏ qua test.

## Quyết định 2 — Auto anomaly phải xử lý seasonality và outlier

- **Giả thuyết:** Z-score trên toàn history gây false positive cuối tuần và một
  outlier lớn có thể che volume drop thật.
- **Đề xuất của agent:** ưu tiên `same_segment_history`, dùng MAD khi đủ mẫu,
  fallback Z-score và có noise floor khi MAD bằng 0.
- **Kiểm thử/minh chứng:** weekend hợp lệ không bị báo; history có outlier vẫn
  bắt current 180; constant history vẫn bắt current 40; NaN/Infinity không tạo
  `score=NaN`.
- **Kết luận:** **Chấp nhận.**
- **Lý do:** giảm false positive theo seasonality mà không cần mô hình ML phức
  tạp; incident drill 600→150 vẫn được phát hiện.

## Quyết định 3 — Distribution drift không thể chỉ so mean

- **Giả thuyết:** hai batch cùng mean nhưng khác variance/tail sẽ lọt qua
  mean-ratio detector.
- **Đề xuất của agent:** kết hợp two-sample KS tự cài đặt với robust
  location/scale effect.
- **Kiểm thử/minh chứng:** baseline quanh 10 và current gồm 0/20 có cùng mean
  nhưng bị phát hiện; permutation của cùng distribution không bị báo.
- **Kết luận:** **Chấp nhận.**
- **Lý do:** bắt đúng failure starter bỏ sót và không thêm dependency vào core
  stable API.

## Quyết định 4 — Chốt bộ hidden-proxy trước khi sửa implementation

- **Giả thuyết:** 10 public tests không đủ chứng minh robustness của chín stable
  APIs.
- **Đề xuất của agent:** tạo đúng 20 test functions độc lập, xa các threshold
  mơ hồ, phủ contract, anomaly, distribution, SLO, lineage và RAG.
- **Kiểm thử/minh chứng:** implementation cuối đạt 20/20 hidden-proxy cases.
  Hai GX integration tests được tách riêng; tổng suite là 32 tests.
- **Kết luận:** **Chấp nhận.**
- **Lý do:** test dựa trên invariant của đề, không dựa trên output của code đã
  viết; stable interface trong `student_api.py` được giữ nguyên.

## Quyết định 5 — Severity phải điều khiển hành động pipeline

- **Giả thuyết:** chỉ gắn nhãn `severity` nhưng luôn exit 0 và không
  quarantine thì chưa phải reliability control.
- **Đề xuất của agent:** GX 1.21 Suite → ValidationDefinition → Checkpoint →
  custom Action; warning tiếp tục, critical block và materialize dòng lỗi.
- **Kiểm thử/minh chứng:** `reports/evidence/gx_action_drill.json` ghi nhận
  warning case = `warn`, exit 0, quarantine 0; duplicate case = `block`,
  exit 1, quarantine đúng 2 rows.
- **Kết luận:** **Chấp nhận.**
- **Lý do:** Action chạy thật và tạo JSON/CSV, không phải log tự mô tả.

## Quyết định 6 — SCD join phải có native dbt unit test

- **Giả thuyết:** hai active customer versions có thể nhân đôi order facts dù
  generic not-null/unique và aggregate nonnegative đều xanh.
- **Đề xuất của agent:** fixture gồm hai active + một inactive version, hai
  completed + một pending order; model chọn deterministic latest active row.
- **Kiểm thử/minh chứng:** expected 2 rows/170 revenue; completed non-USD bị
  fail closed; raw dbt result nằm ở
  `reports/evidence/dbt_run_results.json`.
- **Kết luận:** **Chấp nhận sau hai lần chỉnh sửa.**
- **Lý do:** lần chạy đầu làm lộ `valid_to=''` cast lỗi và cú pháp deprecation;
  sửa bằng `trim/nullif/try_cast` và `arguments.values`, sau đó đạt 23/23.

## Quyết định 7 — Baseline và RCA phải có nguồn gốc tái lập

- **Giả thuyết:** reset cố định 600 rows vào cuối tuần làm baseline khỏe bị báo
  anomaly; report không có hash input thì khó audit.
- **Đề xuất của agent:** reset theo median của UTC weekday, dùng một reference
  clock/run, lưu SHA-256 input, chạy clean-room, incident drill và recovery
  assertions.
- **Kiểm thử/minh chứng:** `reports/evidence/healthy_baseline_metrics.json`,
  `reports/evidence/incident_drill.json` và
  `reports/evidence/verification_summary.json`; clean-room có
  contract 0 lỗi, anomaly false, freshness khỏe, GX PASS và dbt PASS.
- **Kết luận:** **Chấp nhận sau khi chỉnh sửa.**
- **Lý do:** clean-room đầu tiên phát hiện DuckDB không tự tạo parent directory;
  `sync_dbt_seeds.py` được sửa để provision `warehouse/` trước build.
