@echo off
rem VirtualPet restart: close the running VirtualPet.exe, then relaunch (Lively / Ollama / ComfyUI are prepared automatically by VirtualPet.exe).
setlocal
cd /d "%~dp0"
taskkill /IM VirtualPet.exe /F >nul 2>&1
timeout /t 1 /nobreak >nul
start "" "%~dp0VirtualPet.exe"
