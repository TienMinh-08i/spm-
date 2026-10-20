# SpecMind dataset evaluator v2

Hướng dẫn luồng chính, debug không gọi LLM, dataset đã sửa, cache/resume và paired report mới: [DEBUGGING.md](DEBUGGING.md). CLI mặc định dùng policy `behavioral`; chọn `legacy` rõ ràng khi cần đối chiếu. Thay đổi engine yêu cầu output mới, không resume trực tiếp run cũ.

Bản độc lập cho luồng đánh giá assertion trên repository Python. Không import code từ `paper_eval`, không chỉnh `sys.path`, không chứa dataset/cache/run cũ. Thư mục `paper_eval` hiện tại được giữ nguyên để đối chiếu.

## Phạm vi

- Dataset JSONL gồm source hàm và linked pytest tests; chạy toàn bộ linked tests, không cap mutant.
- SpecMind `multiturn` hoặc `retry`, prompt gốc được tách thành file; API tương thích OpenAI.
- Correctness, mutation feedback, chọn submitted candidate tốt nhất; cache và resume.
- Mutmut 2.5.1 isolated-function export hoặc reuse export đã có; không tự sinh lại khi reuse bị thiếu/sai.
- Bằng chứng từng pytest case, từng lần kiểm tra assertion, từng mutant và từng attempt.
- CLI, package cài đặt độc lập, Dockerfile, CSV/JSON/Markdown report.

PR evaluation, EvalPlus và nl2postcond vẫn thuộc bản cũ. Call graph/caller/callee là extension tùy chọn; `context_mode=none` giữ nguyên prompt baseline. Async/generator target và closure được báo unsupported thay vì đánh giá sai thời điểm.

## Cài đặt và chạy

Từ `C:\specmind`:

```powershell
python -m venv .venv-eval-v2
.\.venv-eval-v2\Scripts\python.exe -m pip install -r paper_eval_v2/requirements.lock
.\.venv-eval-v2\Scripts\python.exe -m pip install --no-deps -e ./paper_eval_v2
.\.venv-eval-v2\Scripts\python.exe -m pytest paper_eval_v2/tests -q
.\.venv-eval-v2\Scripts\python.exe -m paper_eval_v2 run --config paper_eval_v2/configs/marshmallow.local.json --output pe_runs/marshmallow_v2
```

API key lấy từ `OPENAI_API_KEY` hoặc `OPENROUTER_API_KEY`; endpoint từ `OPENAI_BASE_URL`. Không ghi key vào config. Các file config mẫu trỏ đến dataset/checkout/export **hiện có trên máy**, không phải dependency code của bản cũ. Khi chuyển máy, chỉnh các đường dẫn đó. Model là ID thực mà endpoint hỗ trợ, không tự suy đoán alias.

Docker, build context chỉ có source mới:

```powershell
docker build -t specmind-eval-v2 ./paper_eval_v2
docker run --rm -e OPENAI_API_KEY -e OPENAI_BASE_URL -e OPENROUTER_API_KEY --mount "type=bind,source=C:\specmind,target=/workspace" specmind-eval-v2 run --config /workspace/paper_eval_v2/configs/marshmallow.docker.json --output /workspace/pe_runs/marshmallow_v2_docker
```

Mutmut 2.5.1 native export yêu cầu Linux/Docker; reuse export và evaluator có thể chạy Windows. Docker image cài dependency cần cho Marshmallow. Dataset có extension native như pandas/numpy cần môi trường target được build tương ứng; chưa được xác nhận qua pilot v2.

## Context pilot Marshmallow 4.3.0

Config `configs/marshmallow.context-pilot.json` khóa commit 4.3.0, 10 hàm, graph đã
xác minh và corpus mutmut ngày 19/09. Nó không dùng corpus AST trong Excel, không
sinh mutant LLM và không gọi code từ `paper_eval`. Đường dẫn dataset/export cũ là
đầu vào dữ liệu; mã thực thi thuộc v2.

Chạy kiểm tra offline trước (assertion cố định, không gọi API):

```powershell
python -m paper_eval_v2 run --config paper_eval_v2/configs/marshmallow.context-smoke.json --output pe_runs/context_smoke
```

