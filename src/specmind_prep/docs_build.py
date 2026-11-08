"""Step 0: build project documentation from the pinned commit in an isolated checkout and venv.

The experiment checkout passed as ``source`` is only read (``git cat-file``/``clone``); it is never
checked out, reset or cleaned. HTML is written outside the build checkout and sealed by a manifest
that is marked complete only after every output check passes.
"""
import json
import os
import re
import signal
import subprocess
import sys
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from specmind_eval.common.fingerprint import digest, file_hash, implementation_digest
from specmind_eval.common.outputs import separate_output
from specmind_eval.common.storage import write

SCHEMA = 'docs-build-v1'
CONFIG_KEYS = {'python_version', 'dependency_source', 'requirements', 'install', 'build',
               'api_reference', 'pages', 'required_anchors'}
PLACEHOLDERS = {'python', 'checkout', 'docs', 'html', 'doctrees', 'requirements'}
GIT_TIMEOUT = 600
OBJECT_ID = re.compile(r'[A-Za-z_][\w]*(\.[A-Za-z_][\w]*)+')
SECRET_NAME = re.compile(r'(TOKEN|PASSWORD|PASSWD|SECRET|API_?KEY|CREDENTIAL|AUTH)', re.I)
ANSI = re.compile(r'\x1b\[[0-9;]*[A-Za-z]')
SCRUBBED_ENV = {'PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP', 'PYTHONUSERBASE', 'VIRTUAL_ENV', 'CONDA_PREFIX'}


class DocsBuildError(RuntimeError):
    def __init__(self, message, log=None):
        super().__init__(message + (f' (log: {log})' if log else ''))
        self.message = message
        self.log = str(log) if log else None


# ---------- configuration ----------

def _strings(value, key):
    if not isinstance(value, list) or not all(isinstance(x, str) and x for x in value):
        raise ValueError(f'docs_build.{key} must be a list of nonempty strings')
    return value


def _command(value, key):
    _strings(value, key)
    for arg in value:
        for name in re.findall(r'{([^{}]*)}', arg):
            if name not in PLACEHOLDERS:
                raise ValueError(f'docs_build.{key} uses unknown placeholder {{{name}}}')
    return value


def validate_docs_config(project):
    """Validate the docs build declaration in a raw project config (before any checkout exists)."""
    if not isinstance(project, dict):
        raise ValueError('project config must be an object')
    commit = project.get('commit')
    if not isinstance(commit, str) or not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('project.commit must be a full 40-character lowercase commit SHA for build-docs')
    if not isinstance(project.get('docs_dir'), str) or not project['docs_dir']:
        raise ValueError('project.docs_dir is required for build-docs')
    cfg = project.get('docs_build')
    if not isinstance(cfg, dict):
        raise ValueError('project.docs_build is required for build-docs')
    unknown = set(cfg) - CONFIG_KEYS
    if unknown:
        raise ValueError('docs_build has unknown keys: ' + ', '.join(sorted(unknown)))
    for key in ('install', 'build'):
        steps = cfg.get(key)
        if not isinstance(steps, list) or not steps:
            raise ValueError(f'docs_build.{key} must be a nonempty list of commands')
        for i, step in enumerate(steps):
            _command(step, f'{key}[{i}]')
    _strings(cfg.get('requirements', []), 'requirements')
    if not _strings(cfg.get('pages'), 'pages'):
        raise ValueError('docs_build.pages must list the API pages to check')
    _strings(cfg.get('required_anchors', []), 'required_anchors')
    if not isinstance(cfg.get('api_reference'), str) or not cfg['api_reference']:
        raise ValueError('docs_build.api_reference must be a docname such as "api_reference"')
    for name in [cfg['api_reference'], *cfg.get('pages', [])]:
        if name.startswith(('/', '.')) or '..' in Path(name).parts or name.endswith('.html'):
            raise ValueError('docs_build pages must be Sphinx docnames without .html: ' + name)
    if cfg.get('python_version') is not None and not re.fullmatch(r'\d+\.\d+', str(cfg['python_version'])):
        raise ValueError('docs_build.python_version must look like "3.11"')
    source = cfg.get('dependency_source')
    if source is not None:
        if not isinstance(source, dict) or set(source) - {'file', 'group', 'lock'} or not source.get('file'):
            raise ValueError('docs_build.dependency_source must be {file, group, lock?}')
    return cfg


