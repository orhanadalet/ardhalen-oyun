# Ardhalen Trading: MT5 portföy botu

Forex major pariteler, altın ve petrol için çok sembollü trend takip sistemi.
Oyun sitesinden bağımsızdır; yalnızca bu klasörde yaşar.

**Durum:** Python araştırma tarafı test edildi. MQL5 kodu yazıldı ama henüz derlenmedi
ve hiçbir gerçek piyasa verisinde sınanmadı. Stratejinin kâr ettiğine dair kanıt yoktur.

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
