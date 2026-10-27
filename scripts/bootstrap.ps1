# Sets up Butler's Python on Windows: bootstrap.sh's twin. A virtualenv in .venv holding exactly
# the packages in requirements.lock, with hashes required and nothing compiled. uv provides
# Python itself. Safe to run again: every update will run it.
#
# uv is the one in scripts/lib/uv.sh, pinned by version and SHA-256. This script reads the pins
# from that file, so a Mac and a PC never pin different uvs.
#
#   powershell -ExecutionPolicy Bypass -File scripts\bootstrap.ps1
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"   # Invoke-WebRequest is many times slower with the bar
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$pins = Get-Content -Raw (Join-Path $root "scripts\lib\uv.sh")
$version = [regex]::Match($pins, 'BUTLER_UV_VERSION="([^"]+)"').Groups[1].Value
$target = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "aarch64-pc-windows-msvc" } else { "x86_64-pc-windows-msvc" }
$sha = [regex]::Match($pins, [regex]::Escape($target) + '\) echo "([0-9a-f]{64})"').Groups[1].Value
if (-not $version -or -not $sha) { throw "scripts/lib/uv.sh has no uv pinned for $target." }

$bin = Join-Path $root "bin"
$uv = Join-Path $bin "uv.exe"
$have = if (Test-Path $uv) { (& $uv --version 2>$null) -split " " | Select-Object -Index 1 } else { "" }
if ($have -ne $version) {
    Write-Host "Getting uv $version, which gives Butler its own Python (https://docs.astral.sh/uv/)."
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("butler-uv-" + [guid]::NewGuid())
    New-Item -ItemType Directory $tmp | Out-Null
    try {
        $zip = Join-Path $tmp "uv.zip"
        $url = "https://github.com/astral-sh/uv/releases/download/$version/uv-$target.zip"
        $tries = 0
        while ($true) {
            try { Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $zip; break }
            catch { $tries++; if ($tries -ge 5) { throw "Couldn't download uv. Check the internet connection and try again." }; Start-Sleep -Seconds ([math]::Pow(2, $tries)) }
        }
        # .NET, not Get-FileHash or Expand-Archive: those live in PowerShell modules that a PC with
        # more than one PowerShell can fail to load.
        $stream = [IO.File]::OpenRead($zip)
        try { $got = ([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($stream)) -replace "-", "").ToLower() }
        finally { $stream.Dispose() }
        if ($got -ne $sha) { throw "The uv download doesn't match its checksum ($got), so Butler won't use it." }
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [IO.Compression.ZipFile]::ExtractToDirectory($zip, (Join-Path $tmp "uv"))
        New-Item -ItemType Directory -Force $bin | Out-Null
        Copy-Item (Join-Path $tmp "uv\uv.exe") $uv -Force
    } finally {
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    }
}

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    & $uv venv --quiet --python 3.13 .venv
    if ($LASTEXITCODE -ne 0) { throw "uv couldn't make Butler's Python." }
}
& $uv pip sync --quiet --python $python --require-hashes --no-build requirements.lock
if ($LASTEXITCODE -ne 0) { throw "Butler's packages didn't install." }
foreach ($dir in "data", "logs", "out") { New-Item -ItemType Directory -Force (Join-Path $root $dir) | Out-Null }
# No chmod here: a folder under C:\Users\<name> is already this account's alone (and the
# computer's administrators'), by the permissions Windows gives a user profile.
Write-Host "Python is ready. Next: .venv\Scripts\python.exe run.py preview"
