# SPDX-License-Identifier: AGPL-3.0-or-later
# Build the ContextPulse Windows installer: PyInstaller (contextpulse.spec) then
# Inno Setup 6 (installer.iss). Prints the installer path, size and SHA-256.
#
# Usage (from the repo root, PowerShell):
#   powershell -ExecutionPolicy Bypass -File scripts\build_windows_installer.ps1
#   ... -SkipPyInstaller   # reuse dist\ContextPulse from a previous build
#
# Needs: .venv with the project installed (editable) plus pyinstaller, and
# Inno Setup 6 (user scope: winget install JRSoftware.InnoSetup --scope user).
# It never stops a running ContextPulse: the build reads the venv, it does not
# replace it.
param(
    [switch]$SkipPyInstaller,
    [string]$Python = ".venv\Scripts\python.exe"
)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$version = (Select-String -Path "pyproject.toml" -Pattern '^version\s*=\s*"([^"]+)"' |
    Select-Object -First 1).Matches[0].Groups[1].Value
if (-not $version) { throw "could not read version from pyproject.toml" }
Write-Host "ContextPulse $version"

if (-not $SkipPyInstaller) {
    # A workspace .env must not leak into the build's import-time config.
    Remove-Item Env:CONTEXTPULSE_DOTENV -ErrorAction SilentlyContinue
    & $Python -m PyInstaller contextpulse.spec --noconfirm --clean --distpath dist --workpath build
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed ($LASTEXITCODE)" }
}
foreach ($exe in "ContextPulse.exe", "ContextPulseCLI.exe") {
    if (-not (Test-Path "dist\ContextPulse\$exe")) { throw "missing dist\ContextPulse\$exe" }
}

$iscc = @(
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) { throw "Inno Setup 6 not found: winget install JRSoftware.InnoSetup --scope user" }

& $iscc "/DMyAppVersion=$version" installer.iss
if ($LASTEXITCODE -ne 0) { throw "ISCC failed ($LASTEXITCODE)" }

$out = Join-Path $root "installer_output\ContextPulseSetup-$version.exe"
$item = Get-Item $out
$hash = (Get-FileHash $out -Algorithm SHA256).Hash.ToLower()
Write-Host "installer=$out"
Write-Host "size_bytes=$($item.Length)"
Write-Host "sha256=$hash"
