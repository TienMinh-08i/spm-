# Chuẩn bị dataset và hai bộ mutant

Chạy các bước chuẩn bị một lần, trước evaluator. Evaluator dùng test và mutant đã chuẩn bị; không tạo/lọc lại trong mỗi attempt. M1 là mutmut, M2 là LLM; chạy và báo cáo hai cách riêng vì mẫu số khác nhau.

## Bước 0–3: tài liệu và lựa chọn API

Project config pin commit, package paths và cấu hình build docs. Build dùng checkout/venv riêng:

```powershell
.\run-stable.ps1 prep build-docs --project <project.json> --source <repo-nguon> --workdir <thu-muc-build-moi> --output <artifact-docs-moi>
```

Nếu đã có artifact đúng commit, dùng `prep validate-docs --output <artifact-docs>` để tái dùng.

Đọc từng trang API reference công khai. Một API phải có def thuộc project, đường dẫn công khai và anchor riêng không khai báo private/internal/provisional/deprecated. Class có docs không tự làm mọi method đủ điều kiện. Experimental không tự đồng nghĩa private. Giữ nguyên docstring, document, link và commit.

Review `eligibility.jsonl` ghi include/exclude cho mọi proposal. `selection.jsonl` giữ đủ API được nhận, thêm keep/drop/delegate. Drop phải có lý do; delegate phải chỉ rõ hàm đích cuối có source. Thân wrapper chỉ forward tham số/hằng, không xử lý thêm. Chưa chắc keep thì để probe quyết định khả năng quan sát.

```powershell
.\run-stable.ps1 prep validate-selection --repo <checkout> --project <project.json> --docs <artifact-docs> --eligibility <eligibility.jsonl> --selection <selection.jsonl> --output <bundle>/candidates
```

Candidates dùng source của target nhưng giữ đặc tả API. `function_id` là API, `problem_id` là hàm phân tích. Khóa đánh giá dùng API identity; nhiều task có thể chung source target.

## Bước 4–5: trace và probe

```powershell
.\run-stable.ps1 prep trace --repo <checkout> --candidates <bundle>/candidates --test <test-nodeid> --output <bundle>/links
.\run-stable.ps1 prep dataset --repo <checkout> --candidates <bundle>/candidates --links <bundle>/links --output <bundle>/dataset
```

Lặp `--test` để chọn thêm test. Trace tách từng API; helper thông thường kiểm API ancestor và hằng forwarding. Decorator có tag riêng cần xét cả factory và deferred callback. Probe giữ test khi assert True pass và assert False làm test fail tại điểm postcondition. Test bắt/nuốt assertion hoặc không tới điểm kiểm sẽ bị loại.

Để chuyển một bộ đã kiểm sang selection mới, trace có `--reuse-from <bundle-cu>`; dataset có `--reuse-from <dataset-cu>`. Chỉ keep task không đổi source/spec/test mới được reuse, với fingerprint và provenance. Delegate mới vẫn phải probe. Trước khi chỉ trace một nhóm nhỏ nhưng reuse test của nhóm khác, candidates cần lưu danh sách `--test` cho bước collection; collection chỉ thu nodeid, không thực thi toàn bộ test.

## Bước 6: self-only scope

```powershell
.\run-stable.ps1 prep scope --repo <checkout> --dataset <bundle>/dataset --output <bundle>/scope
```

Mỗi task có đúng một self target. `scope.json` giữ task và target riêng; `targets.json` dedup theo source function. Không thêm callee hoặc phụ thuộc slicing ở bước này.

## Bước 7: hai corpus độc lập

M1 dùng mutmut 2.5.1 source operators trong vendor directory riêng; không Docker và chưa chạy test trên toàn bộ mutant:

```powershell
.\run-stable.ps1 prep mutmut-local --scope <bundle>/scope --repo <checkout> --vendor <vendor-mutmut-2.5.1> --output <bundle>/mutants_M1
```

