"""Docker export worker. Generates only; never runs the benchmark or an LLM."""
import argparse
import ast
from pathlib import Path
from types import SimpleNamespace
import traceback
from time import perf_counter

from specmind_eval.inputs.dataset import DatasetSpec
from specmind_prep.mutants.mutmut_export import export_mutmut_function_mutants
from specmind_eval.common.fingerprint import digest
from specmind_eval.common.storage import read, write


def executable_ast(source):
    tree=ast.parse(source)
    if len(tree.body)!=1 or not isinstance(tree.body[0],(ast.FunctionDef,ast.AsyncFunctionDef)):
        raise ValueError('Expected exactly one function')
    node=tree.body[0]
    node.decorator_list=[]
    node.returns=None
    for arg in [*node.args.posonlyargs,*node.args.args,*node.args.kwonlyargs]:
        arg.annotation=None
    if node.args.vararg:
        node.args.vararg.annotation=None
    if node.args.kwarg:
        node.args.kwarg.annotation=None
    if node.body and isinstance(node.body[0],ast.Expr) and isinstance(node.body[0].value,ast.Constant) and isinstance(node.body[0].value.value,str):
        node.body.pop(0)
    if not node.body:
        node.body=[ast.Pass()]
    return ast.dump(node,include_attributes=False)


def worker(input_file,repo,output,timeout):
    targets=read(input_file)
    for i,target in enumerate(targets,1):
        key=target['problem_id']
        directory=output/'targets'/digest(key)[:16]
        saved=read(directory/'export.json')
        if (saved and saved.get('status')=='ready' and saved.get('target')==target
                and saved.get('exported')==len(saved.get('mutants',[]))
                and saved.get('discovered')==saved.get('exported',0)+len(saved.get('skipped',[]))
                and all(m.get('source_sha256')==digest(m['function_source']) for m in saved.get('mutants',[]))):
            print(f'[{i}/{len(targets)}] reused {key}',flush=True)
            continue
        print(f'[{i}/{len(targets)}] export {key}',flush=True)
        started=perf_counter()
        try:
            # The isolated module exports a plain function; descriptors are
            # preserved at the runtime owner, not recreated at module level.
            node=ast.parse(target['source']).body[0]
            node.decorator_list=[]
            source=ast.unparse(node)+'\n'
            problem=dict(target,source=source,task_id='Target/'+digest(key)[:16])
            project = target.get('_project') or {'name': target['problem_id'].split('.')[0]}
            problem['_python_path'] = project.get('python_path')
            spec=DatasetSpec(project['name'], project['name'], Path('.'),'','')
            args=SimpleNamespace(_run_dir=str(directory),max_mutants=100000,mutmut_run_timeout=timeout or None)
            _,mutants,manifest=export_mutmut_function_mutants(spec=spec,problem=problem,repo_root=repo,args=args)
            if manifest.get('error'):
                raise ValueError(manifest['error'])
            filtered=[]
            skipped=list(manifest.get('skipped_mutants',[]))
            signatures=set()
            original=executable_ast(source)
            for mutant in mutants:
                try:
                    signature=executable_ast(mutant['function_source'])
                    compile('from __future__ import annotations\n'+mutant['function_source'],'<export-mutant>','exec')
                except (SyntaxError,ValueError) as exc:
                    skipped.append({'id':mutant['id'],'reason':'invalid_function_source','error':str(exc)})
                    continue
                if signature==original:
                    skipped.append({'id':mutant['id'],'reason':'no_executable_change'})
                    continue
                if signature in signatures:
                    skipped.append({'id':mutant['id'],'reason':'duplicate_executable_ast'})
                    continue
                signatures.add(signature)
                filtered.append(dict(mutant,source_sha256=digest(mutant['function_source'])))
            discovered=manifest['total_discovered']
            if discovered!=len(filtered)+len(skipped):
                raise ValueError(f'Export accounting mismatch: {discovered} != {len(filtered)}+{len(skipped)}')
            write(directory/'export.json',{'status':'ready','target':target,'original_function_source':source,
                                          'discovered':discovered,'exported':len(filtered),'skipped':skipped,
                                          'mutants':filtered,'manifest':manifest,'seconds':perf_counter()-started})
            print(f'  ready: discovered={discovered}, exported={len(filtered)}, skipped={len(skipped)}',flush=True)
        except Exception as exc:
            write(directory/'export.json',{'status':'pending','target':target,'error':f'{type(exc).__name__}: {exc}',
                                          'traceback':traceback.format_exc(),'seconds':perf_counter()-started})
            print(f'  pending: {type(exc).__name__}: {exc}',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--timeout',type=int,default=1800)
    args=parser.parse_args()
    worker(args.input,args.repo,args.output,args.timeout)