def _expand(command, values):
    return [arg.format_map(values) for arg in command]


# ---------- secrets ----------

def redact(text, env=None):
    text = re.sub(r'(?i)(\b[a-z][a-z0-9+.-]*://)[^/\s:@]+(:[^/\s@]*)?@', r'\1***@', text)
    text = re.sub(r'(?i)\b((?:[A-Z0-9_]*(?:token|password|passwd|secret|api_?key))\s*[=:]\s*)\S+', r'\1***', text)
    for name, value in (env or {}).items():
        if SECRET_NAME.search(name) and value and len(value) >= 4:
            text = text.replace(value, '***')
    return text


# ---------- subprocess ----------

def _kill_tree(proc):
    if os.name == 'nt':
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(proc.pid)], capture_output=True, timeout=60)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        proc.kill()


def _environment(venv=None):
    env = {k: v for k, v in os.environ.items() if k not in SCRUBBED_ENV}
    env.update(PYTHONDONTWRITEBYTECODE='1', PIP_DISABLE_PIP_VERSION_CHECK='1', PYTHONUTF8='1',
               GIT_TERMINAL_PROMPT='0', PYTHONNOUSERSITE='1', NO_COLOR='1')
    if venv:
        env['VIRTUAL_ENV'] = str(venv)
        env['PATH'] = str(_bin_dir(venv)) + os.pathsep + env.get('PATH', '')
    return env


def run_step(name, command, *, cwd, raw_logs, timeout, env):
    """Run one command with its output streamed to a raw log; raise with the log path on failure."""
    log = Path(raw_logs) / f'{name}.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    with log.open('w', encoding='utf-8', errors='replace') as stream:
        stream.write(f'$ {json.dumps(command)}\n# cwd: {cwd}\n# timeout: {timeout}s\n\n')
        stream.flush()
        kwargs = {'start_new_session': True} if os.name != 'nt' else {
            'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP}
        try:
            proc = subprocess.Popen(command, cwd=cwd, stdout=stream, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, env=env, **kwargs)
        except OSError as exc:
            stream.write(f'\n# could not start: {exc}\n')
            raise DocsBuildError(f'{name}: could not start {command[0]!r}: {exc}', log) from exc
        try:
            code = proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_tree(proc)
            stream.write(f'\n# TIMEOUT after {timeout}s; process tree killed\n')
            raise DocsBuildError(f'{name}: timed out after {timeout}s', log)
        stream.write(f'\n# exit code {code} after {time.time() - started:.1f}s\n')
    if code != 0:
        raise DocsBuildError(f'{name}: exit code {code}', log)
    return log


def _git(*args, cwd=None, timeout=GIT_TIMEOUT):
    proc = subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True, timeout=timeout,
                          env=_environment(), stdin=subprocess.DEVNULL)
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


def _bin_dir(venv):
    return Path(venv) / ('Scripts' if os.name == 'nt' else 'bin')


def _venv_python(venv):
    return _bin_dir(venv) / ('python.exe' if os.name == 'nt' else 'python')


# ---------- checkout ----------

def checkout_status(checkout):
    """Tracked edits, untracked and ignored files; empty means the checkout is pristine."""
    code, out, err = _git('status', '--porcelain', '--untracked-files=all', '--ignored', cwd=checkout)
    if code:
        raise DocsBuildError('git status failed in build checkout: ' + err)
    return out.splitlines()


def head(checkout):
    code, out, err = _git('rev-parse', 'HEAD', cwd=checkout)
    if code:
        raise DocsBuildError('git rev-parse HEAD failed in build checkout: ' + err)
    return out


