# Thử luồng chuẩn bị dataset trên mẫu nhỏ

`python -m specmind_prep.prototype --config <pilot.json>` chạy bản sơ khai bằng các stage hiện có.
Chỉ dùng để kiểm tra đường đi của artifact trên tối đa 5 hàm và 10 pytest nodeid được chỉ định rõ.
Không dùng Docker, không gọi LLM, không sinh assertion và không tính mutation score.

Với phạm vi đã được duyệt, cấu hình có thể đặt `selection_limit: 22` và `test_limit: 40`.
Giới hạn mặc định vẫn là 5 hàm và 10 test selector. Test tham số hóa có thể mở rộng thành nhiều nodeid.
`through: "trace"` dừng sau trace để kiểm tra liên kết. `through: "scope"` dừng sau probe và scope,
để chạy generator LLM riêng thay vì mutmut. Cờ `complete` chỉ áp dụng đến bước `completed_through`
đã yêu cầu, không có nghĩa đã sinh mutant khi dừng sớm.

## Luồng thực tế

0. Đọc artifact `build-docs` có sẵn, kiểm commit, hash và các file bắt buộc. Chưa tự build lại.
1. Dùng inventory RST hiện có làm danh sách gợi ý, đối chiếu mục riêng trong HTML đã build.
   Đây chưa phải bộ thu thập đầy đủ cho mọi project/Sphinx extension.
2. Đọc `selection` do người chạy khai báo, ghi rõ phạm vi mẫu. Các hàm ngoài mẫu chưa được duyệt, không bị coi là drop.
3. Kiểm ID, decision, lý do và bằng chứng tài liệu. Ghi candidates tương thích stage trace.
4. Trace đúng các pytest nodeid đã chọn.
5. Collect đúng các nodeid đó rồi chạy probe True/False bằng evaluator hiện có.
6. Tạo scope chỉ chứa chính hàm, không truy thêm callee.
7. Gọi các toán tử sửa source của mutmut 2.5.1 trong subprocess có timeout, lọc syntax/signature/AST trùng.
   Không chạy CLI test runner của mutmut. Mutant đã chuẩn bị chưa phải mutant bị assertion kill.

Chỉ `keep/drop` được hỗ trợ. `delegate` bị từ chối vì chưa có ánh xạ tham số và giới hạn ngữ cảnh gọi.
Nhánh LLM được ghi `not_run`. Không coi pilot này là hoàn tất toàn bộ thiết kế dataset mới.

## Chuẩn bị

Dùng Python 3.11 và môi trường đã cài SpecMind cùng dependency chạy test của project.
Tạo thư mục vendor riêng ngoài repo để giữ bộ sinh source:

```powershell
python -m pip install --no-deps --target C:\specmind\tmp\pilot_vendor mutmut==2.5.1 toml==0.10.2 parso==0.8.4
```

Không cài đè mutmut đang có. Worker chỉ nhận phiên bản 2.5.1. Bản sơ khai hỗ trợ hàm đồng bộ không decorator.

Ví dụ cấu hình (đường dẫn tương đối được giải từ nơi đặt file cấu hình):

```json
{
  "repo": "C:/path/to/marshmallow",
  "project": "C:/path/to/configs/projects/marshmallow.json",
  "docs": "C:/path/to/sealed-docs-build",
  "output": "C:/path/to/new-pilot-output",
  "mutmut_vendor": "C:/specmind/tmp/pilot_vendor",
  "timeout": 120,
  "tests": ["tests/test_utils.py::test_is_collection"],
  "review_ids": ["marshmallow.decorators.set_hook", "marshmallow.experimental.context.Context.get", "marshmallow.schema.Schema.get_attribute", "marshmallow.schema.Schema.handle_error", "marshmallow.schema.Schema.on_bind_field"],
  "selection": [
    {"function_id": "marshmallow.utils.is_collection", "decision": "keep", "reason": "Chọn làm mẫu kiểm tra pipeline."}
  ]
}
```

`review_ids` là các trường hợp cần thảo luận được khai báo rõ cho pilot, không phải kết quả phân loại tự động tổng quát.
Mỗi quyết định trong mẫu cần lý do. Không được keep hàm thiếu mục HTML hoặc thuộc review_ids.
Giao diện duyệt JSON trước đó chưa được nối trực tiếp với định dạng cấu hình này.

## Chạy và đọc kết quả

```powershell
$env:PYTHONPATH = 'src'
python -m specmind_prep.prototype --config C:\path\to\pilot.json
```

Output phải là thư mục mới. Không ghi đè hoặc tự resume. Nếu cần chạy lại, đổi output.

- `report.json`: trạng thái từng bước và lỗi cuối nếu có.
- `public_api_inventory.jsonl`: danh sách gợi ý và nội dung mục HTML.
- `api_selection.jsonl`: quyết định riêng cho mẫu.
- `candidates/`, `links/`, `dataset/`: các artifact có manifest.
- `dataset/functions/*/result.json`: bằng chứng probe và test được giữ/loại.
- `scope/`: scope self-only.
- `mutants/exports.json`: mutant đã sinh và lý do bỏ từng mutant.
- `mutants/functions.json`: ánh xạ tương thích loader mutant đã chuẩn bị của evaluator.
- `mutants/generation.log`: log worker khi sinh mutant lỗi.

Step 0 được tái sử dụng tại vị trí cũ, không copy lại HTML. Chưa có xác nhận semantic equivalence,
coverage toàn API hoặc tính đúng của assertion. Dữ liệu thí nghiệm cũ được giữ nguyên.
