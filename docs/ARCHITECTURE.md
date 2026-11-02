# Cấu trúc code và luồng chạy

## Bắt đầu từ đâu

- `src/specmind_eval/cli.py`: parser và điều phối lệnh đánh giá.
- `src/specmind_eval/config.py`: giá trị mặc định và kiểm tra config chạy.
- `src/specmind_eval/run.py`: điều phối một lần chạy.
- `src/specmind_prep/cli.py`: các bước chuẩn bị dữ liệu, không gọi LLM.
- `tests/eval/` và `tests/prep/`: kiểm thử bằng dữ liệu nhỏ trong thư mục tạm.

Sau khi cài package, dùng `specmind-eval ...` và `specmind-prep ...`.
Các cách gọi tương đương là `python -m specmind_eval ...` và
`python -m specmind_prep ...`.

## Chuẩn bị dữ liệu

```text
gather → trace → filter/dataset → scope → mutants/corpus
```

`gather/` đọc source; `trace/` chạy test và chỉ dùng vị trí từ candidates. `extract/` là facade tương thích. `source_index.py` cung cấp bảng hàm
cho extraction và scope. `filter/` thu thập nodeid rồi chạy probe True/False.
`scope/` chọn target và callee. `mutants/` dựng corpus qua worker Docker.

Các stage dùng chung bộ chạy pytest trong `specmind_eval.evaluate`.
Artifact chính gồm `candidates`, `links`, `prepared`, `scope`, `mutants`; xem PIPELINE.md
để biết lệnh và các file cần đọc. Facade extract gọi nối hai stage để hỗ trợ API cũ.

## Đánh giá

```text
inputs → context (tùy chọn) → generate/loop → evaluate → report
```

- `inputs/`: đọc dataset, source và prepared artifacts.
- `context/`: đọc call graph và dựng context.
- `generate/`: gọi model, phân tích response và phản hồi.
- `evaluate/`: policy, correctness, mutation và metrics.
- `offline/`: replay, đo suite và ghép bằng chứng từ run đã lưu.
- `experiment.py`: điều phối A/B/C/D, có thể gọi LLM. Không thuộc nhóm offline.
- `legacy_mutants.py`: xử lý export khi config dùng problems/tests thay vì
  prepared artifacts. Đường chạy này vẫn được hỗ trợ; config mẫu dùng prepared artifacts.

`common/` chứa đọc/ghi, JSONL, fingerprint, kiểm tra Git và output path.
Bộ đọc prepared artifact không chạy bước chuẩn bị. CLI và đường chạy không
có prepared artifacts vẫn có các phụ thuộc từ evaluator sang preparation.

## Code trong subprocess

`evaluate/injected/postcondition_plugin.py` được đưa vào subprocess pytest;
nó dùng `mutation_runtime.py` cùng thư mục để cài mutant. Các plugin trace
nằm trong `specmind_prep/injected/`. Cần kiểm tra PYTHONPATH và đường dẫn
module khi di chuyển các file này. Docker worker mount cả `src/` để hai
package dùng chung engine.

## Fingerprint và dữ liệu đã lưu

Fingerprint vẫn nhạy với code, cấu hình, source và môi trường. Hash engine
bao phủ các package của công cụ, không băm toàn bộ site-packages. Các stage
có danh sách implementation riêng vẫn kiểm tra các file triển khai liên quan.

Di chuyển hoặc sửa code có thể làm fingerprint đổi, khiến resume từ chối.
Không sửa fingerprint trong manifest để ép chạy tiếp. Giữ output đã lưu,
sử dụng checkout và môi trường tương ứng khi cần resume, hoặc tạo output mới.
Replay cũng kiểm tra identity của cache và có thể phải thực thi lại.

## Khi thêm hoặc di chuyển code

Giữ thuật toán độc lập với CLI; dùng helper chung thay vì sao chép.
Chạy test sát phần sửa trước, rồi kiểm tra subprocess nếu thay đường dẫn.
Không đổi kỳ vọng test chỉ để che một thay đổi hành vi ngoài phạm vi.
Không cần LLM, toàn bộ dataset thật hoặc Docker thật cho bộ fixture mặc định.

## Import và fingerprint

`tests/test_layering.py` kiểm tra hướng import, bao gồm plugin chỉ dùng stdlib, pytest và helper injected cùng nhóm. Hai bridge tương thích hiện có (`specmind_eval.cli` và `legacy_mutants`) được khai báo rõ để giữ các lệnh/cấu hình cũ; không cho phép module evaluator khác import preparation.

Fingerprint dựa trên entry module và closure import nội bộ, cộng resource prompt/plugin được đọc dưới dạng file. Không băm toàn bộ thư mục package hoặc test. Gather không phụ thuộc prompt, code sinh assertion hoặc settings trace. Các stage có danh sách implementation riêng tiếp tục niêm phong file thực thi tương ứng.
