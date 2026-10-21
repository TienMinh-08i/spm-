# Kiểm tra cài đặt và code

Chạy trong venv từ root repository:

```powershell
python -m pip check
python -m paper_eval_v2 --help
python -m paper_eval_v2 prepare --help
python -m pytest paper_eval_v2/tests/test_preparation_pipeline.py -q
```

Bộ integration preparation dùng checkout nhỏ tạo trong thư mục tạm; kiểm tra liên kết nodeid, probe True/False, property/private method, callee, resume, manifest và mã thoát. Docker được mô phỏng trong test dựng lệnh; test này không chứng minh daemon/image thực tế hoạt động.

Khi thay phần evaluator chung, chạy bộ đầy đủ:

```powershell
python -m pytest paper_eval_v2/tests -q
```

Tests dùng response mô phỏng, không gọi API LLM trả phí. Không cần chạy hết dataset thật để kiểm tra một sửa đổi nhỏ. Chọn test liên quan trước; pilot một hàm theo README khi cần kiểm tra môi trường thực.

## Kiểm tra package

```powershell
python -m pip wheel --no-deps . --wheel-dir dist
```

Build xong trước khi chạy evaluation/preparation. Để kiểm tra wheel độc lập, tạo venv khác, cài requirements.lock rồi cài file wheel trong dist. Chạy CLI từ thư mục khác để không vô tình import source trong checkout.

## Kiểm tra Docker

```powershell
docker info
docker build -t specmind-paper-eval-mutmut-py312:latest -f Dockerfile.mutmut .
```

Sau đó chạy `prepare mutants` trên scope pilot. Image evaluator có mục đích riêng; build bằng `docker build -f paper_eval_v2/Dockerfile -t specmind-eval .` từ root nếu cần chạy evaluator trong container. Khi chuyển môi trường host/container, chuẩn bị và sử dụng artifact trong môi trường nhất quán vì fingerprint có kiểm tra dependency và platform.
