# Tạo dataset và mutant từng bước

Thực hiện các lệnh từ root repo, trong môi trường Python đã cài dependency. Nếu đổi đường dẫn ví dụ, đổi thống nhất ở mọi bước. Không build package hoặc pip install trong lúc pipeline đang chạy.

## 1. Raw: hàm và liên kết test

```powershell
python -m specmind_prep extract --repo checkouts/marshmallow --output artifacts/manual/raw
```

Chỉ quan sát một hàm và một file test:

```powershell
python -m specmind_prep extract --repo checkouts/marshmallow --output artifacts/pilot/raw --task marshmallow.class_registry.get_class --test tests/test_registry.py
```

`--task` và `--test` có thể lặp. `--documented-only` bật lọc symbol được tài liệu RST liệt kê. `--expected-commit SHA` kiểm tra commit; `--timeout 1800` giới hạn lượt trace. Không thêm `--task` thì lấy mọi hàm ứng viên có docstring.

| File trong raw/ | Nội dung |
|---|---|
| `problems.jsonl` | Một đối tượng JSON mỗi dòng: function ID, module, source, docstring, vị trí |
| `tests.jsonl` | Liên kết function ID với pytest nodeid, đường dẫn test và report trace |
| `trace.json` | Test đã collect, các liên kết quan sát và report từng phase |
| `trace.log` | Output pytest |
| `command.json` | Lệnh thực thi và thư mục làm việc |
| `summary.json` | Số hàm, liên kết, test, hàm chưa liên kết |
| `manifest.json` | Trạng thái, môi trường, cấu hình và hash file |

Liên kết chỉ được ghi trong call phase, không lấy các lời gọi chỉ xảy ra lúc setup fixture. Nodeid giữ nguyên tham số, kể cả chuỗi có `::`. Raw có thể chứa test gốc fail; test đó sẽ được xử lý ở bước lọc.

**Để trace đủ test:** bỏ tùy chọn dừng sớm `-x`/`--maxfail` trong cấu hình pytest của checkout. Kiểm tra `trace.log` không báo dừng sớm; `complete` của raw chưa tự phát hiện đầy đủ trường hợp này. Nếu dùng `--exclude-config path.json`, bảo đảm file tồn tại; nội dung là mảng pattern/reason như bảng tham số. Xem `excluded_tests` trong summary để xác nhận exclusions thực sự được áp dụng.

## 2. Lọc test tới assertion

```powershell
python -m specmind_prep dataset --raw artifacts/manual/raw --repo checkouts/marshmallow --output artifacts/manual/prepared --timeout 120 --batch-size 500
```

Với mỗi hàm, giữ test pass khi chèn `assert True`, rồi fail có bằng chứng tại assertion khi thay bằng `assert False`. Exception trước return, lỗi collection/setup hoặc test nuốt AssertionError không được coi là đạt điều kiện.

- `problems.jsonl`, `tests.jsonl`: dữ liệu đã chọn.
- `results.json`: quyết định giữ/loại từng test theo hàm.
- `excluded_functions.json`: hàm bị loại và lý do.
- `progress.json`, `summary.json`: tiến độ và số lượng.
- `functions/<hash>/`: input, kết quả probe và bằng chứng pytest.

`selected_links` đếm cặp hàm–test; một test có thể liên quan nhiều hàm. `unique_tests` là số nodeid khác nhau. `pending_functions` khác 0 nghĩa là chưa hoàn tất; kiểm tra lỗi rồi resume. `--force` buộc đánh giá lại bước lọc, chỉ dùng khi có lý do cụ thể.

## 3. Chọn scope mutation

```powershell
python -m specmind_prep scope --dataset artifacts/manual/prepared --repo checkouts/marshmallow --output artifacts/manual/scope --timeout 300
```

Đếm dòng thân hàm sau khi chuẩn hóa AST, bỏ docstring đầu; không đếm comment hay dòng trống. Hàm dưới 5 dòng được xét thêm callee nội bộ trực tiếp, không mở rộng đệ quy.

- `scope.json`: root, số dòng, target và cách xác định callee.
- `targets.json`: source của các target duy nhất.
- `trace/`: bằng chứng lời gọi.

`observed` nghĩa là quan sát được trong test; `lexical_unobserved` là candidate tìm từ source nhưng chưa quan sát. Hai trạng thái không tương đương coverage.

## 4. Export mutant

Docker cần hoạt động trước khi chạy:

```powershell
docker info
docker build -t specmind-paper-eval-mutmut-py312:latest -f docker/mutmut-worker.Dockerfile .
python -m specmind_prep mutants --scope artifacts/manual/scope --repo checkouts/marshmallow --output artifacts/manual/mutants --timeout 1800
```

`--image` chọn image khác. `--timeout` dành cho mutmut. Package và checkout được mount chỉ đọc; output có mount ghi riêng. Một target dùng chung chỉ export một lần. Mỗi mutant thay đúng một target; assertion vẫn ở root.

| File trong mutants/ | Dùng để xem |
|---|---|
| `functions.json` | Mutant ứng với mỗi hàm gốc, target là self hay callee |
| `targets/<hash>/export.json` | Target source, số export, lỗi và các trường hợp bị loại |
| `targets/<hash>/mutmut_function_exports/` | Source/diff mutant và log export; theo đường dẫn trong manifest |
| `summary.json` | Tổng target, mutant duy nhất, cặp hàm–mutant, pending |
| `docker.log` | Output của worker |
| `manifest.json` | Corpus hoàn tất và liên kết với dataset |

Số mutant duy nhất và số cặp hàm–mutant có thể khác nhau vì callee được dùng chung. Các nhãn của công cụ export không phải điểm assertion; điểm đó được đo trong `run`.

## 5. Validate và resume

```powershell
python -m specmind_prep validate --repo checkouts/marshmallow --raw artifacts/manual/raw --dataset artifacts/manual/prepared --scope artifacts/manual/scope
python -m specmind_eval validate-mutants --repo-root checkouts/marshmallow --dataset artifacts/manual/prepared --mutants artifacts/manual/mutants
python -m specmind_prep inspect --output artifacts/manual/prepared
```

Lệnh validate đầu kiểm tra từng artifact; chưa xác nhận quan hệ dataset–scope khi bạn ghép thủ công. Dùng scope tạo từ chính dataset đó, cùng một thư mục pipeline. Manifest cần đầy đủ danh sách file; không tự sửa manifest. Runner từ chối corpus thiếu hoặc không khớp dataset.

Thêm `--resume` vào đúng lệnh đã chạy để tiếp tục sau gián đoạn. Đầu vào/config/môi trường thay đổi thì chọn output mới. Không chạy hai process ghi cùng output. Các bước chuẩn bị không gọi LLM.
