# Đọc kết quả và debug

## Đọc báo cáo

Trong thư mục `--output` của run:

| File | Nội dung |
|---|---|
| `report.md` | Tổng quan để đọc nhanh |
| `summary.json` | Thống kê máy đọc được |
| `functions.csv` | Kết quả theo hàm |
| `attempts.csv` | Kết quả từng lần sinh/thử assertion |
| `mutants.csv` | Kết quả theo mutant |
| `functions/<hash>/input.json` | Hàm và linked tests đã dùng |
| `functions/<hash>/generation.json` | Hội thoại, response, attempts và candidate được chọn |
| `functions/<hash>/mutants.json` | Corpus dùng cho hàm |
| `functions/<hash>/result.json` | Kết quả hàm |
| `functions/<hash>/arms/` | Log pytest, event và outcome từng lần đánh giá |

Function ID là tên đầy đủ, ví dụ `marshmallow.class_registry.get_class`. Lấy ID từ `functions.csv` hoặc input.json; không dùng tên thư mục hash thay ID.

Correctness pass nghĩa là assertion qua bộ test được chọn trên code gốc. Mutation kill cần bằng chứng assertion violation. `unknown`/`unavailable` nghĩa là chưa đủ bằng chứng, không phải survived. Hàm không có mutant không có mutation score.

Điểm assertion trên outcome đã biết cần đọc cùng số unknown. Lower bound = số kill đã xác nhận / tổng mutant export; upper bound tính thêm các unknown như khả năng kill. Chọn candidate và feedback dùng lower bound để tránh lợi thế từ mất coverage. Chỉ candidate thực sự submit mới được chọn làm best.

## Dựng lại report

```powershell
python -m specmind_eval report --output runs/experiment-01
```

Lệnh chỉ đọc kết quả đã lưu và dựng báo cáo, không gọi LLM hay chạy lại test.

## Replay một attempt

Ví dụ dưới giả định run có hàm và attempt được chỉ định; thay bằng giá trị trong attempts.csv:

```powershell
python -m specmind_eval debug attempt --run runs/experiment-01 --function marshmallow.class_registry.get_class --attempt 1 --output runs/debug-attempt-01
```

Output debug phải nằm ngoài run nguồn và checkout. `--force` bỏ cache để thực thi lại; không cần dùng mặc định. Có thể thêm `--nodeid 'tests/test_file.py::test_name[param]'` để giới hạn đúng một linked test; khi đó kết quả chỉ đại diện phạm vi test đã chọn.

## Thử assertion từ file

Tạo `candidate.py` dạng UTF-8, ví dụ `assert return_value is not None` chỉ khi điều kiện này phù hợp với hợp đồng của hàm. Đây là code Python, không bọc Markdown.

```powershell
python -m specmind_eval debug candidate --run runs/experiment-01 --function marshmallow.class_registry.get_class --assertion-file candidate.py --output runs/debug-candidate-01
```

Lệnh dùng evaluator và policy chung, có thể chạy correctness và mutant trong bundle. Không gọi LLM. Đọc `result.json`, `debug.json` và `arms/` tại output debug.

## Replay một mutant hoặc tính lại metric

Lấy ID đầy đủ từ mutants.csv hoặc mutants.json, không tự đoán số:

```powershell
python -m specmind_eval debug mutant --run runs/experiment-01 --function marshmallow.class_registry.get_class --attempt 1 --mutant 'MUTANT_ID_FROM_REPORT' --output runs/debug-mutant-01
python -m specmind_eval debug metrics --run runs/experiment-01 --function marshmallow.class_registry.get_class --output runs/debug-metrics-01
```

`debug mutant` kiểm tra candidate trước khi chấm mutant đã chọn. `debug metrics` chỉ đọc bằng chứng đã lưu; cần run có kết quả mutation.

## Đo khả năng phát hiện lỗi của test gốc

```powershell
python -m specmind_eval evaluate-suite --run runs/experiment-01 --output runs/suite-01
python -m specmind_eval join-suite --run runs/experiment-01 --suite runs/suite-01 --output runs/paired-01
```

`evaluate-suite` chạy neutral, không gọi LLM; `join-suite` ghép bằng chứng, không chạy test. Combined là hợp các mutant bị suite hoặc assertion kill; new kills là mutant assertion kill mà suite không kill. Metric ghép cần cả hai outcome xác định. Nếu chạy experiment A/B/C/D, `--run` trỏ vào một nhánh cụ thể, ví dụ `runs/context-01/A`.

## Lỗi thường gặp

| Triệu chứng | Cách kiểm tra |
|---|---|
| Không import được package | Kích hoạt đúng venv, cài `pip install --no-deps -e .` từ root |
| Raw không có đủ test | Xem trace.log; kiểm tra selector, exclusions và tùy chọn pytest dừng sớm |
| Hàm/test bị loại | Đọc excluded_functions.json và decisions trong results.json |
| Fingerprint mismatch | Kiểm tra config, source, dependency, thư mục làm việc; dùng output mới khi có thay đổi |
| Docker không phản hồi | Chạy `docker info` riêng; Ctrl+C nếu treo, kiểm tra daemon trước khi export |
| Mutant pending | Đọc docker.log và export.json của target, khắc phục rồi resume |
| API lỗi | Kiểm tra model ID, endpoint, key và timeout; không đưa key vào log chia sẻ |
| Policy từ chối assertion | Xem reason và policy.py; mặc định hạn chế gọi lại target, sửa input, dùng metadata |
| Unknown nhiều | Đọc events/pytest log để phân biệt mất coverage, exception, timeout và lỗi runtime assertion |

Để hiện traceback preparation: `python -m specmind_prep --traceback dataset ...` với các tham số dataset đầy đủ. Các lệnh khác có `--help` riêng. Mã thoát 0 là lệnh thành công; nhóm preparation dùng 1 cho lỗi bắt được và 2 cho công việc còn pending. Argparse cũng dùng 2 cho tham số sai, nên luôn đọc thông báo kèm theo.