Có thể cài dependency riêng bằng `python -m pip install --target <vendor-mutmut-2.5.1> mutmut==2.5.1`. Worker kiểm đúng version, giữ chữ ký/decorator và loại source không compile hoặc AST trùng/không đổi. Một target có thể có zero mutant.

M2 tạo source pool rồi gắn vào scope ready:

```powershell
.\run-stable.ps1 prep llm-target-pool --repo <checkout> --candidates <bundle>/candidates --model <model> --max-new-requests <gioi-han> --output <pool-moi>
.\run-stable.ps1 prep bind-llm-pools --repo <checkout> --dataset <bundle>/dataset --scope <bundle>/scope --pool <pool> --output <bundle>/mutants_M2
```

Lặp `--pool` khi ghép các pool không trùng target. Có thể dùng `llm-target-pool --target <ham-dich>` để bổ sung đúng một target. Xem [chi tiết prompt, cache và token budget](LLM_MUTANT_POOL.md). Pool cũ có thể dùng lại nếu source target đúng; provenance giữ request/response gốc. Không đổi nhãn mutant wrapper thành helper.

## Bước 7b: lọc runtime trung tính cho M1 và M2

Sau khi tạo M1 hoặc ghép M2, chạy bước này trước khi dùng corpus trong evaluator:

```powershell
.\run-stable.ps1 prep filter-mutants --repo <checkout> --dataset <bundle>/dataset --mutants <bundle>/mutants_M1 --output <bundle>/mutants_M1_filtered --workers 4 --timeout 120
.\run-stable.ps1 prep filter-mutants --repo <checkout> --dataset <bundle>/dataset --mutants <bundle>/mutants_M2 --output <bundle>/mutants_M2_filtered --workers 4 --timeout 120
```

Không gọi LLM. Mỗi task kiểm baseline bằng `assert True` trước; baseline lỗi thì dừng. Mỗi mutant chạy trên toàn bộ test đã chuẩn bị của task. Giữ khi mutation được thực thi, postcondition chạy ít nhất một lần, mọi test hoàn tất setup/call/teardown và không có lỗi instrumentation. Test assertion gốc fail sau mutation vẫn được giữ. Loại khỏi corpus mới khi lỗi collection, timeout, cài mutant hoặc không tới postcondition; lý do lưu trong `excluded_mutants.json`.

Lọc theo task–mutant, không gộp các API chung helper. Đây là tiêu chí quan sát được postcondition, không chứng minh mutant không tương đương. Một số lời gọi có thể exception nhưng mutant vẫn được giữ khi có postcondition quan sát được và test execution hoàn tất. Assertion mới vẫn có thể gây runtime error.

Trỏ `prepared_mutants` của cấu hình mới vào corpus `_filtered`. Task hết mutant giữ danh sách rỗng và mutation score N/A. Dùng `--resume` với cùng code, dữ liệu, môi trường và timeout; các quyết định hoàn tất được dùng lại, không tự retry timeout đã ghi nhận. Muốn kiểm lại timeout, chọn output mới. `decisions.json`, manifest và evidence lưu nguồn gốc; corpus gốc được giữ.

## Kiểm tra và chạy

```powershell
.\run-stable.ps1 prep validate --repo <checkout> --candidates <bundle>/candidates --links <bundle>/links --dataset <bundle>/dataset --scope <bundle>/scope --mutants <bundle>/mutants_M2
.\run-stable.ps1 replay --repo <checkout> --dataset <bundle>/dataset --mutants <bundle>/mutants_M2 --function <API-id> --output <replay-moi>
.\run-stable.ps1 eval run --config <eval_M2.json> --output <run-moi>
```

Hai lệnh đầu không gọi LLM. Replay trung tính kiểm mutant được cài/thực thi; test fail sau mutation chưa phải assertion kill. Lệnh evaluator gọi LLM sinh assertion nếu config không dùng mock. Chọn `tasks` trong config để debug một API; bỏ nó chỉ khi muốn chạy toàn bộ phạm vi đã chuẩn bị.

