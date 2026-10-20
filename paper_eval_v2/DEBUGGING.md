# V2: chạy chính và debug độc lập

Thực thi các ví dụ từ `C:\specmind`. Mỗi output là thư mục mới. Không chạy LLM khi chỉ muốn kiểm tra nodeid, candidate, mutant, metric hoặc report.

## Chạy chính

```powershell
python -m paper_eval_v2 run --config paper_eval_v2/configs/marshmallow.repaired.local.json --output pe_runs/mm_repaired
python -m paper_eval_v2 run --config paper_eval_v2/configs/marshmallow.repaired.local.json --output pe_runs/mm_repaired --resume
```

Config repaired dùng bản test đã khôi phục 12 nodeid bị loại nhầm, giữ nguyên 28 hàm, bật `assertion_policy: behavioral`. Lệnh `run` có thể gọi API trả phí. Đặt `tasks` trong một bản sao config để pilot trước khi chạy toàn bộ. Không resume run cũ qua thay đổi code/config/dataset; fingerprint sẽ từ chối. Debug run cũ được phép nhưng luôn đánh giá bằng engine hiện tại và ghi output riêng.

Luồng: resolve dataset → preflight/source/coverage → verified mutant bundle → LLM → lưu response → validate candidate → correctness → assertion mutation feedback → checkpoint → chọn best submitted candidate. Correctness fail bỏ qua mutation. Neutral suite không nằm trong vòng feedback này.

Bộ lọc True/False nằm riêng trong bước offline `prepare-dataset`, xem [DATASET_PREPARATION.md](DATASET_PREPARATION.md). Probe/evidence/decisions được lưu trong artifact preparation. Runner hiện còn preflight cũ; việc chuyển runner sang chỉ đọc artifact là bước tiếp theo cùng với chuẩn bị mutant.

## Debug không gọi LLM

Thay function/attempt/mutant bằng giá trị có trong run. `debug attempt` và `debug candidate` dùng chung evaluator và policy với luồng chính. `debug candidate --assertion-file` cho phép thử assertion từ file UTF-8; không cần sửa source evaluator.

```powershell
python -m paper_eval_v2 debug nodeid --tests datasets/out_marshmallow_docfilter/marshmallow_tests.jsonl --output tmp/nodeids.json
python -m paper_eval_v2 debug attempt --run pe_runs/mm_repaired --function marshmallow.decorators.pre_dump --attempt 3 --output pe_runs/debug_pre_dump_a3
python -m paper_eval_v2 debug mutant --run pe_runs/mm_repaired --function marshmallow.decorators.pre_dump --attempt 3 --mutant 1 --output pe_runs/debug_pre_dump_m1
python -m paper_eval_v2 debug candidate --run pe_runs/mm_repaired --function marshmallow.decorators.pre_dump --assertion-file tmp/candidate.py --output pe_runs/debug_candidate
python -m paper_eval_v2 debug metrics --run pe_runs/mm_repaired --function marshmallow.decorators.pre_dump --output pe_runs/debug_metrics
```

- `--nodeid 'tests/test_file.py::test_name[param]'`: chỉ chạy linked case đó; output ghi `partial_test_scope=true`, không thay kết quả benchmark chính.
- `--force`: bỏ cache và thực thi lại trong output debug; dùng khi nghi ngờ flaky test.
- Mỗi output debug phải nằm ngoài source run/checkout. Debug đọc cache cũ khi đúng identity và ghi bản bằng chứng được dùng vào output riêng.
- `result.json`: kết quả candidate và mutant IDs. `arms/<hash>.json`: full pytest log, events, case outcomes, assertion và mutant source. `debug.json`: phạm vi, nguồn, attempt và identity.
- Debug mutant vẫn cần baseline/correctness để tránh chấm mutant cho assertion sai. Nó chỉ chạy mutant được chọn, không chạy các mutant khác.
- `debug metrics` chỉ đọc evidence đã lưu; `report` chỉ dựng bảng. Hai lệnh này không chạy pytest.

## Suite và paired report

