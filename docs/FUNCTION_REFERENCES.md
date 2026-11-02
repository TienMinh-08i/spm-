# Định vị hàm và tìm caller để chuẩn bị slicing

Lệnh này chỉ đọc source Python. Không import code của project, chạy pytest,
gọi LLM hay tạo mutant. Đây là bước lấy reference và context từ caller,
chưa phải program slicing theo phụ thuộc dữ liệu/điều khiển.

## Chạy

Cài công cụ theo README, chuẩn bị file project theo CONFIGURATION.md rồi chạy:

```powershell
specmind-prep references --repo C:\project --project configs/projects/project.json --function mypackage.schema.Schema._init_fields --output references.json
```

`configs/projects/project.json` và tên hàm trong ví dụ phải được thay bằng dữ liệu
thực tế. Với Marshmallow, dùng `configs/projects/marshmallow.json` và tên hàm
`marshmallow.schema.Schema._init_fields`.

Cách gọi tương đương: `python -m specmind_prep references ...`.
`--context-lines 3` lấy tối đa ba dòng trước/sau lời gọi, giới hạn trong hàm caller.
Khoảng cho phép là 0–20. Bỏ `--output` để chỉ in JSON ra terminal; file output
đã tồn tại sẽ bị từ chối, không ghi đè. Đường dẫn tương đối tính từ thư mục chạy lệnh.

## Đọc kết quả

- `target.location`: `file:line`, đường dẫn tính từ gốc codebase, dòng bắt đầu từ 1.
- `target.line`: dòng `def` hoặc `async def`.
- `target.code_line`: dòng decorator đầu tiên nếu có, phục vụ tracing.
- `callers`: lời gọi có tên được phân giải tĩnh tới đúng function ID, kèm tên
  caller, vị trí định nghĩa caller, vị trí gọi, câu gọi và đoạn source xung quanh.
- `unresolved_calls`: các lời gọi chưa phân giải trong các hàm đã quét. Đây không
  phải danh sách caller đã xác nhận của target. `name_matches_target` chỉ là gợi ý
  lọc theo tên, không chứng minh đích gọi; vẫn xem các mục false khi tìm alias.
- `source_sha256`: hash nội dung file đã đọc để đối chiếu snapshot.
- `column`: vị trí cột bắt đầu từ 1 theo byte UTF-8 của Python AST.

Các đoạn source được chuẩn hóa newline về LF. Hai lời gọi trên cùng một dòng
được phân biệt bằng `column`. Không tự ghép kết quả vào prompt hay thay đổi run.

## Phạm vi và giới hạn

Quét package trong project config; bỏ thư mục test được khai báo. Dùng tên hàm
đầy đủ, ví dụ `mypackage.schema.Schema._init_fields`, để tránh trùng tên.
Hỗ trợ tên hàm cùng module, import thường, alias import và relative import.
Tham số/biến cục bộ che khuất tên, alias cục bộ, re-export và `self.method(...)`
được giữ ở nhóm chưa phân giải. Không suy luận kiểu instance, kế thừa hay MRO.

Không quét lời gọi trong thân module/class, decorator, giá trị mặc định,
hàm lồng nhau hoặc lambda. Chỉ những hàm mà source index hiện tại nhận diện
mới có thể được chọn làm target. Lỗi cú pháp sẽ dừng phân tích, không âm thầm bỏ file.

`static_reference` là liên kết tên theo source, không phải bằng chứng runtime:
decorator, monkey-patching hoặc dispatch động có thể thay đổi hàm thực sự chạy.
Context là đoạn source theo số dòng; chưa theo dấu biến, alias hay nhánh điều kiện.
Caller chỉ cho thấy cách sử dụng cụ thể, không xác định toàn bộ hợp đồng API.

## Kiểm tra

```powershell
python -m pytest tests/prep/test_references.py tests/test_layering.py -q
```

Các test kiểm tra vị trí, import alias, relative import, tên trùng ở module khác,
shadowing, method chưa xác định, decorator, async, layout phẳng, không thực thi
source và không ghi đè output. Bước tiếp theo là phân tích phụ thuộc đối số và
kết quả tại từng vị trí gọi đã chọn.
