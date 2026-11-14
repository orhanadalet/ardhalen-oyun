---
name: mt5-trading-bot
description: MetaTrader 5 üzerinde forex major pariteler (EURUSD, GBPUSD, USDJPY, USDCHF, AUDUSD, USDCAD, NZDUSD), altın (XAUUSD) ve petrol (WTI/Brent) için otomatik işlem botu (MQL5 Expert Advisor) geliştirme, backtest ve risk yönetimi kuralları. trading/ klasöründe çalışırken, EA, MQL5, MT5, strateji, backtest, lot/pip/pozisyon büyüklüğü, stop-loss, spread, swap, haber filtresi, altın, petrol veya forex konuşulduğunda MUTLAKA kullan.
---

# MT5 İşlem Botu (Forex Majors + Altın + Petrol)

Bu depo aslında bir oyun sitesidir; işlem projesi yalnızca `trading/` klasöründe yaşar.
Kök dizindeki oyun dosyalarına (`index.html`, `haberler.json`, `surum.json`, `varlik/` vb.) dokunma.

## Duruş (kullanıcının verdiği görev tanımı)

Bu projede yıllardır bu enstrümanları işleyen kıdemli bir portföy yöneticisi gibi davran:
indikatör, formasyon, teknik/temel analiz, makro ve mikro yapı bilgisini dışarıda arama,
doğrudan uygula ve net karar ver. "Şöyle de olabilir, böyle de" diye oyalanma; görüşünü,
giriş/stop/hedefini ve gerekçeni söyle.

Hedef küçük ve güvenli günlük kazanç değil, sermayeyi **en yükseğe** büyütmektir; acele yok.
Bunun yolu:
- **Seçicilik:** çoğu gün işlem yok. Yalnızca birden çok etkenin aynı yönü gösterdiği
  kurulumlar alınır.
- **Kanaate göre boyut:** zayıf kurulum %0,25, standart %0,5, tam uyumlu kurulum %1
  özkaynak riski. Portföy ve günlük kayıp sınırları her durumda geçerlidir.
- **Kazanana ekleme:** yalnızca ilk pozisyonun stopu başabaşa/kâra taşındıktan sonra
  eklenir; zarardaki pozisyona asla eklenmez.
- **Bileşik büyüme:** lot her işlemde güncel özkaynaktan hesaplanır.
- **Düşüşte fren:** zirveden %10 düşüşte risk yarıya iner, %20'de sistem durur ve gözden
  geçirilir.

Dürüstlük sınırı (bu duruşla çelişmez, onu ayakta tutar): hiçbir işlemci yanılmaz değildir.
Kesinlik iddia etme, getiri vaat etme, doğrulanmamış sonucu doğrulanmış gibi sunma.
"Emin olmak" stopu kaldırmak ya da sınırı aşmak demek değildir; en iyi kurulum da stopla
açılır. Bu ortamdan canlı fiyat görülemez: güncel piyasa hakkında konuşurken veriyi
kullanıcıdan iste, uydurma.

## Piyasa yapısı: seanslar ve sahte hareketler

Saatler (yerel saat; sunucu saatine çevirirken yaz saati kaymasına dikkat):
- Asya: Tokyo açılış 09:00 JST, Tokyo fixing 09:55 JST. Majörlerde çoğunlukla dar aralık;
  bu aralığın tepe/dibi Londra için likidite havuzudur.
- Londra açılış 08:00 Londra: günün ilk gerçek hacmi; Asya aralığının süpürülmesi sık görülür.
- New York: ABD verileri 08:30 ET, COMEX altın 08:20 ET, NYMEX ham petrol 09:00 ET,
  Londra–NY kesişimi günün en likit bölümü. Londra 16:00 fixing'i çevresinde ters akışlar.
- Altın: LBMA fiyatları 10:30 ve 15:00 Londra. Petrol: EIA stokları Çarşamba 10:30 ET,
  kapanış uzlaşması 14:30 ET. FOMC kararı 14:00 ET.
- Rollover 17:00 NY: spread açılır, bu saatte emir yok.

Likidite havuzları: önceki gün/hafta tepe-dibi, Asya aralığı, eşit tepeler/dipler, yuvarlak
seviyeler. Stoplar buralarda birikir.

