# Chuẩn bị dữ liệu theo năm bước

Chạy từ root repo sau khi cài package trong venv. Ví dụ dùng checkout ở `checkouts/marshmallow` và file project `configs/projects/marshmallow.json`. Mỗi output phải nằm ngoài checkout; dùng output mới khi đổi đầu vào.

## 1. Gather: thu thập hàm

```powershell
python -m specmind_prep gather --repo checkouts/marshmallow --project configs/projects/marshmallow.json --output artifacts/manual/candidates
```

Không chạy pytest và không import code target. Có thể thêm `--task FUNCTION_ID`, `--documented-only` hoặc `--resume`.

| File | Nội dung |
|---|---|
| problems.jsonl | Hàm, source, repo_path, dòng def và code_line tính cả decorator |
| summary.json | Số candidate |
| manifest.json | Schema candidates-v1, project, commit, fingerprint và hash file |

## 2. Trace: quan sát test

```powershell
python -m specmind_prep trace --repo checkouts/marshmallow --candidates artifacts/manual/candidates --output artifacts/manual/links --timeout 1800
```

Thêm `--test PATH_OR_NODEID` để thu hẹp test; `--exclude-config exclusions.json` phải chứa danh sách `{pattern, reason}`. Test có thể fail ở bước này nhưng phải chạy hết tập đã chọn. Trace dừng sớm hoặc timeout không làm hỏng candidates. Đổi tests/exclude/timeout thì dùng một output links mới.

| File | Nội dung |
|---|---|
| tests.jsonl | Các cặp function ID–nodeid quan sát được |
| trace.json, trace.log | Bằng chứng và log pytest |
| trace_config.json, command.json | Vị trí theo dõi và lệnh thực thi; có hash niêm phong |
| summary.json | Hàm liên kết, test và trạng thái pytest |
| manifest.json | Schema links-v1; trỏ fingerprint và hash file candidates |

## 3. Dataset: lọc True/False

```powershell
python -m specmind_prep dataset --repo checkouts/marshmallow --candidates artifacts/manual/candidates --links artifacts/manual/links --output artifacts/manual/prepared --timeout 120
```

Lệnh kiểm tra liên kết candidates–links trước khi chạy test. Giữ test pass khi chèn assert True và fail do assert False tại điểm kiểm tra.

| File | Nội dung |
|---|---|
| problems.jsonl, tests.jsonl | Dataset đã lọc |
| results.json, excluded_functions.json | Quyết định giữ/loại và bằng chứng |
| summary.json, progress.json | Số hàm ready/excluded/pending |
| manifest.json | Hash file, project và lineage của hai artifact đầu vào |

## 4. Scope: chọn nơi tạo mutant

```powershell
python -m specmind_prep scope --repo checkouts/marshmallow --dataset artifacts/manual/prepared --output artifacts/manual/scope
```

| File | Nội dung |
|---|---|
| scope.json, targets.json | Hàm gốc và target/callee được chọn |
| trace/ | Bằng chứng gọi hàm |
| summary.json, manifest.json | Tổng hợp, fingerprint và liên kết dataset |

## 5. Mutants: tạo corpus riêng

```powershell
docker build -t specmind-marshmallow-mutmut:latest -f docker/mutmut-worker.Dockerfile --build-arg 'PROJECT_DEPENDENCIES=["simplejson==3.20.2","tzdata==2025.2"]' .
python -m specmind_prep mutants --repo checkouts/marshmallow --scope artifacts/manual/scope --output artifacts/manual/mutants --image specmind-marshmallow-mutmut:latest
```

Dependencies build arg là mảng JSON lấy từ file project. Image evaluator cũng nhận build arg này nếu cần chạy target trong container. Tool không tự cài dependency của target vào máy host.

| File | Nội dung |
|---|---|
| targets_input.json, targets/ | Đầu vào worker và export từng target |
| functions.json | Mutant đã chuẩn bị cho từng hàm |
| summary.json, manifest.json | Số lượng, hash, project và liên kết dataset/scope |

## Chạy nối tiếp và kiểm tra

```powershell
python -m specmind_prep all --config configs/preparation.local.json
python -m specmind_prep validate --repo checkouts/marshmallow --candidates artifacts/manual/candidates --links artifacts/manual/links --dataset artifacts/manual/prepared --scope artifacts/manual/scope
```

Config có key `project` dùng luồng năm bước; mặc định `all` dừng sau dataset. Dùng `--through scope` hoặc `--through mutants` khi muốn tiếp tục. `inspect --output DIRECTORY` chỉ đọc summary/progress.

Luồng đánh giá đọc prepared dataset và mutant đã chuẩn bị; không tạo/lọc lại trong mỗi attempt. Bộ test cục bộ không cần Docker hay LLM.

## Dữ liệu đã lưu

Không chuyển đổi hoặc ghi đè raw/dataset/run cũ. Để dùng luồng năm bước, tạo candidates/links và output mới. API Python `extract` và CLI `extract`/`--raw` vẫn có facade tương thích cho người dùng hiện có; config không có `project` cũng dùng đường này. Các facade gọi chung gather/trace, không có một engine trace khác. Artifact raw đã lưu vẫn được bộ đọc hash kiểm tra theo schema của nó.
