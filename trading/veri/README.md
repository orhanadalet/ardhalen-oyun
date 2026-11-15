# Veri klasörü

`tools/export_mt5_history.py` (ya da `bot/veri_aktar.bat`) ile üretilen `fitil_veri_*.zip` dosyalarını
buraya yükleyin. Zip içinde yalnızca fiyat çubukları ve sembol özellikleri vardır; hesap numarası, şifre ya da
bakiye yoktur.

Backtest bu klasörü doğrudan okur:

```
cd trading\bot
python -m fitil.backtest --config config.json --data ..\veri --commission 0 --no-swap
```
