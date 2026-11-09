"""Caller/target provenance oracles; no dataset or LLM execution."""
import json

import pytest

from specmind_eval.common.storage import read
from specmind_prep.project import validate_project
from specmind_prep.slicing import trace_function, query_slice


def fixture(tmp_path, source, test):
    repo = tmp_path / "repo"
    (repo / "src/demo").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "src/demo/__init__.py").write_text(source, encoding="utf-8")
    (repo / "tests/test_calls.py").write_text(test, encoding="utf-8")
    return repo, validate_project({"name": "demo", "packages": ["demo"]}, repo), tmp_path / "trace"


def run(repo, project, out):
    return trace_function(
        repo, project, "tests/test_calls.py::test_f", "demo.target", out, caller="demo.caller"
    )


def sliced(out, repo, function, variable="$return", *, occurrence=0, when="after"):
    call = [c for c in read(out / "calls.json") if c["function"] == function][occurrence]
    return query_slice(out, repo, call["return_event"], variable, when=when)


def test_actual_formal_links_drop_unused_equal_valued_argument(tmp_path):
    repo, project, out = fixture(
        tmp_path,
        """def target(x,unused):
    y=x*2
    return y

def caller():
    a=4
    b=4
    result=target(a,b)
    return result
""",
        "from demo import caller\ndef test_f():\n    assert caller()==8\n",
    )
    assert run(repo, project, out)["status"] == "complete_supported"
    result = sliced(out, repo, "demo.caller")
    assert {s["line"] for s in result["source_statements"]} == {1, 2, 3, 6, 8, 9}
    assert not any("b" in n["writes"] or "unused" in n["writes"] for n in result["nodes"])
    assert {n["function"] for n in result["nodes"]} == {"demo.caller", "demo.target"}
    call = next(c for c in read(out / "calls.json") if c["function"] == "demo.target")
    assert call["caller"]["function"] == "demo.caller" and call["caller"]["line"] == 8
    assert call["parent_frame"] == 1


def test_keyword_binding_uses_parameter_name_not_equality(tmp_path):
    repo, project, out = fixture(
        tmp_path,
        """def target(x,/,*,y):
    return y

def caller():
    a=7
    b=7
    return target(a,y=b)
""",
        "from demo import caller\ndef test_f():\n    assert caller()==7\n",
    )
    assert run(repo, project, out)["status"] == "complete_supported"
    result = sliced(out, repo, "demo.target")
    assert {s["line"] for s in result["source_statements"]} == {1, 2, 6, 7}
    assert not any("a" in n["writes"] or "x" in n["writes"] for n in result["nodes"])


def test_repeated_same_line_calls_keep_separate_frames_and_result_producers(tmp_path):
    repo, project, out = fixture(
        tmp_path,
        """def target(x):
    return x+1

def caller():
    a=2
    b=9
    result=target(a)+target(b)
    return result
""",
        "from demo import caller\ndef test_f():\n    assert caller()==13\n",
    )
    assert run(repo, project, out)["status"] == "complete_supported"
    first = sliced(out, repo, "demo.target", occurrence=0)
    second = sliced(out, repo, "demo.target", occurrence=1)
    assert not any("b" in n["writes"] for n in first["nodes"])
    assert not any("a" in n["writes"] for n in second["nodes"])
    assert {n["frame"] for n in first["nodes"]} == {1, 2}
    assert {n["frame"] for n in second["nodes"]} == {1, 3}
    final = sliced(out, repo, "demo.caller")
    assert len([n for n in final["nodes"] if n["kind"] == "call_result"]) == 2


def test_constant_target_result_still_depends_on_caller_guard(tmp_path):
    repo, project, out = fixture(
        tmp_path,
        """def target(unused):
    return 5

def caller(flag):
    if flag:
        return 0
    a=99
    return target(a)
""",
        "from demo import caller\ndef test_f():\n    assert caller(False)==5\n",
    )
    assert run(repo, project, out)["status"] == "complete_supported"
    result = sliced(out, repo, "demo.target")
    assert {s["line"] for s in result["source_statements"]} == {2, 4, 5, 8}
    assert not any("a" in n["writes"] for n in result["nodes"])


@pytest.mark.parametrize(
    "case,expected",
    [
        ("return target()", "unsupported"),
        ("return target(target(1))", "unsupported"),
        ("return 0", "criterion_not_reached"),
    ],
)
def test_missing_argument_nested_call_or_unreached_target_is_not_complete(tmp_path, case, expected):
    repo, project, out = fixture(
        tmp_path,
        "def target(x=2):\n    return x\ndef caller():\n    " + case + "\n",
        "from demo import caller\ndef test_f():\n    caller()\n",
    )
    assert run(repo, project, out)["status"] == expected


