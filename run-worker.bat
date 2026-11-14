@echo off
cd /d "%~dp0"
.venv\Scripts\python.exe -m celery -A coderun2026_starter worker --pool=threads --concurrency=16 --loglevel=info
pause
