# Kiểm tra cài đặt và code

Chạy trong venv từ root repository:

```powershell
python -m pip check
python -m specmind_eval --help
python -m specmind_prep --help
python -m pytest tests/prep/test_preparation_pipeline.py -q
```

Bộ integration preparation dùng checkout nhỏ tạo trong thư mục tạm; kiểm tra liên kết nodeid, probe True/False, property/private method, callee, resume, manifest và mã thoát. Docker được mô phỏng trong test dựng lệnh; test này không chứng minh daemon/image thực tế hoạt động.

Khi thay phần evaluator chung, chạy bộ đầy đủ:

```powershell
python -m pytest tests -q
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
docker build -t specmind-paper-eval-mutmut-py312:latest -f docker/mutmut-worker.Dockerfile .
```

Sau đó chạy `specmind-prep mutants` trên scope pilot. Image evaluator có mục đích riêng; build bằng `docker build -f docker/evaluator.Dockerfile -t specmind-eval .` từ root nếu cần chạy evaluator trong container. Khi chuyển môi trường host/container, chuẩn bị và sử dụng artifact trong môi trường nhất quán vì fingerprint có kiểm tra dependency và platform.

## Khi thay cấu trúc code

Kiểm tra nguồn import trong venv trước khi chạy test:

```powershell
python -c "import specmind_eval, specmind_prep; print(specmind_eval.__file__); print(specmind_prep.__file__)"
```

Hai đường dẫn phải thuộc checkout hoặc wheel đang kiểm tra. Giữ nguyên file code
trong suốt lượt test: thay newline cũng có thể làm fingerprint đổi và khiến test
resume thất bại. Kiểm tra wheel trong thư mục build sạch để không mang theo file
từ một build khác. Không đổi manifest của run đã lưu để ép khớp fingerprint.