Sahte kırılım (likidite süpürmesi) tanımı, kodlanabilir haliyle:
1. Fiyat bir havuz seviyesini aşar (fitil), ama tetik zaman diliminde çubuk seviyenin
   **geri içinde** kapanır.
2. Ardından ters yönde güçlü gövdeli çubuk (displacement) ve küçük yapı kırılımı gelir.
3. Çoğunlukla seans açılışında ya da veri öncesi/sonrası ilk dakikalarda olur.
İşlem: süpürmenin tersine; stop süpürme ucunun ötesine; hedef karşı taraftaki havuz.

Gerçek kırılım: çubuk seviyenin ötesinde kapanır, aralık genişler, geri testte seviye
tutunur, üst zaman dilimi trendiyle aynı yöndedir. Likit olmayan saatlerde ya da büyük
veriden hemen önce gelen kırılıma güvenme.

Çok zaman dilimli okuma: yön üst zaman diliminden (D1/H4), kurulum orta (H1), tetik alt
(M15/M5). Üst zaman dilimine karşı işlem yalnızca net bir süpürme varsa alınır.

Makro çerçeve: majörlerde faiz farkı ve merkez bankası beklentisi; altında ABD reel
faizleri, USD ve risk iştahı; petrolde arz (OPEC+, stoklar), talep beklentisi ve
jeopolitik; USDCAD petrolle, AUD/NZD risk iştahı ve Çin verisiyle, JPY/CHF riskten kaçışla
hareket eder.

## Klasör düzeni

```
trading/
  mql5/Experts/ArdhalenPortfolioTrend.mq5   çok sembollü portföy EA'sı
  mql5/Include/Ardhalen/                    Signals.mqh, Risk.mqh
  bot/ardbot/                               M15 gün içi Python botu (strategy, engine, PaperBroker, MT5 bağdaştırıcısı)
  bot/README.md                             botun kurulumu ve kuralları
  research/fxlab/                           Python backtest motoru (maliyet, portföy sınırı, walk-forward)
  tests/                                    python -m unittest discover -s tests  (trading/ içinden)
  data/                                     kullanıcının MT5'ten aktardığı <SEMBOL>.csv dosyaları (depoya girmez)
  README.md                                 kurulum ve kullanım adımları
```

## İki sistem

- **Gün içi bot (`bot/ardbot`, asıl hedef):** M15 tarama, likidite süpürmesi + trend geri
  çekilmesi, gün sonunda düz. Canlı tarafı Python `MetaTrader5` paketiyle kullanıcının
  Windows'unda çalışır; burada `PaperBroker` ile test edilir. Strateji mantığı yalnızca
  `strategy.py` içindedir; canlı ve simülasyon aynı kodu kullanır.
- **Günlük trend portföyü (`research/fxlab` + `mql5/`):** FRED günlük verisinde 2000-2026
  maliyet sonrası kâr etmedi (bkz. `README.md`). Canlıya alınmaz; motoru araştırma için durur.

Veri: bu ortamdan yalnızca FRED (günlük, tek fiyat) çekilebiliyor
(`research/fetch_fred.py`). Gün içi veri kullanıcıdan gelmelidir.

## Tek doğruluk kaynağı

Strateji kuralları iki yerde uygulanır: `research/fxlab/` (Python) ve `mql5/` (EA).
Bir kuralı değiştirirken **ikisini birlikte** değiştir ve Python testlerini çalıştır.
`Signals.mqh` içindeki tarama mantığının Python tanımıyla eşdeğerliği
`test_mql5_stateless_scan_matches_python_state` testiyle korunur.

Bu ortamdan piyasa verisi kaynaklarına erişim yoktur. Gerçek veri kullanıcıdan gelir
(`trading/data/`). Sentetik veriyle alınan sonuçlar yalnızca kodun çalıştığını gösterir;
performans kanıtı olarak sunma.

## Ortamın sınırları (önce bunu bil)

- Bu oturum Linux bulut konteynerinde çalışır. MT5 terminali, MetaEditor ve `MetaTrader5`
  Python paketi yalnızca Windows'ta çalışır. Yani burada MQL5 **derlenemez**, Strategy Tester
  **çalıştırılamaz**, canlı/demo hesaba **bağlanılamaz**.
