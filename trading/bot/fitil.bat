@echo off
rem Fitil: yalnizca sinyal modu (emir gondermez). Emir icin sonuna --trade ekleyin.
cd /d "%~dp0"
python -m fitil --config config.json %*
pause
