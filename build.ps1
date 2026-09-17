$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$buildRoot = Join-Path $root "build"
$dist = Join-Path $buildRoot "dist"
$stamp = [DateTime]::Now.ToString("yyyyMMddHHmmss")
$staging = Join-Path $buildRoot ("staging-" + $stamp)

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw "Python is required."
}

python -m pip install -r (Join-Path $root "requirements.txt")

$nuitka = python -m nuitka --version 2>$null
if ($LASTEXITCODE -ne 0) {
    throw "Nuitka is not installed. Install it with: python -m pip install nuitka"
}

New-Item -ItemType Directory -Force -Path $dist | Out-Null
New-Item -ItemType Directory -Force -Path $staging | Out-Null

& python -m nuitka `
    --onefile `
    --standalone `
    --remove-output `
    --assume-yes-for-downloads `
    --windows-console-mode=force `
    --include-package=modules `
    --output-dir=$staging `
    --output-filename=questy.exe `
    (Join-Path $root "questy.py")

if ($LASTEXITCODE -ne 0) {
    throw "Nuitka build failed with exit code $LASTEXITCODE."
}

Copy-Item -LiteralPath (Join-Path $staging "questy.exe") -Destination (Join-Path $dist "questy.exe") -Force
Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "Built: $dist\questy.exe"
