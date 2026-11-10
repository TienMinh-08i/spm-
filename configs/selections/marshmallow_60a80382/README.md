# Lựa chọn API Marshmallow

Source commit: `60a803821466f3e537fa2729d65e1aaf7df9d2a4`.

- `eligibility.jsonl`: review đủ 31 proposals theo P1–P3; nhận 27 API có anchor riêng, loại bốn method Field không có anchor riêng.
- `selection.jsonl`: đủ 27 API, gồm 16 keep, 8 delegate, 3 drop.
- Step 3 xuất 24 task API, dùng 17 hàm phân tích duy nhất. Các task chung helper giữ riêng đặc tả, test, candidate và kết quả.

Context.get được nhận vì trang experimental.context thuộc API reference công khai; không tự đồng nhất experimental với provisional/private. set_hook có mục riêng và không khai báo private/internal/deprecated, nên cũng được nhận làm task keep; đồng thời có thể là đích của các API delegate.

| API delegate | Hàm đích |
|---|---|
| decorators.post_dump | decorators.set_hook |
| decorators.post_load | decorators.set_hook |
| decorators.pre_dump | decorators.set_hook |
| decorators.pre_load | decorators.set_hook |
| decorators.validates | decorators.set_hook |
| decorators.validates_schema | decorators.set_hook |
| schema.Schema.load | schema.Schema._do_load |
| schema.Schema.get_attribute | utils.get_value |

Tất cả tên trên có tiền tố `marshmallow.`. Schema.dumps/loads/from_dict giữ implementation riêng vì có xử lý thêm. Drop handle_error/on_bind_field vì implementation mặc định không làm gì; drop OneOf.options vì kiểm nội dung iterator lười sẽ tiêu thụ iterator trong harness hiện tại.

Xem [hướng dẫn chạy pipeline](../../../docs/DATASET_PIPELINE.md). `llm-target-pool-v1` là kho source; corpus chạy chính phải được tạo từ dataset ready và scope, với schema `prepared-mutants-v1`.
