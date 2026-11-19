@echo off
rem RecordYourResearch - Windows launcher
rem First run: creates .venv and installs requirements. If an install failed or requirements.txt changed, it re-installs.
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONUTF8=1
set "VPY=.venv\Scripts\python.exe"
if exist "%VPY%" goto deps

echo [RecordYourResearch] 第一次运行：创建虚拟环境 .venv / first run: creating .venv ...
set "PYEXE="
for %%v in (3.12 3.13 3.14 3.11 3.10) do (
  if not defined PYEXE (
    py -%%v -c "import sys" >nul 2>&1 && set "PYEXE=py -%%v"
  )
)
if not defined PYEXE (
  python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1 && set "PYEXE=python"
)
if not defined PYEXE (
  echo 没找到 Python 3.10 或更高版本：请从 https://www.python.org/downloads/ 安装，安装时勾选 "Add python.exe to PATH"。
  echo Python 3.10+ not found. Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
  pause
  exit /b 1
)
%PYEXE% -m venv .venv
if errorlevel 1 (
  echo 创建 .venv 失败（上面是具体原因）/ failed to create .venv, see the error above
  if exist .venv rmdir /s /q .venv
  pause
  exit /b 1
)

:deps
"%VPY%" -c "import hashlib,pathlib,sys; h=hashlib.sha256(pathlib.Path('requirements.txt').read_bytes()).hexdigest(); m=pathlib.Path('.venv/.ryr-deps'); sys.exit(0 if m.exists() and m.read_text().strip()==h else 1)" >nul 2>&1
if errorlevel 1 (
  echo [RecordYourResearch] 安装依赖 / installing requirements ...
  "%VPY%" -m pip install --upgrade pip
  "%VPY%" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo 依赖安装失败：检查网络后再运行一次 start.bat；仍然不行就删除 .venv 文件夹再试。
    echo pip install failed: check your network and run start.bat again; if it keeps failing, delete the .venv folder.
    pause
    exit /b 1
  )
  "%VPY%" -c "import hashlib,pathlib; pathlib.Path('.venv/.ryr-deps').write_text(hashlib.sha256(pathlib.Path('requirements.txt').read_bytes()).hexdigest())"
)

"%VPY%" run.py --open %*
pause
