# Chuẩn bị dataset và mutant

Mục tiêu là tạo artifact dùng lại được. Chuẩn bị xong rồi mới chạy sinh/đánh giá assertion.
Evaluator đọc dataset và mutant đã chuẩn bị; không tạo lại chúng trong mỗi attempt.

## Các bước

| Bước | Làm gì | Đầu ra chính |
|---|---|---|
| 0 | Build và kiểm tra tài liệu tại commit cố định | HTML, môi trường build, manifest và hash |
| 1 | Đối chiếu API đề xuất với source và tài liệu HTML | Inventory, tên public/alias, tài liệu, trạng thái cần review |
| 2 | Ghi quyết định keep/drop và lý do | Selection giữ nguyên các trường inventory; target là null |
| 3 | Kiểm tra selection trước khi chạy test | Candidates đã niêm phong cùng bằng chứng source/docs |
| 4 | Chạy các test được chọn để quan sát hàm được gọi | Liên kết hàm–test và log trace |
| 5 | Chỉ giữ test pass với assert True, rồi fail vì assert False tại điểm chèn | Dataset, test được giữ/loại và bằng chứng probe |
| 6 | Chọn chính hàm đang xét làm nơi tạo mutant | Scope self-only; không mở rộng theo độ dài hàm |
| 7 | Sinh và lưu mutant bằng generator được chọn | Request/response, mutant, lý do loại và manifest |

Alias là các tên public trỏ tới cùng định nghĩa. Inventory giữ các tên đó cùng vị trí định nghĩa.
Delegate hiện chưa được hỗ trợ: không tự chuyển API sang một helper và không gộp các API có đặc tả khác nhau.
Hàm còn thiếu tài liệu hoặc đang chờ review không được keep tự động. Hàm không được chọn cũng không tự biến thành drop.

## Kiểm tra lựa chọn API

Dùng file JSONL: mỗi dòng là một object inventory đầy đủ, thêm decision, reason và target.
Giữ nguyên function_id, commit, public_names, document_link, document, docstring và status.
Điền decision là keep hoặc drop; điền lý do cụ thể; target là null.
Các hàm cần review phải được chỉ định nhất quán khi lập inventory và khi kiểm tra selection.

```powershell
python -m specmind_prep.cli validate-selection `
  --repo C:\work\marshmallow --project configs/projects/marshmallow.json `
  --docs artifacts/docs/marshmallow --selection inputs/api_selection.jsonl `
  --output artifacts/candidates
```

Lệnh chỉ đọc source/docs và kiểm tra selection, không import thư viện đang xét và không chạy pytest.
Nếu sai nhiều trường, thông báo tập hợp các lỗi để sửa cùng lúc.

## Chọn test và tạo scope

Cấu hình project giữ các đường dẫn thư mục test, ví dụ tests.
Danh sách nodeid dùng cho một đợt chuẩn bị được lưu riêng trong test_selection của candidates.
Runner đọc danh sách này khi collection; không thay project.tests bằng danh sách nodeid.

```powershell
python -m specmind_prep.cli scope `
  --dataset artifacts/dataset --repo C:\work\marshmallow --output artifacts/scope
```

Lệnh scope đọc dataset đã chuẩn bị, không chạy test. Mỗi hàm có đúng một mutation target là chính nó.

## Sinh mutant LLM

```powershell
python -m specmind_prep.cli llm-mutants `
  --scope artifacts/scope --dataset artifacts/dataset --repo C:\work\marshmallow `
  --output artifacts/mutants_llm --model openai/gpt-6-luna --method line --max-targets 22
```

Lệnh này có gọi API. Model phải được chỉ định; khóa được đọc từ biến môi trường theo cấu hình LLM.
Method line đề xuất khoảng năm biến thể cho mỗi dòng, rồi áp mỗi biến thể độc lập lên source gốc.
Kiểm tra cú pháp, signature và AST trùng không chứng minh mutant khác ngữ nghĩa hoặc tương đương.

Mỗi hàm có checkpoint riêng. Khi một response lỗi, các hàm thành công vẫn có kết quả lưu lại,
nhưng corpus còn pending sẽ không được đánh dấu complete hay đưa vào evaluator.
Nếu đã gửi request mà chưa lưu được response, chạy lại không tự gửi request đó lần nữa.
Xem checkpoints.json, request và response trước khi quyết định xử lý tiếp.
Khi code hoặc prompt đổi, dùng output mới; không sửa fingerprint để ép nhận cache.

## Kiểm tra và replay

```powershell
python -m specmind_prep.cli validate `
  --repo C:\work\marshmallow --candidates artifacts/candidates --links artifacts/links `
  --dataset artifacts/dataset --scope artifacts/scope --mutants artifacts/mutants_llm

python -m specmind_prep.mutant_replay `
  --dataset artifacts/dataset --mutants artifacts/mutants_llm --repo C:\work\marshmallow `
  --function marshmallow.utils.is_collection --output artifacts/replay/is_collection
```

Replay chạy baseline và một mutant với assert True trên đúng một test đã liên kết.
Có thể thêm --mutant-id và --nodeid; nếu bỏ qua, chọn mutant và test đầu tiên theo thứ tự đã lưu.
Kết quả phân biệt cài lỗi, chưa gọi đến mutant, timeout, test pass và test fail sau khi thay mutant.
Test fail trong replay trung tính không được tính là assertion kill.
Xem report.json và arms để đọc đầy đủ event và log.

Dùng cùng Python, dependency và source checkout đã chuẩn bị artifact.
Manifest của mỗi bước ghi fingerprint của artifact cha. Các artifact tự hợp lệ vẫn có thể không thuộc cùng một chuỗi;
lệnh validate với đầy đủ đầu vào kiểm tra cả quan hệ đó. Output cũ không bị ghi đè.

## Chạy thử luồng bằng config

Module specmind_prep.prototype nhận --config với repo, project, docs, output, selection và tests.
selection ghi quyết định rõ ràng theo function_id; runner bổ sung bằng chứng từ inventory trước bước 3.
tests là danh sách nodeid cụ thể. selection_limit và test_limit giới hạn phạm vi chạy thử.
through là trace, scope hoặc mutants. Chọn scope nếu chỉ muốn chuẩn bị mà chưa sinh mutant.
Để sinh LLM, đặt mutant_generator là llm và model cụ thể; runner dùng method line.
Nếu chọn mutmut, cần mutmut_vendor trỏ tới môi trường mutmut của runner thử nghiệm.
Hai generator có kho kết quả riêng, không cộng chung số lượng.
