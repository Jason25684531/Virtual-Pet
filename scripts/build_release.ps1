# Build the Windows release: .\scripts\build_release.ps1
# Uses only the project venv; never touches system Python.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root "venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Project venv not found: $python" }

Write-Host "[build] Checking build dependencies (nuitka / cython / ordered-set / zstandard)"
& $python -m pip install --disable-pip-version-check -q nuitka cython ordered-set zstandard
if ($LASTEXITCODE -ne 0) { throw "Failed to install build dependencies" }

# No MSVC on this machine: Nuitka and Cython both use the winlibs MinGW64 that Nuitka manages.
# Nuitka's built-in downloader can corrupt the zip on some networks, so fetch it with curl.
$gccTag = "15.2.0posix-13.0.0-msvcrt-r6"
$gccDir = Join-Path $env:LOCALAPPDATA "Nuitka\Nuitka\Cache\downloads\gcc\x86_64\$gccTag"
if (-not (Test-Path (Join-Path $gccDir "mingw64\bin\gcc.exe"))) {
    Write-Host "[build] Downloading MinGW64 ($gccTag)"
    New-Item -ItemType Directory -Force $gccDir | Out-Null
    $zip = Join-Path $gccDir "winlibs.zip"
    curl.exe -L --retry 3 -o $zip "https://github.com/brechtsanders/winlibs_mingw/releases/download/$gccTag/winlibs-x86_64-posix-seh-gcc-15.2.0-mingw-w64msvcrt-13.0.0-r6.zip"
    if ($LASTEXITCODE -ne 0) { throw "MinGW64 download failed" }
    Expand-Archive -Force $zip $gccDir
    Remove-Item $zip
}

& $python (Join-Path $PSScriptRoot "build_release.py")
if ($LASTEXITCODE -ne 0) { throw "Build failed" }
