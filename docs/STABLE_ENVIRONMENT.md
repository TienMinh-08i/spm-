# Môi trường chạy ổn định trên Windows

Dùng venv riêng `.venv-stable`, không dùng package hệ thống hoặc user-site.
`requirements-stable.txt` khóa toàn bộ 27 dependency đã kiểm tra, gồm pytest 8.4.2,
OpenAI SDK 2.36.0, simplejson 3.20.2 và tzdata 2025.2 theo cấu hình của project.
Phiên bản Python đã kiểm tra là 3.11.6, Windows x64.

## Cài từ đầu

Đứng tại root repo. Dùng Python 3.11.6 để tạo môi trường mới, không thêm `--system-site-packages`.

```powershell
python -m venv .venv-stable
.\.venv-stable\Scripts\python.exe -m pip install -r requirements-stable.txt
.\.venv-stable\Scripts\python.exe -m pip install --no-build-isolation --no-deps -e .
.\.venv-stable\Scripts\python.exe -m pip check
```

Không tạo đè một môi trường đang phục vụ thí nghiệm khác. Nếu `.venv-stable` đã tồn tại,
kiểm tra nó trước khi quyết định sửa hoặc cài lại. Không cài thêm package trong môi trường
đã dùng để chuẩn bị dữ liệu nếu muốn tiếp tục dùng đúng fingerprint đó.

## Chạy

```powershell
.\run-stable.ps1 prep --help
.\run-stable.ps1 eval --help
.\run-stable.ps1 test tests/prep/test_environment_diagnostics.py -q
.\run-stable.ps1 replay --help
```

Script kiểm tra venv không lấy package hệ thống; dùng thư mục repo làm working directory;
đặt PYTHONPATH về src của repo, tắt user-site và pytest plugin autoload.
Các plugin pytest được chỉ định bằng `-p` vẫn được tải. Các biến môi trường của shell gọi
được khôi phục khi script kết thúc. Mã thoát của lệnh được trả lại cho shell gọi.
Script không cài package, không sửa manifest và không tự tạo dữ liệu hay gọi LLM.
Lệnh `prep llm-mutants` vẫn có thể gọi API khi được người dùng chủ động chạy.

## Khi artifact cũ báo lệch môi trường

Thông báo hiện nêu rõ package thêm/bớt hoặc source/Python/platform thay đổi.
Điều kiện kiểm tra vẫn chặt như trước: lệch thì dừng; không có chế độ tự bỏ qua.

Dữ liệu được chuẩn bị bằng môi trường cũ không tự trở thành dữ liệu đã kiểm chứng trong venv mới.
Giữ nguyên artifact cũ để đối chiếu. Nếu chuyển môi trường, chạy các bước kiểm tra đã thống nhất
vào thư mục output mới, rồi mới đưa artifact mới vào evaluator.
Response mutant đã lưu chỉ được tái dùng nếu model, prompt và các thiết lập tương ứng khớp;
không gọi LLM thêm chỉ để chuyển môi trường.

## Kiểm tra artifact trước khi chạy

Manifest complete chỉ xác nhận producer hoàn tất phạm vi đầu vào đã chọn; không
có nghĩa đã chạy toàn bộ test của thư viện. Kiểm tra `test_selection` trong
candidate manifest và `links/command.json`. Dùng toàn thư mục `tests` khi mục tiêu
là tìm đầy đủ liên kết; không thay bằng danh sách nodeid pilot hoặc reuse subset.

Chạy lại sang output mới nếu thay dataset/test selection. Mutant source có thể
được rebind khi source và API ownership không đổi, nhưng kết quả runtime filter
cũ không được tái dùng. Chạy filter-mutants trên dataset mới rồi validate liên kết:

```powershell
.\run-stable.ps1 prep validate --repo <checkout> --candidates <candidates> --links <links> --dataset <dataset> --scope <scope> --mutants <filtered-corpus>
```

Đọc `dataset/results.json` để thấy quyết định True/False cho từng nodeid và
`excluded_mutants.json` để thấy lý do loại từng task-mutant. Source/environment
fingerprint phải khớp. Slicing chỉ dùng artifact đúng model và đủ task; corpus
partial không được coi là đầy đủ cho C/D.