Correctness pass chỉ có nghĩa pass trên bộ test đã chọn. Correctness fail thì không đo mutation score; target không có mutant ghi N/A. Số mutant duy nhất của kho khác tổng số liên kết task–mutant khi nhiều API dùng chung helper.

Nếu hợp đồng cần đọc trạng thái ứng dụng có tên dunder, khai báo `state_attributes` trong config, theo analysis target. Ví dụ `{"marshmallow.decorators.set_hook": ["__marshmallow_hook__"]}` cho phép kiểm hiệu ứng đăng ký hook. Policy chỉ nhận trường mà source thực sự ghi; các trường Python introspection như `__code__`/`__globals__` vẫn bị cấm. Prompt cũng nhận đúng ngoại lệ này.

## Caller context tự động từ dynamic slicing

Chuẩn bị context trước khi chạy evaluator. Luồng chính không trace lại trong mỗi
attempt. Bắt đầu bằng preflight, không chạy test:

```powershell
.\run-stable.ps1 prep slicing-pipeline --dataset <prepared-dataset> --repo <checkout> --project <project.json> --output <new-output> --preflight-only
```

Để trace các cặp caller/test vượt preflight và tạo context:

```powershell
.\run-stable.ps1 prep slicing-pipeline --dataset <prepared-dataset> --repo <checkout> --project <project.json> --output <new-output> --max-tests 1 --timeout 60
```

Lệnh tự tìm caller package có tham chiếu tĩnh, kiểm tra model, chọn test đã chuẩn
bị, chạy baseline/instrumentation, tạo criterion và xuất DDG/RDDG/context.
Không gọi LLM, không sinh mutant, không tự tạo/lọc lại dataset. Output phải mới và
nằm ngoài checkout. `--max-tests` giới hạn số test được thử cho mỗi caller.
Caller/test được sắp theo thứ tự ổn định; chọn cặp đầu tiên trace hợp lệ và giữ
mọi invocation target trong execution đó. Đây không phải bao phủ mọi caller/test.

### Criterion và context

- INPUT ORIGINS: backward từ event đánh giá mỗi actual argument. Với dict/list,
  bao gồm các producer của nội dung quan sát được ngay trước invocation.
- OUTPUT USES: forward từ call-result bridge.
- MUTATION USES: forward từ writer cuối của location bị target sửa, chỉ hiển thị
  các use sau boundary trả về.

Frame, occurrence và object identity được ghi tự động. Keyword được nối với tên
formal parameter. Không gộp các lời gọi trên cùng dòng, không chọn context ngắn
nhất. Object mutation/alias hỗ trợ exact builtin dict với key exact str và list
với index exact int; list hiện chỉ hỗ trợ thay giá trị tại index. Baseline và bản
instrumented đối chiếu return cùng trạng thái argument sau lời gọi, bao gồm alias.
Source excerpts là bằng chứng từ một execution, không phải đặc tả đầy đủ hay lời
cho phép thêm lời gọi vào assertion.

### Artifact và cách kiểm tra

- `REPORT.md`, `summary.json`, `coverage.json`: coverage và lý do từng task.
- `traces/`: source map, events, DDG, baseline/instrumented evidence, fingerprint.
- `reduced.review.json`: RDDG offline; kiểm tra backward source closure của mọi
  occurrence với DDG đầy đủ. Có quy tắc immediate dependencies và cycle-subset
  của Approach 4. Giữ occurrence audit map nên không tuyên bố bộ nhớ streaming.
- `automatic.review.json`: criterion và truy vấn tự chọn. Forward dùng DDG đầy đủ.
- `packs.json`, `manifest.json`: context offline để evaluator nạp.

