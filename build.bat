@echo off
rem Build the exe (output: dist\<app name>.exe). The actual steps are in build.py.
cd /d "%~dp0"
set PYTHONUTF8=1

rem Install pinned build tools (fixed versions so a rebuild gives the same result)
python -m pip install --quiet "pyinstaller==6.22.3" "comtypes==1.5.0" || goto :error

python build.py || goto :error
pause
exit /b 0

:error
echo.
echo Build failed.
pause
exit /b 1