```powershell
python -m paper_eval_v2 evaluate-suite --run pe_runs/mm_repaired --output pe_runs/mm_suite
python -m paper_eval_v2 join-suite --run pe_runs/mm_repaired --suite pe_runs/mm_suite --output pe_runs/mm_paired
python -m paper_eval_v2 report --output pe_runs/mm_repaired
```

Join không chạy test. Nó kiểm tra fingerprint nguồn run, identity source/engine/dependencies/pytest settings, input hash, baseline eligible nodeids và hash từng mutant. Run cũ thiếu identity không được tự ghép. Thiếu outcome giữ unknown, không tự coi là survived.

- `assertion`: denominator là số mutant biết outcome assertion.
- `assertion_on_exported`: lower/upper bound trên toàn export, unknown báo riêng. Feedback và chọn best dựa lower bound.
- `test_suite`, `combined`, `new_assertion`: cùng denominator paired-known.
- `combined = suite union assertion`; `new_assertion = assertion minus suite` trên paired set.
- Assertion-only run có suite/combined/new là N/A. `unresolved_in_final_mutation_results` là thiếu paired evidence, khác `assertion_unknown`.

## Chuẩn bị test dataset mới

```powershell
python -m paper_eval_v2 prepare-tests --tests datasets/out_marshmallow_docfilter/marshmallow_tests.jsonl --repo-root paper_eval/.cache/clones/marshmallow-code/marshmallow/latest --output datasets/mm_tests_next
```

Lệnh collect checkout, ghi `tests_valid.jsonl` với nodeid chính xác, `tests_invalid.jsonl`, `summary.json`, `collection.log`. Không ghi đè dataset cũ. Collection lỗi thì không xuất một dataset bị lọc một phần. Collectable không đồng nghĩa pass hay phủ đủ input.

## Cache và resume

Cache arm gồm AST assertion (không tính comment), mutant source, problem, tests, source/config checkout, engine, dependency environment và timeout/batch-size. Không reuse cache khi đổi tập test hoặc môi trường. Không tái dùng arm lỗi hạ tầng/runtime assertion. Full pytest log nằm ở artifact, terminal chỉ cần tiến độ.

`generation_done` là đã hết lượt/dừng sinh. `evaluation_complete` là mọi candidate attempt đã có kết quả xác định (correctness fail cũng là kết quả xác định). `done` yêu cầu cả hai. Khi còn unknown, trạng thái `evaluation_pending`; resume chỉ đánh giá lại các attempt chưa rõ, giữ response/usage cũ và tính lại best. Mỗi lần resume chỉ thử một lượt, không retry vô hạn. Các feedback cũ đã dẫn tới response cũ không thể thay đổi quá khứ sinh; bản response và attempt vẫn được giữ để audit.

## Policy và giới hạn

CLI mặc định `behavioral`; `legacy` phải ghi rõ trong config để đối chiếu. Behavioral yêu cầu một assert cuối không nằm trong nhánh, chỉ cho input/return_value, local assignments và các phép gọi/import có trong `policy.py`. Cấm tham chiếu target, metadata dunder, globals không khai báo, gọi lại target và các thao tác sửa input rõ ràng. Các predicate như `inspect.isgenerator(obj)` vẫn được cho phép.

Đây là kiểm tra protocol bảo thủ, **không phải sandbox bảo mật hoặc chứng minh tính thuần**: object tùy biến có thể có property/method gây side effect; một số assertion hợp lệ về ngữ nghĩa có thể bị policy từ chối. `assert True` vẫn được chấp nhận để giữ định nghĩa correctness hiện tại, nhưng không tạo assertion kill. Pass trên test không chứng minh đúng cho mọi input; bổ sung coverage là công việc riêng.

## Kiểm thử nhanh trước khi chạy benchmark

```powershell
python -m pytest paper_eval_v2/tests/test_debug_pipeline.py -q
python -m pytest paper_eval_v2/tests -q
```

Bộ đầu dùng fixture/mock nhẹ để kiểm tra nodeid, policy, resume và khóa cache. Bộ đầy đủ có subprocess pytest để kiểm chứng hook, chạy end-to-end, offline suite, debug cache và join; lâu hơn nhưng không gọi API LLM.
