$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $ProjectRoot

if (Get-Command py -ErrorAction SilentlyContinue) {
    $Python = "py"
    $PythonArgs = @("-3")
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $Python = "python"
    $PythonArgs = @()
} else {
    throw "Python 3 was not found. Install Python 3.10+ and try again."
}

& $Python @PythonArgs -m venv .venv

$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$PyInstaller = Join-Path $ProjectRoot ".venv\Scripts\pyinstaller.exe"

& $VenvPython -m pip install --upgrade pip
& $VenvPython -m pip install -e ".[dev]"
npm install
npm run build

& $PyInstaller `
    --clean `
    --onefile `
    --noconsole `
    --name codex-proxy `
    --distpath src-tauri\binaries `
    --add-data "static;static" `
    codex_proxy_launcher.py

npm run tauri build
