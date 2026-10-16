@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONPATH=D:\codex\black-white-review-site\python"
"C:\Users\18110\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" scripts\manage.py serve
pause