Sau khi sẵn sàng gọi API, chạy A/B/C/D hoặc từng mode bằng `run`:

```powershell
python -m paper_eval_v2 experiment --config paper_eval_v2/configs/marshmallow.context-pilot.json --output pe_runs/context_abcd_430
python -m paper_eval_v2 experiment --config paper_eval_v2/configs/marshmallow.context-pilot.json --output pe_runs/context_abcd_430 --resume
python -m paper_eval_v2 compare --output pe_runs/context_abcd_430
```

`experiment` tạo A=none, B=callee, C=caller, D=callgraph từ một config chung.
B/C/D được dựng từ graph riêng theo budget, không cắt bớt pack D. Mỗi hàm có
`context.json`; prompt thực tế nằm trong `generation.json`. Summary chưa bật.
Không có hàng xóm ở mode được chọn được ghi `no_neighbors`; thiếu target, sai
hash/commit hoặc có hàng xóm nhưng không lấy được snippet sẽ báo lỗi.

`comparison.csv/json` chứa điểm từng arm và điểm `common_*` trên giao của tập
mutant có outcome assertion xác định (hoặc paired cho attribution) của cả bốn arm. Không có giao thì điểm để trống. Khác corpus,
source/tests/engine/environment hoặc graph giữa các arm bị từ chối. Token usage
không được API cung cấp để trống, không ghi thành 0.

Config pilot bật `generate_without_mutants`: vẫn sinh và đo correctness cho
`validates` dù export có 0 mutant; mutation unavailable và không thay corpus ngầm.
Config thông thường vẫn giữ hành vi bỏ qua hàm không có mutant.
Bộ lọc True/False được đặt ở bước offline `prepare-dataset`, xem
[DATASET_PREPARATION.md](DATASET_PREPARATION.md). Runner hiện còn giao thức
preflight cũ; việc chuyển runner sang đọc dataset/mutant chuẩn bị sẵn là bước tiếp theo.

Các trường mới: `context_mode`, `context_budget` (8000), `context_depth` (1),
`call_graph_dir`, `expected_repo_commit`, `generate_without_mutants`, `negative_control`.
Graph và context đã chọn tham gia fingerprint; sửa chúng phải tạo run mới.

Feedback, threshold và quy tắc chọn submission giữ nguyên. Corpus AST 61 mutant
cũ có 10 mutant chỉ đổi docstring, không phù hợp guard v2; không nạp nguyên trạng
để so điểm với Excel. Context không bảo đảm cải thiện assertion; cần chạy thí nghiệm.

## Resume

Chạy lại cùng lệnh với `--resume`. Run đang có dữ liệu không được ghi đè ngầm.

```powershell
python -m paper_eval_v2 run --config paper_eval_v2/configs/marshmallow.local.json --output pe_runs/marshmallow_v2 --resume
python -m paper_eval_v2 report --output pe_runs/marshmallow_v2
```

Fingerprint gồm config, source/config của checkout, dataset/tests, source evaluator, dependency environment và nội dung export được reuse. Thay đổi các yếu tố này cần run mới. Output của LLM được lưu trước evaluation; resume không gọi lại LLM nếu đã nhận và lưu response đó. Các arm pytest hoàn tất được reuse; arm lỗi/timeout được chạy lại. Không coi cache là bằng chứng suite không flaky: các thí nghiệm về tính ổn định nên dùng run mới.

Một process được ghi một run tại một thời điểm. Lỗi của một hàm được lưu rồi chuyển sang hàm tiếp theo; CLI trả mã 2 nếu còn hàm chưa hoàn tất. `Ctrl+C` dừng process; checkpoint đã ghi vẫn còn. Timeout riêng cho API/pytest/mutmut; giá trị `0` vô hiệu hóa timeout tương ứng, không phải đảm bảo thời gian toàn hàm.

## Cấu hình

