# Builds the Butler installer for Windows from this checkout's committed files: build-pkg.sh's twin.
#
#   powershell -File packaging\windows\build-installer.ps1 -Version v1.2.3 [-Out dist]
#
# Needs Inno Setup 6 (ISCC.exe). The result is dist\Butler-Setup.exe, unsigned until Butler has
# a Windows signing certificate (#40); an unsigned build carries .butler-unsigned-build, as on
# the Mac, and SmartScreen warns before it runs.
param(
    [string]$Version = "v0.0.0-dev",
    [string]$Out = ""
)
$ErrorActionPreference = "Stop"
$repo = Resolve-Path (Join-Path $PSScriptRoot "..\..")
if (-not $Out) { $Out = Join-Path $repo "dist" }
New-Item -ItemType Directory -Force $Out | Out-Null
$number = ($Version.TrimStart("v") -split "-")[0]

$work = Join-Path ([IO.Path]::GetTempPath()) ("butler-build-" + [guid]::NewGuid())
$stage = Join-Path $work "Butler"
New-Item -ItemType Directory -Force $stage | Out-Null
try {
    $tar = Join-Path $work "source.tar"
    git -C $repo archive --format=tar -o $tar HEAD
    if ($LASTEXITCODE -ne 0) { throw "git archive failed" }
    tar -x -f $tar -C $stage
    if ($LASTEXITCODE -ne 0) { throw "tar failed" }
    Remove-Item -Recurse -Force (Join-Path $stage ".github"), (Join-Path $stage "packaging")
    Set-Content -NoNewline -Encoding ascii (Join-Path $stage ".butler-version") "$Version`n"
    Set-Content -NoNewline -Encoding ascii (Join-Path $stage ".butler-unsigned-build") "$Version`n"

    $iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
              "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $iscc) { throw "Inno Setup 6 isn't installed (ISCC.exe not found)." }
    & $iscc /Q "/DSource=$stage" "/DVersion=$number" "/DOutput=$Out" (Join-Path $PSScriptRoot "butler.iss")
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup failed" }
    Write-Host "built $(Join-Path $Out 'Butler-Setup.exe')"
} finally {
    Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
}
