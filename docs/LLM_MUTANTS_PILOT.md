# Sinh mutant bằng LLM trên mẫu nhỏ

Lệnh riêng này đọc dataset và scope đã chuẩn bị, sinh tối đa 10 mutant/hàm cho tối đa hai hàm self-only.
Không trace/probe lại, không chạy Docker, không sửa source và không tính mutation score.
Mặc định chỉ cho hai target. Đợt đã duyệt lớn hơn phải truyền `--max-targets 22` (giới hạn trên 22),
vẫn tối đa 10 mutant/hàm và scope self-only. Chỉ các hàm đã qua probe trong dataset được đưa vào scope.

```powershell
$env:PYTHONPATH = 'src'
python -m specmind_prep.llm_mutants `
  --scope artifacts/prep_pilot/marshmallow_20261107/scope `
  --dataset artifacts/prep_pilot/marshmallow_20261107/dataset `
  --repo C:\specmind\paper_eval\.cache\clones\marshmallow-code\marshmallow\latest `
  --output artifacts/prep_pilot/marshmallow_20261107/mutants_llm `
  --model openai/gpt-6-luna --count 10
```

Dùng môi trường Python đã tạo dataset để khớp kiểm tra môi trường. Model phải tồn tại trên endpoint cấu hình.
Ưu tiên `OPENAI_API_KEY` và `OPENAI_BASE_URL` như evaluator. Nếu chỉ có `OPENROUTER_API_KEY`, dùng endpoint OpenRouter.
Không ghi API key vào cấu hình hoặc artifact.

## Request và cache

- Mỗi hàm một request, timeout 120 giây, không retry tự động, giới hạn output 7000 token.
- Prompt chỉ gồm yêu cầu sinh mutant và source hàm (bao gồm signature/docstring). Không cung cấp test hoặc assertion.
- Lưu `*.request.json` trước khi gọi, `*.response.json` ngay sau khi nhận response và trước khi parse.
- Chạy lại cùng lệnh sẽ dùng artifact hoàn tất hoặc response đã lưu, không gọi lại.
- Nếu có request nhưng chưa có response (ví dụ timeout), lệnh dừng để người chạy kiểm tra trước khi gọi tốn phí lần nữa.
- Đổi model/count/source/implementation phải dùng thư mục output mới.
- `--reuse-from <kho_llm_cũ>` kiểm hash artifact cũ và chỉ dùng response khi model, prompt đầy đủ
  (bao gồm source và số mutant yêu cầu), giới hạn token khớp. Response được copy sang kho mới,
  lọc lại và gắn với dataset mới. File `*.reuse.json` ghi nguồn cùng hash.
- `new_requests_this_execution` chỉ đếm request của lần thực thi hiện tại, không phải tổng số lịch sử
  nếu một đợt bị ngắt rồi chạy tiếp.

## Lọc và đầu ra

Loại mutant sai cú pháp, không phải đúng một hàm, đổi tên/signature/decorator, thêm import/global/nonlocal,
AST thực thi không đổi hoặc trùng với mutant đã nhận. Không khẳng định AST khác đồng nghĩa hành vi khác.

- `exports.json`: số sinh, mutant được giữ, lý do loại, usage và model thực tế trả về.
- `functions.json`: ánh xạ tương thích loader mutant chuẩn bị của evaluator.
- `summary.json`, `manifest.json`: tổng số và hash artifact, gắn với đúng dataset/scope.
- `*.response.json`: response gốc để audit/replay mà không gọi lại model.

Kho LLM tách riêng kho mutmut, không cộng mẫu số hai kho để báo mutation score.
Chưa chạy các mutant để xác minh runtime, tương đương ngữ nghĩa hay khả năng bị assertion kill.
Code LLM chưa qua sandbox thực thi; bộ lọc AST không phải cơ chế bảo đảm an toàn khi chạy code.
