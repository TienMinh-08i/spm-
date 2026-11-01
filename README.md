# SpecMind

Công cụ tạo dataset từ mã nguồn và test của project Python được cấu hình, tạo các biến thể lỗi (*mutant*), rồi sinh và đánh giá assertion bằng mô hình ngôn ngữ.

Một assertion mô tả điều kiện cần đúng sau khi hàm trả về. Dataset chứa hàm và các test đi tới vị trí kiểm tra assertion. Mutant là một thay đổi nhỏ trong code, dùng để kiểm tra assertion có phát hiện hành vi sai hay không.

```text
Checkout → gather → trace → lọc True/False → scope → mutant
                                                            ↓
                                      sinh assertion → đánh giá → báo cáo
```

## 1. Chuẩn bị môi trường

Cần Git, Python 3.11 và Docker Desktop chạy Linux containers nếu muốn tạo mutant. Có thể trích xuất và lọc dataset mà chưa cần Docker. API key chỉ cần ở bước sinh assertion bằng LLM.

Các lệnh dưới đây dùng PowerShell trên Windows. Luôn chạy từ thư mục gốc repo:

```powershell
git clone https://github.com/TienMinh-08i/spm-.git
cd spm-
python --version
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.lock
python -m pip install --no-deps -e .
python -m pip check
python -m specmind_eval --help
```

Trên Linux/macOS, kích hoạt bằng `source .venv/bin/activate`; các lệnh `python -m ...` giữ nguyên. Dependency lock được dùng với Python 3.11; các nền tảng khác cần được kiểm tra trong môi trường của bạn.

Nếu PowerShell không cho kích hoạt môi trường, thay `python` trong các lệnh bằng `.\.venv\Scripts\python.exe`. Không cần đổi policy hệ thống. Mỗi terminal mới cần kích hoạt lại môi trường.

## 2. Lấy source thư viện cần kiểm tra

```powershell
git clone https://github.com/marshmallow-code/marshmallow.git checkouts/marshmallow
git -C checkouts/marshmallow checkout 60a803821466f3e537fa2729d65e1aaf7df9d2a4
python -m pip install --no-deps -e ./checkouts/marshmallow
python -m pip install simplejson==3.20.2 tzdata==2025.2
python -m specmind_prep doctor --repo checkouts/marshmallow --project configs/projects/marshmallow.json
```

Commit trên khớp file project mẫu. Khi đổi commit, cập nhật `commit` trong file project và dùng output mới. Hoàn tất cài đặt dependency trước khi chạy pipeline.

## 3. Tạo config chuẩn bị dữ liệu

```powershell
Copy-Item configs/preparation.example.json configs/preparation.local.json
```

File mẫu trỏ tới `configs/projects/marshmallow.json`. File project mô tả package, thư mục test, PYTHONPATH, commit và dependency; config preparation mô tả đường dẫn checkout/output và tham số của lần chạy. Xem [cấu hình](docs/CONFIGURATION.md). Có thể dùng nguyên nội dung mẫu. Nếu chỉ muốn thử nhanh một hàm, thêm hai trường sau vào JSON trước khi chạy:

```json
"tasks": ["marshmallow.class_registry.get_class"],
"tests": ["tests/test_registry.py"]
```

Nhớ đặt dấu phẩy giữa các trường JSON. Khi chuyển từ pilot sang toàn bộ, bỏ `tasks` và `tests`, đổi `output` thành thư mục mới. Xem [bảng tham số](docs/CONFIGURATION.md) để hiểu từng trường.

## 4. Tạo dataset

```powershell
python -m specmind_prep all --config configs/preparation.local.json
```

Lệnh này trích xuất hàm, quan sát test liên quan và lọc True/False; mặc định dừng sau dataset. Không gọi LLM hay Docker.

Với config mẫu, đọc `artifacts/marshmallow/prepared/summary.json`: `ready_functions` là số hàm giữ lại, `selected_links` là số liên kết hàm–test, `pending_functions` là số hàm chưa xử lý xong. Kiểm tra thêm `manifest.json` có `complete: true` trước khi tiếp tục.

## 5. Chọn scope và tạo mutant

Scope xác định phần code được phép thay đổi. Hàm dưới 5 dòng thân hàm theo AST được mở rộng tới callee nội bộ trực tiếp — tức hàm được nó gọi. Assertion vẫn được kiểm tra tại hàm gốc.

