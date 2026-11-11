# Tạo kho LLM mutant cho các hàm đích

Kho mutant có thể được chuẩn bị từ candidates trước khi chạy trace/probe. Mỗi hàm đích chỉ sinh một lần, kể cả khi nhiều API delegate tới cùng helper. Đặc tả và kết quả đánh giá của từng API vẫn thuộc task riêng.

## Chạy

Cần candidates đã được `validate-selection` kiểm chứng, checkout đúng commit và API key được cấu hình trong môi trường. Dùng cùng launcher và môi trường khi tạo, kiểm tra và đọc artifact.

```powershell
.\run-stable.ps1 prep llm-target-pool `
  --candidates <thu-muc-candidates> `
  --repo <checkout> `
  --output <thu-muc-pool-moi> `
  --model <model> `
  --reuse-from <kho-LLM-da-hoan-tat> `
  --max-new-requests 2 `
  --new-max-tokens 32768
```

`--reuse-from` có thể bỏ nếu không có cache. `--max-new-requests` mặc định là 0: lệnh kiểm số request cần thiết trước khi gọi LLM. Chỉ tăng giới hạn khi muốn sinh thêm. Chạy lại cùng lệnh và output để tiếp tục đọc response đã lưu; không thay tham số hoặc sửa manifest để vượt fingerprint.

## Prompt và cache

Protocol sinh khoảng năm phương án thay thế cho mỗi dòng có ý nghĩa, mỗi mutant chỉ đổi một dòng. Prompt chứa source và tài liệu của chính hàm đích, không chứa test hoặc assertion. Với helper chung không có API reference riêng, dùng docstring của helper; đặc tả API gốc vẫn nằm trong candidates.

Cache chỉ được tái dùng khi model, messages, temperature và token budget khớp, đồng thời hash request/response được kiểm tra. Request tái dùng giữ budget 8192 token; request mới dùng `--new-max-tokens`. Budget lớn hơn giúp tránh cắt cụt response của hàm dài, không quy định số mutant phải được nhận.

Lưu request trước khi gọi và response thô trước khi parse. Nếu request đã bắt đầu nhưng mất response, không tự gọi lại. Kiểm `checkpoints.json` trước khi xử lý tiếp để tránh trả phí lặp.

## Đầu ra

| File | Nội dung |
|---|---|
| `targets_input.json` | Source của các hàm đích duy nhất |
| `task_bindings.json` | Ánh xạ từng task/API tới hàm đích; chưa phải bộ lọc runtime |
| `*.request.json`, `*.response.json` | Prompt, cấu hình, response gốc |
| `*.reuse.json` | Nguồn và hash của response được tái dùng |
| `functions.json` | Mutant được nhận, nhóm theo hàm đích |
| `exports.json` | Phương án nhận/loại và coverage dòng |
| `checkpoints.json` | Trạng thái từng hàm đích |
| `summary.json`, `manifest.json` | Số lượng, trạng thái và hash artifact |

Bộ lọc kiểm cú pháp, chữ ký/decorator, vị trí dòng, executable AST không đổi và trùng lặp. Các kiểm tra này chưa chứng minh mọi mutant khác hành vi hoặc chạy được trên mọi input.

Schema của kho này là `llm-target-pool-v1`, `dataset_ready=false`, coverage của mutant là `not_probed`. Evaluator không nạp nó như `prepared-mutants-v1`. Muốn chạy đánh giá phải hoàn tất trace/probe của các API task, gắn pool vào scope phù hợp rồi replay có mục tiêu. Cờ `complete` của pool chỉ xác nhận hoàn tất tạo và lọc source mutant.
