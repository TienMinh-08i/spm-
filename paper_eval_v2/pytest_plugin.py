from __future__ import annotations

import base64
import functools
import importlib
import inspect
import json
import os
import traceback
import ast
import sys
import pytest
import textwrap
from pathlib import Path


MARKER = "SPECMIND_DATASET_POSTCONDITION_FAILURE"
CURRENT_NODEID = None
CURRENT_PHASE = None
SESSION = None
MUTATION_CASES = set()


def _event(**data):
    if data.get('kind') == 'mutation_call' and CURRENT_NODEID and CURRENT_PHASE == 'call':
        MUTATION_CASES.add(CURRENT_NODEID)
    path = os.environ.get("SPECMIND_DATASET_EVENTS")
    if path:
        with open(path, "a", encoding="utf-8") as stream:
            stream.write(json.dumps(dict(nodeid=CURRENT_NODEID, **data)) + "\n")


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    global CURRENT_NODEID, CURRENT_PHASE
    CURRENT_NODEID = item.nodeid
    CURRENT_PHASE = 'setup'


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_call(item):
    global CURRENT_PHASE
    CURRENT_PHASE = 'call'


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_teardown(item):
    global CURRENT_PHASE
    CURRENT_PHASE = 'teardown'


def pytest_runtest_logfinish(nodeid, location):
    global CURRENT_NODEID, CURRENT_PHASE
    CURRENT_NODEID, CURRENT_PHASE = None, None


def pytest_runtest_logreport(report):
    _event(kind="report", report_nodeid=report.nodeid, when=report.when, outcome=report.outcome)
    if (os.environ.get('SPECMIND_SUITE_STOP_ON_KILL') == '1'
            and report.when == 'call' and report.failed
            and report.nodeid in MUTATION_CASES and SESSION is not None):
        SESSION.shouldfail = 'Confirmed suite kill after mutant execution'


def pytest_collectreport(report):
    if report.failed:
        _event(kind='collection_error', collector_nodeid=report.nodeid,
               message=str(report.longrepr)[-4000:])


def pytest_collection_finish(session):
    _event(kind="collection", nodeids=[item.nodeid for item in session.items])


def _source_ast(source):
    tree = ast.parse(textwrap.dedent(source).strip())
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            while (
                node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            ):
                node.body.pop(0)
    return ast.dump(tree)


def _descriptor_name(node):
    names = [d.id for d in node.decorator_list
             if isinstance(d, ast.Name) and d.id in {"classmethod", "staticmethod", "property"}]
    if len(names) > 1:
        raise RuntimeError("Ambiguous method descriptor in source")
    return names[0] if names else None