```powershell
python -m specmind_prep all --config configs/preparation.local.json --through scope --resume
docker info
docker build -t specmind-marshmallow-mutmut:latest -f docker/mutmut-worker.Dockerfile --build-arg 'PROJECT_DEPENDENCIES=["simplejson==3.20.2","tzdata==2025.2"]' .
python -m specmind_prep all --config configs/preparation.local.json --through mutants --resume
python -m specmind_eval validate-mutants --dataset artifacts/marshmallow/prepared --mutants artifacts/marshmallow/mutants --repo-root checkouts/marshmallow
```

`--resume` dùng lại bước đã hoàn thành khi đầu vào không đổi. Image trong config phải trùng tên image vừa build. Chi tiết các bước và file output: [Pipeline](docs/PIPELINE.md).

## 6. Chạy thử không gọi API

```powershell
python -m specmind_eval run --config configs/offline.example.json --output runs/smoke
```

Config offline dùng assertion cố định để kiểm tra đường chạy; không đo chất lượng sinh assertion. Nó vẫn cần dataset và mutant đã chuẩn bị. Nếu đổi output ở bước chuẩn bị, sửa `prepared_dataset` và `prepared_mutants` trong config chạy cho khớp.

## 7. Sinh và đánh giá assertion bằng LLM

```powershell
Copy-Item configs/prepared.example.json configs/run.local.json
```

Mở `configs/run.local.json`, thay `REPLACE_WITH_YOUR_MODEL_ID` bằng model ID của endpoint bạn dùng. Mẫu đặt `max_turns: 24`; giảm số này hoặc thêm `tasks` để thử phạm vi nhỏ.

Nhập API key trong PowerShell mà không ghi key vào file config:

```powershell
$secureKey = Read-Host 'API key' -AsSecureString
$env:OPENAI_API_KEY = [System.Net.NetworkCredential]::new('', $secureKey).Password
```

Nếu dùng endpoint tương thích khác, đặt thêm `$env:OPENAI_BASE_URL = 'https://YOUR_ENDPOINT/v1'`. Công cụ cũng đọc `OPENROUTER_API_KEY` khi không có `OPENAI_API_KEY`. Công cụ không tự đọc file `.env`. Trên Bash có thể dùng `read -rs OPENAI_API_KEY; export OPENAI_API_KEY`.

```powershell
python -m specmind_eval run --config configs/run.local.json --output runs/experiment-01
```

Lệnh này gọi API và có thể phát sinh phí. Luồng đánh giá đọc dữ liệu đã chuẩn bị; không lọc test hay tạo mutant trong vòng sinh assertion.

## 8. Đọc kết quả và tiếp tục

```powershell
python -m specmind_eval report --output runs/experiment-01
python -m specmind_eval run --config configs/run.local.json --output runs/experiment-01 --resume
```

Đọc `report.md` trước; dùng `functions.csv`, `attempts.csv`, `mutants.csv` để xem chi tiết. Nếu thay source, config, code hoặc dependency, tạo output mới. [Kết quả và debug](docs/DEBUGGING.md) giải thích metric, file bằng chứng và replay một trường hợp.

## Tra cứu

| Cần làm gì? | Đọc ở đâu? |
|---|---|
| Chỉnh model, số attempt, timeout, batch, tập hàm/test | [Tham số](docs/CONFIGURATION.md) |
| Chạy riêng từng bước tạo dữ liệu, hiểu output | [Pipeline](docs/PIPELINE.md) |
| Đọc kết quả, replay, xử lý lỗi | [Kết quả và debug](docs/DEBUGGING.md) |
| Kiểm tra cài đặt và test code | [Kiểm thử](docs/TESTING.md) |
| Tìm module cần sửa và hiểu phụ thuộc | [Cấu trúc code](docs/ARCHITECTURE.md) |

`checkouts/`, `artifacts/`, `runs/`, `.venv/` và `*.local.json` là dữ liệu/cấu hình trên máy bạn, được Git bỏ qua. Sau khi cài đặt, dùng `specmind-eval` cho đánh giá và `specmind-prep` cho chuẩn bị. Hai lệnh tương đương là `python -m specmind_eval` và `python -m specmind_prep`. Lệnh `python -m paper_eval_v2` vẫn chuyển tiếp tới CLI đánh giá.
