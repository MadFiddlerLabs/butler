# Sets up Butler's Python on Windows: bootstrap.sh's twin. A virtualenv at .venv holding exactly
# the packages in requirements.lock, with hashes required and nothing compiled. uv provides
# Python itself. Safe to run again: every update runs it, while Butler may be running.
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
    Write-Host "Getting a small tool (uv $version) that gives Butler its own copy of Python."
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("butler-uv-" + [guid]::NewGuid())
    New-Item -ItemType Directory $tmp | Out-Null
    try {
        $zip = Join-Path $tmp "uv.zip"
        $url = "https://github.com/astral-sh/uv/releases/download/$version/uv-$target.zip"
        # Windows' own curl.exe (Windows 10 1803 and later), with a time limit: Invoke-WebRequest
        # can stall with no error and wait forever (owner's test PC, 28 Oct 2026), where curl fetched
        # the same file in 15 seconds. Invoke-WebRequest, also time-limited, only where curl is missing.
        $curl = Join-Path $env:SystemRoot "System32\curl.exe"
        $tries = 0
        while ($true) {
            try {
                if (Test-Path $curl) {
                    & $curl -fsSL --connect-timeout 30 --max-time 300 -o $zip $url
                    if ($LASTEXITCODE -ne 0) { throw "curl exit $LASTEXITCODE" }
                } else {
                    Invoke-WebRequest -UseBasicParsing -TimeoutSec 300 -Uri $url -OutFile $zip
                }
                break
            }
            catch { $tries++; Write-Host "The download didn't finish ($_). Trying again."; if ($tries -ge 5) { throw "Couldn't download uv. Check the internet connection and try again." }; Start-Sleep -Seconds ([math]::Pow(2, $tries)) }
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

# Windows won't replace a file a running program has loaded, and the portal and the tick keep
# Butler's packages loaded. So each lock gets a virtualenv of its own, in .venvs\<lock id>, and
# .venv is a junction to the current one: a new lock builds its virtualenv beside the old, then
# moves .venv over, while what's running keeps the old. Going back to a lock re-points .venv at
# its virtualenv, still there. Two are kept: the current one and the one before it.
# A plain .venv folder (a developer's checkout) is synced in place, as before.
$link = Join-Path $root ".venv"
$existing = Get-Item -Force $link -ErrorAction SilentlyContinue
if ($existing -and -not ($existing.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
    $python = Join-Path $link "Scripts\python.exe"
    if (-not (Test-Path $python)) {
        & $uv venv --quiet --python 3.13 .venv
        if ($LASTEXITCODE -ne 0) { throw "uv couldn't make Butler's Python." }
    }
    & $uv pip sync --quiet --python $python --require-hashes --no-build requirements.lock
    if ($LASTEXITCODE -ne 0) { throw "Butler's packages didn't install." }
} else {
    $stream = [IO.File]::OpenRead((Join-Path $root "requirements.lock"))
    try { $lockId = ([BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash($stream)) -replace "-", "").ToLower().Substring(0, 12) }
    finally { $stream.Dispose() }
    $venvs = Join-Path $root ".venvs"
    $want = Join-Path $venvs "$lockId-$target"
    $python = Join-Path $want "Scripts\python.exe"
    if (-not (Test-Path (Join-Path $want ".butler-ready"))) {
        # Half made by a run that stopped: nothing points at it, so it starts again.
        if (Test-Path $want) { Remove-Item -Recurse -Force $want }
        Write-Host "Getting Python 3.13 and making Butler's own copy of it."
        & $uv venv --quiet --python 3.13 $want
        if ($LASTEXITCODE -ne 0) { throw "uv couldn't make Butler's Python." }
        Write-Host "Installing Butler's packages, exactly as requirements.lock pins them."
        & $uv pip sync --quiet --python $python --require-hashes --no-build requirements.lock
        if ($LASTEXITCODE -ne 0) { throw "Butler's packages didn't install." }
        Set-Content -Encoding ascii (Join-Path $want ".butler-ready") $lockId
    }
    $current = if ($existing) { $existing.Target | Select-Object -First 1 } else { $null }
    if (-not $current -or ([IO.Path]::GetFullPath($current).TrimEnd("\") -ne [IO.Path]::GetFullPath($want).TrimEnd("\"))) {
        # rmdir removes the junction only, never what it points at (Remove-Item -Recurse would).
        if ($existing) { cmd /c rmdir "$link"; if ($LASTEXITCODE -ne 0) { throw "Couldn't move .venv to the new Python." } }
        New-Item -ItemType Junction -Path $link -Target $want | Out-Null
        if ($current) { Set-Content -Encoding ascii (Join-Path $venvs "previous") (Split-Path -Leaf $current) }
    }
    $previous = "$(Get-Content -ErrorAction SilentlyContinue (Join-Path $venvs "previous"))".Trim()
    foreach ($old in Get-ChildItem -Directory $venvs) {
        if ($old.Name -ne (Split-Path -Leaf $want) -and $old.Name -ne $previous) {
            # One still in use stays, and goes next time.
            Remove-Item -Recurse -Force $old.FullName -ErrorAction SilentlyContinue
        }
    }
}
foreach ($dir in "data", "logs", "out") { New-Item -ItemType Directory -Force (Join-Path $root $dir) | Out-Null }
# No chmod here: a folder under C:\Users\<name> is already this account's alone (and the
# computer's administrators'), by the permissions Windows gives a user profile.
Write-Host "Python is ready. Next: .venv\Scripts\python.exe run.py preview"
