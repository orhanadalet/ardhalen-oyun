@echo off
rem Fitil: yalnizca sinyal modu (emir gondermez). Emir icin sonuna --trade ekleyin.
rem MetaTrader5 paketi Python 3.10-3.14 destekler: uyumlu surum ozellikle aranir.
cd /d "%~dp0"
set "PY="
for %%V in (3.13 3.12 3.14 3.11 3.10) do (
  if not defined PY py -%%V --version >nul 2>&1 && set "PY=py -%%V"
)
if not defined PY set "PY=python"
echo Kullanilan Python: %PY%
%PY% -m fitil --config config.json %*
pause
