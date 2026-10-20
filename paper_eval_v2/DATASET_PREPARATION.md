# Bước 1: chuẩn bị dataset offline

Đầu vào là danh sách hàm có docstring và các test liên kết đã được extractor tạo. Với Marshmallow, hiện có 59 hàm ứng viên. Bước này không gọi LLM, không tạo hoặc đánh giá mutant.

## Luồng chuẩn bị

1. Đọc problems/tests; kiểm tra ID hàm trùng, đường dẫn và cấu hình.
2. Collect pytest một lần; chuẩn hóa nodeid, lưu test collectable và test không tìm thấy.
3. Với từng hàm, xác minh source runtime khớp dataset, chạy toàn bộ test liên kết với `assert True`.
4. Chạy `assert False` trên các test pass ở lượt True.
5. Giữ test fail ở call phase, có `assertion_violation` của postcondition, setup/teardown pass và không có lỗi runtime trong postcondition.
6. Lưu checkpoint từng hàm; xuất dataset đã lọc và manifest sau cùng.

Test không gọi hàm, chỉ gặp exception trước return, nuốt AssertionError hoặc fail sẵn bị loại. Mutant chưa tồn tại trong bước này. Hai probe dùng source gốc. Test có thể gọi hàm nhiều lần; điều kiện chọn là có ít nhất một postcondition failure quan sát được, không chứng minh mọi nhánh/input đều được phủ.

Hook chỉ thực thi postcondition ở pytest call phase. Các lời gọi khi import/collect hoặc setup/teardown vẫn chạy hàm gốc nhưng không thực thi assertion. Nhờ vậy `assert False` không phá collection hoặc fixture setup, đồng thời phạm vi nhất quán với trace call phase. Source guard phân biệt implementation thật với các khai báo `@overload` cùng tên.

## Chạy toàn bộ 59 hàm

```powershell
python -m paper_eval_v2 prepare-dataset --problems datasets/marshmallow_all_docstrings_20261004/marshmallow_problems.jsonl --tests datasets/marshmallow_all_docstrings_tests_v2_20261004_verified/tests_valid.jsonl --repo-root paper_eval/.cache/clones/marshmallow-code/marshmallow/latest --output datasets/marshmallow_prepared_59_20261004_verified
```

Mặc định timeout 120 giây **mỗi batch pytest**, batch tối đa 500 test và tự chia nhỏ nếu command quá dài. Dùng `--pytest-timeout` và `--batch-size` để thay đổi. Timeout 0 tắt giới hạn. Chạy lại cùng output phải thêm `--resume`; thay source, inputs, environment, code chuẩn bị hoặc settings phải chọn output mới.

```powershell
python -m paper_eval_v2 prepare-dataset --problems datasets/marshmallow_all_docstrings_20261004/marshmallow_problems.jsonl --tests datasets/marshmallow_all_docstrings_tests_v2_20261004_verified/tests_valid.jsonl --repo-root paper_eval/.cache/clones/marshmallow-code/marshmallow/latest --output datasets/marshmallow_prepared_59_20261004_verified --resume
python -m paper_eval_v2 validate-dataset --dataset datasets/marshmallow_prepared_59_20261004_verified --repo-root paper_eval/.cache/clones/marshmallow-code/marshmallow/latest
```

`validate-dataset` chỉ đọc và kiểm tra hash/source/environment/evidence, không chạy pytest. Nếu chuẩn bị còn pending, CLI trả exit code 2 và manifest chưa complete; loader từ chối dùng dataset đó. Resume giữ hàm đã xong và chỉ làm lại hàm pending. Các probe hoàn tất được cache; `--resume --force` chạy lại cả hai probe.

## Debug một hàm

Thêm `--tasks marshmallow.decorators.pre_dump` và dùng output riêng, ví dụ `tmp/mm_prepare_pre_dump`. Có thể chọn nhiều ID chính xác sau `--tasks`. Không ghi đè artifact của lần chuẩn bị chính.

## Artifact

```text
manifest.json              schema, fingerprint, source/environment, settings, hashes, complete
problems.jsonl             chỉ các hàm có test đạt điều kiện
tests.jsonl                test đã lọc, có nodeid chính xác và problem_id
summary.json               số hàm ready/excluded/pending, liên kết và test riêng biệt
progress.json              checkpoint tiến độ
results.json               kết quả, test được chọn, quyết định từng test cho mọi hàm
excluded_functions.json    hàm bị loại và lý do
collection/                kết quả collect và log đầy đủ
functions/<hash>/
  input.json               source và test trước lọc
  filter.json              kết quả hai probe, evidence và lý do từng test
  result.json              checkpoint của hàm
  arms/<hash>.json          assertion True/False, pytest events và toàn bộ log
```

Không tự coi timeout/lỗi hạ tầng là test không đạt. Probe chưa hoàn tất giữ trạng thái pending, không xuất tập chọn một phần cho hàm đó. Source mismatch được ghi rõ và loại hàm. Source/environment được kiểm tra lại cuối bước để tránh publish kết quả khi checkout thay đổi trong lúc chạy.

`load_prepared_dataset(directory, repo)` là API đọc artifact dành cho bước tích hợp luồng chính sau này. Fingerprint preparation gồm code chuẩn bị; kiểm tra khi tiêu thụ chỉ yêu cầu source/environment và artifact giữ nguyên, nên sửa code runner không bắt phải lọc lại dataset.

Hiện đây là bước chuẩn bị độc lập. Việc chuyển runner sang bắt buộc đọc artifact này và mutant chuẩn bị sẵn sẽ làm ở các bước tiếp theo. Không dùng runner cũ để thay thế bước chuẩn bị.
