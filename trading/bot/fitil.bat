@echo off
rem Fitil: yalnizca sinyal modu (emir gondermez). Emir icin sonuna --trade ekleyin.
cd /d "%~dp0"
set "PY=python"
py -3 --version >nul 2>&1 && set "PY=py -3"
%PY% -m fitil --config config.json %*
pause
