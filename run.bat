@echo off
rem 啟動 ECHOES 桌面寵物 - 直接使用 venv Python 避免 Windows App Execution Alias 問題
cd /d "%~dp0"
.venv\Scripts\python.exe main.py %*
