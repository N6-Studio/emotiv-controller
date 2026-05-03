<#
.SYNOPSIS
    Build the PyInstaller EXE and optionally Authenticode-sign it for Windows.

.DESCRIPTION
    Runs PyInstaller against app.spec in the Python project folder, then signs dist\app.exe with signtool.
    Signing requires the Windows SDK (signtool.exe) and a code signing certificate.

    SmartScreen / "Windows protected your PC":
    - Self-signed certificates do NOT remove this warning for other users.
    - You need a standard code signing cert from a public CA (DigiCert, Sectigo, etc.).
    - EV certificates typically get immediate SmartScreen trust; OV may need download reputation.

.PARAMETER RepoRoot
    Optional root of the repo (folder that contains python\). If set, the build runs in RepoRoot\python\.
    If omitted, the script assumes it lives in python\scripts\ and uses the parent of scripts\ as the Python project directory.

.PARAMETER SkipSign
    Only build; do not run signtool.

.PARAMETER PfxPath
    Path to a .pfx code signing certificate (alternative to -CertThumbprint).

.PARAMETER PfxPassword
    Plain password for the PFX (avoid in production; prefer CertThumbprint or prompt).

.PARAMETER CertThumbprint
    Thumbprint of a code signing cert already in the CurrentUser\My store (recommended on dev machines).

.PARAMETER TimestampUrl
    RFC3161 timestamp server (DigiCert is widely used).

.PARAMETER Debug
    PyInstaller builds with a console window (stdout/stderr). Omit for a windowed EXE (no console).

.EXAMPLE
    .\build-and-sign.ps1 -SkipSign

.EXAMPLE
    .\build-and-sign.ps1 -SkipSign -Debug

.EXAMPLE
    .\build-and-sign.ps1 -CertThumbprint "A1B2C3..." -TimestampUrl "http://timestamp.digicert.com"

.EXAMPLE
    .\build-and-sign.ps1 -PfxPath "D:\certs\codesign.pfx"
    # You will be prompted for the PFX password unless you pass -PfxPassword (not recommended in scripts).
#>
param(
    [string] $RepoRoot = "",

    [switch] $SkipSign,

    [switch] $Debug,

    [string] $PfxPath,

    [SecureString] $PfxPassword,

    [string] $CertThumbprint,

    [string] $TimestampUrl = "http://timestamp.digicert.com"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
# Pip often emits benign warnings on stderr; PS 7.4+ can treat that as a terminating error with -Stop.
if (Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue) {
    $PSNativeCommandUseErrorActionPreference = $false
}

$scriptDir = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
if ($RepoRoot) {
    $pythonDir = Join-Path $RepoRoot "python"
}
else {
    $pythonDir = Split-Path -Parent $scriptDir
}

function Get-PythonCommand {
    $py = Get-Command python -ErrorAction SilentlyContinue
    if ($py) { return @{ Exe = "python"; ArgsPrefix = @() } }
    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher) { return @{ Exe = "py"; ArgsPrefix = @("-3") } }
    return $null
}

function Find-SignTool {
    $roots = @(
        "${env:ProgramFiles(x86)}\Windows Kits\10\bin",
        "${env:ProgramFiles}\Windows Kits\10\bin"
    )
    foreach ($root in $roots) {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        $candidates = Get-ChildItem -Path $root -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match '^\d+\.\d+\.\d+\.\d+$' } |
            Sort-Object { [version]$_.Name } -Descending
        foreach ($ver in $candidates) {
            $exe = Join-Path $ver.FullName "x64\signtool.exe"
            if (Test-Path -LiteralPath $exe) { return $exe }
            $exe = Join-Path $ver.FullName "x86\signtool.exe"
            if (Test-Path -LiteralPath $exe) { return $exe }
        }
    }
    return $null
}

$specPath = Join-Path $pythonDir "app.spec"
$distExe = Join-Path $pythonDir "dist\app.exe"

if (-not $SkipSign -and -not $PfxPath -and -not $CertThumbprint) {
    Write-Error @"
Provide signing credentials or skip signing explicitly:
  -CertThumbprint '<SHA1 thumbprint>'   (cert in CurrentUser\My), or
  -PfxPath 'D:\path\codesign.pfx'       (password prompted unless -PfxPassword), or
  -SkipSign                             (build only; unsigned EXE)
"@
}

