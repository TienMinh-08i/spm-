# Sinh mutant LLM theo từng dòng

Luồng này thích nghi từ file người dùng cung cấp `generate_leetcode_mutants.py`.
Nó yêu cầu **khoảng 5 biến thể cho mỗi dòng thực thi có ý nghĩa** và tạo một mutant độc lập cho từng biến thể.
Các mutant không tích lũy nhiều thay đổi vào một phiên bản hàm.

## Phần giữ theo file gốc

- Giữ cấu trúc system/user prompt, khoảng 5 biến thể mỗi dòng, phủ từ đầu đến cuối thân hàm.
- Trả một fenced JSON array chứa `original_content`, `dedup_key`, `mutant`.
- Giữ nguyên khoảng trắng đầu dòng và cách đếm lần xuất hiện của dòng trùng từ 0.
- Không chuyên biệt mutant cho một assertion/checkpoint.
- Temperature 0.0, tối đa 8192 output token.

## Phần điều chỉnh cho SpecMind

- Đầu vào là hàm trong dataset đã qua probe và scope self-only; không tải dataset LeetCode.
- `problem` lấy từ document API, dự phòng docstring; `completion` là source hàm trong artifact.
- Câu giới thiệu LeetCode được thay bằng hàm thư viện và tài liệu API. Thêm câu nhắc source chỉ là baseline,
  pass test không chứng minh đúng toàn API. Những quy tắc tạo mutant khác giữ theo prompt gốc.
- Dùng model đã cấu hình, trong lần chạy hiện tại là `openai/gpt-6-luna`, không tự chuyển sang model mặc định của file gốc.
- Gọi tuần tự, một request/hàm, timeout 120 giây, không retry vô hạn. Lưu request/response trước khi parse.
- Không dùng cache của prompt sinh cả hàm cũ vì giao thức và đầu vào khác nhau.
- Chỉ sửa một dòng vật lý. Chặn khai báo hàm, docstring, nested function/class, import/decorator và dòng không khớp.
- Chạy kiểm cú pháp, signature/decorator, AST không đổi/trùng bằng bộ lọc SpecMind sau khi áp từng dòng.
- Không tự kiểm chứng lỗi ngữ nghĩa hoặc chạy mutant. Giữ mỗi lý do loại trong exports.

Các quy tắc “lỗi hợp lý”, “lỗi ngữ nghĩa khác nhau” và “giữ identifier” là yêu cầu cho model.
Bộ lọc tĩnh chưa chứng minh đầy đủ những thuộc tính này. Thống kê dòng là dòng vật lý được phép xem xét,
không phải bằng chứng đã sinh đủ 5 lỗi có ý nghĩa cho mọi statement.

## Chạy

Dùng cùng môi trường Python và source đã chuẩn bị dataset:

```powershell
cd C:\specmind\tmp\spm_publish_20261020
$env:PYTHONPATH = 'src'
& C:\specmind\tmp\specmind_restructure_audit_20261101\venv\Scripts\python.exe -m specmind_prep.llm_mutants `
  --scope artifacts/prep_llm22/marshmallow_20261107/scope `
  --dataset artifacts/prep_llm22/marshmallow_20261107/dataset `
  --repo C:\specmind\paper_eval\.cache\clones\marshmallow-code\marshmallow\latest `
  --output artifacts/prep_llm_line/marshmallow_20261108/mutants_llm `
  --model openai/gpt-6-luna --method line --max-targets 22
```

`--method line` chọn giao thức mới. `--count` của cách sinh cả hàm không giới hạn số mutant ở cách này.
Đầu ra mới được giữ riêng với kho 137 mutant cũ. Entry point `specmind_prep.cli llm-mutants` mặc định chọn `line`; module này vẫn cần chỉ định `--method line` rõ ràng.

## Đầu ra

- `exports.json`: response records, code mutant hoàn chỉnh, dòng gốc/thay thế, occurrence index,
  diff, lý do loại, model, usage và thống kê dòng có đề xuất/được giữ.
- `functions.json`: mapping mutant theo hàm, tương thích evaluator chuẩn bị sẵn.
- `*.request.json`, `*.response.json`: prompt và response gốc.
- `manifest.json`: `mutation_method: line`, prompt, hash file tham khảo, số biến thể yêu cầu mỗi dòng,
  cấu hình API và lineage dataset/scope.
- `summary.json`: số mutant mỗi hàm và thống kê dòng.

Chạy lại output hoàn tất dùng cache, không gọi thêm. Nếu request đã bắt đầu nhưng chưa có response được lưu,
chương trình yêu cầu kiểm tra thay vì tự gọi lại. Nếu code/prompt đổi, cần output mới hoặc một lần chuyển cache
được kiểm tra rõ ràng; không sửa fingerprint để ép chạy tiếp.

Xem [luồng chuẩn bị và replay](PREPARATION_WORKFLOW.md) để kiểm tra selection, scope và chạy một mutant.
