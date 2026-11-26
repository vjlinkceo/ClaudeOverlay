@echo off
cd /d "%~dp0"
echo [1/3] Creating virtual environment...
if not exist .venv\Scripts\python.exe (
  py -3.12 -m venv .venv 2>nul || py -3 -m venv .venv
)
if not exist .venv\Scripts\python.exe (
  echo Python not found. Install Python 3.12 from https://www.python.org/downloads/
  pause
  exit /b 1
)
echo [2/3] Installing packages...
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 (
  echo Installation failed. Check the errors above, then run setup.bat again.
  pause
  exit /b 1
)
echo [3/3] Checking for NVIDIA GPU...
where nvidia-smi >nul 2>nul
if %errorlevel%==0 (
  echo NVIDIA GPU found: installing CUDA libraries for fast speech recognition ^(~1.2 GB^)...
  .venv\Scripts\python.exe -m pip install -r requirements-gpu.txt
) else (
  echo No NVIDIA GPU: speech recognition will run on CPU.
)
if not exist .env copy .env.example .env >nul
echo.
echo Done. Run start.bat: on the first start the app asks for your API key.
pause