if (-not (Test-Path -LiteralPath $specPath)) {
    Write-Error "app.spec not found at: $specPath"
}

$pythonCmd = Get-PythonCommand
if (-not $pythonCmd) {
    Write-Error "Python not found. Add Python to PATH or install the Windows 'py' launcher."
}

Push-Location $pythonDir
try {
    Write-Host "Installing / upgrading PyInstaller (user site ok)..." -ForegroundColor Cyan
    & $pythonCmd.Exe @($pythonCmd.ArgsPrefix + @("-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel")) 2>$null
    & $pythonCmd.Exe @($pythonCmd.ArgsPrefix + @("-m", "pip", "install", "pyinstaller>=6,<7"))

    if ($LASTEXITCODE -ne 0) { Write-Error "pip install pyinstaller failed." }

    $prevEmotivPyiDebug = $env:EMOTIV_PYI_DEBUG
    try {
        $env:EMOTIV_PYI_DEBUG = if ($Debug) { "1" } else { "0" }
        if ($Debug) {
            Write-Host "PyInstaller: console enabled (-Debug)." -ForegroundColor Yellow
        }
        else {
            Write-Host "PyInstaller: windowed (no console)." -ForegroundColor Cyan
        }
        Write-Host "Building EXE with PyInstaller..." -ForegroundColor Cyan
        & $pythonCmd.Exe @($pythonCmd.ArgsPrefix + @("-m", "PyInstaller", "--noconfirm", "--clean", "app.spec"))
        if ($LASTEXITCODE -ne 0) { Write-Error "PyInstaller failed." }
    }
    finally {
        if ($null -eq $prevEmotivPyiDebug) {
            Remove-Item Env:\EMOTIV_PYI_DEBUG -ErrorAction SilentlyContinue
        }
        else {
            $env:EMOTIV_PYI_DEBUG = $prevEmotivPyiDebug
        }
    }
}
finally {
    Pop-Location
}

if (-not (Test-Path -LiteralPath $distExe)) {
    Write-Error "Expected output not found: $distExe"
}

Write-Host "Built: $distExe" -ForegroundColor Green

if ($SkipSign) {
    Write-Host "SkipSign: not signing." -ForegroundColor Yellow
    exit 0
}

$signTool = Find-SignTool
if (-not $signTool) {
    Write-Error "signtool.exe not found. Install the Windows SDK (Desktop development with C++ / Windows SDK) or Windows Kit."
}

Write-Host "Using signtool: $signTool" -ForegroundColor Cyan

$signArgs = @(
    "sign",
    "/v",
    "/fd", "SHA256",
    "/td", "SHA256",
    "/tr", $TimestampUrl
)

if ($CertThumbprint) {
    $signArgs += @("/sha1", $CertThumbprint.Trim())
}
else {
    if (-not (Test-Path -LiteralPath $PfxPath)) {
        Write-Error "PFX not found: $PfxPath"
    }
    $signArgs += @("/f", (Resolve-Path -LiteralPath $PfxPath).Path)
    if ($PfxPassword) {
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($PfxPassword)
        try {
            $plain = [Runtime.InteropServices.Marshal]::PtrToStringUni($bstr)
            $signArgs += @("/p", $plain)
        }
        finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) | Out-Null
        }
    }
    else {
        $sec = Read-Host "PFX password" -AsSecureString
        $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
        try {
            $plain = [Runtime.InteropServices.Marshal]::PtrToStringUni($bstr)
            $signArgs += @("/p", $plain)
        }
        finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) | Out-Null
        }
    }
}

$signArgs += $distExe

Write-Host "Signing $distExe ..." -ForegroundColor Cyan
& $signTool @signArgs
if ($LASTEXITCODE -ne 0) { Write-Error "signtool failed with exit code $LASTEXITCODE" }

Write-Host "Signed successfully." -ForegroundColor Green
Write-Host "Verify: Get-AuthenticodeSignature -FilePath '$distExe'" -ForegroundColor Gray
