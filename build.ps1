$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$python = if (Test-Path -LiteralPath '.venv\Scripts\python.exe') { '.venv\Scripts\python.exe' } else { 'python' }
& $python -m pip install -r requirements.txt pyinstaller
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
& $python -m PyInstaller --noconfirm --windowed --onedir --name OffworkPet --exclude-module PySide6.QtWebEngineCore --exclude-module PySide6.QtWebEngineWidgets main.py
if ($LASTEXITCODE -ne 0) { throw 'Build failed' }
New-Item -ItemType Directory -Path 'dist\OffworkPet\docs' -Force | Out-Null
Copy-Item -Path 'docs\*.md' -Destination 'dist\OffworkPet\docs'
Write-Host 'Ready: dist\OffworkPet\OffworkPet.exe (distribute the entire OffworkPet directory)'
