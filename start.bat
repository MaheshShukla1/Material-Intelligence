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

python main.py
pause
