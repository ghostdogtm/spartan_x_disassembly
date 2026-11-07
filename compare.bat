@echo off
cd /d "%~dp0"
call "%~dp0assemble.bat"
if errorlevel 1 exit /b 1
python tools/compare.py
