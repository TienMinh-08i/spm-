# Pipeline và debug

| Bước | Module | Bằng chứng |
|---|---|---|
| Raw | preparation/extract.py, documentation.py, trace_plugin.py | problems.jsonl, tests.jsonl, trace.json, trace.log, command.json |
| True/False | prepare_dataset.py, dataset_filter.py, execution.py | results.json, excluded_functions.json, functions/ |
| Scope | mutation_scope.py | scope.json, targets.json, trace/ |
| Mutant | prepare_mutants.py, mutant_worker.py, mutmut.py | functions.json, targets/<hash>/export.json, docker.log |
| Run | runner.py, specmind.py, evaluation.py | generation.json, arms/, reports |
| Debug | debug.py, suite_evaluation.py, join.py | output riêng |

Mỗi bước có manifest, fingerprint và trạng thái complete. Không có engine thứ hai trong preparation. Các lệnh preparation cũ vẫn dùng được; module engine được giữ vị trí để giảm rủi ro đổi import.

## Quy tắc

Raw liên kết test theo call phase, giữ chính xác nodeid kể cả tham số chứa `::`. Setup-only không được coi là liên kết. Raw giữ hàm chưa liên kết và cả test fail để audit, chưa phải dataset dùng chạy chính.

Dataset giữ test pass với True rồi fail với False có bằng chứng assertion được chèn. Collection/setup lỗi, exception trước return hoặc test nuốt AssertionError không đạt điều kiện. Không dùng tổng số 57 làm điều kiện pass; phải kiểm tra danh sách và lý do loại.

Scope đếm dòng AST-unparsed thân hàm, bỏ docstring đầu. Dưới 5 dòng mở rộng thêm callee nội bộ trực tiếp, không đệ quy. Một target dùng chung chỉ export một lần. Mỗi subprocess thay đúng một target; assertion ở hàm gốc. Không tới assertion là unknown, không phải survived.

## Chạy từng bước

```powershell
python -m paper_eval_v2 prepare extract --repo checkouts/marshmallow --output artifacts/pilot/raw --task marshmallow.class_registry.get_class
python -m paper_eval_v2 prepare dataset --raw artifacts/pilot/raw --repo checkouts/marshmallow --output artifacts/pilot/prepared
python -m paper_eval_v2 prepare scope --dataset artifacts/pilot/prepared --repo checkouts/marshmallow --output artifacts/pilot/scope
python -m paper_eval_v2 prepare mutants --scope artifacts/pilot/scope --repo checkouts/marshmallow --output artifacts/pilot/mutants
python -m paper_eval_v2 prepare inspect --output artifacts/pilot/prepared
```

`--task` và `--test` của extract có thể lặp để thu nhỏ phạm vi. `prepare --traceback <bước> ...` hiện traceback. Timeout, selector, exclusions thay đổi cần output mới. `all --resume` xác minh artifact rồi tiếp tục các bước còn thiếu.

## Tìm nguyên nhân

- Raw lỗi: đọc trace.log và command.json, kiểm tra checkout/dependency/selector.
- Hàm bị loại: đọc excluded_functions.json và results.json, xem quyết định từng nodeid.
- Callee thiếu: đọc scope.json và trace; phân biệt lexical với observed.
- Export lỗi: đọc docker.log và export.json của target; khắc phục rồi resume.
- Fingerprint mismatch: đối chiếu code, source, dependency và config; không sửa hash để lách guard.
- Candidate hoặc mutant bất thường: dùng CLI debug replay vào output mới.

RST helper chỉ giữ dependency cần cho đọc tài liệu từ gói cũ; không mang theo pipeline tracing cũ. Các tài liệu kỹ thuật lịch sử có thể chứa đường dẫn máy tác giả; dùng README và tài liệu này cho bản tích hợp.

Hoàn tất build/cài dependency trước khi chạy; không build hoặc pip install đồng thời với preparation/evaluation. Giữ cùng môi trường và thư mục làm việc khi resume vì metadata package cũng thuộc fingerprint.