Nếu thiếu task, manifest có complete=false; C/D từ chối bộ thiếu. Task unsupported
không được thay bằng context heuristic. Context vượt budget được báo lỗi, không
âm thầm lược bỏ invocation. Để dùng bộ hoàn chỉnh, đặt caller_context="slice" và
slice_context_dir trong config; A=none, B=callee, C=caller, D=callee+caller.

### Debug một cặp caller/target

```powershell
.\run-stable.ps1 prep slice-trace --repo <checkout> --project <project.json> --test <pytest-nodeid> --target <function-id> --caller <caller-id> --heap --output <new-trace>
```

Có thể dùng `caller-slice-context --criteria <file.json>` để xuất lại từ trace đã
xác minh. Mỗi hàng: function (task), trace (relative to file), caller, mode="auto".
Criterion thủ công event/variable/when vẫn dùng được khi không đặt mode=auto.

### Phạm vi hiện tại

Model heap hỗ trợ hai hàm top-level đồng bộ, biến local và if/while có cấu trúc,
actual/formal/return, dict/list allocation, item read và single-target item store.
Chưa hỗ trợ method/custom object attributes, for/comprehension, variadic arguments,
list structural mutation, general exception CFG hoặc API-wrapper-helper chain.
Không tuyên bố hỗ trợ đầy đủ Python. Bộ Marshmallow 24 task vẫn bị chặn ở preflight;
đọc coverage từng task trước khi quyết định mở rộng model. Không dùng corpus này
để chạy ABCD khi manifest chưa complete.


## Đổi tập test mà giữ source mutant

Khi source và API ownership không đổi, dùng corpus gốc chưa lọc làm đầu vào:

```powershell
.\run-stable.ps1 prep rebind-mutants --source <raw-corpus> --old-dataset <old-dataset> --dataset <new-dataset> --scope <new-scope> --repo <checkout> --output <new-rebound>
.\run-stable.ps1 prep filter-mutants --dataset <new-dataset> --mutants <new-rebound> --repo <checkout> --output <new-filtered> --workers 4 --batch-size 500 --timeout 300
```

Rebind giữ ID/source và kiểm tra source, signature/location, delegate binding,
scope cùng task set. Không dùng corpus đã lọc cũ làm nguồn vì phải xét lại cả
những mutant từng bị loại. Rebound có runtime_filter_required=true; evaluator
mặc định từ chối nó. Chỉ producer filter được phép đọc dạng này. Corpus sau lọc
có flag false và đầy đủ evidence/decision của từng liên kết API-task–mutant.

Các test assertion của thư viện có thể kill mutant; đó không phải lý do tự loại
nếu mutation và điểm postcondition vẫn quan sát được. Runtime eligibility dưới
assert True không chứng minh mutant khác nghĩa, cũng không bảo đảm mọi candidate
sau này không tạo postcondition runtime error. Score vẫn tách kill/not-kill/unknown.

## Provenance của response và preflight ABCD

Generation lưu response_origin: live_model, cached_response, custom_callable,
mock hoặc unknown_saved_response. Pending response gắn với hash toàn bộ messages;
khác prompt/protocol phải dùng output mới. Input parameter bindings giữ giá trị
của lời gọi gốc; nội dung object mutable được quan sát sau khi target trả về.
Assertion không có biến snapshot trước lời gọi, trừ khi model được bổ sung riêng.

ABCD kiểm tra input/context của cả bốn nhánh trước khi gọi model ở nhánh nào.
Thiếu caller slice hoặc corpus chưa lọc sẽ chặn trước khi phát sinh model call.
Comparison summary thống kê origin; không coi cached/custom/unrecorded response
là một lượt gọi model mới được xác minh. live_model chỉ mô tả transport đã ghi
nhận, không tự chứng minh các mẫu độc lập về thống kê.

Trace manifest ghi test_selection thực thi. Prepare dùng scope của trace, không
lấy danh sách pilot từ candidate để thu hẹp. Reuse links khác test/exclusion scope
bị từ chối; legacy mixed-scope cần trace lại để tránh mất test âm thầm.
