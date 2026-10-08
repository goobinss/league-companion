@echo off
cd /d "%~dp0"
py -3 -m league_companion %*
if errorlevel 1 (
  echo.
  echo Install Python 3.11 or newer with Tcl/Tk and the Python launcher enabled.
  pause
)