def test_direct_target_call_outside_selected_caller_is_explicitly_unsupported(tmp_path):
    repo, project, out = fixture(
        tmp_path,
        "def target(x):\n    return x\ndef caller():\n    return target(2)\n",
        "from demo import caller,target\ndef test_f():\n    target(1)\n    caller()\n",
    )
    assert run(repo, project, out)["status"] == "unsupported"


def test_cross_module_import_alias_and_source_maps(tmp_path):
    repo, project, out = fixture(
        tmp_path,
        "def target(x):\n    return x+1\nfrom .other import caller\n",
        "from demo import caller\ndef test_f():\n    assert caller()==4\n",
    )
    (repo / "src/demo/other.py").write_text(
        "from . import target as alias\ndef caller():\n    a=3\n    return alias(a)\n", encoding="utf-8"
    )
    result = trace_function(
        repo, project, "tests/test_calls.py::test_f", "demo.target", out, caller="demo.other.caller"
    )
    assert result["status"] == "complete_supported"
    result = sliced(out, repo, "demo.target")
    assert {s["file"] for s in result["source_statements"]} == {"src/demo/__init__.py", "src/demo/other.py"}


def test_changed_callable_binding_is_not_silently_linked_to_old_target(tmp_path):
    repo, project, out = fixture(
        tmp_path,
        "def target(x):\n    return x\ndef caller():\n    return target(1)\n",
        "import demo\ndef test_f():\n    demo.target=lambda x: x\n    assert demo.caller()==1\n",
    )
    assert run(repo, project, out)["status"] == "unsupported"


def test_cli_selected_caller_and_target_input_query(tmp_path, capsys):
    from specmind_prep.cli import main

    repo, project, out = fixture(
        tmp_path,
        "def target(x):\n    return x+1\ndef caller():\n    seed=3\n    return target(seed)\n",
        "from demo import caller\ndef test_f():\n    assert caller()==4\n",
    )
    config = tmp_path / "project.json"
    config.write_text(json.dumps(project), encoding="utf-8")
    assert (
        main(
            [
                "slice-trace",
                "--repo",
                str(repo),
                "--project",
                str(config),
                "--test",
                "tests/test_calls.py::test_f",
                "--target",
                "demo.target",
                "--caller",
                "demo.caller",
                "--output",
                str(out),
            ]
        )
        == 0
    )
    call = next(c for c in read(out / "calls.json") if c["function"] == "demo.target")
    result_path = tmp_path / "target-input.json"
    assert (
        main(
            [
                "slice-call",
                "--repo",
                str(repo),
                "--trace",
                str(out),
                "--event",
                str(call["event"]),
                "--variable",
                "x",
                "--when",
                "before",
                "--output",
                str(result_path),
            ]
        )
        == 0
    )
    assert {s["line"] for s in read(result_path)["source_statements"]} == {1, 4, 5}
    capsys.readouterr()


@pytest.mark.parametrize(
    "expression", ["flag and target(x)", "target(x) if flag else 0", "0 < int(flag) < target(x)"]
)
def test_conditionally_executed_call_inside_expression_is_explicitly_unsupported(tmp_path, expression):
    repo, project, out = fixture(
        tmp_path,
        "def target(x):\n    return x\ndef caller(flag,x):\n    return " + expression + "\n",
        "from demo import caller\ndef test_f():\n    assert caller(True,3)\n",
    )
    assert run(repo, project, out)["status"] == "unsupported"
    assert not read(out / "ddg.json")
    assert not (out / "baseline.json").exists()
    assert "Conditional traced calls" in " ".join(read(out / "summary.json")["issues"])


@pytest.mark.parametrize(
    "expression,needs_flag", [("target(x if flag else x+1)", True), ("target(x) and flag", False)]
)
def test_argument_condition_and_unconditional_first_operand_remain_supported(
    tmp_path, expression, needs_flag
):
    repo, project, out = fixture(
        tmp_path,
        "def target(x):\n    return x\ndef caller(flag,x):\n    return " + expression + "\n",
        "from demo import caller\ndef test_f():\n    assert caller(True,3)\n",
    )
    assert run(repo, project, out)["status"] == "complete_supported"
    result = sliced(out, repo, "demo.target")
    flags = [n for n in result["nodes"] if n["kind"] == "parameter" and "flag" in n["writes"]]
    assert bool(flags) is needs_flag