def prepare_checkout(source, checkout, commit, *, raw_logs, timeout):
    """Clone the local source repository into a new directory and detach at ``commit``.

    Only read-only git commands touch ``source``.
    """
    code, out, _ = _git('-C', str(source), 'cat-file', '-t', commit + '^{commit}')
    if code or out != 'commit':
        raise DocsBuildError(f'Commit {commit} not found in source repository {source}')
    env = _environment()
    run_step('git-clone', ['git', 'clone', '--no-checkout', '--no-hardlinks', '--quiet', str(source), str(checkout)],
             cwd=checkout.parent, raw_logs=raw_logs, timeout=min(timeout, GIT_TIMEOUT), env=env)
    run_step('git-checkout', ['git', '-c', 'advice.detachedHead=false', 'checkout', '--detach', '--quiet', commit],
             cwd=checkout, raw_logs=raw_logs, timeout=min(timeout, GIT_TIMEOUT), env=env)
    verify_checkout(checkout, commit, 'after checkout')
    code, tree, err = _git('rev-parse', 'HEAD^{tree}', cwd=checkout)
    if code:
        raise DocsBuildError('could not read checkout tree: ' + err)
    return tree


def verify_checkout(checkout, commit, when):
    actual = head(checkout)
    if actual != commit:
        raise DocsBuildError(f'Build checkout commit mismatch {when}: expected {commit}, got {actual}')
    dirty = checkout_status(checkout)
    if dirty:
        raise DocsBuildError(f'Build checkout modified {when}: ' + '; '.join(dirty[:10]))


def dependency_source(checkout, cfg):
    """Cross-check configured requirement names against the declared dependency group."""
    source = cfg.get('dependency_source')
    if not source:
        return None
    path = checkout / source['file']
    if not path.is_file():
        raise DocsBuildError('docs_build.dependency_source.file missing at commit: ' + source['file'])
    result = {'file': source['file'], 'sha256': file_hash(path)}
    if source.get('group'):
        import tomllib
        from packaging.requirements import Requirement
        from packaging.utils import canonicalize_name
        groups = tomllib.loads(path.read_text(encoding='utf-8')).get('dependency-groups', {})
        if source['group'] not in groups:
            raise DocsBuildError(f'dependency group {source["group"]!r} not declared in {source["file"]}')
        declared = sorted(canonicalize_name(Requirement(x).name) for x in groups[source['group']] if isinstance(x, str))
        configured = sorted(canonicalize_name(Requirement(x).name) for x in cfg.get('requirements', []))
        if declared != configured:
            raise DocsBuildError(f'docs_build.requirements {configured} do not match group '
                                 f'{source["group"]!r} {declared} in {source["file"]}')
        result.update(group=source['group'], declared=groups[source['group']])
    if source.get('lock'):
        lock = checkout / source['lock']
        if not lock.is_file():
            raise DocsBuildError('docs_build.dependency_source.lock missing at commit: ' + source['lock'])
        result.update(lock=source['lock'], lock_sha256=file_hash(lock))
    return result


# ---------- HTML checks ----------

class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ids, self.objects, self.links = set(), set(), []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        for key in ('id',) + (('name',) if tag == 'a' else ()):
            if attrs.get(key):
                self.ids.add(attrs[key])
        if tag == 'dt' and attrs.get('id') and 'sig-object' in (attrs.get('class') or '').split():
            self.objects.add(attrs['id'])
        if tag == 'a' and attrs.get('href') is not None:
            self.links.append(attrs['href'])


def _parse(path, cache):
    path = path.resolve()
    if path not in cache:
        page = _Page()
        page.feed(path.read_text(encoding='utf-8', errors='replace'))
        cache[path] = page
    return cache[path]


