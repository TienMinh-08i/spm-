"""Keep runtime helpers independent and constrain compatibility bridges explicitly."""
import ast
import importlib.util
import sys
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]/'src'
BRIDGES={'specmind_eval.cli','specmind_eval.legacy_mutants'}
ALLOWED={'common':{'common'},'inputs':{'common','inputs'},'context':{'common','inputs','context'},
         'evaluate':{'common','inputs','evaluate'},'generate':{'common','inputs','context','evaluate','generate'}}

def violation(module,dependency):
 if module.startswith('specmind_eval.') and dependency.startswith('specmind_prep'):
  return module not in BRIDGES
 if '.injected.' in module:
  owner=module.rsplit('.',1)[0]+'.'
  return dependency.split('.')[0] not in sys.stdlib_module_names|{'pytest'} and not dependency.startswith(owner)
 if module.startswith('specmind_eval.') and dependency.startswith('specmind_eval.'):
  group=module.split('.')[1]
  return group in ALLOWED and dependency.split('.')[1] not in ALLOWED[group]
 if module.startswith('specmind_prep.') and dependency.startswith('specmind_eval.'):
  return dependency.split('.')[1] not in {'common','inputs','evaluate'}
 return False

def dependencies(module,tree,is_package):
 package=module if is_package else module.rsplit('.',1)[0]
 for node in ast.walk(tree):
  if isinstance(node,ast.Import):
   yield from (a.name for a in node.names)
  elif isinstance(node,ast.ImportFrom):
   yield importlib.util.resolve_name('.'*node.level+(node.module or ''),package) if node.level else (node.module or '')

def test_repository_dependency_boundaries():
 errors=[]
 for path in ROOT.rglob('*.py'):
  module='.'.join(path.relative_to(ROOT).with_suffix('').parts)
  module=module.removesuffix('.__init__')
  for dependency in dependencies(module,ast.parse(path.read_text(encoding='utf8')),path.name=='__init__.py'):
   if violation(module,dependency):errors.append((module,dependency))
 assert not errors,errors

@pytest.mark.parametrize('module,dependency',[
 ('specmind_eval.evaluate.evaluator','specmind_prep.project'),
 ('specmind_eval.common.storage','specmind_eval.evaluate.evaluator'),
 ('specmind_eval.evaluate.injected.plugin','openai'),
 ('specmind_prep.injected.plugin','specmind_eval.run'),
 ('specmind_prep.gather.gather','specmind_eval.generate.loop'),
])
def test_forbidden_edges_are_detected(module,dependency):
 assert violation(module,dependency)
