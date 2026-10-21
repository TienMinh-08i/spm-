# SpecMind evaluator V2

Một repo, một package và một CLI để tạo dataset Marshmallow, chuẩn bị mutant offline, sinh và đánh giá assertion. Không cần code V1. Dataset, checkout, API key và kết quả runtime không được đưa vào Git.

## Cài đặt

Python >= 3.11. Từ thư mục gốc repo:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r paper_eval_v2/requirements.lock
python -m pip install --no-deps -e .
python -m paper_eval_v2 --help
python -m pytest paper_eval_v2/tests -q
```

Linux kích hoạt môi trường bằng `source .venv/bin/activate`. Cài dependency trước khi chuẩn bị dữ liệu: fingerprint kiểm tra cả môi trường.

## Tạo dữ liệu

```powershell
git clone https://github.com/marshmallow-code/marshmallow.git checkouts/marshmallow
git -C checkouts/marshmallow checkout 60a803821466f3e537fa2729d65e1aaf7df9d2a4
python -m pip install --no-deps -e ./checkouts/marshmallow
python -m paper_eval_v2 prepare doctor --repo checkouts/marshmallow
python -m paper_eval_v2 prepare all --config configs/preparation.example.json
```

`all` mặc định dừng sau dataset. Output tại `artifacts/marshmallow/`: `raw/` chứa hàm và liên kết test; `prepared/` chứa test đã qua True/False. Mặc định lấy hàm có docstring; `documented_only: true` lọc thêm theo RST. Không hardcode 59 hay 57 hàm. Không có exclusions ngầm; nếu thêm vào config phải ghi pattern và lý do.

Tiếp tục scope và mutant, không gọi LLM:

```powershell
python -m paper_eval_v2 prepare all --config configs/preparation.example.json --through scope --resume
docker build -t specmind-paper-eval-mutmut-py312:latest -f Dockerfile.mutmut .
python -m paper_eval_v2 prepare all --config configs/preparation.example.json --through mutants --resume
python -m paper_eval_v2 prepare validate --repo checkouts/marshmallow --raw artifacts/marshmallow/raw --dataset artifacts/marshmallow/prepared --scope artifacts/marshmallow/scope --mutants artifacts/marshmallow/mutants
```

Checkout có thể nằm ngoài repo công cụ. Docker mount package và checkout riêng chỉ đọc, output có mount ghi riêng. Dockerfile.mutmut phục vụ export. Build image evaluator bằng `docker build -f paper_eval_v2/Dockerfile -t specmind-eval-v2 .` từ root.

## Chạy đánh giá

```powershell
python -m paper_eval_v2 run --config configs/offline.example.json --output runs/smoke
```

Offline dùng assertion cố định để kiểm tra đường chạy, không đánh giá chất lượng LLM. Có thể thêm `tasks` với một function ID để pilot. Runner đọc prepared dataset/mutants, không lọc hay tạo mutant trong vòng lặp.

Để gọi LLM, sao chép `configs/prepared.example.json` thành `configs/run.local.json`, thay model ID phù hợp endpoint. Cấp key qua `OPENAI_API_KEY` hoặc `OPENROUTER_API_KEY`, endpoint tùy chọn qua `OPENAI_BASE_URL`. Lệnh online có thể phát sinh phí:

```powershell
python -m paper_eval_v2 run --config configs/run.local.json --output runs/experiment-01
python -m paper_eval_v2 run --config configs/run.local.json --output runs/experiment-01 --resume
python -m paper_eval_v2 report --output runs/experiment-01
```

Mọi đường dẫn trong config tính từ thư mục file config. Dùng output mới khi source, code, config hoặc dependency thay đổi. Không sửa manifest để vượt fingerprint. A/B/C/D dùng `experiment` và cần graph đã xác minh cho B/C/D; config baseline chưa đủ.

## Cấu trúc và debug

- `paper_eval_v2/preparation/`: extraction, đọc RST, trace call-phase và CLI offline.
- Các module preparation/evaluation hiện có trong `paper_eval_v2/` dùng chung, không nhân đôi engine.
- `configs/`: config chuẩn bị và config đánh giá riêng.
- `docs/PIPELINE.md`: quy tắc dữ liệu, lệnh từng bước, file bằng chứng.
- `paper_eval_v2/tests/`: regression và integration test.
- `checkouts/`, `artifacts/`, `runs/`: runtime, Git bỏ qua.

```powershell
python -m paper_eval_v2 prepare --help
python -m paper_eval_v2 debug --help
python -m paper_eval_v2 evaluate-suite --run runs/experiment-01 --output runs/suite-01
python -m paper_eval_v2 join-suite --run runs/experiment-01 --suite runs/suite-01 --output runs/paired-01
```

Debug và suite evaluation ghi output riêng, không gọi LLM. Unknown không được diễn giải thành survivor; neutral suite chạy riêng. Đây là SpecMind thích ứng repository, không tuyên bố tái lập nguyên xi paper.

Xem [pipeline](docs/PIPELINE.md) và [kiểm thử](VALIDATION.md). Tài liệu trong `docs/history` là lịch sử, không phải hướng dẫn cài đặt hiện tại. `SOURCE_SNAPSHOT.json` ghi hash bản phát hành đầu tiên; thay đổi tích hợp được theo dõi bằng Git. `provenance.json` ghi nguồn prompt/parser.
