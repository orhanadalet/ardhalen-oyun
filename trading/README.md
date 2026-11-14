# Ardhalen Trading

Forex major pariteler, altın ve petrol için işlem altyapısı. Oyun sitesinden bağımsızdır;
yalnızca bu klasörde yaşar. İki ayrı sistem içerir:

| Sistem | Yer | Durum |
|---|---|---|
| **Fitil**: M15 gün içi Python botu | `bot/` (bkz. `bot/README.md`) | Altyapı test edildi ve incelendi. MT5 bağlantısı gerçek terminalde çalıştırılmadı. Gerçek EURUSD verisinde geri çekilme kurulumu 2019-20'de kazandırdı, 2017'de kaybettirdi; kanıt henüz yeterli değil. |
| Günlük trend portföyü | `research/fxlab/`, `mql5/` | Gerçek günlük veride maliyet sonrası kâr etmedi (aşağıda). Canlıya alınmaz. |

Testler: `cd trading && python -m unittest discover -s tests`

---

# Günlük trend portföyü (arşiv)

## İlk gerçek veri sonucu (Kasım 2026)

FRED günlük verisi, 2000-2026, 7 major parite + WTI + Brent (altın yok; günde tek fiyat,
gün içi stop yok; swap sabit varsayım):

| Senaryo | Yıllık getiri | Sharpe | Azami düşüş | İşlem | Kazanma |
|---|---|---|---|---|---|
| Temel (%0,5 risk) | -%1,5 | -0,06 | -%63 | 2574 | %35 |
| Maliyetsiz | +%1,3 | 0,17 | -%47 | 2560 | %37 |
| Kısmi kâr 1R | -%1,9 | -0,16 | -%61 | 2580 | %42 |
| Stop 5 ATR | -%0,3 | 0,01 | -%39 | 1419 | %34 |

Walk-forward (5 yıl eğitim, 1 yıl test, 21 dilim): yıllık -%2,7, 21 dilimin 5'i pozitif.
2000-2013 arası Sharpe 0,24; 2013-2026 arası -0,40.

**Sonuç: bu trend takip kuralları bu enstrümanlarda maliyet sonrası para kazandırmıyor.
EA bu haliyle canlıya ya da demoya alınmamalı.** Altyapı (motor, risk sınırları, testler)
geçerli; değişmesi gereken sinyal.

## İçerik

| Yol | Ne |
|---|---|
| `research/fxlab/` | Python backtest motoru: maliyetler, portföy risk sınırları, walk-forward |
| `tests/` | Motorun testleri |
| `mql5/Experts/ArdhalenPortfolioTrend.mq5` | Aynı kuralları uygulayan EA |
| `mql5/Include/Ardhalen/` | EA'nın sinyal ve risk kütüphaneleri |
| `data/` | MT5'ten dışa aktardığınız CSV'ler (depoya girmez) |

## Strateji kuralları

- **Sinyal:** 20/60/120 günlük Donchian kırılımlarının ortalaması. Üçünden en az ikisi
  aynı yöndeyse giriş, skor sıfıra ya da ters yöne dönünce çıkış.
- **Stop:** 3 × ATR(20); her gün kapanışta pozisyon lehine ilerler.
- **Boyut:** işlem başına özkaynağın %0,5'i riske edilir.
- **Portföy:** toplam açık risk en fazla %4, tek USD yönünde net risk en fazla %2,
  günlük kayıp %2'yi aşarsa o gün yeni giriş yok (son madde yalnızca EA'da).

- **Kısmi kâr (isteğe bağlı, şimdilik yalnızca Python'da):** `Params(partial_r=1.0)` ile fiyat
  stop mesafesi kadar lehe gidince pozisyonun yarısı kapatılır ve stop girişe çekilir.
  Kazanma oranını yükseltir, büyük kazançları küçültür.

## 1. Python testleri

```
cd trading
python -m unittest discover -s tests -v
```

## 2. Gerçek veriyle backtest

MT5'te: Görünüm > Semboller > sembolü seç > Çubuklar > D1 > tarih aralığı > Dışa Aktar.
Dosyaları `data/` altına şu adlarla koyun: `EURUSD.csv`, `GBPUSD.csv`, `USDJPY.csv`,
`USDCHF.csv`, `AUDUSD.csv`, `USDCAD.csv`, `NZDUSD.csv`, `XAUUSD.csv`, `WTI.csv`, `BRENT.csv`.

```
cd trading/research
python -m fxlab --data ../data --symbols EURUSD GBPUSD USDJPY XAUUSD WTI
python -m fxlab --data ../data --symbols EURUSD GBPUSD USDJPY XAUUSD WTI --wf
```

Maliyet varsayımları `research/fxlab/instruments.py` içindedir ve brokerınızın gerçek
değerleri değildir; spread, komisyon ve swap'ı kendi sembol özelliklerinizle güncelleyin.

## 3. EA'yı MT5'e kurma

1. MT5'te Dosya > Veri Klasörünü Aç.
2. `mql5/Experts/ArdhalenPortfolioTrend.mq5` dosyasını `MQL5/Experts/` altına,
   `mql5/Include/Ardhalen/` klasörünü `MQL5/Include/` altına kopyalayın.
3. MetaEditor'da EA'yı açıp F7 ile derleyin. Hata çıkarsa hata listesini aynen iletin.
4. `InpSymbols` girdisine Market Watch'taki tam sembol adlarını yazın.
5. Strategy Tester: D1, "Every tick based on real ticks", herhangi bir sembolün grafiği.
6. Sonra demo hesap. Gerçek hesap kararı sizindir.
