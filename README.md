# SpecMind evaluator V2

Gói độc lập để sinh và đánh giá postcondition trên repository Python. Python >= 3.11. Mã engine giữ nguyên so với bản nguồn; không import V1. Gói này không chứa dataset 57 hàm, mutant, checkout hay kết quả thí nghiệm trên máy tác giả.

## Cài đặt

Chạy từ thư mục gốc repo sau khi clone:

```powershell
git clone https://github.com/TienMinh-08i/spm-.git
cd spm-
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r paper_eval_v2/requirements.lock
python -m pip install --no-deps -e ./paper_eval_v2
python -m paper_eval_v2 --help
python -m pytest paper_eval_v2/tests -q
```

Linux: kích hoạt bằng `source .venv/bin/activate`. Cài dependency của repository đích vào cùng môi trường trước khi chuẩn bị dữ liệu. File lock hiện được kiểm tra trên Windows/Python 3.11; xem [VALIDATION.md](VALIDATION.md) về phạm vi xác nhận.

## Bố trí dữ liệu

```text
paper_eval_v2/          engine, prompts, tests và tài liệu kỹ thuật
configs/               config mẫu portable
checkouts/marshmallow/  checkout đúng commit của dataset
raw/                   problems.jsonl và tests.jsonl đầu vào
artifacts/dataset/     kết quả lọc True/False
artifacts/scope/       hàm gốc và callee được chọn
artifacts/mutants/     corpus mutant đã chuẩn bị
runs/                  kết quả chạy và debug
```

Các thư mục dữ liệu được Git bỏ qua. Chuẩn bị raw JSONL từ công cụ tạo dataset riêng; xem [định dạng và bước lọc](paper_eval_v2/DATASET_PREPARATION.md). V2 không tự tải lại bộ 57 hàm. Không mặc định coi artifact từ máy khác hoặc từ công cụ riêng là tương thích: phải chạy validate với source và môi trường hiện tại. Fingerprint thay đổi cần chuẩn bị artifact mới, không bỏ qua guard.

## Chuẩn bị offline, chạy riêng từng bước

Các lệnh dưới đây giả định raw JSONL và checkout tương ứng đã có. Không gọi LLM.

```powershell
python -m paper_eval_v2 prepare-dataset --problems raw/problems.jsonl --tests raw/tests.jsonl --repo-root checkouts/marshmallow --output artifacts/dataset
python -m paper_eval_v2 validate-dataset --dataset artifacts/dataset --repo-root checkouts/marshmallow
python -m paper_eval_v2 prepare-mutation-scope --dataset artifacts/dataset --repo-root checkouts/marshmallow --output artifacts/scope
docker build -t specmind-paper-eval-mutmut-py312:latest -f Dockerfile.mutmut .
python -m paper_eval_v2 prepare-mutants --scope artifacts/scope --repo-root checkouts/marshmallow --output artifacts/mutants
python -m paper_eval_v2 validate-mutants --mutants artifacts/mutants --dataset artifacts/dataset --repo-root checkouts/marshmallow
```

Lọc test pass với `assert True`, sau đó giữ test có bằng chứng fail tại assertion `False`. Hàm dưới 5 dòng thân hàm theo AST được mở rộng sang callee nội bộ trực tiếp; assertion vẫn ở hàm gốc. Thêm `--resume` để tiếp tục chuẩn bị cùng fingerprint.

**Giới hạn bố trí hiện tại:** bước Docker tạo mutant yêu cầu checkout nằm dưới thư mục cha của `paper_eval_v2`, như cấu trúc trên. Dùng source checkout và editable install cho bước này. Wheel cài độc lập không phải cách chạy được hỗ trợ cho Docker preparation. Dockerfile.mutmut không có ENTRYPOINT để worker được gọi đúng bởi orchestrator; image evaluator trong `paper_eval_v2/Dockerfile` có mục đích khác.

## Luồng chính

Config dùng cặp `prepared_dataset` và `prepared_mutants`: runner chỉ đọc artifact, không lọc test, dò callee hay sinh mutant. Nhánh cấu hình raw cũ vẫn tồn tại nhưng không dùng trong hướng dẫn này.

```powershell
python -m paper_eval_v2 run --config configs/offline.example.json --output runs/offline-smoke
```

Config offline dùng assertion mô phỏng để kiểm tra đường chạy, không đo chất lượng assertion. Có thể thêm `tasks` chứa một function ID để chạy pilot nhỏ. Chỉ chạy sau khi artifact đã validate.

Sao chép `configs/prepared.example.json` thành `configs/run.local.json`, đặt `model` đúng với endpoint. Các đường dẫn được tính từ **thư mục file config**. Cấp key qua `OPENAI_API_KEY` hoặc `OPENROUTER_API_KEY`, endpoint tùy chọn qua `OPENAI_BASE_URL`; không lưu key vào config.

```powershell
python -m paper_eval_v2 run --config configs/run.local.json --output runs/experiment-01
python -m paper_eval_v2 run --config configs/run.local.json --output runs/experiment-01 --resume
python -m paper_eval_v2 report --output runs/experiment-01
```

Luồng online có thể phát sinh phí API. Không dùng thư mục kết quả cũ nếu source, engine, config hay dependency thay đổi. Để chạy A/B/C/D, chuẩn bị graph đã xác minh và thêm `call_graph_dir`, rồi dùng `experiment` thay `run`; config baseline không đủ cho B/C/D.

## Debug và kiểm tra kết quả

Debug chạy riêng, không gọi LLM và không sửa run nguồn:

```powershell
python -m paper_eval_v2 debug --help
python -m paper_eval_v2 evaluate-suite --run runs/experiment-01 --output runs/suite-01
python -m paper_eval_v2 join-suite --run runs/experiment-01 --suite runs/suite-01 --output runs/paired-01
```

Xem [DEBUGGING.md](paper_eval_v2/DEBUGGING.md), [MUTANT_PREPARATION.md](paper_eval_v2/MUTANT_PREPARATION.md) và CLI `--help` cho tham số cụ thể. Ví dụ đường dẫn `C:/specmind` trong tài liệu kỹ thuật cũ chỉ là lịch sử; dùng bố trí và lệnh portable ở README này.

Mutant chưa tới assertion hoặc thiếu bằng chứng được ghi unknown, không tính là survived. Điểm test suite được đo riêng. Giữ lại manifest, logs, source/diff mutant và từng attempt để truy vết. Đây là bản SpecMind thích ứng cho repository, không tuyên bố tái lập nguyên xi paper.

## Nguồn và xác nhận

[provenance.json](paper_eval_v2/provenance.json) ghi nguồn prompt/parser. [SOURCE_SNAPSHOT.json](SOURCE_SNAPSHOT.json) ghi hash source Python ban đầu để kiểm tra engine không bị đổi khi đóng gói. [docs/history](docs/history) giữ báo cáo cũ làm tham khảo, không phải kết quả xác nhận bản phát hành này. Xem [VALIDATION.md](VALIDATION.md) cho kiểm thử mới.
