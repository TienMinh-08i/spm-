# Tham số cấu hình

Có hai config độc lập: chuẩn bị dữ liệu và chạy đánh giá. Sao chép file mẫu trong `configs/` thành `*.local.json` rồi chỉnh bản sao. JSON không hỗ trợ comment hoặc dấu phẩy cuối. Key không hợp lệ bị từ chối trong lệnh `all` và `run`.

Đường dẫn trong JSON tính từ **thư mục chứa file config**; đường dẫn truyền qua CLI tính từ **thư mục terminal đang đứng**. Ví dụ `../checkouts/marshmallow` trong `configs/run.local.json` trỏ tới `checkouts/marshmallow` ở root.

## Chuẩn bị: preparation.example.json

Bảng dưới ghi mặc định của `specmind-prep all` khi bỏ trường; file mẫu có thể ghi giá trị rõ ràng.

| Trường | Mặc định | Ý nghĩa / khi nào đổi |
|---|---|---|
| `repo` | Bắt buộc | Checkout có `src/marshmallow` và `tests` |
| `output` | Bắt buộc | Thư mục chứa raw, prepared, scope, mutants; ngoài checkout |
| `expected_commit` | Không khóa | Commit yêu cầu; mẫu khóa một SHA cụ thể |
| `tasks` | Mọi hàm ứng viên | Mảng function ID chính xác; dùng cho pilot |
| `tests` | `["tests"]` | Mảng đường dẫn test hoặc nodeid, tính từ checkout |
| `documented_only` | `false` | `false`: hàm có docstring; `true`: lọc thêm symbol trong tài liệu `.rst` |
| `exclude` | `[]` | Mảng `{ "pattern": "...", "reason": "..." }`; pattern dùng biểu thức pytest `-k` |
| `trace_timeout` | `1800` | Giây cho lượt pytest quan sát raw; phải lớn hơn 0 |
| `pytest_timeout` | `120` | Timeout pytest ở bước lọc; không phải thời gian tối đa toàn pipeline |
| `scope_timeout` | `300` | Timeout pytest quan sát callee |
| `batch_size` | `500` | Số nodeid tối đa mỗi batch; không giới hạn tổng test |
| `image` | `specmind-paper-eval-mutmut-py312:latest` | Docker image export mutant |
| `mutmut_timeout` | `1800` | Timeout export mutmut cho target |

Dùng timeout dương và batch_size >= 1. Tăng timeout khi một nhóm test thực sự cần thêm thời gian; giảm batch_size để thu nhỏ mỗi subprocess, đổi lại có nhiều lần khởi động pytest hơn.

Ví dụ pilot:

```json
{
  "repo": "../checkouts/marshmallow",
  "output": "../artifacts/pilot",
  "tasks": ["marshmallow.class_registry.get_class"],
  "tests": ["tests/test_registry.py"],
  "documented_only": false,
  "pytest_timeout": 120,
  "batch_size": 100
}
```

Các cờ của `all`: `--through dataset` (mặc định), `--through scope`, `--through mutants`; `--resume` tiếp tục cùng cấu hình. Khi thay tasks/tests/output, chỉnh cả config đánh giá cho đúng bộ artifact mới.

## Đánh giá: prepared.example.json

| Trường | Mặc định trong CLI | Ý nghĩa |
|---|---|---|
| `repo_root` | Bắt buộc | Checkout khớp artifact |
| `prepared_dataset` | Bắt buộc khi dùng prepared mode | Thư mục dataset đã lọc |
| `prepared_mutants` | Bắt buộc cùng prepared_dataset | Thư mục corpus mutant |
| `tasks` | Mọi hàm trong dataset | Danh sách function ID muốn chạy |
| `model` | `gpt-5.4-mini` | Phải chọn model thực sự có trên endpoint; mẫu yêu cầu bạn điền |
| `max_turns` | `12` | Giới hạn lượt sinh; **file mẫu đặt 24** |
| `mode` | `multiturn` | `multiturn`: giữ hội thoại phản hồi; `retry`: chế độ thử lại |
| `threshold` | `0.9` | Ngưỡng mutation score trong [0,1]; candidate correctness pass đạt ngưỡng thì dừng, gồm cả explore |
| `temperature` | `0` | Tham số lấy mẫu gửi tới API |
| `max_tokens` | `1024` | Giới hạn token phản hồi gửi tới API |
| `api_timeout` | `180` | Timeout một request API, giây |
| `api_retries` | `2` | Số retry API; 0 là không retry |
| `pytest_timeout` | `120` | Timeout pytest, giây |
| `batch_size` | `25` | Số test mỗi batch; **file mẫu đặt 500** |
| `evaluation_retry_limit` | `2` | Giới hạn thử lại evaluation chưa rõ trong quá trình sinh |
| `mock_assertion` | Không đặt | Assertion cố định; dùng giá trị này thì không gọi LLM |
| `assertion_policy` | `behavioral` | Kiểm tra cấu trúc và các phép dùng trong assertion; tùy chọn khác: `legacy` |
| `generate_without_mutants` | `false` | Có sinh assertion khi hàm không có mutant hay không |
| `context_mode` | `none` | `none`, `callee`, `caller`, `callgraph` |
| `call_graph_dir` | Không đặt | Graph đã chuẩn bị, cần cho các mode context |
| `context_budget` | `8000` | Budget ký tự context |
| `context_depth` | `1` | Độ sâu graph, ít nhất 1 |
| `expected_repo_commit` | Không khóa | Commit checkout yêu cầu khi chạy |

`api_timeout` và `pytest_timeout` bằng 0 vô hiệu hóa timeout tương ứng. Không nên dùng 0 nếu cần giới hạn thời gian. Tham số API có được chấp nhận hay không phụ thuộc model/endpoint.

`max_turns` giới hạn lượt sinh; response sai định dạng hoặc không tạo submission hữu ích vẫn có thể tiêu tốn lượt. Pass test không chứng minh assertion đúng với mọi đầu vào.

Ví dụ đổi model, số lượt và chọn một hàm: chỉnh các trường sau trong bản sao của config đầy đủ:

```json
"model": "YOUR_MODEL_ID",
"max_turns": 8,
"tasks": ["marshmallow.class_registry.get_class"]
```

Đổi tham số tạo một thư mục `--output` mới. Để chạy lại đúng cấu hình sau gián đoạn, giữ nguyên config và dùng `--resume`.

## Thử nghiệm context

`experiment` tạo A=none, B=callee, C=caller, D=callgraph. Cần có graph hợp lệ trong `call_graph_dir`; config baseline chỉ có dataset/mutants chưa đủ. `prepare-context --help` mô tả đầu vào graph và usage ZIP, không tự xây graph từ checkout.

```powershell
python -m specmind_eval experiment --config configs/context.local.json --output runs/context-01
python -m specmind_eval compare --output runs/context-01
```

Chỉ dùng lệnh trên sau khi bạn đã tạo `configs/context.local.json` với graph phù hợp. Đọc `context.json` của từng hàm để biết context thực sự được gửi.
