# Validation — 2026-09-30

## Tích hợp graph/context vào v2 — 2026-09-30

- 31 tests pass trong 96.86s (Python 3.11, pytest 8.4.2, Windows). JUnit: `C:/specmind/pe_runs/v2_context_validation/tests.xml`.
- Ruff check và format check pass. Kiểm thử bao gồm prompt baseline không đổi, context prompt, kiểm tra provenance, bốn nhánh A/B/C/D, resume và từ chối so sánh corpus không khớp.
- Wheel cuối build và cài vào `tmp/v2_context_install_final` thành công; isolated imports xác nhận đủ prompts, bản sửa event và không import legacy `paper_eval`. SHA256 wheel: `328eb47f9f57bd0d5fd35f99108e64981b22786526495f51b4a45586ae669509`. Import trực tiếp file wheel dạng ZIP không hỗ trợ module execution vì module cần đọc file pytest plugin; dùng wheel đã cài bằng pip.
- Graph Marshmallow 4.3.0 được kiểm tra với commit `b596fdb0d1bda68ae400ab29e1f1bde2f06900b3`: 1104 nodes, 1257 edges, 38 source files. Hash và vị trí source khớp; cho phép khác biệt newline LF/CRLF.
- Cả 10 repository_context dựng lại ở nhánh D khớp chính xác pack cung cấp. Tổng cộng 30 packs B/C/D nằm trong budget 8000 ký tự.
- Smoke trên Marshmallow thật với `assert True`, không gọi API LLM: `post_dump`, `set_value`, `timedelta_to_microseconds` hoàn tất correctness và mutation; 19/19 mutants có paired outcome, không unknown. Test suite kill 17; assertion kill 0 đúng với assertion mô phỏng.
- `validates` ban đầu không qua negative control vì nhánh xử lý invalid nodeid làm mất event đã ghi. Đã sửa giữ event và thêm regression test. Chạy lại riêng hàm này: negative control được xác nhận, correctness pass, không có mutant nên mutation score để null.
- Run ban đầu được giữ tại `pe_runs/v2_context_integration_430_smoke` (còn 1 unfinished); run xác nhận sau sửa tại `pe_runs/v2_context_validates_verified` (0 unfinished). Đây là hai run riêng, không gộp thành một thí nghiệm hoàn chỉnh.
- `usage_summary` vẫn để trống; chưa tạo tổng hợp observed preconditions bằng LLM. Context hiện dùng snippet và quan hệ graph.
- Chưa chạy thí nghiệm LLM thật trên 10 hàm × 4 nhánh; chưa kiểm chứng chất lượng assertion sinh bằng context. Không dùng kết quả smoke để kết luận context cải thiện mutation score.

## Kiểm chứng nền tảng — 2026-09-26

## Đã kiểm thử

- 22 tests pass (pytest 8.4.2, Python 3.11, Windows). JUnit: `C:/specmind/pe_runs/v2_release/tests.xml`.
- Ruff check + format check pass; pip check không có dependency conflict.
- Build wheel thành công, cài vào thư mục độc lập và import trong Python isolated mode.
- Không import package `paper_eval` hoặc các vendor cũ khi chạy wheel.
- Kiểm tra reuse export trên 28 functions: 94 mutants, 5 hàm export bằng 0. Không thiếu file, không mismatch original mutant source.
- Pilot Marshmallow thật bằng wheel: `timedelta_to_microseconds`, assertion cố định, không gọi API LLM.
- Pilot: requested/collected/executed/covered đều 25; assertion thực thi 82 lần trên original.
- Correctness pass; test suite kill 8/8; assertion kill 8/8; combined 8/8; new assertion kill 0/8.
- Pilot mất 49.73s trong môi trường kiểm thử này; resume 0.21s, dùng lại kết quả. Đây không phải ước tính cho full run.
- Các source cũ được chụp hash trong provenance.json vẫn không đổi.

## Lỗi phát hiện khi kiểm tra môi trường sạch

Pilot wheel đầu tiên bị `ZoneInfoNotFoundError: America/Chicago` trong conftest do thiếu tzdata. Đã bổ sung `tzdata==2025.2` vào dependency Marshmallow và lock. Pilot sau sửa pass. Run lỗi vẫn giữ tại `pe_runs/v2_release/marshmallow_wheel_smoke` để truy vết; run đã xác nhận là `pe_runs/v2_release/marshmallow_wheel_verified`.

## Phạm vi chưa xác nhận

- Docker daemon không chạy trong lượt này; chưa build/run container.
- Native mutmut export 2.5.1 yêu cầu Linux/Docker, chưa smoke fresh export; reuse toàn bộ export cũ đã được kiểm tra.
- Chưa gọi API LLM thực. Multi-turn logic được kiểm thử bằng response mô phỏng, gồm explore/submit, fail/unavailable, giữ best và resume sau gián đoạn.
- Chưa chạy full 28 hàm, pandas/numpy, PR/EvalPlus hoặc call graph.

## Bằng chứng

- `C:/specmind/pe_runs/v2_release/marshmallow_wheel_verified/report.md`
- `C:/specmind/pe_runs/v2_release/marshmallow_wheel_verified/functions.csv`
- `C:/specmind/pe_runs/v2_release/marshmallow_wheel_verified/attempts.csv`
- `C:/specmind/pe_runs/v2_release/marshmallow_wheel_verified/mutants.csv`
- `C:/specmind/pe_runs/v2_release/wheel_validation.json`
- `C:/specmind/pe_runs/v2_manifest_audit/report.json`
