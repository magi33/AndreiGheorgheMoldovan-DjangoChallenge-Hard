@echo off
cd /d "%~dp0"
py -3.12 -m venv .venv
if errorlevel 1 goto failed
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto failed
.venv\Scripts\python.exe manage.py migrate
if errorlevel 1 goto failed
echo.
echo Setup complete. Open run-worker.bat and run-web.bat.
pause
exit /b 0
:failed
echo.
echo Setup failed. Copy the error above and ask for help.
pause
exit
