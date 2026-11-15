@echo off
rem Fitil: MT5 fiyat gecmisini disa aktarir (emir gondermez). MT5 acik ve hesaba giris yapilmis olmali.
cd /d "%~dp0"
set "PY=python"
py -3 --version >nul 2>&1 && set "PY=py -3"
%PY% tools\export_mt5_history.py --config config.json --per-symbol --out ..\veri %*
pause