def _verify_runtime(problem, original, descriptor_type=None):
    source = inspect.getsource(original)
    actual = Path(inspect.getfile(original)).resolve()
    expected_path = problem.get("_expected_source_path")
    expected = problem.get("_expected_source")
    _event(kind="runtime_source", path=str(actual), source=source)
    if expected_path and actual != Path(expected_path).resolve():
        raise RuntimeError(f"Runtime import path mismatch: {actual} != {expected_path}")
    # Read the lexical definition independently of inspect.getsource(): the
    # dataset extractor starts at `def`, whereas inspect includes decorators.
    checkout = ast.parse(actual.read_text(encoding="utf-8-sig"))
    node = checkout
    for part in original.__qualname__.split("."):
        matches = [n for n in node.body if isinstance(
            n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == part]
        if len(matches) > 1 and part == original.__qualname__.split('.')[-1]:
            # @overload declarations share the implementation's lexical name.
            matches = [n for n in matches if _source_ast(ast.unparse(n)) == _source_ast(source)]
        if len(matches) != 1:
            raise RuntimeError("Cannot uniquely resolve target in checkout source")
        node = matches[0]
    checkout_source = ast.unparse(node)
    if _source_ast(checkout_source) != _source_ast(source):
        raise RuntimeError("Runtime source AST mismatch with checkout")
    declared = _descriptor_name(node)
    if node.name == '__new__' and declared is None:
        declared = 'staticmethod'  # Python implicitly wraps class __new__.
    runtime_descriptor = descriptor_type.__name__ if descriptor_type else None
    if declared != runtime_descriptor:
        raise RuntimeError(f"Runtime descriptor mismatch: {runtime_descriptor} != {declared}")
    if expected:
        expected_tree = ast.parse(textwrap.dedent(expected).strip())
        if len(expected_tree.body) != 1 or not isinstance(
            expected_tree.body[0], (ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            raise RuntimeError("Expected source must contain exactly one function")
        expected_node = expected_tree.body[0]
        expected_descriptor = _descriptor_name(expected_node)
        if expected_descriptor and expected_descriptor != declared:
            raise RuntimeError("Dataset descriptor mismatch with checkout")
        if declared and not expected_descriptor:
            # Only legacy omission of a verified built-in descriptor is allowed.
            # Preserve every other decorator and its order.
            node.decorator_list = [d for d in node.decorator_list if not (
                isinstance(d, ast.Name) and d.id == declared)]
        if _source_ast(expected) != _source_ast(ast.unparse(node)):
            raise RuntimeError("Runtime source AST mismatch with dataset")
    _event(kind="source_verified", descriptor=runtime_descriptor)
    if not problem.get('_mutation_only') and (inspect.iscoroutinefunction(original) or inspect.isgeneratorfunction(original)):
        raise RuntimeError("Async/generator target needs a dedicated hook; unsupported in v2")
    if original.__closure__ and (not problem.get('_mutation_only') or set(original.__code__.co_freevars)-{'__class__'}):
        raise RuntimeError("Closure target needs cell-preserving mutation; unsupported in v2")


def _loads_env_json(name):
    return json.loads(base64.b64decode(os.environ[name]).decode("utf-8"))


def _loads_env_text(name):
    value = os.environ.get(name)
    if not value:
        return None
    return base64.b64decode(value).decode("utf-8")


def _module_name(problem):
    module = problem.get("module")
    if module:
        if module.endswith(".__init__"):
            module = module[: -len(".__init__")]
        return module
    problem_id = problem.get("problem_id") or problem.get("qualified_name") or ""
    parts = problem_id.split(".")
    if len(parts) < 2:
        raise RuntimeError(f"Cannot infer module from problem_id={problem_id!r}")
    return ".".join(parts[:-1])


def _function_name(problem):
    return (
        problem.get("function_name")
        or problem.get("entry_point")
        or (problem.get("problem_id") or "").split(".")[-1]
    )


def _resolve_owner(problem):
    problem_id = problem.get("problem_id") or problem.get("qualified_name") or ""
    if not problem.get("module") and problem_id:
        parts = problem_id.split(".")
        last_error = None
        for split_at in range(len(parts) - 1, 0, -1):
            module_name = ".".join(parts[:split_at])
            try:
                module = importlib.import_module(module_name)
            except Exception as exc:
                last_error = exc
                continue
            owner = module
            for part in parts[split_at:-1]:
                owner = getattr(owner, part)
            attr = parts[-1]
            if inspect.isclass(owner) and attr.startswith('__') and not attr.endswith('__'):
                attr = '_'+owner.__name__.lstrip('_')+attr
            return module, owner, attr
        raise RuntimeError(f"Cannot resolve problem_id={problem_id!r}: {last_error}")

    module = importlib.import_module(_module_name(problem))
    owner = module
    parent_name = problem.get("parent_name")
    if parent_name:
        for part in str(parent_name).split("."):
            if part:
                owner = getattr(owner, part)
    attr = _function_name(problem)
    if inspect.isclass(owner) and attr.startswith('__') and not attr.endswith('__'):
        attr = '_'+owner.__name__.lstrip('_')+attr
    return module, owner, attr


def _raw_descriptor(owner, attr_name):
    if inspect.isclass(owner):
        return owner.__dict__.get(attr_name)
    return getattr(owner, attr_name)


def _unwrap_descriptor(raw, resolved):
    if isinstance(raw, property):
        return raw.fget, property
    if isinstance(raw, staticmethod):
        return raw.__func__, staticmethod
    if isinstance(raw, classmethod):
        return raw.__func__, classmethod
    return resolved, None


def _compile_mutant(mutant_source, original, module, attr_name):
    from paper_eval_v2.mutation_runtime import compile_mutant
    return compile_mutant(mutant_source, original, module)


def _run_postcondition(target, module, postcondition, args, kwargs, return_value):
    namespace = dict(getattr(target, "__globals__", module.__dict__))
    try:
        signature = inspect.signature(target)
        bound = signature.bind_partial(*args, **kwargs)
        bound.apply_defaults()
        namespace.update(bound.arguments)
    except Exception:
        namespace["args"] = args
        namespace["kwargs"] = kwargs
    namespace["return_value"] = return_value
    _event(kind="post_check")
    try:
        exec(compile(postcondition, "<dataset-postcondition>", "exec"), namespace, namespace)
    except Exception as exc:
        tb = exc.__traceback__
        while tb.tb_next:
            tb = tb.tb_next
        assertion_lines = {
            line
            for n in ast.walk(ast.parse(postcondition))
            if isinstance(n, ast.Assert)
            for line in range(n.lineno, n.end_lineno + 1)
        }
        semantic = (
            isinstance(exc, AssertionError)
            and tb.tb_frame.f_code.co_filename == "<dataset-postcondition>"
            and tb.tb_lineno in assertion_lines
        )
        _event(kind="assertion_violation" if semantic else "post_runtime_error", error=type(exc).__name__)
        detail = f"{type(exc).__name__}: {exc}"
        raise AssertionError(f"{MARKER}: {detail}") from exc


def pytest_sessionstart(session):
    global SESSION
    SESSION = session
    problem = _loads_env_json("SPECMIND_DATASET_PROBLEM_JSON_B64")
    postcondition = _loads_env_text("SPECMIND_DATASET_POSTCONDITION_B64")
    mutant_source = _loads_env_text("SPECMIND_DATASET_MUTANT_SOURCE_B64")
    mutation_problem = _loads_env_json('SPECMIND_MUTATION_PROBLEM_B64') if os.environ.get('SPECMIND_MUTATION_PROBLEM_B64') else None
    if not postcondition:
        raise RuntimeError("Missing postcondition")
    compile(postcondition, "<dataset-postcondition>", "exec")

    try:
        distinct = mutation_problem and mutation_problem.get('problem_id') != problem.get('problem_id')
        if distinct:
            from paper_eval_v2.mutation_runtime import install_mutant
            mutation_problem = dict(mutation_problem, _mutation_only=True)
            install_mutant(mutation_problem, mutant_source, resolve=_resolve_owner, raw_descriptor=_raw_descriptor,
                           unwrap=_unwrap_descriptor, verify=_verify_runtime, event=_event,
                           active=lambda: CURRENT_NODEID is not None and CURRENT_PHASE=='call')
        module, owner, attr_name = _resolve_owner(problem)
        resolved = getattr(owner, attr_name)
        raw = _raw_descriptor(owner, attr_name)
        original, descriptor_type = _unwrap_descriptor(raw, resolved)
        _verify_runtime(problem, original, descriptor_type)
        target = _compile_mutant(mutant_source, original, module, attr_name) if mutant_source and not distinct else original

        @functools.wraps(target)
        def wrapped(*args, **kwargs):
            _event(kind="target_call")
            if mutant_source and not distinct and CURRENT_PHASE=='call':
                _event(kind='mutation_call', mutation_target=problem.get('problem_id'))
            try:
                return_value = target(*args, **kwargs)
            except Exception as exc:
                _event(kind="target_runtime_error", error=type(exc).__name__)
                raise
            # Bind the public/original signature: a mutant changing a default
            # must not also redefine the assertion's interpretation of the input.
            if CURRENT_NODEID is not None and CURRENT_PHASE == 'call':
                _run_postcondition(original, module, postcondition, args, kwargs, return_value)
            return return_value

        replacement = raw.getter(wrapped) if descriptor_type is property else descriptor_type(wrapped) if descriptor_type else wrapped
        setattr(owner, attr_name, replacement)
        if mutant_source and target.__globals__.get(attr_name) is target:
            target.__globals__[attr_name] = wrapped
        # Some libraries re-export functions from package __init__ modules, and
        # tests often import those aliases before collection. Patch only aliases
        # that still point to the exact original object so unrelated same-name
        # attributes are not touched.
        for loaded_module in list(sys.modules.values()):
            try:
                for alias, value in list(vars(loaded_module).items()):
                    if value is original:
                        setattr(loaded_module, alias, replacement)
            except Exception:
                pass
    except Exception as exc:
        _event(kind="hook_install_error", error=type(exc).__name__, message=str(exc))
        traceback.print_exc()
        raise RuntimeError(
            f"Failed to install dataset postcondition hook: {type(exc).__name__}: {exc}"
        ) from exc