| Trường | Ý nghĩa |
|---|---|
| `problems`, `tests`, `repo_root` | Bắt buộc; đường dẫn tương đối tính từ file config |
| `reuse_mutmut_rows` | File export index cũ; bỏ trường này để export mới |
| `path_mappings` | Chuyển prefix đường dẫn export, ví dụ `/workspace` → `C:/specmind` |
| `tasks` | Danh sách qualified function IDs; bỏ trường để chạy toàn dataset |
| `max_turns` | Tổng số lần gọi LLM tối đa, mặc định 12 |
| `mode` | `multiturn` hoặc `retry` |
| `assertion_policy` | CLI mặc định `behavioral`; `legacy` giữ cách chấp nhận cũ. Xem DEBUGGING.md về giới hạn policy |
| `threshold` | Ngưỡng lower bound trong [0,1], mặc định 0.9 |
| `batch_size` | Số nodeid requested/batch, không giới hạn tổng test |
| `pytest_timeout`, `mutmut_timeout`, `api_timeout` | Giây; 0 = không giới hạn |
| `temperature`, `max_tokens`, `api_retries` | Tham số API được ghi vào manifest |
| `mock_assertion` | Assertion cố định để kiểm tra evaluator, không gọi LLM |

Không chấp nhận key lạ trong config. Dùng config mẫu và đặt `tasks` chỉ một hàm cùng `mock_assertion` để smoke test trước khi trả phí API.

## Protocol và công thức

1. Chạy original + `assert True`; trong chính process pytest, kiểm tra file import và AST của function. Khác docstring được ghi nhận qua source artifact, executable AST phải khớp.
2. Baseline phải pass và có ít nhất một case thực thi assertion. Requested/collected/executed/covered/check-count được báo riêng. Nodeid không hợp lệ không bị silently bỏ qua.
3. Xác nhận bộ mutant: original source khớp dataset, đủ JSON files theo manifest, IDs không trùng, source parse được và khác executable AST. Bộ export bằng 0 được skip, không diễn giải thành không thể có lỗi.
4. Candidate chạy correctness trên toàn linked tests. Assertion violation hoặc postcondition runtime error trên original là fail; không có coverage hoặc lỗi hạ tầng là unavailable.
5. Luồng sinh/chấm assertion chỉ chạy candidate trên từng mutant. Không có tùy chọn chạy neutral mutant trong luồng này. Baseline trên code gốc ở bước 1 vẫn cần để xác minh hook và coverage. Bằng chứng lưu trong `arms/`.
6. `suite_killed=true` nếu có call-phase test failure trong neutral arm, kể cả target exception làm test fail. Setup/collection failure không phải suite kill. `assertion_killed=true` khi event chứng minh assert trong candidate fail, kể cả nếu test bắt exception đó. Postcondition runtime error không phải assertion kill.
7. Kết luận không kill cần đủ bằng chứng hoàn tất. Mutant chưa đi đến assertion, runtime error hoặc thiếu bằng chứng là unknown, không phải survived. Một assertion violation rõ ràng vẫn là bằng chứng kill khi các test khác lỗi.

Assertion score dùng các mutant có outcome assertion xác định, độc lập với neutral. Báo riêng `assertion_evaluated` và `assertion_unresolved`. Trong kết quả sinh assertion, `suite_killed=null`, `neutral_status=not_run`; điểm test-suite/combined/new-assertion là null, không suy diễn mutant sống sót qua test gốc.

Chạy đánh giá test gốc riêng khi cần, dùng một run đơn (ví dụ nhánh A):

```powershell
python -B -m paper_eval_v2 evaluate-suite --run pe_runs/my_experiment/A --output pe_runs/my_suite_eval
```

Lệnh này chỉ chạy neutral, không gọi LLM hoặc chạy candidate. Nó đọc `input.json` và `mutants.json` đã lưu, dùng linked tests và baseline coverage của từng hàm, lưu artifact ở output riêng và không sửa kết quả assertion. Chạy lại cùng lệnh sẽ dùng cache; source/tests/mutants/engine/environment thay đổi sẽ bị chặn bằng fingerprint. `results.json` ghi ID và hash source từng mutant để đối chiếu sau. Không tự ghép vào báo cáo A/B/C/D hoặc chạy lại theo từng context arm. Đây là test-suite score trên linked tests của dataset, không phải toàn bộ test trong repository.

Ghép hai kết quả mà không chạy lại test: `python -m paper_eval_v2 join-suite --run pe_runs/my_experiment/A --suite pe_runs/my_suite_eval --output pe_runs/my_paired`. Lệnh kiểm tra identity và hash mutant; không ghi đè run nguồn. Các metric attribution cần tập paired có cả hai outcome xác định:

```
T = mutant IDs bị neutral test suite kill
A = mutant IDs có generated assertion violation
test score      = |T| / |paired|
assertion score = số assertion kill / số outcome assertion xác định
combined score  = |T union A| / |paired|
new kills       = |A minus T|
```

Unknowns nằm ngoài paired score và luôn được báo. Vì vậy paired score vẫn là kết quả có điều kiện, không thể so trực tiếp hai run có paired set khác nhau. Báo cả exported/paired/unresolved. Micro score cộng tử/mẫu số; macro score trung bình score từng hàm có mẫu số > 0.

So sánh A/B/C/D dùng `common_assertion_evaluated` cho assertion và `common_paired` cho attribution. Hai mẫu số này không nhất thiết bằng nhau. Engine fingerprint thay đổi; dùng thư mục output mới cho lượt chạy sau thay đổi, không resume kết quả engine cũ.

**Feedback dùng mẫu số export cố định**: lower bound = số assertion kill chứng minh được / exported; upper bound = (kill chứng minh + assertion outcomes chưa biết) / exported. Báo rõ unknown, không gọi chúng là survivor. Best candidate được xếp theo lower bound; không có lợi thế do candidate làm mất coverage. Chỉ gửi diff của mutant đã xác nhận assertion không kill.

Correctness cuối = số submitted assertions pass / số submitted assertions có kết quả correctness pass hoặc fail. Unavailable/no submission/no mutant báo riêng. Candidate bị completeness unavailable không làm mất correctness đã pass.

## Quan hệ với paper và bản cũ

Prompt baseline và parser được trích từ bản SpecMind gốc đã vendor trong project; vị trí nguồn ghi trong `provenance.json`. Vòng lặp giữ explore/submit, correctness trước completeness, best submitted candidate, ngưỡng và giới hạn turn.

Đây là **repository-adapted SpecMind**, không tuyên bố tái lập nguyên xi toàn bộ thí nghiệm paper. V2 cố ý thay đổi:

- Feedback lower/upper bound trên bộ export cố định để không thưởng việc mất mẫu số.
- Không tự nâng explore ở turn cuối thành submit. Không có submission thì báo no submission.
- Không ghi đè correctness khi mutation lỗi; không mất best trước đó khi lượt sau unavailable.
- Attribution theo event và tập mutant; không dùng per-test incremental làm suite-level new kills.
- Runtime source/path guard và recursive mutant self-calls được hook.
- Input của postcondition được bind theo chữ ký original, để mutant đổi default không đổi luôn nghĩa của input dùng để kiểm tra.

Những thay đổi này có thể làm kết quả khác bản cũ. Phải báo version/protocol khi so sánh; không dùng thay đổi score làm bằng chứng tự động rằng prompt tốt hơn.

## Đọc source

```
__main__.py → runner.py
               ├─ dataset.py
               ├─ evaluation.py → execution.py → pytest_plugin.py
               ├─ mutants.py → mutmut.py
               └─ specmind.py → feedback.py / parsing.py / prompts/
metrics.py → report.py
storage.py: fingerprint, atomic writes, process lock
```

Đọc `runner.py` trước, sau đó `evaluation.py`, `metrics.py`, `specmind.py`. Test tương ứng nằm trong `tests/`.

## Artifacts

```
run/
  manifest.json          # config, environment, fingerprint
  summary.json           # aggregate metrics
  results.json           # final per-function records
  functions.csv          # assertion cuối và metrics từng hàm
  attempts.csv           # correctness/completeness qua các turn
  mutants.csv            # attribution cuối theo mutant
  report.md
  functions/<id hash>/
    input.json           # function source + linked tests
    preflight.json       # test/hook counts
    mutants.json         # verified exported mutant source
    generation.json      # attempts, feedback, best, history
    pending_response.json
    result.json
    arms/<content hash>.json # log, events, per-case evidence, duration
```

Giới hạn thực nghiệm: pass test không chứng minh correctness với mọi input; mutation kill không chứng minh semantic strength tuyệt đối; prompt baseline chưa có caller/callee context; không tự phát hiện flaky tests. Xem `VALIDATION.md` để biết chính xác phần đã/chưa kiểm thử.