def check_html(html, cfg):
    """Check required pages, API anchors and internal links of the checked pages."""
    html = Path(html).resolve()
    errors, cache, pages = [], {}, {}
    api = cfg['api_reference']
    required = list(dict.fromkeys([api, *cfg.get('pages', [])]))
    for name in required:
        path = html / (name + '.html')
        if not path.is_file():
            errors.append(f'missing page {name}.html')
            continue
        pages[name] = _parse(path, cache)
    if api in pages:
        linked = {(html / urlsplit(h).path).resolve() for h in pages[api].links
                  if h and not urlsplit(h).scheme and urlsplit(h).path}
        for name in cfg.get('pages', []):
            if (html / (name + '.html')).resolve() not in linked:
                errors.append(f'{api}.html does not link to {name}.html')
    anchors = {}
    for name in cfg.get('pages', []):
        if name in pages:
            found = sorted(a for a in pages[name].objects if OBJECT_ID.fullmatch(a))
            anchors[name] = len(found)
            if not found:
                errors.append(f'no API object anchors in {name}.html')
    all_objects = set().union(*(p.objects for p in pages.values())) if pages else set()
    for anchor in cfg.get('required_anchors', []):
        if anchor not in all_objects:
            errors.append(f'required API anchor missing: {anchor}')
    checked_links = 0
    for name, page in pages.items():
        base = html / (name + '.html')
        for href in page.links:
            parts = urlsplit(href)
            if parts.scheme or parts.netloc or href.startswith(('mailto:', 'javascript:')):
                continue
            target = (base.parent / unquote(parts.path)).resolve() if parts.path else base.resolve()
            checked_links += 1
            if not target.is_relative_to(html):
                errors.append(f'{name}.html: link leaves build directory: {href}')
                continue
            if target.is_dir():
                target = target / 'index.html'
            if not target.is_file():
                errors.append(f'{name}.html: missing link target {href}')
                continue
            fragment = unquote(parts.fragment)
            if fragment and target.suffix == '.html' and fragment not in _parse(target, cache).ids:
                errors.append(f'{name}.html: missing anchor {href}')
    return {'pages': required, 'api_anchors_per_page': anchors, 'internal_links_checked': checked_links,
            'errors': errors}


# ---------- artifact sealing ----------

def output_files(output):
    output = Path(output)
    return {p.relative_to(output).as_posix(): file_hash(p) for p in sorted(output.rglob('*'))
            if p.is_file() and p != output / 'manifest.json'}


def validate_docs(output, expected_commit=None):
    """Recompute hashes of every artifact file; detect changed, missing and extra files."""
    from specmind_eval.common.storage import read
    output = Path(output).resolve()
    manifest = read(output / 'manifest.json')
    if not manifest or manifest.get('schema') != SCHEMA:
        raise ValueError('Docs build manifest missing or wrong schema: ' + str(output / 'manifest.json'))
    if not manifest.get('complete'):
        raise ValueError('Docs build is not complete: ' + str(manifest.get('error')))
    if expected_commit and manifest.get('commit') != expected_commit:
        raise ValueError(f'Docs build commit {manifest.get("commit")} != expected {expected_commit}')
    recorded = manifest.get('files')
    config = manifest.get('config', {})
    required = {'project.json', 'environment.json', 'checks.json'}
    if not isinstance(config, dict) or not config.get('api_reference') or not config.get('pages'):
        raise ValueError('Docs manifest missing build configuration')
    required.update('html/' + page + '.html' for page in config['pages'])
    required.add('html/' + config['api_reference'] + '.html')
    if not isinstance(recorded, dict) or not required.issubset(recorded):
        raise ValueError('Docs manifest missing required artifact hashes')
    if not re.fullmatch(r'[0-9a-f]{40}', str(manifest.get('commit', ''))):
        raise ValueError('Docs manifest missing pinned commit')
    actual = output_files(output)
    problems = [f'changed: {n}' for n in sorted(set(recorded) & set(actual)) if recorded[n] != actual[n]]
    problems += [f'missing: {n}' for n in sorted(set(recorded) - set(actual))]
    problems += [f'unexpected: {n}' for n in sorted(set(actual) - set(recorded))]
    if problems or digest(recorded) != manifest.get('output_digest'):
        raise ValueError('Docs artifact mismatch: ' + '; '.join(problems[:20] or ['output_digest']))
    checks = read(output / 'checks.json', {})
    if checks.get('errors') != []:
        raise ValueError('Docs checks missing or contain errors')
    project = read(output / 'project.json', {})
    if project.get('commit') != manifest['commit'] or project.get('name') != manifest.get('project'):
        raise ValueError('Docs project metadata mismatch')
    return manifest


# ---------- orchestration ----------

