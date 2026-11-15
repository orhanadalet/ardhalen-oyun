@echo off
rem Fitil: MT5 fiyat gecmisini disa aktarir (emir gondermez). MT5 acik ve hesaba giris yapilmis olmali.
cd /d "%~dp0"
python tools\export_mt5_history.py --config config.json --per-symbol --out ..\veri %*
pause
