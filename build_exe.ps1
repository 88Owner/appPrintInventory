param(
  [string]$OutDir = "dist"
)

$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

if (!(Test-Path ".\.venv\Scripts\python.exe")) {
  Write-Host "Missing venv. Run: py -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install -r requirements.txt"
  exit 1
}

.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe .\tools\generate_icon.py

$icon = Join-Path $PSScriptRoot "assets\app.ico"

.\.venv\Scripts\python.exe -m PyInstaller `
  --noconfirm `
  --clean `
  --name "appPrintInv" `
  --icon "$icon" `
  --add-data "config.json;." `
  --add-data "assets\app.ico;assets" `
  --windowed `
  --onefile `
  .\run_app.py

Write-Host "Built exe at: $PSScriptRoot\dist\appPrintInv.exe"