def _python_info(python, timeout=120):
    code = ('import json,platform,sys;print(json.dumps({"executable":sys.executable,"version":platform.python_version(),'
            '"implementation":platform.python_implementation(),"platform":platform.platform()}))')
    try:
        proc = subprocess.run([str(python), '-c', code], capture_output=True, text=True, timeout=timeout,
                              env=_environment(), stdin=subprocess.DEVNULL)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DocsBuildError(f'could not run Python {python}: {exc}') from exc
    if proc.returncode:
        raise DocsBuildError(f'could not run Python {python}: {proc.stderr.strip()[-500:]}')
    return json.loads(proc.stdout)


def create_venv(python, venv, *, raw_logs, timeout):
    run_step('venv', [str(python), '-m', 'venv', str(venv)], cwd=venv.parent, raw_logs=raw_logs,
             timeout=timeout, env=_environment())
    return _venv_python(venv)


def _freeze(python, timeout):
    try:
        proc = subprocess.run([str(python), '-m', 'pip', 'freeze', '--all'], capture_output=True, text=True,
                              timeout=timeout, env=_environment(), stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as exc:
        raise DocsBuildError(f'pip freeze timed out after {timeout}s') from exc
    if proc.returncode:
        raise DocsBuildError('pip freeze failed in docs environment: ' + proc.stderr.strip()[-500:])
    return sorted(line for line in proc.stdout.splitlines() if line.strip())


def _publish_logs(raw_logs, logs):
    logs.mkdir(parents=True, exist_ok=True)
    for path in sorted(Path(raw_logs).glob('*.log')):
        text = path.read_text(encoding='utf-8', errors='replace')
        (logs / path.name).write_text(redact(text, os.environ), encoding='utf-8')


def build_warnings(raw_logs):
    """Warning lines from build logs, with ANSI colour codes removed."""
    lines = []
    for path in sorted(Path(raw_logs).glob('build-*.log')):
        for line in ANSI.sub('', path.read_text(encoding='utf-8', errors='replace')).splitlines():
            if re.search(r'(^|: )WARNING:', line):
                lines.append(redact(line.strip(), os.environ)[:500])
    return lines


def _now():
    return time.strftime('%Y-%m-%dT%H:%M:%S%z')


def build_docs(project_path, source, workdir, output, *, timeout=1800, python=None):
    """Build docs for ``project.commit``; return a summary or raise DocsBuildError/ValueError."""
    from specmind_prep.project import validate_project
    project_path, source, workdir, output = (Path(p).expanduser().resolve()
                                             for p in (project_path, source, workdir, output))
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or timeout <= 0:
        raise ValueError('timeout must be a positive number of seconds')
    raw = json.loads(project_path.read_text(encoding='utf-8'))
    cfg = validate_docs_config(raw)
    commit = raw['commit']
    if _git('-C', str(source), 'rev-parse', '--git-dir')[0]:
        raise ValueError('--source is not a git repository: ' + str(source))
    separate_output(output, source, workdir)
    separate_output(workdir, source)
    for path, label in ((output, 'Output'), (workdir, 'Workdir')):
        if path.exists():
            raise FileExistsError(f'{label} already exists: {path}; choose a new directory (never overwritten)')
    for path in (output, workdir):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.mkdir()
    raw_logs, logs = workdir / 'raw-logs', output / 'logs'
    checkout, venv, html = workdir / 'checkout', workdir / 'venv', output / 'html'
    manifest = {'schema': SCHEMA, 'complete': False, 'status': 'running', 'project': raw['name'],
                'commit': commit, 'started_at': _now(), 'workdir': str(workdir), 'timeout_seconds': timeout}
    write(output / 'manifest.json', manifest)
    try:
        origin = _git('-C', str(source), 'remote', 'get-url', 'origin')[1]
        manifest['repo'] = {'source': str(source), 'origin': redact(origin) or None,
                            'source_head': _git('-C', str(source), 'rev-parse', 'HEAD')[1]}
        tree = prepare_checkout(source, checkout, commit, raw_logs=raw_logs, timeout=timeout)
        project = validate_project(raw, checkout, documented_only=True)
        dependency = dependency_source(checkout, cfg)
        base = _python_info(python or sys.executable)
        wanted = cfg.get('python_version')
        if wanted and not base['version'].startswith(wanted + '.'):
            raise DocsBuildError(f'docs_build.python_version is {wanted} but {base["executable"]} is '
                                 f'{base["version"]}; pass --python with a matching interpreter')
        vpy = create_venv(base['executable'], venv, raw_logs=raw_logs, timeout=timeout)
        requirements = workdir / 'requirements-docs.txt'
        requirements.write_text(''.join(r + '\n' for r in cfg.get('requirements', [])), encoding='utf-8')
        values = {'python': str(vpy), 'checkout': str(checkout), 'docs': str(checkout / project['docs_dir']),
                  'html': str(html), 'doctrees': str(workdir / 'doctrees'), 'requirements': str(requirements)}
        commands = {key: [_expand(c, values) for c in cfg[key]] for key in ('install', 'build')}
        manifest['commands'] = commands
        env = _environment(venv)
        for i, command in enumerate(commands['install'], 1):
            run_step(f'install-{i}', command, cwd=checkout, raw_logs=raw_logs, timeout=timeout, env=env)
        verify_checkout(checkout, commit, 'after install')
        venv_info = _python_info(vpy)
        environment = {'base_python': base, 'docs_python': venv_info, 'requirements': cfg.get('requirements', []),
                       'dependency_source': dependency, 'packages': _freeze(vpy, min(timeout, 300))}
        write(output / 'environment.json', environment)
        for i, command in enumerate(commands['build'], 1):
            run_step(f'build-{i}', command, cwd=checkout, raw_logs=raw_logs, timeout=timeout, env=env)
        verify_checkout(checkout, commit, 'after build')
        if not html.is_dir():
            raise DocsBuildError('build finished but HTML directory was not created: ' + str(html))
        checks = check_html(html, cfg)
        write(output / 'checks.json', checks)
        if checks['errors']:
            raise DocsBuildError(f'{len(checks["errors"])} output check(s) failed, first: {checks["errors"][0]}',
                                 output / 'checks.json')
        warnings = build_warnings(raw_logs)
        write(output / 'project.json', raw)
        _publish_logs(raw_logs, logs)
        files = output_files(output)
        manifest.update(
            status='complete', complete=True, finished_at=_now(), tree=tree, docs_dir=project['docs_dir'],
            config=cfg, python={'version': venv_info['version'], 'base_executable': base['executable']},
            fingerprint=digest({'project': raw, 'commit': commit, 'tree': tree, 'dependency_source': dependency,
                                'python': venv_info['version'], 'packages': environment['packages'],
                                'code': implementation_digest('specmind_prep.docs_build')}),
            checks={k: checks[k] for k in ('pages', 'api_anchors_per_page', 'internal_links_checked')},
            build_warnings=len(warnings), build_warning_lines=warnings, files=files, output_digest=digest(files),
            paths={'html': str(html), 'api_reference': str(html / (cfg['api_reference'] + '.html'))})
        write(output / 'manifest.json', manifest)
        return {'status': 'complete', 'commit': commit, 'tree': tree, 'output': str(output),
                'manifest': str(output / 'manifest.json'), 'api_reference': manifest['paths']['api_reference'],
                'pages': len(checks['pages']), 'api_anchors': sum(checks['api_anchors_per_page'].values()),
                'internal_links_checked': checks['internal_links_checked'], 'build_warnings': len(warnings),
                'python': venv_info['version']}
    except Exception as exc:
        log = getattr(exc, 'log', None)
        if log and Path(log).parent == raw_logs:
            log = str(logs / Path(log).name)
        try:
            _publish_logs(raw_logs, logs)
        except OSError:
            pass
        message = exc.message if isinstance(exc, DocsBuildError) else f'{type(exc).__name__}: {exc}'
        message = redact(message, os.environ)
        manifest.update(status='failed', complete=False, error=message, log=log, finished_at=_now())
        write(output / 'manifest.json', manifest)
        raise DocsBuildError('Docs build failed: ' + message, log or output / 'manifest.json') from exc