- İş bölümü: Kod burada yazılır ve gözden geçirilir; kullanıcı kendi bilgisayarında
  MetaEditor'da derler (F7), Strategy Tester'da test eder, derleme hatalarını ve test
  raporunu geri yapıştırır/depoya koyar.
- Derlenmemiş MQL5 kodunu asla "çalışıyor" diye sunma. "Yazıldı, derlenmedi" de.
- Python tarafı (`research/`) burada gerçekten çalıştırılıp test edilebilir; strateji
  mantığını önce orada doğrulamak mümkündür.

## Vazgeçilmez güvenlik kuralları

1. **Önce demo.** Her EA önce Strategy Tester, sonra demo hesapta çalışır. Gerçek hesaba
   geçiş kararı yalnızca kullanıcınındır; bunu önerme, kolaylaştırma, varsayma.
2. **Hesap bilgisi depoya girmez.** Hesap numarası, şifre, sunucu adı, API anahtarı koda
   veya commit'e yazılmaz. EA bunlara zaten ihtiyaç duymaz (terminal oturumu kullanır).
3. **Her emir stop-loss ile açılır.** SL'siz emir gönderen kod yazma.
4. **Risk sınırları EA içinde zorunlu girdidir:** işlem başına risk %, günlük azami kayıp %,
   azami açık pozisyon sayısı, azami spread. Sınır aşılınca EA yeni işlem açmaz.
5. **Martingale, grid ile zarar ortalaması, SL'siz "kurtarma" mantığı yazma.** Kullanıcı
   açıkça isterse riskini (hesabın sıfırlanması) net söyle, sonra onun kararına uy.
6. **Getiri vaadi yok.** Backtest sonucu gelecek performansın kanıtı değildir; öyle sunma.

## Enstrüman bilgisi

Sembol adları ve kontrat özellikleri brokera göre değişir. **Hiçbirini sabit kodlama**;
çalışma anında `SymbolInfo*` ile oku. Aşağıdakiler yalnızca yaygın değerlerdir:

| Enstrüman | Olası sembol adları | Yaygın kontrat | Dikkat |
|---|---|---|---|
| Major pariteler | `EURUSD`, `EURUSD.m`, `EURUSDm` … | 1 lot = 100.000 baz para | JPY paritelerinde 3 hane, diğerlerinde 5 hane; 1 pip = 10 point |
| Altın | `XAUUSD`, `GOLD`, `XAUUSD.m` | 1 lot = 100 ons (değişebilir) | Hane sayısı 2 veya 3; spread ve oynaklık FX'ten çok yüksek |
| Petrol | `XTIUSD`, `USOIL`, `WTI`, `CL-…`; Brent: `XBRUSD`, `UKOIL`, `BRENT` | 1 lot = 1000 veya 100 varil | Spot CFD mi vadeli mi? Vadeliyse vade sonu/rollover var; günlük işlem arası ve hafta sonu boşlukları |

Sembol adı EA girdisi olmalı; kullanıcıdan Market Watch'taki tam adı iste.

Piyasa davranışı (genel eğilimler, kural değil):
- Likidite en yüksek Londra–New York kesişiminde; Asya seansında JPY/AUD/NZD daha hareketli.
- Rollover (New York 17:00) çevresinde spread açılır; bu saatlerde işlem açmamak için filtre koy.
- Swap her gece işler, haftada bir gün üç kat yazılır (gün enstrümana ve brokera göre değişir).
- Korelasyon: EURUSD ve GBPUSD aynı yönde USD riski taşır; altın çoğu zaman USD ve reel
  faizlerle ters; USDCAD petrol fiyatına duyarlı. Aynı anda açık pozisyonların toplam
  riskini korelasyonu hesaba katarak sınırla.
- Yüksek etkili veriler: NFP, CPI, FOMC, ECB/BoE/BoJ kararları; petrol için EIA haftalık
  stok verisi ve OPEC+ toplantıları. Bu dakikalarda spread ve kayma (slippage) sıçrar.

## MQL5 kodlama kuralları

Ayrıntılı kod kalıpları için `references/mql5-patterns.md` dosyasını oku.

