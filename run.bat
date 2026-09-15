@echo off
setlocal
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$root=(Resolve-Path '.').Path; Get-CimInstance Win32_Process | Where-Object { $_.Name -in 'python.exe','pythonw.exe' -and $_.CommandLine -and $_.CommandLine.Contains($root) -and $_.CommandLine -match 'main\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; Get-Process Lively -ErrorAction SilentlyContinue | Stop-Process -Force; Start-Sleep -Milliseconds 750; $pkg=Get-AppxPackage -Name '12030rocksdanister.LivelyWallpaper'; if($pkg){ $exe=Join-Path $pkg.InstallLocation 'Build\Lively.exe'; if(Test-Path $exe){ Start-Process $exe } else { Start-Process 'shell:AppsFolder\12030rocksdanister.LivelyWallpaper_97hta09mmv6hy!App' } } else { Write-Host 'Lively Wallpaper not found; continuing without it.' }; $python=Join-Path $root 'venv\Scripts\pythonw.exe'; if(-not (Test-Path $python)){ $python=Join-Path $root 'venv\Scripts\python.exe' }; if(-not (Test-Path $python)){ throw 'Python virtual environment not found.' }; Start-Process $python -ArgumentList ('\"'+(Join-Path $root 'main.py')+'\"') -WorkingDirectory $root; Start-Sleep -Seconds 5; if(-not (Get-CimInstance Win32_Process | Where-Object { $_.Name -in 'python.exe','pythonw.exe' -and $_.CommandLine -and $_.CommandLine.Contains($root) -and $_.CommandLine -match 'main\.py' })){ throw 'ECHOES did not stay running.' }; Write-Host 'ECHOES restart verified.'"
if errorlevel 1 (
  echo Restart failed. Run "venv\Scripts\python.exe main.py" to see the error.
  exit /b 1
)

echo Restart complete.
