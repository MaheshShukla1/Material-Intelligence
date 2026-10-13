@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>&1 && (set PY=py -3) || (set PY=python)

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  %PY% -m venv .venv
)

call ".venv\Scripts\activate.bat"
python -m pip install --quiet --upgrade pip
python -m pip install --quiet -r requirements.txt

rem ---- Admin password for Site Progress Rebuild. Keep this line ABOVE "python main.py".
set "MI_ADMIN_PASSWORD=Businessman!@#"

rem ---- If an OLD server is still running on this port, the new one cannot start and the browser keeps talking to the old one.
if not defined PORT set "PORT=8000"
netstat -ano | findstr /R /C:":%PORT% .*LISTENING" >nul
if not errorlevel 1 (
  echo.
  echo  Port %PORT% is already in use: an OLD server is still running.
  echo  Close its window, or in this window run:
  echo      netstat -ano ^| findstr :%PORT%
  echo      taskkill /PID number-from-last-column /F
  echo  Then start this file again.
  echo.
  pause
  exit /b 1
)

if defined MI_ADMIN_PASSWORD (echo Admin password: SET) else (echo Admin password: NOT SET)
echo Running from: %CD%
python main.py
pause