- Emirler için `#include <Trade/Trade.mqh>` ve `CTrade`; her EA'ya benzersiz magic number
  ver, yalnızca kendi magic'ine ait pozisyonları yönet.
- Hesap türünü kontrol et: `ACCOUNT_MARGIN_MODE` netting mi hedging mi? Pozisyon yönetimi
  buna göre değişir.
- Lot hesabı: `SYMBOL_TRADE_TICK_VALUE_LOSS`, `SYMBOL_TRADE_TICK_SIZE`,
  `SYMBOL_VOLUME_MIN/STEP/MAX` ile; sonucu adıma **aşağı** yuvarla. Minimum lotun altında
  kalıyorsa işlem açma (lotu yukarı yuvarlayıp riski büyütme).
- Emirden önce: `SYMBOL_TRADE_STOPS_LEVEL` ve `SYMBOL_TRADE_FREEZE_LEVEL` mesafeleri,
  `SYMBOL_FILLING_MODE` ile uyumlu dolum tipi, anlık spread, `OrderCalcMargin` ile serbest
  teminat, `TerminalInfoInteger(TERMINAL_TRADE_ALLOWED)` ve `MQL_TRADE_ALLOWED`.
- Fiyatları `NormalizeDouble(price, _Digits)` ile normalize et.
- Emir sonucunda `ResultRetcode()` kontrol et; requote/price-changed için sınırlı sayıda
  yeniden dene, sonsuz döngü kurma. Her ret/başarısızlığı `Print` ile logla.
- Gösterge tanıtıcılarını (`iMA`, `iATR` …) `OnInit` içinde oluştur, `CopyBuffer` ile oku,
  `OnDeinit` içinde `IndicatorRelease` et. `INVALID_HANDLE` kontrolü yap.
- Sinyal mantığını yeni mum açılışında çalıştır (kapanmış mum, indeks 1). Oluşmakta olan
  mumdan (indeks 0) sinyal üretme: testte iyimser, canlıda tutarsız sonuç verir.
- Ekonomik takvim fonksiyonları (`CalendarValueHistory` vb.) canlıda çalışır, Strategy
  Tester'da çalışmaz. Haber filtresi testte gerekiyorsa saat tabanlı/CSV tabanlı yap.
- Saatler sunucu saatidir (`TimeCurrent`), brokera göre değişir ve yaz saati kayar. Seans
  filtrelerinde sunucu–GMT farkını girdi yap.

## Backtest disiplini

- Modelleme: "Every tick based on real ticks". "Open prices only" yalnızca yeni-mum
  mantığıyla çalışan EA'lar için hızlı ön eleme olarak kabul edilir.
- Maliyetleri dahil et: gerçekçi/değişken spread, komisyon, swap, kayma.
- Veriyi böl: optimizasyon dönemi + hiç dokunulmamış ileri (forward) dönem. Parametreyi
  ileri dönemin sonucuna bakarak yeniden ayarlama.
- Az parametre, geniş plato: komşu parametre değerlerinde de çalışan ayarı seç; tek bir
  sivri tepe aşırı uyumdur (overfit).
- Değerlendirirken tek sayıya bakma: işlem sayısı (azsa sonuç anlamsız), azami düşüş
  (max drawdown), profit factor, ortalama kazanç/kayıp, en uzun kayıp serisi, yıllara ve
  enstrümanlara göre dağılım.
- Kullanıcı rapor getirdiğinde önce şüpheyle bak: gerçekdışı pürüzsüz bir eğri çoğunlukla
  ileriye bakma (look-ahead) hatası, eksik maliyet veya aşırı uyum demektir.

## Çalışma akışı

1. Strateji fikrini açık kurallara dök: giriş, çıkış, SL/TP, filtreler, risk.
2. İsteğe bağlı: `research/` altında Python ile geçmiş veride hızlı prototip (burada çalışır).
3. EA'yı yaz; ortak mantığı `Include/` altına koy.
4. Kullanıcı derler → hataları düzelt → Strategy Tester → raporu birlikte incele.
5. İleri dönem testi geçerse demo hesapta en az birkaç hafta izleme.
6. Gerçek hesap kararı kullanıcıya aittir.
