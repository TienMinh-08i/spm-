# Những gì thay đổi và lý do

## Tổ chức lại source

- Một CLI `run/report`; config JSON đầy đủ, từ chối key không biết.
- Tách dataset, pytest execution/plugin, mutant export/import, evaluator, metrics, SpecMind, storage và report.
- Bỏ dependency runtime vào vendor/crawler/PR/nl2postcond; không sửa sys.path.
- Prompt và parser baseline được trích từ source gốc có trong project, ghi hash nguồn trong provenance.json.
- Dataset, target checkout, mutant export và experiment output đều là dữ liệu ngoài package.

## Các sửa đổi có ảnh hưởng kết quả

| Sửa | Lý do | Kiểm chứng |
|---|---|---|
| Kill mới = assertion IDs trừ suite IDs | Một test neutral pass chưa chứng minh cả suite không kill mutant | Test hai cases: một case suite kill, một case chỉ assertion kill |
| Giữ correctness riêng mutation | Unavailable mutation không biến correctness pass thành fail | Fake multi-turn trace giữ best trước lượt unavailable |
| Feedback có mẫu số export cố định | Candidate không được hưởng lợi khi coverage hoặc evaluator bị lỗi | Test unknown làm lower bound 1/2, không nâng thành 1/1 |
| Runtime path + AST guard | Source trên đĩa chưa chứng minh đúng code được import | Hai negative tests cho source/path mismatch |
| Exception của postcondition không tự là assertion kill | NameError/TypeError không chứng minh bắt đúng lỗi semantic | Real pytest runtime-error test |
| Hook alias, method descriptor và recursive mutant | Test có thể gọi qua alias/method; recursive mutant cần giữ hook | Real pytest alias, classmethod, staticmethod, recursion tests |
| Bind input theo original signature | Mutant đổi default không được đổi luôn input kỳ vọng của assertion | Mutant default 1→2, test gốc pass nhưng assertion kill mới |
| Không tự promote explore thành submit | Phân biệt candidate thử và kết quả model thực sự submit | Fake trace có explore tốt hơn nhưng không được chọn |

Những thay đổi trên là thay đổi protocol thích ứng repository. Kết quả v2 phải gắn nhãn phiên bản, không ghép vào bảng v1 như cùng điều kiện thí nghiệm.

## Tối ưu và tái lập

- Reuse mutant cần manifest source khớp, file đủ và IDs không trùng. Không im lặng regenerate hoặc bỏ missing files.
- Cache từng neutral/candidate arm; lưu output model trước khi đánh giá; resume attempt bị gián đoạn.
- Fingerprint source/config/tests/export/environment/engine; sai fingerprint yêu cầu output mới.
- Ghi artifact atomically và dùng OS lock ngăn hai process ghi cùng run.
- Pin dependency trực tiếp và có requirements.lock đã resolve trên Windows/Python 3.11.
- Bổ sung tzdata để baseline Marshmallow chạy được trong môi trường Windows sạch.

## Giới hạn được giữ rõ

- Chưa bao gồm call graph/caller/callee; baseline không tự thay đổi context.
- Chưa thay PR/EvalPlus/nl2postcond runners của bản cũ.
- Native mutmut export chỉ hỗ trợ Linux/Docker và mutmut 2.5.1; chưa kiểm thử container trong lượt này do daemon không chạy.
- Chưa chạy API LLM thực hoặc full 28-function experiment bằng v2. Pilot dùng assertion cố định để kiểm chứng evaluator.
- Closure/async/generator target chưa hỗ trợ; ghi unavailable thay vì đo sai.
- Paired mutation score có điều kiện trên paired set; luôn cần đọc exported/paired/unresolved cùng nhau.
- Cache dùng giả định môi trường/test xác định; không tự chứng minh suite không flaky.
