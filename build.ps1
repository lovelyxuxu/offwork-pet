$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = if (Test-Path -LiteralPath '.venv\Scripts\python.exe') { '.venv\Scripts\python.exe' } else { 'python' }
& $python -m pip install -r requirements.txt pyinstaller
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
& $python tools/package_release.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
Write-Host 'Ready: versioned ZIP in dist (includes release notes; excludes docs and personal configuration)'
