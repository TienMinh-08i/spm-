# Dynamic slicing theo từng lần thực thi

Triển khai mốc đầu của Approach 3 (Dynamic Dependence Graph) trong
[Dynamic Program Slicing — Agrawal và Horgan, 1990](https://doi.org/10.1145/93542.93576).
Mỗi lần chạy câu lệnh là một node riêng; không trộn các vòng lặp chỉ vì cùng file:line.

Hiện hỗ trợ một hàm top-level đồng bộ, biến cục bộ primitive và điều khiển có cấu trúc.
Đây chưa phải slicing liên hàm/heap cho toàn Python. Các input từ caller là boundary;
chưa lần ngược các câu lệnh trong caller để tạo context và chưa thay đổi prompt evaluator.

## 1. Thu trace cho đúng một test

Dùng venv riêng đã khóa dependency. Xem [môi trường chạy](STABLE_ENVIRONMENT.md).

```powershell
.\run-stable.ps1 prep slice-trace `
  --repo C:\work\project --project configs/projects/my-project.json `
  --test 'tests/test_example.py::test_compute' --target demo.compute `
  --output artifacts/slicing/compute-trace
```

- `--test` phải là một pytest nodeid chính xác, có cả tham số `[case]` nếu test parametrized.
  Nếu selector thu được nhiều test, công cụ dừng trước khi chạy.
- `--target` là function ID top-level trong package khai báo ở project config.
- Test chạy hai lượt riêng: baseline chưa sửa và bản instrumented trong bộ nhớ.
  Chọn test có fixture cô lập, vì test được thực thi hai lần.
- Không ghi lại source vào checkout. Chỉ thay code object trong subprocess và khôi phục khi kết thúc.
- So kết quả gọi hàm, argument/return primitive, stdout/stderr và báo cáo pytest.
  Đây là kiểm tra hành vi quan sát được của test đã chọn, không chứng minh tương đương toàn Python.
- Một test fail sau khi gọi hàm vẫn có thể cung cấp trace hợp lệ nếu hai lượt có cùng hành vi.
- `--timeout` mặc định 60 giây cho mỗi lượt; `--max-events` mặc định 20000, tối đa 200000.
  Recorder cũng có giới hạn byte. Hết ngân sách/timeout cho trạng thái incomplete, không phải slice rỗng.
  Timeout phải là số dương hữu hạn. Log stdout/stderr được giữ tối đa 8 MB và dừng subprocess
  khi vượt; plugin ghi result JSON theo luồng với giới hạn 24 MB. Không coi các giới hạn này
  là sandbox cho mọi file hoặc tài nguyên mà test có thể sử dụng.

## 2. Chọn lần gọi và truy vấn backward

Mở `calls.json` để chọn đúng lần gọi. Mỗi phần tử có frame ID, caller thực tế,
phase setup/call/teardown, event lúc vào hàm và return_event.
Hai lần gọi trên cùng dòng vẫn có frame/event khác nhau.

```powershell
# Ví dụ chủ động chọn lần gọi đầu tiên sau khi đã xem calls.json.
$call = (Get-Content -Raw artifacts/slicing/compute-trace/calls.json | ConvertFrom-Json)[0]

.\run-stable.ps1 prep slice-call `
  --repo C:\work\project --trace artifacts/slicing/compute-trace `
  --event $call.return_event --variable total --when before `
  --output artifacts/slicing/compute-total.json
```

`before` xem giá trị biến ngay trước event đã chọn; `after` xem ngay sau event đó.
Để xem producer của chính giá trị trả về, dùng `--variable '$return' --when after`
tại return_event. Dấu nháy đơn cần thiết trong PowerShell để không thay `$return` bằng biến shell.

Truy vấn chỉ đọc trace, không chạy lại test. Output phải là file mới ngoài checkout và ngoài thư mục trace.
Không có fallback về thuật toán tĩnh trước đây và không tự chọn occurrence khi thiếu `--event`.
Forward và Reduced DDG chưa được triển khai; `--direction` hiện chỉ nhận backward.

## 3. File và ý nghĩa

| File | Nội dung |
|---|---|
| manifest.json | Model, source/config/environment/implementation fingerprint, giới hạn, trạng thái và hash file |
| events.json | Các occurrence thực thi, frame, read/write, input và control producer |
| ddg.json | Node động và depends_on, kiểm tra cạnh chỉ tới producer đã xảy ra |
| calls.json | Các lần gọi, caller/file:line, phase và return event |
| sites.json | Source span gốc; cột byte UTF-8 bắt đầu từ 1 |
| baseline/instrumented.json | Bằng chứng hành vi và kết quả subprocess |
| baseline/instrumented.log | Log pytest đầy đủ |
| execution.json, summary.json | Kết quả chạy và trạng thái mô hình |

Slice JSON giữ cả node động và source_statements. Chỉ gộp các occurrence về source sau
khi duyệt DDG. Source map được đối chiếu lại với AST gốc khi đọc artifact.
Output là bằng chứng phụ thuộc, không mặc định là chương trình độc lập chạy được.

## 4. Phạm vi hiện hỗ trợ

- Input/return và local variable có kiểu chính xác None, bool, int, finite float hoặc str có giới hạn kích thước.
- Phép gán tên đơn/nhiều tên cùng giá trị, augmented assignment cho tên cục bộ.
- if/else, while không có break/continue/else và một return cuối hàm.
- Thêm guard top-level dạng `if condition: return value` không có else, trước return cuối.
  Phần tiếp theo được instrument như nhánh else của guard, nên giữ đúng phụ thuộc
  vào predicate False. Source map vẫn trỏ về code gốc; không ghi lại source checkout.
- Biểu thức primitive, so sánh, short-circuit và conditional expression; chỉ ghi các read thực sự được đánh giá.
- Các builtin int/float/bool/str/abs/min/max đúng binding; input() là input origin ở phép gán,
  print positional là output boundary. Không suy ra nguồn bằng cách so hai giá trị bằng nhau.
- Default/annotation không được đánh giá lại khi dựng code instrumented.
- Kiểm tra binding, bytecode/source snapshot, hash artifact và pytest phase.

Chưa hỗ trợ: method/decorator/closure, heap/alias dict/list/attribute, global/nonlocal,
callee tùy ý, for, exception flow trong hàm, return sớm ngoài dạng guard trên, break/continue,
comprehension, generator/async và code sinh động. Không tự coi lời gọi không rõ là pure.
Điều khiển bằng nesting chỉ áp dụng cho tập structured đã chấp nhận; chưa có CFG tổng quát.

Trace mới dùng model `primitive-local-guard-ddg-v2`. Loader vẫn đọc model v1 của
MVP trước vì semantics của subset cũ được giữ nguyên; không đổi nhãn hoặc fingerprint
artifact cũ. Đây chưa phải mở rộng mọi early exit của Python.

## 5. Trạng thái

| Trạng thái | Ý nghĩa |
|---|---|
| complete_supported | Trace hợp lệ và checks hành vi pass trong model đã khai báo |
| unsupported | Source, giá trị hay effect ngoài model; không công bố slice hoàn chỉnh |
| incomplete | Timeout, ngân sách hoặc thiếu kết quả chạy |
| criterion_not_reached | Test không gọi hàm đã chọn |
| instrumentation_changed_behavior | Hai lượt khác hành vi quan sát được; không dùng slice |

Artifact có thể được ghi hoàn tất để lưu lỗi dù trace_status chưa dùng được.
Reader luôn kiểm tra trace_status riêng; complete trong manifest không tự cho phép trả một slice.
Source/test hoặc môi trường thay đổi thì reader từ chối artifact cũ.

## 6. Kiểm chứng

```powershell
.\run-stable.ps1 test tests/prep/test_slicing.py tests/prep/test_slice_instrumentation.py tests/prep/test_slice_trace.py tests/test_layering.py -q
```

Có oracle độc lập cho phản ví dụ vòng lặp của bài: chọn đúng nhánh tại lần quan sát Z;
giữ đúng định nghĩa A từ vòng trước dù Z được tính lại ở vòng sau. Các f_i trong ví dụ
được thay bằng biểu thức scalar giữ cùng cấu trúc phụ thuộc.
Kiểm tra thêm gán đè, phase fixture, callable bị thay binding, bytecode không khớp source,
CRLF/multiline/annotation/default, test fail, timeout, budget và artifact bị sửa.
