@echo off
rem 双击运行，不留黑框：pythonw.exe 是无控制台版本的解释器。
cd /d "%~dp0"
start "" pythonw app.py
