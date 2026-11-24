# Chạy SpecMind E với dynamic slicing

Mở notebook `notebooks/SpecMind_E_Colab.ipynb` trong Colab. Đặt ZIP đầu vào tại `MyDrive/specmind/inputs/marshmallow-inputs.zip` và bật quyền đọc secret `OPENROUTER_API_KEY`. Chọn Run all.

E bổ sung các dòng Python đã chọn từ những lần gọi trong test vào prompt gốc. Mỗi public API có pack riêng, kể cả khi nhiều API dùng chung helper. File `experiments/marshmallow_e/packs.json` chứa đúng chuỗi đưa vào prompt. Context có thể gồm đoạn code rời từ nhiều file và còn thiếu một số quan hệ phụ thuộc.

Chế độ full chạy 24 API trên M1 và M2, tối đa 12 attempt mỗi tổ hợp. Pilot chạy Schema.dumps trên M1 với một attempt. Dùng cùng model, bộ dữ liệu và tham số như lần chạy ABCD cần đối chiếu. Mở RESULTS.csv trên Drive và ghép các dòng theo task và corpus.

## Tạo lại context

Dùng Python 3.11 trong môi trường riêng cho DynaPyt. Các lệnh chạy từ thư mục gốc repo. DATA là thư mục chứa problems.jsonl và tests.jsonl. REPO là bản clone Marshmallow có commit 60a803821466f3e537fa2729d65e1aaf7df9d2a4.

```sh
python -m pip install -r tools/dynamic_slicing/requirements.txt
python tools/dynamic_slicing/run.py --repo REPO --data DATA --out NEW_RESULTS
python tools/dynamic_slicing/audit_occurrences.py NEW_RESULTS
python tools/dynamic_slicing/export_code.py --results NEW_RESULTS --problems DATA/problems.jsonl --output NEW_PACKS
```

Mở NEW_RESULTS/CONTEXTS.md để đối chiếu source và mô tả từng ví dụ. Trace của từng test có trong NEW_RESULTS/traces. Khi chạy evaluator, đặt context_mode thành slice và slice_context_dir tới NEW_PACKS.
