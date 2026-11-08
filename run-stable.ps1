param(
    [Parameter(Position=0)]
    [ValidateSet('prep', 'eval', 'test', 'replay')]
    [string]$Command = 'prep',
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$CliArgs
)
$ErrorActionPreference = 'Stop'
$runtimeRoot = $PSScriptRoot
$runtimePython = Join-Path $runtimeRoot '.venv-stable\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $runtimePython -PathType Leaf)) {
    throw 'Missing .venv-stable. Follow docs/STABLE_ENVIRONMENT.md first.'
}
$originalPythonPath = $env:PYTHONPATH
$originalNoUserSite = $env:PYTHONNOUSERSITE
$originalUtf8 = $env:PYTHONUTF8
$originalPluginAutoload = $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD
Push-Location -LiteralPath $runtimeRoot
try {
    $env:PYTHONPATH = Join-Path $runtimeRoot 'src'
    $env:PYTHONNOUSERSITE = '1'
    $env:PYTHONUTF8 = '1'
    $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
    & $runtimePython -c "import pathlib,site,sys; assert sys.version_info[:2] == (3,11), 'Use Python 3.11'; assert sys.prefix != sys.base_prefix, 'Virtual environment required'; assert not site.ENABLE_USER_SITE, 'User site must be disabled'; cfg=(pathlib.Path(sys.prefix)/'pyvenv.cfg').read_text(); assert 'include-system-site-packages = false' in cfg.lower(), 'System site-packages must be disabled'"
    if ($LASTEXITCODE -ne 0) { throw 'Runtime isolation check failed' }
    $module = switch ($Command) {
        'prep' { 'specmind_prep' }
        'eval' { 'specmind_eval' }
        'test' { 'pytest' }
        'replay' { 'specmind_prep.mutant_replay' }
    }
    & $runtimePython -m $module @CliArgs
    $runtimeExitCode = $LASTEXITCODE
}
finally {
    $env:PYTHONPATH = $originalPythonPath
    $env:PYTHONNOUSERSITE = $originalNoUserSite
    $env:PYTHONUTF8 = $originalUtf8
    $env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = $originalPluginAutoload
    Pop-Location
}
exit $runtimeExitCode
