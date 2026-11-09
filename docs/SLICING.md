# Dynamic slicing theo từng lần thực thi

Triển khai mốc đầu của Approach 3 (Dynamic Dependence Graph) trong
[Dynamic Program Slicing — Agrawal và Horgan, 1990](https://doi.org/10.1145/93542.93576).
Mỗi lần chạy câu lệnh là một node riêng; không trộn các vòng lặp chỉ vì cùng file:line.

Mặc định hỗ trợ một hàm top-level đồng bộ, biến cục bộ primitive và điều khiển có cấu trúc.
Có thể chọn thêm một caller bằng --caller để lần ngược nguồn argument và return qua lời gọi.
Đây chưa phải slicing liên hàm/heap cho toàn Python và chưa thay đổi prompt evaluator.

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

### Theo dõi nguồn đối số trong một caller được chọn

Thêm `--caller` để instrument cả một caller top-level trong package và target:

```powershell
.\run-stable.ps1 prep slice-trace `
  --repo C:\work\project --project configs/projects/my-project.json `
  --test 'tests/test_example.py::test_compute' `
  --caller demo.prepare_and_compute --target demo.compute `
  --output artifacts/slicing/caller-compute
```

Model `primitive-call-boundary-ddg-v3` ghi từng actual argument riêng, binding sang
parameter theo tên và nối return qua event call_result về caller. Không suy nguồn bằng
giá trị bằng nhau; một argument không được target dùng không tự bị kéo vào slice.
Invocation giữ phụ thuộc điều khiển ở vị trí gọi mà không kéo toàn bộ argument vào kết quả.
Đây là phần mở rộng cho Python/SpecMind trên nguyên tắc DDG theo occurrence, không phải
một thuật toán liên hàm riêng được paper này trình bày.

Trong calls.json, lọc theo `function` để chọn caller hay target, xem `parent_frame`,
`invocation`, caller name/file:line và event. Để lần ngược nguồn input của target,
chọn `event` lúc vào target, `--variable <parameter>` và `--when before`.
Để lần ngược kết quả về caller, chọn return_event của caller và `--variable '$return' --when after`.

Phạm vi: đúng một caller + một target top-level đồng bộ đã chỉ định, trong source package;
global import alias được xác minh bằng runtime function identity. Cung cấp tường minh
mọi parameter của target, positional/keyword hợp lệ; chưa theo nguồn default bị lược bỏ,
`*args/**kwargs`, method, heap, recursion, hoặc traced call lồng trong actual argument.
Caller từ test/fixture hiện là boundary bên ngoài; chưa tự instrument thân test/fixture
hay caller-of-caller. Target bị gọi bên ngoài caller đã chọn được báo unsupported.
Caller chạy nhưng không gọi target cho criterion_not_reached.
Các giới hạn primitive/control flow hiện tại vẫn áp dụng. Không có --caller thì giữ model v2.

Chưa có predicate event riêng cho lời gọi chạy có điều kiện bên trong biểu thức:
`flag and target(x)`, `target(x) if flag else ...`, hoặc vị trí so sánh được short-circuit
trong comparison chain. Các dạng này bị từ chối trước execution, vì nếu thiếu cạnh
điều khiển thì query input của target có thể bỏ mất nguồn của flag. Dùng if/else tường minh
trong code thử nghiệm thuộc subset hỗ trợ; không tự viết lại checkout để vượt giới hạn.
Trace v3 cũ chứa các dạng này cũng bị reader từ chối, không đổi artifact cũ để cho qua.
Điều kiện nằm trong chính argument như `target(x if flag else y)` vẫn được theo dõi:
argument event ghi các read thực sự xảy ra, gồm flag và nhánh đã chọn.

### Hàm chỉ đọc object/type và chuỗi helper

`--readonly-helpers` chọn model `readonly-expression-ddg-v4`, tách khỏi model primitive.
Ví dụ với checkout Marshmallow đúng commit trong project config:

```powershell
.\run-stable.ps1 prep slice-trace `
  --repo C:\work\marshmallow --project configs/projects/marshmallow.json `
  --test 'tests/test_utils.py::test_is_collection' `
  --target marshmallow.utils.is_collection `
  --readonly-helpers marshmallow.utils.is_iterable_but_not_string marshmallow.utils.is_generator `
  --output artifacts/slicing/is-collection
```

Mỗi hàm được chọn phải top-level đồng bộ, có đúng một parameter không default và một
return expression sau docstring. Helper trong cùng module phải được chọn tường minh,
tối đa bảy helper, không đệ quy. Không kết hợp với --caller. Truy vấn dùng slice-call như trên;
lọc calls.json theo function để chọn đúng return_event của root và dùng '$return' sau event.

Model ghi expression/data edges và predicate cho từng operand and/or thực sự được đánh giá.
Nguồn đưa vào frame khác với sub-invocation bên trong frame, nên return của helper lồng nhau
không bị nối nhầm. Đây là mở rộng AST/Python trên nguyên tắc occurrence DDG, không phải RDDG.

- Input exact list/tuple/dict/bytes được ghi bằng type và token trong một root occurrence.
  Không đọc nội dung container, không giữ object sống lâu hơn lần gọi để cấp token.
- Chỉ tin cậy lookup hasattr với __iter__/strip, isinstance với Mapping và hai predicate
  inspect.isgeneratorfunction/isgenerator trên các exact builtin input đã nhận. Kết quả thao tác
  được lấy từ một lần gọi thật; binding/code, namespace chuẩn và ABC token được kiểm tra.
  Inspect được biểu diễn bằng operation summary có source hash, không giả vờ trace toàn bộ stdlib.
- Custom object/subclass, container truth/content reads, mutation, method hoặc protocol khác
  vẫn unsupported. Hàm được chọn phải trả bool. Đây chưa phải hỗ trợ toàn API is_collection.
- Caller name/file:line và input trước body được giữ. Khi call site trong test không mơ hồ,
  source argument được gắn với external_input. Nếu nhiều call lồng/cùng dòng không phân biệt
  được, giữ origin ở boundary của root; không lấy nhầm argument của call khác.
- Thân test/fixture và phần tính input trước call chưa được slice. Nội dung object không được
  snapshot trước/sau; scope và limitations trong JSON nêu rõ các boundary này.

Source projection có thể giữ nguyên cả dòng return chứa and/or, gồm cả đoạn không chạy.
Xem predicate/call/operation events để phân biệt nhánh đã chạy; không coi fragment đó là
chương trình độc lập. Trace/query ghi thư mục mới, không thay dataset/mutant hoặc prompt.

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
