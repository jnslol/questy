$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$buildRoot = Join-Path $root "build"
$dist = Join-Path $buildRoot "dist"

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python is required."
}

python -m pip install -r (Join-Path $root "requirements.txt")

$pyinstaller = python -m PyInstaller --version 2>$null

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller is not installed. Install it with: python -m pip install pyinstaller"
}

New-Item -ItemType Directory -Force -Path $dist | Out-Null

& python -m PyInstaller `
    --onefile `
    --name questy `
    --distpath $dist `
    --workpath (Join-Path $buildRoot "pyinstaller") `
    --specpath $buildRoot `
    --collect-all textual `
    --collect-all rich `
    --collect-submodules modules `
    (Join-Path $root "questy.py")

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed with exit code $LASTEXITCODE."
}

Write-Host "Built: $dist\questy.exe"