# Step 0: build tài liệu từ commit đã pin

`specmind-prep build-docs` build HTML tài liệu của project tại đúng `project.commit`. Step 1 (thu thập Public API) và Step 3 (kiểm tra) đọc bản build này. Lệnh không gọi LLM, không dùng Docker, không chạy test của project.

## Chạy

Từ root repo (sau `pip install --no-deps -e .`, hoặc đặt `PYTHONPATH=src`):

```powershell
python -m specmind_prep build-docs `
  --project configs/projects/marshmallow.json `
  --source C:\specmind\paper_eval\.cache\clones\marshmallow-code\marshmallow\latest `
  --workdir C:\specmind\tmp\docs_build_work\marshmallow-60a80382 `
  --output artifacts/docs/marshmallow-60a80382
```

| Tham số | Ý nghĩa |
|---|---|
| `--project` | File project, chứa `commit`, `docs_dir` và `docs_build` |
| `--source` | Repo git cục bộ có commit đó. Chỉ được đọc (`git cat-file`, `git clone`); không checkout, reset hay clean |
| `--workdir` | Thư mục **mới** chứa checkout riêng (`checkout/`), virtualenv riêng (`venv/`), doctrees và log thô. Không phải artifact; xoá được sau khi build xong |
| `--output` | Thư mục artifact **mới**. Phải nằm ngoài source và workdir |
| `--python` | Python gốc để tạo venv. Mặc định là Python đang chạy lệnh; phải khớp `docs_build.python_version` |
| `--timeout` | Số giây tối đa cho mỗi subprocess (clone, cài, build). Mặc định 1800 |

`--output` hoặc `--workdir` đã tồn tại thì lệnh dừng ngay, không ghi gì. Muốn build lại thì dùng thư mục mới.

Kiểm tra lại artifact (hash của mọi file, kể cả file thừa hoặc thiếu):

```powershell
python -m specmind_prep validate-docs --output artifacts/docs/marshmallow-60a80382 --expected-commit 60a803821466f3e537fa2729d65e1aaf7df9d2a4
```

Mã thoát: `0` thành công, `1` lỗi (thông báo ở stderr kèm đường dẫn log).

## Lệnh làm gì

1. Kiểm commit có trong `--source`, rồi `git clone --no-checkout` sang `workdir/checkout` và `git checkout --detach <commit>`.
2. Kiểm HEAD bằng `project.commit` và checkout sạch (không file sửa, thêm hay bị ignore).
3. Đối chiếu `docs_build.requirements` với nhóm dependency khai báo trong `dependency_source` (tên gói phải trùng).
4. Tạo venv, chạy các lệnh `install`, kiểm lại checkout, ghi `pip freeze`.
5. Chạy các lệnh `build`, kiểm lại checkout.
6. Kiểm HTML: trang API reference và các trang `pages` tồn tại; API reference có link tới mọi trang trong `pages`; mỗi trang có ít nhất một anchor API (`<dt class="sig-object" id="pkg.mod.name">`); mọi anchor trong `required_anchors` có mặt; mọi link nội bộ trên các trang được kiểm trỏ tới file và anchor có thật.
7. Chỉ khi mọi bước đạt mới ghi `manifest.json` với `"complete": true`.

## Đổi cấu hình

Cấu hình nằm trong khối `docs_build` của file project (ví dụ `configs/projects/marshmallow.json`):

| Key | Ý nghĩa |
|---|---|
| `python_version` | Phiên bản Python `X.Y` của venv; không khớp thì dừng |
| `dependency_source` | `{file, group, lock}`: nơi project khai báo dependency docs, để đối chiếu và ghi hash |
| `requirements` | Danh sách pip đã pin, ghi ra `requirements-docs.txt` trong workdir |
| `install` | Danh sách lệnh (mỗi lệnh là mảng argv) chạy trong checkout |
| `build` | Danh sách lệnh build |
| `api_reference` | Docname của trang API reference, không có `.html` |
| `pages` | Docname các trang API cần kiểm |
| `required_anchors` | Anchor bắt buộc phải có (tên đầy đủ) |

Placeholder dùng được trong lệnh: `{python}` (python của venv), `{checkout}`, `{docs}` (`checkout/docs_dir`), `{html}` (`output/html`), `{doctrees}`, `{requirements}`. Placeholder khác bị từ chối. Lệnh chạy không qua shell.

Với Marshmallow, phiên bản trong `requirements` lấy từ `uv.lock` tại commit, nhánh Python 3.11 (`sphinx==9.0.4`). Đổi `commit` thì cập nhật cả `requirements` theo lock file mới. Đổi bất kỳ trường nào thì build vào `--output` mới.

## Đầu ra

```
<output>/
  html/                 bản build; mở html/api_reference.html hoặc html/index.html trong trình duyệt
  logs/                 venv.log, git-*.log, install-N.log, build-N.log (đã che token/mật khẩu)
  environment.json      Python gốc, Python của venv, pip freeze, hash pyproject/lock
  checks.json           kết quả kiểm HTML, gồm danh sách lỗi
  project.json          bản sao file project đã dùng
  manifest.json         schema docs-build-v1, repo, commit, tree, lệnh đã chạy, fingerprint, hash mọi file, complete
```

Virtualenv và checkout chỉ nằm trong `--workdir`, không vào artifact. `artifacts/` đã có trong `.gitignore`.

## Khi lỗi

- Thông báo lỗi ở stderr ghi bước hỏng và đường dẫn log, ví dụ `Docs build failed: install-1: exit code 1 (log: ...\logs\install-1.log)`.
- `manifest.json` có `"status": "failed"`, `"complete": false`, `error` và `log`.
- Lỗi kiểm HTML nằm trong `checks.json` (`errors`).
- Timeout: log kết thúc bằng `# TIMEOUT after Ns; process tree killed`.
- Log thô chưa che (có thể chứa biến môi trường) nằm ở `workdir/raw-logs/`; không chia sẻ thư mục này.

Lỗi thường gặp: không có mạng tới PyPI (xem `install-1.log`); Python không đúng `python_version` (dùng `--python`); `requirements` không khớp nhóm `docs` sau khi đổi commit.

## Giới hạn

- Chỉ pin dependency trực tiếp của nhóm docs; dependency bắc cầu do pip giải và được ghi lại trong `environment.json` (không cài từ `uv.lock`).
- Sphinx cần mạng cho intersphinx (`docs.python.org`); mất mạng chỉ sinh warning, không làm build hỏng. Số warning và nội dung nằm trong manifest (`build_warnings`, `build_warning_lines`).
- Kiểm link chỉ áp cho các trang `api_reference` và `pages`, không cho toàn bộ site.
