"""Step 0 docs build: fixture git repo + fake Sphinx script; no network, no Docker, no real venv."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from specmind_prep import docs_build
from specmind_prep.docs_build import DocsBuildError, build_docs, redact, validate_docs
from specmind_eval.common.storage import read

FAKE_SPHINX = r'''
import sys
from pathlib import Path
mode, html, checkout = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
if mode == "fail":
    print("Sphinx error: boom"); sys.exit(2)
if mode == "sleep":
    import time; time.sleep(60)
if mode == "dirty":
    (checkout / "docs" / "generated.rst").write_text("x")
html.mkdir(parents=True)
pages = {"api_reference": '<a href="acme.core.html">core</a> <a href="acme.util.html#acme.util.g">g</a>',
         "acme.core": '<dt class="sig sig-object py" id="acme.core.f">f</dt><a href="#acme.core.f">self</a>'
                      '<a href="acme.util.html#acme.util.g">g</a><a href="https://example.org/x">ext</a>',
         "acme.util": '<dt class="sig sig-object py" id="acme.util.g">g</dt><a href="api_reference.html">up</a>'}
if mode == "no-page":
    del pages["acme.util"]
if mode == "no-anchor":
    pages["acme.util"] = '<p>no objects</p>'
if mode == "broken-link":
    pages["acme.core"] += '<a href="acme.util.html#acme.util.missing">bad</a><a href="gone.html">gone</a>'
for name, body in pages.items():
    (html / (name + ".html")).write_text("<html><body>" + body + "</body></html>")
print("\x1b[91mWARNING: fake warning\x1b[39;49;00m")
print("/docs/x.rst:3: WARNING: second")
print("build succeeded, 2 warnings.")
'''


def git(cwd, *args):
    return subprocess.run(['git', *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def source(tmp_path):
    repo = tmp_path / 'src_repo'
    (repo / 'src/acme').mkdir(parents=True)
    (repo / 'tests').mkdir()
    (repo / 'docs').mkdir()
    (repo / 'src/acme/__init__.py').write_text('def f():\n    """F."""\n    return 1\n')
    (repo / 'tests/test_x.py').write_text('def test_x():\n    pass\n')
    (repo / 'docs/index.rst').write_text('Index\n')
    (repo / 'pyproject.toml').write_text('[dependency-groups]\ndocs = ["sphinx>=8", "furo"]\n')
    git(repo, 'init', '-q')
    git(repo, 'add', '.')
    git(repo, '-c', 'user.name=t', '-c', 'user.email=t@t', 'commit', '-q', '-m', 'init')
    return repo


@pytest.fixture
def fake_env(tmp_path, monkeypatch):
    script = tmp_path / 'fake_sphinx.py'
    script.write_text(FAKE_SPHINX)
    monkeypatch.setattr(docs_build, 'create_venv', lambda python, venv, **kw: Path(sys.executable))
    monkeypatch.setattr(docs_build, '_freeze', lambda python, timeout: ['fake-sphinx==1.0'])
    return script


def write_project(tmp_path, source, script, *, mode='ok', install=None, commit=None):
    project = {
        'name': 'acme', 'commit': commit or git(source, 'rev-parse', 'HEAD'), 'packages': ['acme'],
        'docs_dir': 'docs',
        'docs_build': {
            'dependency_source': {'file': 'pyproject.toml', 'group': 'docs'},
            'requirements': ['sphinx==9.0.4', 'furo==1'],
            'install': install or [['{python}', '-c', 'print("installed")']],
            'build': [['{python}', str(script), mode, '{html}', '{checkout}']],
            'api_reference': 'api_reference',
            'pages': ['acme.core', 'acme.util'],
            'required_anchors': ['acme.core.f'],
        },
    }
    path = tmp_path / f'project-{mode}.json'
    path.write_text(json.dumps(project))
    return path


def run(tmp_path, source, project, name='run', **kw):
    return build_docs(project, source, tmp_path / (name + '-work'), tmp_path / (name + '-out'), **kw)


def test_build_success_manifest_and_source_untouched(tmp_path, source, fake_env):
    before = (git(source, 'rev-parse', 'HEAD'), git(source, 'status', '--porcelain', '--ignored'))
    result = run(tmp_path, source, write_project(tmp_path, source, fake_env))
    out = tmp_path / 'run-out'
    manifest = read(out / 'manifest.json')
    assert result['status'] == 'complete' and manifest['complete'] is True
    assert manifest['schema'] == 'docs-build-v1'
    assert manifest['commit'] == git(source, 'rev-parse', 'HEAD')
    assert manifest['tree'] == git(source, 'rev-parse', 'HEAD^{tree}')
    assert manifest['commands']['build'][0][2] == 'ok' and manifest['fingerprint']
    assert manifest['checks']['api_anchors_per_page'] == {'acme.core': 1, 'acme.util': 1}
    assert manifest['checks']['internal_links_checked'] >= 4 and manifest['build_warnings'] == 2
    assert manifest['build_warning_lines'] == ['WARNING: fake warning', '/docs/x.rst:3: WARNING: second']
    for name in ('html/api_reference.html', 'logs/build-1.log', 'logs/install-1.log', 'environment.json',
                 'checks.json', 'project.json'):
        assert name in manifest['files']
    assert read(out / 'environment.json')['packages'] == ['fake-sphinx==1.0']
    assert not any(p.name in {'checkout', 'venv'} for p in out.rglob('*'))
    assert validate_docs(out, manifest['commit'])['complete']
    assert (git(source, 'rev-parse', 'HEAD'), git(source, 'status', '--porcelain', '--ignored')) == before


def test_unknown_commit_fails_before_checkout(tmp_path, source, fake_env):
    project = write_project(tmp_path, source, fake_env, commit='0' * 40)
    with pytest.raises(DocsBuildError, match='not found in source'):
        run(tmp_path, source, project)
    manifest = read(tmp_path / 'run-out/manifest.json')
    assert manifest['complete'] is False and manifest['status'] == 'failed'
    with pytest.raises(ValueError, match='not complete'):
        validate_docs(tmp_path / 'run-out')


def test_checkout_on_wrong_commit_is_detected(tmp_path, source, fake_env, monkeypatch):
    monkeypatch.setattr(docs_build, 'head', lambda checkout: 'f' * 40)
    with pytest.raises(DocsBuildError, match='commit mismatch after checkout'):
        run(tmp_path, source, write_project(tmp_path, source, fake_env))
    assert read(tmp_path / 'run-out/manifest.json')['complete'] is False


def test_build_that_modifies_checkout_is_rejected(tmp_path, source, fake_env):
    with pytest.raises(DocsBuildError, match='modified after build'):
        run(tmp_path, source, write_project(tmp_path, source, fake_env, mode='dirty'))
    assert read(tmp_path / 'run-out/manifest.json')['complete'] is False


@pytest.mark.parametrize('stage', ['install', 'build'])
def test_failed_command_reports_log_and_exit_code(tmp_path, source, fake_env, stage, capsys):
    from specmind_prep.cli import main
    install = [['{python}', '-c', 'import sys; print("pip: no matching distribution"); sys.exit(3)']]
    project = write_project(tmp_path, source, fake_env, mode='fail' if stage == 'build' else 'ok',
                            install=install if stage == 'install' else None)
    code = main(['build-docs', '--project', str(project), '--source', str(source),
                 '--workdir', str(tmp_path / 'w'), '--output', str(tmp_path / 'o')])
    err = capsys.readouterr().err
    log = tmp_path / 'o/logs' / f'{stage}-1.log'
    assert code == 1 and 'exit code' in err and str(log) in err
    assert ('no matching distribution' if stage == 'install' else 'Sphinx error') in log.read_text()
    manifest = read(tmp_path / 'o/manifest.json')
    assert manifest['complete'] is False and manifest['log'] == str(log)


def test_subprocess_timeout_is_killed_and_reported(tmp_path, source, fake_env):
    with pytest.raises(DocsBuildError, match='timed out after 3s') as info:
        run(tmp_path, source, write_project(tmp_path, source, fake_env, mode='sleep'), timeout=3)
    assert info.value.log.endswith('build-1.log')
    assert 'TIMEOUT' in Path(info.value.log).read_text()
    assert read(tmp_path / 'run-out/manifest.json')['complete'] is False


@pytest.mark.parametrize('mode,expected', [('no-page', 'missing page acme.util.html'),
                                           ('no-anchor', 'no API object anchors in acme.util.html'),
                                           ('broken-link', 'acme.core.html: missing anchor acme.util.html#acme.util.missing')])
def test_successful_build_with_bad_output_is_not_complete(tmp_path, source, fake_env, mode, expected):
    with pytest.raises(DocsBuildError, match='output check'):
        run(tmp_path, source, write_project(tmp_path, source, fake_env, mode=mode))
    out = tmp_path / 'run-out'
    assert expected in read(out / 'checks.json')['errors']
    if mode == 'broken-link':
        assert 'acme.core.html: missing link target gone.html' in read(out / 'checks.json')['errors']
    assert read(out / 'manifest.json')['complete'] is False


def test_existing_output_or_workdir_is_never_overwritten(tmp_path, source, fake_env):
    project = write_project(tmp_path, source, fake_env)
    (tmp_path / 'run-out').mkdir()
    (tmp_path / 'run-out/keep.txt').write_text('mine')
    with pytest.raises(FileExistsError, match='Output already exists'):
        run(tmp_path, source, project)
    assert (tmp_path / 'run-out/keep.txt').read_text() == 'mine'
    assert not (tmp_path / 'run-work').exists()
    (tmp_path / 'other-work').mkdir()
    with pytest.raises(FileExistsError, match='Workdir already exists'):
        run(tmp_path, source, project, name='other')
    assert not (tmp_path / 'other-out').exists()


def test_tampered_artifacts_are_detected(tmp_path, source, fake_env, capsys):
    from specmind_prep.cli import main
    run(tmp_path, source, write_project(tmp_path, source, fake_env))
    out = tmp_path / 'run-out'
    assert main(['validate-docs', '--output', str(out)]) == 0
    page = out / 'html/acme.core.html'
    original = page.read_bytes()
    page.write_bytes(original.replace(b'acme.core.f', b'acme.core.F'))
    with pytest.raises(ValueError, match='changed: html/acme.core.html'):
        validate_docs(out)
    page.write_bytes(original)
    (out / 'html/extra.html').write_text('x')
    assert main(['validate-docs', '--output', str(out)]) == 1
    assert 'unexpected: html/extra.html' in capsys.readouterr().err
    (out / 'html/extra.html').unlink()
    with pytest.raises(ValueError, match='expected'):
        validate_docs(out, expected_commit='1' * 40)


def test_dependency_group_mismatch_is_rejected(tmp_path, source, fake_env):
    project = write_project(tmp_path, source, fake_env)
    value = json.loads(project.read_text())
    value['docs_build']['requirements'] = ['sphinx==9.0.4']
    project.write_text(json.dumps(value))
    with pytest.raises(DocsBuildError, match='do not match group'):
        run(tmp_path, source, project)


@pytest.mark.parametrize('change,match', [({'install': [['{python}', '{nope}']]}, 'unknown placeholder'),
                                          ({'pages': []}, 'pages'), ({'extra': 1}, 'unknown keys')])
def test_invalid_docs_config(change, match):
    cfg = {'install': [['x']], 'build': [['y']], 'api_reference': 'api', 'pages': ['p'], **change}
    with pytest.raises(ValueError, match=match):
        docs_build.validate_docs_config({'name': 'n', 'commit': 'a' * 40, 'docs_dir': 'docs', 'docs_build': cfg})
    with pytest.raises(ValueError, match='40-character'):
        docs_build.validate_docs_config({'name': 'n', 'commit': 'abc', 'docs_dir': 'docs', 'docs_build': cfg})


def test_redact_hides_credentials():
    text = redact('git clone https://user:hunter2@host/x.git\nPIP_TOKEN=abc123 password: s3cret\nvalue zzzz9999',
                  {'MY_API_KEY': 'zzzz9999', 'HOME': 'h'})
    assert 'hunter2' not in text and 'abc123' not in text and 's3cret' not in text and 'zzzz9999' not in text
    assert 'https://***@host/x.git' in text


def test_empty_manifest_cannot_validate(tmp_path):
    from specmind_eval.common.storage import write
    from specmind_eval.common.fingerprint import digest
    write(tmp_path/'manifest.json', {'schema': docs_build.SCHEMA, 'complete': True,
                                    'files': {}, 'output_digest': digest({})})
    with pytest.raises(ValueError, match='missing'):
        validate_docs(tmp_path)


def test_nested_manifest_is_hashed(tmp_path):
    nested = tmp_path/'html'; nested.mkdir()
    (nested/'manifest.json').write_text('{}')
    (tmp_path/'manifest.json').write_text('{}')
    assert set(docs_build.output_files(tmp_path)) == {'html/manifest.json'}
