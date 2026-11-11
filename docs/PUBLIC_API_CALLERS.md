# Public API và caller của một checkout

Lệnh này tạo danh sách API từ tài liệu RST và thống kê lời gọi trong source/test.
Không import thư viện đích, chạy test đích, Docker hoặc gọi LLM. Không tạo program
slice và không thay đổi context của các lần đánh giá hiện có.

## Chạy

Từ gốc repo SpecMind sau khi cài package:

```powershell
python -m specmind_prep public-api-callers --repo C:\path\to\marshmallow --project configs/projects/marshmallow.json --output artifacts/public_api_callers/my-new-audit
```

Thay đường dẫn checkout bằng đường dẫn thật. Output phải nằm ngoài checkout được
phân tích và chưa tồn tại. Chạy lại dùng thư mục khác, không ghi đè bằng chứng.
Commit trong project config phải khớp HEAD. Manifest lưu cả trạng thái dirty và
hash source, tài liệu, test và implementation; đầu vào thay đổi giữa lúc quét sẽ bị từ chối.

## Tiêu chí public API

- Đọc toctree có caption chính xác `API Reference` trong `docs_dir/api_reference.rst`.
  Đổi tên index qua `--api-index`. Không đi vào toctree `Private API`.
- Đọc `automodule`, `autoclass`, `autoexception`, `autofunction`, `automethod`,
  `autoattribute`, `currentmodule`, tùy chọn members/exclude-members và literal `__all__`.
- Đối chiếu AST với imports/re-exports và alias gán tên tĩnh. Gộp các tên public
  trỏ cùng một định nghĩa; lưu từng tên và dòng tài liệu làm bằng chứng.
- Hàm/method phải có tên public, có docstring hoặc được yêu cầu đích danh/undoc-members.
  Class được thống kê riêng; lời gọi class là tạo instance, không phải gọi từng method.
- Chỉ mở rộng member tự định nghĩa của class. `members` hoặc `private-members`
  kích hoạt xem member, nhưng các tên `_...`, dunder, meta-private và property
  được ghi vào exclusions, không cộng vào mẫu số hàm/method public.
- Không mở rộng toàn bộ API kế thừa, không xử lý hook Sphinx hoặc cấu hình động.
  Đây là chính sách inventory tĩnh có thể kiểm tra, không phải bản sao hoàn chỉnh
  của tài liệu đã render. Tên export là hằng số/module được ghi riêng, không mất dấu.

Tham khảo ý nghĩa các tùy chọn tại
[Sphinx autodoc](https://www.sphinx-doc.org/en/master/usage/extensions/autodoc.html).
Không được đồng nhất mọi symbol có mặt trong docs với public API.

## Caller và vị trí gọi

Quét package cấu hình và thư mục test, phân biệt `source` / `tests`. Bao gồm hàm
lồng nhau, lambda, thân module/class. Caller ID gồm file, dòng định nghĩa và tên,
tránh gộp hai test trùng tên. Có hai mức kết quả:

1. `static_reference`: tên tĩnh/import/alias trỏ được tới API. Vẫn giả định không
   monkey-patching và không chứng minh dispatch runtime.
2. `name_only_possible`: chỉ trùng tên cuối, chưa xác định kiểu receiver hoặc binding.
   Không cộng vào số caller xác định được. Một vị trí có thể nghi vấn nhiều API.

Lời gọi qua instance, fixture, local import hoặc alias cục bộ có thể không phân giải.
Callable được truyền làm đối số, ví dụ `accessor=self.get_attribute`, không phải
lời gọi trực tiếp. Không tìm thấy call site không chứng minh API không được dùng.
Lời gọi API từ ứng dụng ngoài checkout không nằm trong phạm vi.

`@post_load` là lời gọi decorator ngầm, ghi `invocation=decorator`; `@post_load()`
chứa lời gọi factory trực tiếp, ghi `invocation=call`. Decorator ở class body được
đếm là vị trí gọi ở class body, không gán sai cho thân method được decorate.

## Sáu file kết quả

| File | Nội dung |
|---|---|
| manifest.json | Commit, project, phạm vi, hash và số liệu tổng |
| public_api.json | Inventory, alias, bằng chứng tài liệu, exclusions |
| calls.jsonl | Các vị trí gọi đã phân giải tĩnh |
| unresolved.jsonl | Mục tài liệu không phải callable/chưa xác định và call site nghi vấn |
| summary.csv | Một dòng mỗi định nghĩa API, mở bằng Excel |
| REPORT.md | Tổng hợp và giới hạn |

`source_callers` / `test_callers`: số hàm chứa lời gọi khác nhau.
`source_sites` / `test_sites`: số vị trí gọi, gồm module/class body.
Hai lời gọi cùng dòng phân biệt bằng cột byte UTF-8 bắt đầu từ 1.
`implicit_decorator_sites` nằm trong số sites, nhưng được tách cột để giải thích.
`possible_sites` là nghi vấn theo tên; không cộng cột này qua nhiều API để ra tổng
duy nhất. Tất cả là số vị trí tĩnh, không phải số lần thực thi hay test coverage.

## Kiểm tra nhỏ

```powershell
python -m pytest tests/prep/test_public_api.py tests/prep/test_references.py tests/test_layering.py -q
```

Bộ kiểm tra dùng project giả, bao gồm public/private docs, alias, property,
shadowing, nested caller, decorator, trùng tên, thống kê source/test và output guards.
