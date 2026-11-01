"""Read project facts without importing target code or starting pytest."""
import json
import re
from pathlib import Path

KEYS = {"name", "commit", "source_root", "packages", "tests", "python_path", "docs_dir", "dependencies"}

def relative_path(repo, value, key):
    if not isinstance(value, str) or not value:
        raise ValueError(key + " must be a nonempty relative path")
    path = Path(value)
    resolved = (repo / path).resolve()
    if path.is_absolute() or not resolved.is_relative_to(repo):
        raise ValueError(key + " must stay inside repo")
    return resolved

def validate_project(value, repo, *, documented_only=False):
    repo = Path(repo).resolve()
    if not isinstance(value, dict) or set(value) - KEYS:
        raise ValueError("project has unknown keys or is not an object")
    if not isinstance(value.get("name"), str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]*", value["name"]):
        raise ValueError("project.name is required and must be a simple identifier")
    result = dict(value)
    result.setdefault("source_root", "src")
    result.setdefault("tests", ["tests"])
    result.setdefault("python_path", list(dict.fromkeys([".", result["source_root"]])))
    result.setdefault("dependencies", [])
    for key in ("packages", "tests", "python_path", "dependencies"):
        if not isinstance(result.get(key), list) or not all(isinstance(x,str) and x for x in result[key]):
            raise ValueError("project." + key + " must be a list of strings")
    if not result["packages"]:
        raise ValueError("project.packages is required and cannot be empty")
    source = relative_path(repo,result["source_root"],"source_root")
    for package in result["packages"]:
        if not all(part.isidentifier() for part in package.split(".")):
            raise ValueError("project.packages contains an invalid package name")
        if not (source / package.replace(".","/")).is_dir():
            raise ValueError("project.packages directory missing: " + package)
    for key in ("tests","python_path"):
        for entry in result[key]:
            if not relative_path(repo,entry,"project."+key).exists():
                raise ValueError("project."+key+" path missing: " + entry)
    if documented_only and not result.get("docs_dir"):
        raise ValueError("project.docs_dir is required for documented_only")
    if result.get("docs_dir") and not relative_path(repo,result["docs_dir"],"project.docs_dir").is_dir():
        raise ValueError("project.docs_dir directory missing")
    if result.get("commit") is not None and not isinstance(result["commit"],str):
        raise ValueError("project.commit must be a string")
    return result

def load_project(path, repo, *, documented_only=False):
    with Path(path).open(encoding="utf8") as stream:
        return validate_project(json.load(stream),repo,documented_only=documented_only)

def infer_project(repo):
    """Compatibility for existing Python callers; new CLI configs specify a project."""
    repo=Path(repo).resolve()
    source=repo/"src" if (repo/"src").is_dir() else repo
    packages=sorted(p.name for p in source.iterdir() if p.is_dir() and (p/"__init__.py").is_file() and p.name not in {"tests","test"})
    if not packages:
        raise ValueError("project.packages must be configured")
    value={"name":packages[0],"source_root":source.relative_to(repo).as_posix(),"packages":packages}
    if (repo/"docs").is_dir():value["docs_dir"]="docs"
    return validate_project(value,repo)
