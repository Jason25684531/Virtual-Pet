# 產生 Windows 發布版：.\scripts\build_release.ps1
# 只使用專案 venv，不碰系統 Python。
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [Text.Encoding]::UTF8  # 中文訊息在重新導向時不變成問號
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root "venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "找不到專案 venv：$python" }

Write-Host "[build] 檢查 build dependencies（nuitka / cython / ordered-set / zstandard）"
& $python -m pip install --disable-pip-version-check -q nuitka cython ordered-set zstandard
if ($LASTEXITCODE -ne 0) { throw "安裝 build dependencies 失敗" }

# 本機沒有 MSVC：Nuitka 與 Cython 都使用 Nuitka 管理的 winlibs MinGW64。
# Nuitka 內建的下載器在部分網路環境會讓 zip 損毀，所以改用 curl。
$gccTag = "15.2.0posix-13.0.0-msvcrt-r6"
$gccDir = Join-Path $env:LOCALAPPDATA "Nuitka\Nuitka\Cache\downloads\gcc\x86_64\$gccTag"
if (-not (Test-Path (Join-Path $gccDir "mingw64\bin\gcc.exe"))) {
    Write-Host "[build] 下載 MinGW64（$gccTag）"
    New-Item -ItemType Directory -Force $gccDir | Out-Null
    $zip = Join-Path $gccDir "winlibs.zip"
    curl.exe -L --retry 3 -o $zip "https://github.com/brechtsanders/winlibs_mingw/releases/download/$gccTag/winlibs-x86_64-posix-seh-gcc-15.2.0-mingw-w64msvcrt-13.0.0-r6.zip"
    if ($LASTEXITCODE -ne 0) { throw "MinGW64 下載失敗" }
    Expand-Archive -Force $zip $gccDir
    Remove-Item $zip
}

# Lively 官方安裝程式與授權（GPLv3），只下載一次，快取在 scripts\vendor\。
$vendor = Join-Path $PSScriptRoot "vendor"
New-Item -ItemType Directory -Force $vendor | Out-Null
$lively = Join-Path $vendor "lively_setup_x86_full_v2210.exe"
if (-not (Test-Path $lively)) {
    Write-Host "[build] 下載 Lively 安裝程式"
    curl.exe -L --retry 3 -o $lively "https://github.com/lively-community/lively/releases/download/v2.2.1.0/lively_setup_x86_full_v2210.exe"
    if ($LASTEXITCODE -ne 0) { throw "Lively 下載失敗" }
}
$livelyLicense = Join-Path $vendor "lively_LICENSE"
if (-not (Test-Path $livelyLicense)) {
    curl.exe -L --retry 3 -o $livelyLicense "https://raw.githubusercontent.com/lively-community/lively/core-separation/LICENSE"
    if ($LASTEXITCODE -ne 0) { throw "Lively LICENSE 下載失敗" }
}

# Playwright Chromium 不存在時先安裝（安裝在使用者的 ms-playwright 快取，build 會從那裡複製）。
$browsers = if ($env:PLAYWRIGHT_BROWSERS_PATH) { $env:PLAYWRIGHT_BROWSERS_PATH } else { Join-Path $env:LOCALAPPDATA "ms-playwright" }
if (-not (Get-ChildItem $browsers -Filter "chromium-*" -Directory -ErrorAction SilentlyContinue)) {
    & $python -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw "Playwright Chromium 安裝失敗" }
}

& $python (Join-Path $PSScriptRoot "build_release.py") @args
if ($LASTEXITCODE -ne 0) { throw "Build 失敗" }
